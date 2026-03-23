import os

from .model.io.upbound.dev.meta.e2etest import v1alpha1 as e2etest
from .model.io.k8s.apimachinery.pkg.apis.meta import v1 as metav1
from .model.io.k8s.apimachinery.pkg.apis.core.meta import v1 as corev1meta
from .model.io.k8s.api.core import v1 as corev1
from .model.cloud.mbcp.data.cache import v1alpha2 as cachev1alpha2
from .model.io.upbound.m.azure.clusterproviderconfig import v1beta1 as azurepcv1beta1
from .model.io.crossplane.m.kubernetes.clusterproviderconfig import v1alpha1 as kubepcv1alpha1
from .model.io.upbound.m.azure.resourcegroup import v1beta1 as rgv1beta1
from .model.io.upbound.m.azure.network.virtualnetwork import v1beta1 as vnetv1beta1
from .model.io.upbound.m.azure.network.subnet import v1beta1 as subnetv1beta1
from .model.io.k8s.api.rbac import v1 as rbacv1

# Exercises the full cloud path: Azure Redis is provisioned and becomes Ready.
# Verifies that the composition switches active backend to "cloud" and publishes
# the connection secret pointing at the Azure Redis endpoint.
#
# Provisions a self-contained Azure environment (resource group, VNet, subnet)
# required by the Cache composition's resource group and subnet selectors.

_LOCATION = "westeurope"
_ARD_ID = "ARD-001"
_APP = "e2e-app"

azure_creds = os.environ.get("UP_CLOUD_CREDENTIALS", "")

# Grant provider-kubernetes service account permissions to manage workload
# resources in the default namespace (required for fallback Redis Objects).
provider_kubernetes_rbac = rbacv1.ClusterRoleBinding(
    apiVersion="rbac.authorization.k8s.io/v1",
    kind="ClusterRoleBinding",
    metadata=corev1meta.ObjectMeta(name="provider-kubernetes-admin"),
    roleRef=rbacv1.RoleRef(
        apiGroup="rbac.authorization.k8s.io",
        kind="ClusterRole",
        name="cluster-admin",
    ),
    subjects=[rbacv1.Subject(
        kind="Group",
        apiGroup="rbac.authorization.k8s.io",
        name="system:serviceaccounts:crossplane-system",
    )],
)

azure_secret = corev1.Secret(
    apiVersion="v1",
    kind="Secret",
    metadata=corev1meta.ObjectMeta(
        name="azure-creds",
        namespace="crossplane-system",
    ),
    stringData={"credentials": azure_creds},
)

azure_provider_config = azurepcv1beta1.ClusterProviderConfig(
    apiVersion="azure.m.upbound.io/v1beta1",
    kind="ClusterProviderConfig",
    metadata=metav1.ObjectMeta(name="azure-provider"),
    spec=azurepcv1beta1.Spec(
        credentials=azurepcv1beta1.Credentials(
            source="Secret",
            secretRef=azurepcv1beta1.SecretRef(
                key="credentials",
                name="azure-creds",
                namespace="crossplane-system",
            ),
        ),
    ),
)

kubernetes_provider_config = kubepcv1alpha1.ClusterProviderConfig(
    apiVersion="kubernetes.m.crossplane.io/v1alpha1",
    kind="ClusterProviderConfig",
    metadata=metav1.ObjectMeta(name="kubernetes-provider"),
    spec=kubepcv1alpha1.Spec(
        credentials=kubepcv1alpha1.Credentials(source="InjectedIdentity"),
    ),
)

resource_group = rgv1beta1.ResourceGroup(
    apiVersion="azure.m.upbound.io/v1beta1",
    kind="ResourceGroup",
    metadata=metav1.ObjectMeta(
        name="rg-e2e-ard-001",
        namespace="default",
        labels={"ccoe.mbcp.cloud/ardId": _ARD_ID},
    ),
    spec=rgv1beta1.Spec(
        providerConfigRef=rgv1beta1.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        forProvider=rgv1beta1.ForProvider(location=_LOCATION),
    ),
)

virtual_network = vnetv1beta1.VirtualNetwork(
    apiVersion="network.azure.m.upbound.io/v1beta1",
    kind="VirtualNetwork",
    metadata=metav1.ObjectMeta(name="vnet-e2e-ard-001", namespace="default"),
    spec=vnetv1beta1.Spec(
        providerConfigRef=vnetv1beta1.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        forProvider=vnetv1beta1.ForProvider(
            location=_LOCATION,
            addressSpace=["10.0.0.0/16"],
            resourceGroupNameRef=vnetv1beta1.ResourceGroupNameRef(
                name="rg-e2e-ard-001",
            ),
        ),
    ),
)

subnet = subnetv1beta1.Subnet(
    apiVersion="network.azure.m.upbound.io/v1beta1",
    kind="Subnet",
    metadata=metav1.ObjectMeta(
        name="snet-cache-e2e-ard-001",
        namespace="default",
        labels={
            "ccoe.mbcp.cloud/ardId": _ARD_ID,
            "ccoe.mbcp.cloud/subnet-type": "cache",
        },
    ),
    spec=subnetv1beta1.Spec(
        providerConfigRef=subnetv1beta1.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        forProvider=subnetv1beta1.ForProvider(
            addressPrefixes=["10.0.1.0/24"],
            resourceGroupNameRef=subnetv1beta1.ResourceGroupNameRef(
                name="rg-e2e-ard-001",
            ),
            virtualNetworkNameRef=subnetv1beta1.VirtualNetworkNameRef(
                name="vnet-e2e-ard-001",
            ),
        ),
    ),
)

xr = cachev1alpha2.Cache(
    apiVersion="data.mbcp.cloud/v1alpha2",
    kind="Cache",
    metadata=metav1.ObjectMeta(
        name="e2e-cache",
        namespace="default",
        labels={"ccoe.mbcp.cloud/solution": "e2e-solution", "ai": "enabled"},
    ),
    spec=cachev1alpha2.Spec(
        providerConfigRef=cachev1alpha2.ProviderConfigRef(
            kind="ClusterProviderConfig",
            name="azure-provider",
        ),
        parameters=cachev1alpha2.Parameters(
            application=_APP,
            ardId=_ARD_ID,
            sku="xs",
        ),
    ),
)

test = e2etest.E2ETest(
    metadata=metav1.ObjectMeta(name="e2etest-cache"),
    spec=e2etest.Spec(
        crossplane=e2etest.Crossplane(
            version="2.1.4-up.2",
            autoUpgrade=e2etest.AutoUpgrade(channel="Rapid"),
        ),
        defaultConditions=["Ready"],
        initResources=[
            provider_kubernetes_rbac.model_dump(exclude_unset=True, by_alias=True),
        ],
        extraResources=[
            azure_secret.model_dump(exclude_unset=True, by_alias=True),
            azure_provider_config.model_dump(exclude_unset=True, by_alias=True),
            kubernetes_provider_config.model_dump(exclude_unset=True, by_alias=True),
        ],
        manifests=[
            resource_group.model_dump(exclude_unset=True, by_alias=True),
            virtual_network.model_dump(exclude_unset=True, by_alias=True),
            subnet.model_dump(exclude_unset=True, by_alias=True),
            xr.model_dump(exclude_unset=True, by_alias=True),
        ],
        timeoutSeconds=2400,
        skipDelete=True
    ),
)
