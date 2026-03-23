import hashlib

from crossplane.function import resource
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from .model.io.k8s.apimachinery.pkg.apis.meta import v1 as metav1
from .model.io.k8s.apimachinery.pkg.apis.core.meta import v1 as corev1meta
from .model.io.k8s.api.apps import v1 as appsv1
from .model.io.k8s.api.core import v1 as corev1
from .model.cloud.mbcp.data.cache import v1alpha2 as cachev1alpha2
from .model.io.crossplane.m.kubernetes.object import v1alpha1 as kubeobjv1alpha2
from .model.io.upbound.m.azure.cache.rediscache import v1beta1 as rediscachev1beta1
from .model.io.upbound.m.azure.network.privateendpoint import v1beta1 as pev1beta1

_SKU_MAP = {
    "xs": ("Basic",    "C", 0),
    "s":  ("Standard", "C", 1),
    "m":  ("Standard", "C", 3),
    "l":  ("Standard", "C", 4),
    "xl": ("Premium",  "P", 1),
}


def _make_object(
    name: str,
    namespace: str,
    manifest: dict,
    provider_config_name: str = "kubernetes-provider",
) -> kubeobjv1alpha2.Object:
    """Wrap a Kubernetes manifest in a provider-kubernetes Object MR."""
    return kubeobjv1alpha2.Object(
        metadata=metav1.ObjectMeta(name=name, namespace=namespace),
        spec=kubeobjv1alpha2.Spec(
            forProvider=kubeobjv1alpha2.ForProvider(manifest=manifest),
            providerConfigRef=kubeobjv1alpha2.ProviderConfigRef(
                kind="ClusterProviderConfig",
                name=provider_config_name,
            ),
        ),
    )


def _is_cloud_ready(req: fnv1.RunFunctionRequest) -> tuple[bool, str, int, str, str]:
    """Return (ready, hostname, ssl_port, resource_id, password) from observed RedisCache MR."""
    if "redis-cloud" not in req.observed.resources:
        return False, "", 6380, "", ""
    try:
        observed = req.observed.resources["redis-cloud"]
        redis_mr = rediscachev1beta1.RedisCache(
            **resource.struct_to_dict(observed.resource)
        )
        if redis_mr.status and redis_mr.status.conditions:
            for cond in redis_mr.status.conditions:
                if (
                    getattr(cond, "type", None) == "Ready"
                    and getattr(cond, "status", None) == "True"
                ):
                    hostname = ""
                    ssl_port = 6380
                    resource_id = ""
                    if redis_mr.status.atProvider:
                        hostname = redis_mr.status.atProvider.hostname or ""
                        ssl_port = int(redis_mr.status.atProvider.sslPort or 6380)
                        resource_id = redis_mr.status.atProvider.id or ""
                    password = observed.connection_details.get(
                        "attribute.primary_access_key", b""
                    ).decode()
                    return True, hostname, ssl_port, resource_id, password
    except Exception:
        pass
    return False, "", 6380, "", ""


def _resolve_solution_config(req: fnv1.RunFunctionRequest, ard_id: str) -> dict:
    """Resolve solution config from platform-injected observed resource (spec §4.2)."""
    defaults = {"location": "westeurope", "env": "dv"}
    key = f"solution-config-{ard_id}"
    if key not in req.observed.resources:
        return defaults
    data = resource.struct_to_dict(req.observed.resources[key].resource).get("data", {})
    return {
        "location": data.get("location", defaults["location"]),
        "env": data.get("env", defaults["env"]),
    }


def _connection_secret_values(
    host: str,
    port: int,
    password: str,
    ssl_enabled: bool,
    active_backend: str,
    db_index: int = 0,
) -> dict:
    """Build the 9-key stable connection secret values (FR-05)."""
    connection_string = (
        f"{host}:{port},password={password},"
        f"ssl={'true' if ssl_enabled else 'false'},"
        f"abortConnect=False,database={db_index}"
    )
    return {
        "DistributedCache__Servers__0__Address": host,
        "DistributedCache__Servers__0__Port": str(port),
        "DistributedCache__Username": "",
        "DistributedCache__Password": password,
        "DistributedCache__EnableSSL": str(ssl_enabled).lower(),
        "DistributedCache__DatabaseIndex": str(db_index),
        "DistributedCache__AbortConnect": "False",
        "RedisSettings__ConnectionString": connection_string,
        "RedisSettings__DbNum": str(db_index),
        "CacheBackend__Active": active_backend,
    }


