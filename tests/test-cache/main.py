from .model.io.upbound.dev.meta.compositiontest import v1alpha1 as compositiontest
from .model.io.k8s.apimachinery.pkg.apis.meta import v1 as metav1
from .model.io.k8s.apimachinery.pkg.apis.core.meta import v1 as corev1meta
from .model.io.k8s.api.apps import v1 as appsv1
from .model.io.k8s.api.core import v1 as corev1
from .model.io.crossplane.m.kubernetes.object import v1alpha1 as kubeobjv1alpha2
from .model.io.upbound.m.azure.cache.rediscache import v1beta1 as rediscachev1beta1
from .model.io.upbound.m.azure.network.privateendpoint import v1beta1 as pev1beta1

_NAMESPACE = "default"
_APP = "example-app"
_REDIS_RESOURCE_ID = "/subscriptions/sub123/resourceGroups/rg-ard001/providers/Microsoft.Cache/Redis/redis-example-app"


def _kubeobj(name: str, manifest: dict) -> kubeobjv1alpha2.Object:
    return kubeobjv1alpha2.Object(
        apiVersion="kubernetes.m.crossplane.io/v1alpha1",
        kind="Object",
        metadata=metav1.ObjectMeta(name=name, namespace=_NAMESPACE),
        spec=kubeobjv1alpha2.Spec(
            forProvider=kubeobjv1alpha2.ForProvider(manifest=manifest),
            providerConfigRef=kubeobjv1alpha2.ProviderConfigRef(
                kind="ClusterProviderConfig",
                name="kubernetes-provider",
            ),
        ),
    )


_labels = {"app": f"redis-fallback-{_APP}"}

fallback_deployment = _kubeobj(
    f"redis-fallback-{_APP}",
    appsv1.Deployment(
        apiVersion="apps/v1",
        kind="Deployment",
        metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{_APP}", namespace=_NAMESPACE),
        spec=appsv1.DeploymentSpec(
            replicas=1,
            selector=corev1meta.LabelSelector(matchLabels=_labels),
            template=corev1.PodTemplateSpec(
                metadata=corev1meta.ObjectMeta(labels=_labels),
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
                                    name=f"redis-fallback-{_APP}",
                                    key="password",
                                ),
                            ),
                        )],
                    )],
                ),
            ),
        ),
    ).model_dump(exclude_unset=True, by_alias=True),
)

fallback_service = _kubeobj(
    f"redis-fallback-svc-{_APP}",
    corev1.Service(
        apiVersion="v1",
        kind="Service",
        metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{_APP}", namespace=_NAMESPACE),
        spec=corev1.ServiceSpec(
            selector=_labels,
            ports=[corev1.ServicePort(port=6379, targetPort=6379)],
            type="ClusterIP",
        ),
    ).model_dump(exclude_unset=True, by_alias=True),
)

fallback_configmap = _kubeobj(
    f"redis-fallback-cm-{_APP}",
    corev1.ConfigMap(
        apiVersion="v1",
        kind="ConfigMap",
        metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{_APP}", namespace=_NAMESPACE),
        data={"redis.conf": "bind 0.0.0.0\nprotected-mode yes\n"},
    ).model_dump(exclude_unset=True, by_alias=True),
)

fallback_secret = _kubeobj(
    f"redis-fallback-secret-{_APP}",
    corev1.Secret(
        apiVersion="v1",
        kind="Secret",
        metadata=corev1meta.ObjectMeta(name=f"redis-fallback-{_APP}", namespace=_NAMESPACE),
        type="Opaque",
    ).model_dump(exclude_unset=True, by_alias=True),
)

connection_secret = _kubeobj(
    f"cache-connection-{_APP}",
    corev1.Secret(
        apiVersion="v1",
        kind="Secret",
        metadata=corev1meta.ObjectMeta(name=f"cache-{_APP}", namespace=_NAMESPACE),
        type="Opaque",
    ).model_dump(exclude_unset=True, by_alias=True),
)

