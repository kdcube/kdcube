# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Operator tool for the host vault home (the privileged installer side).

Two roles share one script so the flows stay in one place:

Host side (runs as the vault user against KDCUBE_HOST_VAULT_HOME):
  init --server-name NAME [--server-name NAME ...]
      issuing CA (ca/ca.key 0400 + tls/ca.crt), server certificate for the
      listed names (tls/server.crt, tls/server.key), first root key, empty
      trust registry.
  enroll --deployment-id ID --namespace T/P/APP [...] --csr FILE --out FILE
      one-use enrollment: the operator session IS the provisioning channel in
      this phase (ticket minted and consumed in one step), the CSR's own
      subject is discarded, the issued certificate goes to --out.
  rotate-identity --fingerprint FP --csr FILE --out FILE [--overlap SECONDS]
  revoke --fingerprint FP
  list
  rotate-root-key
      new root key becomes current and every committed record is rewrapped
      (values untouched, still readable).

Deployment side (runs INSIDE the deployment boundary, never on an operator
laptop):
  deployment-keygen --dir DIR
      private key (host-vault-client.key, 0400) + CSR (host-vault-client.csr)
      generated in place; only the CSR leaves DIR.
  deployment-install --dir DIR --cert FILE --ca FILE
      writes the issued certificate and CA beside the key.

No command prints, reads, or accepts a secret value.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kdcube_ai_app.infra.secrets.host_vault.identity import DeploymentKey, HostIssuingCA, TrustRegistry
from kdcube_ai_app.infra.secrets.host_vault.keys import load_root_key_provider
from kdcube_ai_app.infra.secrets.host_vault.storage import FileDurableSecretStore


def _home(args: argparse.Namespace) -> Path:
    return Path(args.home or os.getenv("KDCUBE_HOST_VAULT_HOME", "/var/lib/kdcube-host-vault"))


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_ca(home: Path) -> HostIssuingCA:
    return HostIssuingCA(key_pem=(home / "ca" / "ca.key").read_bytes(), cert_pem=(home / "tls" / "ca.crt").read_bytes())


def cmd_init(args: argparse.Namespace) -> int:
    home = _home(args)
    if (home / "ca" / "ca.key").exists():
        print(f"refusing: {home}/ca/ca.key already exists", file=sys.stderr)
        return 2
    ca = HostIssuingCA.generate()
    (home / "tls").mkdir(parents=True, exist_ok=True)
    (home / "tls" / "ca.crt").write_bytes(ca.cert_pem)
    key_pem, cert_pem = ca.issue_server(hostnames=list(args.server_name))
    _write_private(home / "tls" / "server.key", key_pem)
    (home / "tls" / "server.crt").write_bytes(cert_pem)
    _write_private(home / "ca" / "ca.key", ca.key_pem)
    key_id = load_root_key_provider(home).rotate()
    TrustRegistry(home / "trust.json", ca=ca)
    (home / "store").mkdir(parents=True, exist_ok=True)
    os.chmod(home / "store", 0o700)
    print(json.dumps({"home": str(home), "root_key": key_id, "server_names": list(args.server_name)}))
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    home = _home(args)
    registry = TrustRegistry(home / "trust.json", ca=_load_ca(home))
    ticket = registry.mint_ticket(deployment_id=args.deployment_id, namespaces=list(args.namespace), ttl_seconds=60)
    cert_pem, record = registry.enroll(ticket_id=ticket.ticket_id, csr_pem=Path(args.csr).read_bytes(), days=args.days)
    Path(args.out).write_bytes(cert_pem)
    print(json.dumps(record.to_dict()))
    return 0


def cmd_rotate_identity(args: argparse.Namespace) -> int:
    home = _home(args)
    registry = TrustRegistry(home / "trust.json", ca=_load_ca(home))
    cert_pem, record = registry.rotate(
        current_fingerprint=args.fingerprint, csr_pem=Path(args.csr).read_bytes(), days=args.days,
        overlap_seconds=args.overlap,
    )
    Path(args.out).write_bytes(cert_pem)
    print(json.dumps(record.to_dict()))
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    TrustRegistry(_home(args) / "trust.json").revoke(args.fingerprint)
    print(json.dumps({"revoked": args.fingerprint}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for record in TrustRegistry(_home(args) / "trust.json").records():
        print(json.dumps(record.to_dict()))
    return 0


def cmd_rotate_root_key(args: argparse.Namespace) -> int:
    home = _home(args)
    keys = load_root_key_provider(home)
    key_id = keys.rotate()
    rewrapped = FileDurableSecretStore(home / "store", keys).rewrap_all()
    print(json.dumps({"root_key": key_id, "rewrapped": rewrapped}))
    return 0


def cmd_deployment_keygen(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    if (directory / "host-vault-client.key").exists():
        print(f"refusing: {directory}/host-vault-client.key already exists", file=sys.stderr)
        return 2
    key = DeploymentKey.generate()
    _write_private(directory / "host-vault-client.key", key.pem)
    (directory / "host-vault-client.csr").write_bytes(key.csr())
    print(json.dumps({"csr": str(directory / "host-vault-client.csr")}))
    return 0


def cmd_deployment_install(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    (directory / "host-vault-client.crt").write_bytes(Path(args.cert).read_bytes())
    (directory / "host-vault-ca.crt").write_bytes(Path(args.ca).read_bytes())
    print(json.dumps({"identity": str(directory)}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hostvaultctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", help="vault home (default $KDCUBE_HOST_VAULT_HOME or /var/lib/kdcube-host-vault)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--server-name", action="append", required=True, help="DNS name or IP the vault answers on (repeatable)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("enroll")
    p.add_argument("--deployment-id", required=True)
    p.add_argument("--namespace", action="append", required=True, help="tenant/project/application or tenant/project/* (repeatable)")
    p.add_argument("--csr", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("rotate-identity")
    p.add_argument("--fingerprint", required=True)
    p.add_argument("--csr", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--overlap", type=int, default=24 * 3600)
    p.set_defaults(func=cmd_rotate_identity)

    p = sub.add_parser("revoke")
    p.add_argument("--fingerprint", required=True)
    p.set_defaults(func=cmd_revoke)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("rotate-root-key").set_defaults(func=cmd_rotate_root_key)

    p = sub.add_parser("deployment-keygen")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_deployment_keygen)

    p = sub.add_parser("deployment-install")
    p.add_argument("--dir", required=True)
    p.add_argument("--cert", required=True)
    p.add_argument("--ca", required=True)
    p.set_defaults(func=cmd_deployment_install)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
