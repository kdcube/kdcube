# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Host vault service entrypoint (runs on the host, under the vault user).

Thin by design: environment to paths, then ``HostVaultServer`` over the
focused modules. Nothing here is KDCube-runtime code; the process owns the
root keys, the durable store, the trust registry, and the audit log.

Environment:
  KDCUBE_HOST_VAULT_HOME     base directory (default /var/lib/kdcube-host-vault)
    <home>/tls/server.crt|server.key|ca.crt    server identity + issuing CA cert
    <home>/root-keys/                          FileRootKeyProvider directory (default)
    <home>/store/                              FileDurableSecretStore root
    <home>/trust.json                          TrustRegistry (identify-only here)
    <home>/audit.log                           FileAuditSink (append-only JSON lines)
  KDCUBE_HOST_VAULT_BIND     host (default 127.0.0.1)
  KDCUBE_HOST_VAULT_PORT     port (default 7781)
  KDCUBE_HOST_VAULT_ROOT_KEY_PROVIDER
                             root-key custody: file (default) or module:factory,
                             factory(home) -> RootKeyProvider (e.g. TEE/HSM-backed)

Enrollment, revocation, and root-key rotation are operator actions through
``hostvaultctl.py`` against the same home; the server picks registry changes
up on the next connection.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from kdcube_ai_app.infra.secrets.host_vault.audit import FileAuditSink
from kdcube_ai_app.infra.secrets.host_vault.identity import TrustRegistry
from kdcube_ai_app.infra.secrets.host_vault.keys import load_root_key_provider
from kdcube_ai_app.infra.secrets.host_vault.service import HostVaultService
from kdcube_ai_app.infra.secrets.host_vault.storage import FileDurableSecretStore
from kdcube_ai_app.infra.secrets.host_vault.transport import HostVaultServer, ServerTLS

LOGGER = logging.getLogger("kdcube.host_vault.server")


def build(home: Path) -> tuple[HostVaultService, ServerTLS]:
    tls_dir = home / "tls"
    service = HostVaultService(
        store=FileDurableSecretStore(home / "store", load_root_key_provider(home)),
        registry=TrustRegistry(home / "trust.json"),
        audit=FileAuditSink(home / "audit.log"),
    )
    tls = ServerTLS(cert_file=tls_dir / "server.crt", key_file=tls_dir / "server.key", ca_file=tls_dir / "ca.crt")
    return service, tls


def main() -> int:
    logging.basicConfig(level=os.getenv("KDCUBE_HOST_VAULT_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    home = Path(os.getenv("KDCUBE_HOST_VAULT_HOME", "/var/lib/kdcube-host-vault"))
    service, tls = build(home)
    server = HostVaultServer(
        tls=tls,
        handler=lambda body, peer: service.handle(body, peer_cert_pem=peer),
        host=os.getenv("KDCUBE_HOST_VAULT_BIND", "127.0.0.1"),
        port=int(os.getenv("KDCUBE_HOST_VAULT_PORT", "7781")),
    )
    LOGGER.info("host vault listening on %s:%s home=%s", *server.address, home)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