test_fallback = compositiontest.CompositionTest(
    metadata=metav1.ObjectMeta(name="test-cache-fallback-resources"),
    spec=compositiontest.Spec(
        compositionPath="apis/caches/composition.yaml",
        xrPath="examples/cache/example-cache.yaml",
        xrdPath="apis/caches/definition.yaml",
        assertResources=[
            fallback_deployment.model_dump(exclude_unset=True, by_alias=True),
            fallback_service.model_dump(exclude_unset=True, by_alias=True),
            fallback_configmap.model_dump(exclude_unset=True, by_alias=True),
            fallback_secret.model_dump(exclude_unset=True, by_alias=True),
        ],
        timeoutSeconds=120,
        validate=False,
    ),
)

cloud_redis = rediscachev1beta1.RedisCache(
    apiVersion="cache.azure.m.upbound.io/v1beta1",
    kind="RedisCache",
    metadata=metav1.ObjectMeta(
        name=f"redis-{_APP}",
        namespace=_NAMESPACE,
        labels={
            "ccoe.mbcp.cloud/solution": "example-solution",
            "ccoe.mbcp.cloud/application": _APP,
        },
    ),
    spec=rediscachev1beta1.Spec(
        providerConfigRef=rediscachev1beta1.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        forProvider=rediscachev1beta1.ForProvider(
            location="westeurope",
            skuName="Standard",
            family="C",
            capacity=1,
            redisVersion="6",
            nonSslPortEnabled=False,
            minimumTlsVersion="1.2",
            publicNetworkAccessEnabled=False,
            resourceGroupNameSelector=rediscachev1beta1.ResourceGroupNameSelector(
                matchLabels={"ccoe.mbcp.cloud/ardId": "ARD-001"},
                namespace=_NAMESPACE,
            ),
        ),
    ),
)

private_endpoint = pev1beta1.PrivateEndpoint(
    apiVersion="network.azure.m.upbound.io/v1beta1",
    kind="PrivateEndpoint",
    metadata=metav1.ObjectMeta(
        name=f"pe-redis-{_APP}",
        namespace=_NAMESPACE,
    ),
    spec=pev1beta1.Spec(
        providerConfigRef=pev1beta1.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        forProvider=pev1beta1.ForProvider(
            location="westeurope",
            resourceGroupNameSelector=pev1beta1.ResourceGroupNameSelector(
                matchLabels={"ccoe.mbcp.cloud/ardId": "ARD-001"},
                namespace=_NAMESPACE,
            ),
            subnetIdSelector=pev1beta1.SubnetIdSelector(
                matchLabels={
                    "ccoe.mbcp.cloud/ardId": "ARD-001",
                    "ccoe.mbcp.cloud/subnet-type": "cache",
                },
                namespace=_NAMESPACE,
            ),
            privateServiceConnection=pev1beta1.PrivateServiceConnection(
                name=f"psc-redis-{_APP}",
                isManualConnection=False,
                privateConnectionResourceId=_REDIS_RESOURCE_ID,
                subresourceNames=["redisCache"],
            ),
        ),
    ),
)

test_cloud_redis = compositiontest.CompositionTest(
    metadata=metav1.ObjectMeta(name="test-cache-cloud-redis"),
    spec=compositiontest.Spec(
        compositionPath="apis/caches/composition.yaml",
        xrPath="examples/cache/example-cache.yaml",
        xrdPath="apis/caches/definition.yaml",
        assertResources=[
            cloud_redis.model_dump(exclude_unset=True, by_alias=True),
        ],
        timeoutSeconds=120,
        validate=False,
    ),
)

_observed_redis_ready = rediscachev1beta1.RedisCache(
    apiVersion="cache.azure.m.upbound.io/v1beta1",
    kind="RedisCache",
    metadata=metav1.ObjectMeta(
        name=f"redis-{_APP}",
        namespace=_NAMESPACE,
        annotations={"crossplane.io/composition-resource-name": "redis-cloud"},
    ),
    spec=rediscachev1beta1.Spec(
        providerConfigRef=rediscachev1beta1.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        forProvider=rediscachev1beta1.ForProvider(
            location="westeurope",
            skuName="Standard",
            family="C",
            capacity=1,
        ),
    ),
    status=rediscachev1beta1.Status(
        atProvider=rediscachev1beta1.AtProvider(
            hostname=f"redis-{_APP}.redis.cache.windows.net",
            id=_REDIS_RESOURCE_ID,
            sslPort=6380.0,
        ),
        conditions=[rediscachev1beta1.Condition(
            type="Ready",
            status="True",
            reason="Available",
            lastTransitionTime="2026-03-01T10:00:00Z",
        )],
    ),
)

