"""Provider-neutral delegated KDCube management client contracts."""

from kdcube_cli.management.client import ManagementClient
from kdcube_cli.management.errors import ManagementCliError
from kdcube_cli.management.models import (
    APPLICATION_RELOAD,
    APPLICATION_SURFACES_READ,
    DEFAULT_MANAGEMENT_SCOPE,
    DEPLOYMENT_INSPECT,
    SECRET_DELETE,
    SECRET_METADATA_READ,
    SECRET_OPERATIONS,
    SECRET_VALUE_READ,
    SECRET_VALUE_WRITE,
    USER_PROPS_IMPORT,
    ConsentRecovery,
    ManagementDenial,
    ManagementRequest,
    ManagementResult,
    ManagementSecretTarget,
    ManagementTarget,
)
from kdcube_cli.management.presentation import management_view
from kdcube_cli.management.secret_descriptors import (
    SecretDescriptorExport,
    validate_secret_descriptor_export,
    write_secret_descriptors,
)
from kdcube_cli.management.secret_export import (
    BrowserSecretExportService,
    ExportedSecret,
    HttpxSecretExportTransport,
    SecretExportClient,
    SecretExportRequest,
    SecretExportResult,
    SecretExportStart,
    SecretExportTransport,
)
from kdcube_cli.management.secret_output import (
    validate_private_secret_output,
    write_private_secret,
)
from kdcube_cli.management.transport import (
    HttpxManagementTransport,
    ManagementTransport,
)

__all__ = [
    "APPLICATION_RELOAD",
    "APPLICATION_SURFACES_READ",
    "DEFAULT_MANAGEMENT_SCOPE",
    "DEPLOYMENT_INSPECT",
    "SECRET_DELETE",
    "SECRET_METADATA_READ",
    "SECRET_OPERATIONS",
    "SECRET_VALUE_READ",
    "SECRET_VALUE_WRITE",
    "USER_PROPS_IMPORT",
    "BrowserSecretExportService",
    "ConsentRecovery",
    "ExportedSecret",
    "HttpxManagementTransport",
    "HttpxSecretExportTransport",
    "ManagementCliError",
    "ManagementClient",
    "ManagementDenial",
    "ManagementRequest",
    "ManagementResult",
    "ManagementSecretTarget",
    "ManagementTarget",
    "ManagementTransport",
    "SecretDescriptorExport",
    "SecretExportClient",
    "SecretExportRequest",
    "SecretExportResult",
    "SecretExportStart",
    "SecretExportTransport",
    "management_view",
    "validate_private_secret_output",
    "validate_secret_descriptor_export",
    "write_private_secret",
    "write_secret_descriptors",
]