def compose(req: fnv1.RunFunctionRequest, rsp: fnv1.RunFunctionResponse):
    """Cache composition function (FR-01 through FR-07).

    Always provisions:
    - Fallback Redis (Deployment + Service + ConfigMap + Secret) in workload namespace
    - Cloud Redis MR + Private Endpoint (Azure)

    Selects active backend based on observed cloud Redis Ready condition (FR-04.2).
    Publishes stable 9-key connection secret (FR-05) pointing to active backend.
    Updates XR status with backend, fallback, and cache context (FR-06).
    """
    observed_xr = cachev1alpha2.Cache(
        **resource.struct_to_dict(req.observed.composite.resource)
    )
    assert observed_xr.metadata is not None
    assert observed_xr.metadata.name is not None
    assert observed_xr.metadata.namespace is not None
    assert observed_xr.spec is not None
    assert observed_xr.spec.parameters is not None

    app = observed_xr.spec.parameters.application
    namespace = observed_xr.metadata.namespace
    xr_name = observed_xr.metadata.name
    ard_id = observed_xr.spec.parameters.ardId
    sku = observed_xr.spec.parameters.sku
    provider_config_name = (
        observed_xr.spec.providerConfigRef.name
        if observed_xr.spec.providerConfigRef
        else "azure-provider"
    )
    solution_labels = {
        "ccoe.mbcp.cloud/solution": (observed_xr.metadata.labels or {}).get(
            "ccoe.mbcp.cloud/solution", ""
        ),
        "ccoe.mbcp.cloud/application": app,
    }

    solution_config = _resolve_solution_config(req, ard_id)
    location = solution_config["location"]
    cloud_ready, cloud_host, cloud_port, redis_resource_id, cloud_password = _is_cloud_ready(req)

    # --- Fallback Redis (always provisioned, FR-04) ---

    fallback_password = hashlib.sha256(
        f"{namespace}/{xr_name}/fallback-redis".encode()
    ).hexdigest()[:48]
    fallback_labels = {"app": f"redis-fallback-{app}"}

    fallback_secret = _make_object(
        f"redis-fallback-secret-{app}", namespace,
        corev1.Secret(
            apiVersion="v1",
            kind="Secret",
            metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{app}", namespace=namespace),
            type="Opaque",
            stringData={"password": fallback_password},
        ).model_dump(exclude_unset=True, exclude_none=True, by_alias=True),
    )
    resource.update(rsp.desired.resources["fallback-secret"], fallback_secret)

    fallback_deployment = _make_object(
        f"redis-fallback-{app}", namespace,
        appsv1.Deployment(
            apiVersion="apps/v1",
            kind="Deployment",
            metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{app}", namespace=namespace),
            spec=appsv1.DeploymentSpec(
                replicas=1,
                selector=corev1meta.LabelSelector(matchLabels=fallback_labels),
                template=corev1.PodTemplateSpec(
                    metadata=corev1meta.ObjectMeta(labels=fallback_labels),
                    spec=corev1.PodSpec(
                        containers=[corev1.Container(
                            name="redis",
                            image="redis:7-alpine",
                            args=["--requirepass", "$(REDIS_PASSWORD)"],
                            ports=[corev1.ContainerPort(containerPort=6379)],
                            env=[corev1.EnvVar(
                                name="REDIS_PASSWORD",
                                valueFrom=corev1.EnvVarSource(
                                    secretKeyRef=corev1.SecretKeySelector(
                                        name=f"redis-fallback-{app}",
                                        key="password",
                                    ),
                                ),
                            )],
                        )],
                    ),
                ),
            ),
        ).model_dump(exclude_unset=True, exclude_none=True, by_alias=True),
    )
    resource.update(rsp.desired.resources["fallback-deployment"], fallback_deployment)

    fallback_service = _make_object(
        f"redis-fallback-svc-{app}", namespace,
        corev1.Service(
            apiVersion="v1",
            kind="Service",
            metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{app}", namespace=namespace),
            spec=corev1.ServiceSpec(
                selector=fallback_labels,
                ports=[corev1.ServicePort(port=6379, targetPort=6379)],
                type="ClusterIP",
            ),
        ).model_dump(exclude_unset=True, exclude_none=True, by_alias=True),
    )
    resource.update(rsp.desired.resources["fallback-service"], fallback_service)

    fallback_configmap = _make_object(
        f"redis-fallback-cm-{app}", namespace,
        corev1.ConfigMap(
            apiVersion="v1",
            kind="ConfigMap",
            metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{app}", namespace=namespace),
            data={"redis.conf": "bind 0.0.0.0\nprotected-mode yes\n"},
        ).model_dump(exclude_unset=True, exclude_none=True, by_alias=True),
    )
    resource.update(rsp.desired.resources["fallback-configmap"], fallback_configmap)

    # --- Cloud Redis MR (FR-01) ---

    sku_name, family, capacity = _SKU_MAP.get(sku, ("Standard", "C", 1))

    redis_cache = rediscachev1beta1.RedisCache(
        metadata=metav1.ObjectMeta(name=f"redis-{app}", namespace=namespace, labels=solution_labels),
        spec=rediscachev1beta1.Spec(
            providerConfigRef=rediscachev1beta1.ProviderConfigRef(
                kind="ClusterProviderConfig",
                name=provider_config_name,
            ),
            forProvider=rediscachev1beta1.ForProvider(
                location=location,
                skuName=sku_name,
                family=family,
                capacity=capacity,
                redisVersion="6",
                nonSslPortEnabled=False,
                minimumTlsVersion="1.2",
                publicNetworkAccessEnabled=False,
                resourceGroupNameSelector=rediscachev1beta1.ResourceGroupNameSelector(
                    matchLabels={"ccoe.mbcp.cloud/ardId": ard_id},
                    namespace=namespace,
                ),
            ),
        ),
    )
    resource.update(rsp.desired.resources["redis-cloud"], redis_cache)

    # --- Private Endpoint (NFR-03) ---
    # Only created once the RedisCache has been provisioned and has an Azure
    # resource ID — Azure rejects a PrivateEndpoint with an empty target ID.

    if redis_resource_id and sku_name != "Basic":
        private_endpoint = pev1beta1.PrivateEndpoint(
            metadata=metav1.ObjectMeta(name=f"pe-redis-{app}", namespace=namespace),
            spec=pev1beta1.Spec(
                providerConfigRef=pev1beta1.ProviderConfigRef(
                    kind="ClusterProviderConfig",
                    name=provider_config_name,
                ),
                forProvider=pev1beta1.ForProvider(
                    location=location,
                    resourceGroupNameSelector=pev1beta1.ResourceGroupNameSelector(
                        matchLabels={"ccoe.mbcp.cloud/ardId": ard_id},
                        namespace=namespace,
                    ),
                    subnetIdSelector=pev1beta1.SubnetIdSelector(
                        matchLabels={
                            "ccoe.mbcp.cloud/ardId": ard_id,
                            "ccoe.mbcp.cloud/subnet-type": "cache",
                        },
                        namespace=namespace,
                    ),
                    privateServiceConnection=pev1beta1.PrivateServiceConnection(
                        name=f"psc-redis-{app}",
                        isManualConnection=False,
                        privateConnectionResourceId=redis_resource_id,
                        subresourceNames=["redisCache"],
                    ),
                ),
            ),
        )
        resource.update(rsp.desired.resources["private-endpoint"], private_endpoint)

    # --- Connection secret (FR-05) ---
    # Only written once the active backend is confirmed ready to avoid stale secrets.

    fallback_deployed = "fallback-deployment" in req.observed.resources

    if cloud_ready:
        conn_values = _connection_secret_values(
            host=cloud_host, port=cloud_port, password=cloud_password,
            ssl_enabled=True, active_backend="cloud",
        )
    elif fallback_deployed:
        conn_values = _connection_secret_values(
            host=f"redis-fallback-{app}.{namespace}.svc.cluster.local",
            port=6379, password=fallback_password,
            ssl_enabled=False, active_backend="local",
        )
    else:
        conn_values = None

    if conn_values is not None:
        connection_secret = _make_object(
            f"cache-connection-{app}", namespace,
            corev1.Secret(
                apiVersion="v1",
                kind="Secret",
                metadata=corev1meta.ObjectMeta(name=f"cache-{app}", namespace=namespace),
                type="Opaque",
                stringData=conn_values,
            ).model_dump(exclude_unset=True, exclude_none=True, by_alias=True),
        )
        resource.update(rsp.desired.resources["connection-secret"], connection_secret)
        rsp.desired.composite.connection_details.update({k: v.encode() for k, v in conn_values.items()})

    # --- XR status (FR-06) ---

    rsp.desired.composite.resource["status"] = {
        "backend": {
            "active": "cloud" if cloud_ready else "local",
            "cloud": {"ready": cloud_ready},
            "fallback": {
                "ready": True,
                "serviceName": f"redis-fallback-{app}",
                "port": 6379,
            },
            "reason": "CloudReady" if cloud_ready else "CloudNotReady",
        },
        "cacheConnection": {
            "secretName": f"cache-{app}",
            "serviceName": f"cache-{app}",
            "port": 6380 if cloud_ready else 6379,
            "configMapName": f"redis-fallback-{app}",
        },
    }