test_private_endpoint = compositiontest.CompositionTest(
    metadata=metav1.ObjectMeta(name="test-cache-private-endpoint"),
    spec=compositiontest.Spec(
        compositionPath="apis/caches/composition.yaml",
        xrPath="examples/cache/example-cache.yaml",
        xrdPath="apis/caches/definition.yaml",
        observedResources=[
            _observed_redis_ready.model_dump(exclude_unset=True, by_alias=True),
        ],
        assertResources=[
            private_endpoint.model_dump(exclude_unset=True, by_alias=True),
        ],
        timeoutSeconds=120,
        validate=False,
    ),
)

cloud_connection_secret = _kubeobj(
    f"cache-connection-{_APP}",
    corev1.Secret(
        apiVersion="v1",
        kind="Secret",
        metadata=corev1meta.ObjectMeta(name=f"cache-{_APP}", namespace=_NAMESPACE),
        type="Opaque",
    ).model_dump(exclude_unset=True, by_alias=True),
)

test_cloud_active = compositiontest.CompositionTest(
    metadata=metav1.ObjectMeta(name="test-cache-cloud-active-when-redis-ready"),
    spec=compositiontest.Spec(
        compositionPath="apis/caches/composition.yaml",
        xrPath="examples/cache/example-cache.yaml",
        xrdPath="apis/caches/definition.yaml",
        observedResources=[
            _observed_redis_ready.model_dump(exclude_unset=True, by_alias=True),
        ],
        assertResources=[
            cloud_connection_secret.model_dump(exclude_unset=True, by_alias=True),
        ],
        timeoutSeconds=120,
        validate=False,
    ),
)

_observed_fallback_deployed = kubeobjv1alpha2.Object(
    apiVersion="kubernetes.m.crossplane.io/v1alpha1",
    kind="Object",
    metadata=metav1.ObjectMeta(
        name=f"redis-fallback-{_APP}",
        namespace=_NAMESPACE,
        annotations={"crossplane.io/composition-resource-name": "fallback-deployment"},
    ),
    spec=kubeobjv1alpha2.Spec(
        forProvider=kubeobjv1alpha2.ForProvider(manifest={}),
        providerConfigRef=kubeobjv1alpha2.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="kubernetes-provider",
        ),
    ),
)

local_connection_secret = _kubeobj(
    f"cache-connection-{_APP}",
    corev1.Secret(
        apiVersion="v1",
        kind="Secret",
        metadata=corev1meta.ObjectMeta(name=f"cache-{_APP}", namespace=_NAMESPACE),
        type="Opaque",
    ).model_dump(exclude_unset=True, by_alias=True),
)

test_local_active = compositiontest.CompositionTest(
    metadata=metav1.ObjectMeta(name="test-cache-local-connection-when-fallback-deployed"),
    spec=compositiontest.Spec(
        compositionPath="apis/caches/composition.yaml",
        xrPath="examples/cache/example-cache.yaml",
        xrdPath="apis/caches/definition.yaml",
        observedResources=[
            _observed_fallback_deployed.model_dump(exclude_unset=True, by_alias=True),
        ],
        assertResources=[
            local_connection_secret.model_dump(exclude_unset=True, by_alias=True),
        ],
        timeoutSeconds=120,
        validate=False,
    ),
)

items = [
    test_fallback.model_dump(exclude_unset=True, by_alias=True),
    test_cloud_redis.model_dump(exclude_unset=True, by_alias=True),
    test_private_endpoint.model_dump(exclude_unset=True, by_alias=True),
    test_cloud_active.model_dump(exclude_unset=True, by_alias=True),
    test_local_active.model_dump(exclude_unset=True, by_alias=True),
]
