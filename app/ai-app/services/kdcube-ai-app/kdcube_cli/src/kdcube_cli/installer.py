# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import re
import shutil
import json
import subprocess
import yaml
import subprocess
from dataclasses import dataclass
import secrets
import tempfile
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.control import Control
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text


ENV_FILES = [
    ".env",
    ".env.ingress",
    ".env.proc",
    ".env.metrics",
    ".env.postgres.setup",
    ".env.proxylogin",
]

DEFAULT_PG_PASSWORD = "postgres"
DEFAULT_REDIS_PASSWORD = "redispass"


DEFAULT_BUNDLES_JSON = [
    "AGENTIC_BUNDLES_JSON='{",
    "  \"default_bundle_id\": \"demo.bundle@1.0.0\",",
    "  \"bundles\": {",
    "        \"demo.bundle@1.0.0\": {",
    "          \"id\": \"demo.bundle@1.0.0\",",
    "          \"name\": \"Demo Bundle\",",
    "          \"path\": \"/bundles\",",
    "          \"module\": \"demo.entrypoint\",",
    "          \"singleton\": false,",
    "          \"description\": \"Example bundle used for quickstart.\"",
    "        }",
    "  }",
    "}'",
]


@dataclass
class EnvFile:
    path: Path
    lines: List[str]
    entries: Dict[str, Tuple[int, str]]


@dataclass
class PathsContext:
    lib_root: Path
    ai_app_root: Path
    docker_dir: Path
    sample_env_dir: Path
    workdir: Path
    config_dir: Path
    data_dir: Path


def is_placeholder(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip("'\"")
    if not stripped:
        return True
    if stripped.upper() in {"TENANT_ID", "PROJECT_ID"}:
        return True
    if "<" in stripped and ">" in stripped:
        return True
    if "/absolute/path" in stripped or "absolute/path" in stripped:
        return True
    if "path/to/" in stripped or stripped.startswith("path/to"):
        return True
    if "relative_path" in stripped.lower():
        return True
    if "platform-repo/" in stripped or "frontend-repo/" in stripped:
        return True
    if "..." in stripped:
        return True
    if "changeme" in stripped.lower():
        return True
    return False


def is_default_tenant_project(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip("'\"").lower()
    return stripped in {"default", "demo-tenant", "demo-project"}


def normalize_secrets_provider(value: Optional[object], *, default: str) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"local", "service", "sidecar", "secrets-service"}:
        return "secrets-service"
    if raw in {"aws", "aws-sm", "awssm"}:
        return "aws-sm"
    if raw in {"memory", "in-memory", "inmemory", "none", "env", "disabled"}:
        return "in-memory"
    if raw:
        return raw
    return default


def parse_env(lines: List[str]) -> Dict[str, Tuple[int, str]]:
    entries: Dict[str, Tuple[int, str]] = {}
    for idx, line in enumerate(lines):
        if not line or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        entries[key] = (idx, value)
    return entries


def update_env_value(env_file: EnvFile, key: str, value: str) -> None:
    if key in env_file.entries:
        idx, _ = env_file.entries[key]
        env_file.lines[idx] = f"{key}={value}"
    else:
        env_file.lines.append(f"{key}={value}")
    env_file.entries = parse_env(env_file.lines)


def update_if_placeholder(env_file: EnvFile, key: str, value: str) -> None:
    current = env_file.entries.get(key, (None, None))[1]
    if is_placeholder(current):
        update_env_value(env_file, key, value)


def _normalize_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return value


def _as_str(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return None


def _resolve_descriptor_path(
    value: Optional[str],
    *,
    repo_root: Optional[Path],
    descriptor_dir: Optional[Path],
) -> Optional[Path]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    if repo_root is not None:
        repo_candidate = repo_root / candidate
        if repo_candidate.exists():
            return repo_candidate
    if descriptor_dir is not None:
        descriptor_candidate = descriptor_dir / candidate
        if descriptor_candidate.exists():
            return descriptor_candidate
    if repo_root is not None:
        return repo_root / candidate
    if descriptor_dir is not None:
        return descriptor_dir / candidate
    return candidate


def _extract_multiline_value(env: EnvFile, key: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    start_idx = None
    for idx, line in enumerate(env.lines):
        if line.startswith(f"{key}="):
            start_idx = idx
            break
    if start_idx is None:
        return None, None, None
    value = env.lines[start_idx].split("=", 1)[1]
    end_idx = start_idx
    if value.count("'") % 2 == 1:
        while end_idx + 1 < len(env.lines):
            end_idx += 1
            value += "\n" + env.lines[end_idx]
            if env.lines[end_idx].count("'") % 2 == 1:
                break
    return value, start_idx, end_idx


def _format_json_multiline(key: str, data: Dict[str, object]) -> List[str]:
    json_text = json.dumps(data, indent=2)
    lines = json_text.splitlines()
    lines[0] = f"{key}='" + lines[0]
    lines[-1] = lines[-1] + "'"
    return lines


def _extract_tenant_project(env: EnvFile) -> Tuple[Optional[str], Optional[str]]:
    raw, _, _ = _extract_multiline_value(env, "GATEWAY_CONFIG_JSON")
    if raw is None:
        return None, None
    stripped = raw.strip()
    if stripped.startswith("'") and stripped.endswith("'"):
        json_text = stripped[1:-1]
    else:
        json_text = stripped
    try:
        data = json.loads(json_text)
        tenant = data.get("tenant")
        project = data.get("project")
        if tenant in {"<TENANT_ID>", "TENANT_ID"}:
            tenant = None
        if project in {"<PROJECT_ID>", "PROJECT_ID"}:
            project = None
        return tenant, project
    except json.JSONDecodeError:
        tenant_match = re.search(r'"tenant"\s*:\s*"([^"]+)"', json_text)
        project_match = re.search(r'"project"\s*:\s*"([^"]+)"', json_text)
        tenant = tenant_match.group(1) if tenant_match else None
        project = project_match.group(1) if project_match else None
        if tenant in {"<TENANT_ID>", "TENANT_ID"}:
            tenant = None
        if project in {"<PROJECT_ID>", "PROJECT_ID"}:
            project = None
        return tenant, project


def patch_gateway_config_json(env: EnvFile, tenant: str, project: str) -> None:
    raw, start_idx, end_idx = _extract_multiline_value(env, "GATEWAY_CONFIG_JSON")
    if raw is None:
        return

    stripped = raw.strip()
    if stripped.startswith("'") and stripped.endswith("'"):
        json_text = stripped[1:-1]
    else:
        json_text = stripped

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        updated = re.sub(r'"tenant"\s*:\s*"[^"]*"', f'"tenant":"{tenant}"', json_text)
        updated = re.sub(r'"project"\s*:\s*"[^"]*"', f'"project":"{project}"', updated)
        if updated != json_text:
            replace_multiline_block(env, "GATEWAY_CONFIG_JSON", [f"GATEWAY_CONFIG_JSON='{updated}'"])
        return

    data["tenant"] = tenant
    data["project"] = project
    replace_multiline_block(env, "GATEWAY_CONFIG_JSON", _format_json_multiline("GATEWAY_CONFIG_JSON", data))


def _load_json_file(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_gateway_descriptor(path: Path) -> Dict[str, object]:
    try:
        text = path.read_text()
    except Exception:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    try:
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_release_descriptor(path: Path) -> Dict[str, object]:
    try:
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_release_descriptor(path: Path, data: Dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    except Exception:
        pass


def _get_nested(dct: Dict[str, object], *keys: str) -> Optional[object]:
    cur: object = dct
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _set_nested(dct: Dict[str, object], keys: List[str], value: object) -> None:
    cur: Dict[str, object] = dct
    for key in keys[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[keys[-1]] = value


def _delete_nested(dct: Dict[str, object], keys: List[str]) -> None:
    cur: object = dct
    parents: List[Tuple[Dict[str, object], str]] = []
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return
        parents.append((cur, key))
        cur = cur.get(key)
    parent, last_key = parents[-1]
    parent.pop(last_key, None)
    # Clean up empty parent dicts.
    for parent, key in reversed(parents[:-1]):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)
        else:
            break

def _has_nested(dct: Dict[str, object], *keys: str) -> bool:
    cur: object = dct
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur.get(key)
    return True


def is_git_repo(path: Path) -> bool:
    return path.is_dir() and (path / ".git").is_dir()


def git_clone_or_update(console: Console, repo: str, ref: Optional[str], dest: Path) -> Path:
    repo_path = Path(repo).expanduser()
    if repo_path.exists():
        console.print(f"[dim]Using local frontend repo:[/dim] {repo_path}")
        return repo_path.resolve()

    dest.mkdir(parents=True, exist_ok=True)
    if is_git_repo(dest):
        try:
            subprocess.run(["git", "fetch", "--all", "--tags"], cwd=dest, check=True)
        except Exception:
            pass
    else:
        subprocess.run(["git", "clone", repo, str(dest)], check=True)
    if ref:
        try:
            subprocess.run(["git", "checkout", ref], cwd=dest, check=True)
        except Exception:
            try:
                subprocess.run(["git", "checkout", f"origin/{ref}"], cwd=dest, check=True)
            except Exception:
                console.print(f"[yellow]Warning: failed to checkout ref {ref} in {dest}[/yellow]")
    return dest.resolve()


def normalize_routes_prefix(value: Optional[str]) -> str:
    prefix = (value or "").strip()
    if not prefix:
        return "/chatbot"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if prefix != "/" and prefix.endswith("/"):
        prefix = prefix.rstrip("/")
    return prefix


def normalize_domain_host(value: Optional[str], *, keep_port: bool = False) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        host = parsed.netloc or parsed.path
    else:
        host = raw
    host = host.split("/")[0].strip()
    if not keep_port and host.startswith("[") and "]" in host:
        closing = host.find("]")
        return host[: closing + 1]
    if not keep_port and ":" in host:
        return host.split(":", 1)[0]
    return host


def sync_nginx_proxy_config(target_path: Path, ai_app_root: Path, template_rel: str) -> None:
    repo_root = ai_app_root.parent.parent
    src = repo_root / template_rel
    if not src.exists():
        return
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target_path)
    except Exception:
        return


def update_nginx_routes_prefix(path: Path, routes_prefix: str) -> None:
    routes_prefix = normalize_routes_prefix(routes_prefix)
    if not path.exists():
        return
    try:
        current = path.read_text()
    except Exception:
        return
    updated = current.replace("/chatbot", routes_prefix)
    if updated != current:
        path.write_text(updated)


def update_nginx_ssl_domain(path: Path, domain: str) -> None:
    domain = normalize_domain_host(domain)
    if not domain or not path.exists():
        return
    try:
        current = path.read_text()
    except Exception:
        return
    updated = current.replace("YOUR_DOMAIN_NAME", domain)
    if updated != current:
        path.write_text(updated)


def write_frontend_config(
    path: Path,
    tenant: str,
    project: str,
    token: str = "test-admin-token-123",
    *,
    template_path: Optional[Path] = None,
    cognito_region: Optional[str] = None,
    cognito_user_pool_id: Optional[str] = None,
    cognito_app_client_id: Optional[str] = None,
    routes_prefix: Optional[str] = None,
    company_name: Optional[str] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    template_data: Dict[str, object] = {}
    if template_path and template_path.exists():
        template_data = _load_json_file(template_path)

    data: Dict[str, object] = {}
    if path.exists():
        data = _load_json_file(path)

    merged: Dict[str, object] = {}
    merged.update(template_data)
    merged.update(data)

    merged["tenant"] = tenant
    merged["project"] = project
    if "tenant_id" in merged:
        merged["tenant_id"] = tenant
    if "project_id" in merged:
        merged["project_id"] = project
    if routes_prefix:
        merged["routesPrefix"] = routes_prefix
    else:
        merged.setdefault("routesPrefix", "/chatbot")

    auth = merged.get("auth") if isinstance(merged.get("auth"), dict) else {}
    auth_type = auth.get("authType") or "hardcoded"
    auth["authType"] = auth_type
    if auth_type == "hardcoded":
        if auth.get("token") in (None, "", "test-admin-token-123"):
            auth["token"] = token
    elif auth_type == "cognito":
        if "token" in auth:
            auth.pop("token", None)
        oidc_cfg = auth.get("oidcConfig") if isinstance(auth.get("oidcConfig"), dict) else {}
        if cognito_region and cognito_user_pool_id:
            oidc_cfg["authority"] = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"
        if cognito_app_client_id:
            oidc_cfg["client_id"] = cognito_app_client_id
        auth["oidcConfig"] = oidc_cfg
    elif auth_type == "delegated":
        if "token" in auth:
            auth.pop("token", None)
        if company_name:
            if auth.get("totpAppName") in (None, "", "COMPANY_NAME", "<COMPANY_NAME>"):
                auth["totpAppName"] = company_name
            if auth.get("totpIssuer") in (None, "", "COMPANY_NAME", "<COMPANY_NAME>"):
                auth["totpIssuer"] = company_name
        auth.setdefault("apiBase", "/auth/")
    merged["auth"] = auth

    path.write_text(json.dumps(merged, indent=2) + "\n")


def replace_multiline_block(env_file: EnvFile, key: str, new_lines: List[str]) -> None:
    start_idx = None
    for idx, line in enumerate(env_file.lines):
        if line.startswith(f"{key}="):
            start_idx = idx
            break
    if start_idx is None:
        if env_file.lines and env_file.lines[-1].strip():
            env_file.lines.append("")
        env_file.lines.extend(new_lines)
        env_file.entries = parse_env(env_file.lines)
        return

    end_idx = start_idx
    quote_open = env_file.lines[start_idx].count("'") % 2 == 1
    while quote_open and end_idx + 1 < len(env_file.lines):
        end_idx += 1
        if env_file.lines[end_idx].count("'") % 2 == 1:
            quote_open = False
    env_file.lines[start_idx : end_idx + 1] = new_lines
    env_file.entries = parse_env(env_file.lines)


def ensure_env_files(target_dir: Path, sample_env_dir: Path) -> None:
    for env_name in ENV_FILES:
        target = target_dir / env_name
        if target.exists():
            continue
        sample = sample_env_dir / env_name
        if not sample.exists():
            raise FileNotFoundError(f"Missing sample env file: {sample}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, target)


def ensure_assembly_template(target_path: Path, ai_app_root: Path) -> bool:
    if target_path.exists():
        return True
    src = ai_app_root / "deployment/assembly.yaml"
    if not src.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target_path)
    return True


def stage_assembly_descriptor(
    target_path: Path,
    *,
    source_path: Optional[Path],
    ai_app_root: Path,
) -> bool:
    if source_path and source_path.exists():
        if target_path.resolve() != source_path.resolve():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
        return True
    return ensure_assembly_template(target_path, ai_app_root)


def ensure_secrets_template(target_path: Path, ai_app_root: Path) -> bool:
    if target_path.exists():
        return True
    src = ai_app_root / "deployment/secrets.yaml"
    if not src.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target_path)
    return True


def stage_secrets_descriptor(
    target_path: Path,
    *,
    source_path: Optional[Path],
    ai_app_root: Path,
) -> bool:
    if source_path and source_path.exists():
        if target_path.resolve() != source_path.resolve():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
        return True
    return ensure_secrets_template(target_path, ai_app_root)


def ensure_bundles_template(target_path: Path, ai_app_root: Path) -> bool:
    if target_path.exists():
        return True
    src = ai_app_root / "deployment/bundles.yaml"
    if not src.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target_path)
    return True


def stage_bundles_descriptor(
    target_path: Path,
    *,
    source_path: Optional[Path],
    ai_app_root: Path,
) -> bool:
    if source_path and source_path.exists():
        if target_path.resolve() != source_path.resolve():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
        return True
    return ensure_bundles_template(target_path, ai_app_root)


def ensure_bundles_secrets_template(target_path: Path, ai_app_root: Path) -> bool:
    if target_path.exists():
        return True
    src = ai_app_root / "deployment/bundles.secrets.yaml"
    if not src.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target_path)
    return True


def stage_bundles_secrets_descriptor(
    target_path: Path,
    *,
    source_path: Optional[Path],
    ai_app_root: Path,
) -> bool:
    if source_path and source_path.exists():
        if target_path.resolve() != source_path.resolve():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
        return True
    return ensure_bundles_secrets_template(target_path, ai_app_root)


def ensure_gateway_template(target_path: Path, ai_app_root: Path) -> None:
    if target_path.exists():
        return
    src = ai_app_root / "deployment/gateway.yaml"
    if not src.exists():
        raise FileNotFoundError(f"Missing gateway template: {src}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target_path)


def stage_gateway_descriptor(
    target_path: Path,
    *,
    source_path: Optional[Path],
    ai_app_root: Path,
) -> None:
    if source_path and source_path.exists():
        if target_path.resolve() != source_path.resolve():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
        return
    ensure_gateway_template(target_path, ai_app_root)


def ensure_nginx_configs(target_dir: Path, ai_app_root: Path, docker_dir: Path) -> None:
    if docker_dir.name == "custom-ui-managed-infra":
        src_dir = ai_app_root / "deployment/docker/custom-ui-managed-infra/nginx/conf"
        names = (
            "nginx_ui.conf",
            "nginx_proxy.conf",
            "nginx_proxy_delegated.conf",
            "nginx_proxy_ssl_hardcoded.conf",
            "nginx_proxy_ssl_cognito.conf",
            "nginx_proxy_ssl_delegated_auth.conf",
        )
    else:
        src_dir = ai_app_root / "deployment/docker/all_in_one_kdcube/nginx/conf"
        names = ("nginx_ui.conf", "nginx_proxy.conf", "nginx_proxy_delegated.conf")
    for name in names:
        target = target_dir / name
        if target.exists():
            continue
        src = src_dir / name
        if not src.exists():
            # Older repos may not ship delegated/ssl variants; fall back to base proxy config if available.
            fallback = None
            if "ssl" in name:
                candidate = src_dir / "nginx_proxy_ssl_hardcoded.conf"
                if candidate.exists():
                    fallback = candidate
            if fallback is None:
                candidate = src_dir / "nginx_proxy.conf"
                if candidate.exists():
                    fallback = candidate
            if fallback is None:
                # No usable template in this repo; skip without failing.
                print(f"[kdcube-cli] Missing nginx config template: {src} (skipped)")
                continue
            src = fallback
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)


def ensure_local_dirs(data_dir: Path, logs_dir: Path) -> None:
    for path in [
        data_dir / "kdcube-storage",
        data_dir / "exec-workspace",
        data_dir / "bundle-storage",
        data_dir / "bundles",
        data_dir / "postgres",
        data_dir / "redis",
        data_dir / "clamav-db",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("chat-ingress", "chat-proc"):
        (logs_dir / subdir).mkdir(parents=True, exist_ok=True)
    for path in (logs_dir, logs_dir / "chat-ingress", logs_dir / "chat-proc"):
        try:
            os.chmod(path, 0o777)
        except Exception:
            pass


def compose_env(env_file: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env["COMPOSE_ENV_FILES"] = str(env_file)
    try:
        entries = parse_env(env_file.read_text().splitlines())
        for key, (_idx, value) in entries.items():
            env[key] = value.strip().strip("'\"")
    except Exception:
        pass
    return env


def list_compose_services(ctx: PathsContext, env_file: Path) -> List[str]:
    try:
        output = subprocess.check_output(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "config",
                "--services",
            ],
            cwd=ctx.docker_dir,
            env=compose_env(env_file),
            text=True,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]
    except Exception as exc:
        print(f"[kdcube-cli] Unable to list compose services: {exc}")
        return []


def apply_runtime_secrets(console: Console, ctx: PathsContext, secrets: Dict[str, str], env_file: Path) -> None:
    if not secrets:
        return
    if not wait_for_secrets_ready(console, ctx, env_file):
        console.print("[red]Secrets service not ready. Skipping secret injection.[/red]")
        return
    console.print("[dim]Injecting runtime secrets into secrets service...[/dim]")
    for key, value in secrets.items():
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "exec",
                    "-T",
                    "kdcube-secrets",
                    "python",
                    "/app/secretsctl.py",
                    "set",
                    key,
                    value,
                ],
                cwd=ctx.docker_dir,
                check=True,
                env=compose_env(env_file),
            )
        except FileNotFoundError:
            console.print("[red]Docker not found. Please install Docker and rerun.[/red]")
            return
        except subprocess.CalledProcessError:
            console.print("[red]Failed to inject secrets. Ensure kdcube-secrets is running.[/red]")
            return


def wait_for_secrets_ready(console: Console, ctx: PathsContext, env_file: Path, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "exec",
                    "-T",
                    "kdcube-secrets",
                    "python",
                    "-c",
                    (
                        "import sys,urllib.request\n"
                        "try:\n"
                        "    r=urllib.request.urlopen('http://127.0.0.1:7777/health',timeout=1)\n"
                        "    sys.exit(0 if r.status==200 else 1)\n"
                        "except Exception:\n"
                        "    sys.exit(1)\n"
                    ),
                ],
                cwd=ctx.docker_dir,
                check=True,
                env=compose_env(env_file),
            )
            return True
        except Exception:
            time.sleep(1)
    console.print("[yellow]Timed out waiting for secrets service.[/yellow]")
    return False


def generate_runtime_tokens() -> Dict[str, str]:
    admin = secrets.token_urlsafe(24)
    ingress = secrets.token_urlsafe(16)
    proc = secrets.token_urlsafe(16)
    return {
        "SECRETS_ADMIN_TOKEN": admin,
        "SECRETS_READ_TOKENS": f"{ingress},{proc}",
        "SECRETS_TOKEN_INGRESS": ingress,
        "SECRETS_TOKEN_PROC": proc,
    }


def write_env_overlay(base_env: Path, overrides: Dict[str, str]) -> Path:
    env = load_env_file(base_env)
    for key, value in overrides.items():
        update_env_value(env, key, value)
    fd, tmp_path = tempfile.mkstemp(prefix="kdcube-env-", suffix=".env")
    os.close(fd)
    env.path = Path(tmp_path)
    save_env_file(env)
    return env.path


def load_env_file(path: Path) -> EnvFile:
    lines = path.read_text().splitlines()
    entries = parse_env(lines)
    return EnvFile(path=path, lines=lines, entries=entries)


def save_env_file(env_file: EnvFile) -> None:
    text = "\n".join(env_file.lines).rstrip() + "\n"
    env_file.path.write_text(text)

def missing_build_keys(env_main: EnvFile) -> List[str]:
    ui_image = env_main.entries.get("KDCUBE_UI_IMAGE", (None, None))[1]
    skip_ui = bool(ui_image and not is_placeholder(ui_image))
    keys = [
        "UI_BUILD_CONTEXT",
        "UI_DOCKERFILE_PATH",
        "UI_SOURCE_PATH",
        "NGINX_UI_CONFIG_FILE_PATH",
        "PROXY_BUILD_CONTEXT",
        "PROXY_DOCKERFILE_PATH",
        "NGINX_PROXY_CONFIG_FILE_PATH",
    ]
    if skip_ui:
        keys = [key for key in keys if not key.startswith("UI_") and key != "NGINX_UI_CONFIG_FILE_PATH"]
    missing = []
    for key in keys:
        val = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(val):
            missing.append(key)
    return missing


def discover_lib_root() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "kdcube_ai_app").is_dir():
            return parent
    return None


def find_ai_app_root(lib_root: Optional[Path]) -> Optional[Path]:
    if lib_root is not None:
        candidate = lib_root.parent.parent
        compose = candidate / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return candidate

    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        compose = parent / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return compose.parents[3]
    return None


def prompt_for_ai_app_root(console: Console) -> Path:
    while True:
        raw = ask(console, "Path to ai-app root (contains deployment/docker/all_in_one_kdcube)")
        candidate = Path(raw).expanduser().resolve()
        compose = candidate / "deployment/docker/all_in_one_kdcube/docker-compose.yaml"
        if compose.exists():
            return candidate
        console.print("[red]Could not find docker-compose.yaml under that path.[/red]")


def _label(text: str) -> str:
    return f"[bold blue]{text}[/]"


def _mask(value: str) -> str:
    return "*" * len(value)

def _abort_if_quit(value: str) -> None:
    if value.strip().lower() in {"q", "quit", "exit"}:
        raise SystemExit("Setup cancelled by user.")


def ask(console: Console, label: str, default: Optional[str] = None, secret: bool = False) -> str:
    value = Prompt.ask(_label(label), default=default or "", password=secret)
    _abort_if_quit(value)
    return value


def ask_confirm(console: Console, label: str, default: bool = False) -> bool:
    default_hint = "y" if default else "n"
    while True:
        try:
            raw = console.input(f"{label} [y/n] ({default_hint}): ").strip().lower()
        except UnicodeDecodeError:
            console.print("[yellow]Input encoding error (keyboard shortcut?). Please try again.[/yellow]")
            continue
        if not raw:
            return default
        if raw in {"q", "quit", "exit"}:
            raise SystemExit("Setup cancelled by user.")
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print("[red]Please enter y/n or q to quit.[/red]")


def prompt_optional(console: Console, label: str, secret: bool = False) -> str:
    console.print(f"{_label(label)} [dim](leave blank to skip)[/dim]")
    while True:
        try:
            value = console.input("> ", password=secret).strip()
        except UnicodeDecodeError:
            console.print("[yellow]Input encoding error (keyboard shortcut?). Please try again.[/yellow]")
            continue
        break
    _abort_if_quit(value)
    return value


def prompt_optional_keep(console: Console, label: str, current: Optional[str]) -> Optional[str]:
    if current and not is_placeholder(current):
        console.print(f"{_label(label)} [dim](press Enter to keep current)[/dim]")
    else:
        console.print(f"{_label(label)} [dim](leave blank to skip)[/dim]")
    while True:
        try:
            value = console.input("> ").strip()
        except UnicodeDecodeError:
            console.print("[yellow]Input encoding error (keyboard shortcut?). Please try again.[/yellow]")
            continue
        break
    _abort_if_quit(value)
    if not value:
        return current if current and not is_placeholder(current) else None
    return value


def ensure_absolute(
    console: Console,
    label: str,
    current: Optional[str],
    default: Optional[str],
    *,
    force_prompt: bool = False,
) -> str:
    current_value = None if is_placeholder(current) else current
    if not force_prompt and current_value and Path(current_value).is_absolute():
        return current_value
    while True:
        value = ask(console, label, default=current_value or default or "")
        if not value:
            console.print("[red]Please provide a value.[/red]")
            continue
        resolved = Path(value).expanduser().resolve()
        return str(resolved)


def prompt_secret(
    console: Console,
    env_file: EnvFile,
    key: str,
    label: str,
    *,
    required: bool = False,
    force_prompt: bool = False,
) -> Optional[str]:
    current = env_file.entries.get(key, (None, None))[1]
    if not force_prompt and not is_placeholder(current):
        return current
    while True:
        if force_prompt and current and not is_placeholder(current):
            console.print(f"{_label(label)} [dim](press Enter to keep current)[/dim]")
            value = console.input("> ", password=True).strip()
            _abort_if_quit(value)
            if not value:
                return current
        elif required:
            value = ask(console, label, secret=True)
        else:
            value = prompt_optional(console, label, secret=True)
        if value:
            update_env_value(env_file, key, value)
            console.print(f"{_label(label)}: [dim]{_mask(value)}[/]")
            return value
        if required:
            console.print("[red]This value is required. Please enter a value.[/red]")
            continue
        return current if force_prompt else None


def prompt_secret_value(
    console: Console,
    label: str,
    *,
    required: bool = False,
    current: Optional[str] = None,
    force_prompt: bool = False,
    echo: bool = True,
) -> Optional[str]:
    current_value = None if is_placeholder(current) else current
    if not force_prompt and current_value:
        return current_value
    while True:
        if force_prompt and current_value:
            console.print(f"{_label(label)} [dim](press Enter to keep current)[/dim]")
            value = console.input("> ", password=True).strip()
            _abort_if_quit(value)
            if not value:
                return current_value
        elif required:
            value = ask(console, label, secret=True)
        else:
            value = prompt_optional(console, label, secret=True)
        if value:
            if echo:
                console.print(f"{_label(label)}: [dim]{_mask(value)}[/]")
            return value
        if required:
            console.print("[red]This value is required. Please enter a value.[/red]")
            continue
        return current_value if force_prompt else None


def prompt_choice(console: Console, label: str, choices: List[str], default: str) -> str:
    value = Prompt.ask(_label(label), choices=choices, default=default)
    _abort_if_quit(value)
    return value


def maybe_remove_legacy_containers(console: Console) -> None:
    legacy_names = [
        "kdcube-secrets",
        "kdcube-postgres",
        "kdcube-postgres-setup",
        "kdcube-redis",
        "kdcube-clamav",
        "kdcube-chat-ingress",
        "kdcube-chat-proc",
        "kdcube-metrics",
        "kdcube-web-ui",
        "kdcube-web-proxy",
        "pgadmin4",
    ]
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return
    existing = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    to_remove = [name for name in legacy_names if name in existing]
    if not to_remove:
        return
    if ask_confirm(
        console,
        "Found legacy fixed-name containers from older installs. Remove them to avoid conflicts?",
        default=True,
    ):
        try:
            subprocess.run(["docker", "rm", "-f", *to_remove], check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            console.print("[yellow]Could not remove one or more legacy containers.[/yellow]")


def normalize_env_build_relative(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip().strip("'\"")
    if not cleaned:
        return None
    if cleaned.startswith("/") and not (len(cleaned) > 2 and cleaned[1] == ":" and cleaned[2] in {"/", "\\"}):
        cleaned = cleaned.lstrip("/")
    if is_placeholder(cleaned) or "path/to/" in cleaned:
        return ".env.ui.build"
    return cleaned


def parse_bool(value: Optional[object]) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
    return None


def ensure_ui_env_build_file(console: Console, build_context: Optional[str], env_build_relative: Optional[str]) -> None:
    if not build_context or not env_build_relative:
        return
    try:
        rel_path = Path(env_build_relative)
        if rel_path.is_absolute():
            return
        root = Path(build_context).expanduser().resolve()
        target = (root / rel_path).resolve()
        if not str(target).startswith(str(root)):
            return
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        console.print(f"[yellow]Created empty UI build env file at {target}[/yellow]")
    except Exception:
        return


def normalize_docker_host(console: Console, host: Optional[str], label: str) -> Optional[str]:
    if not host:
        return host
    host_str = str(host).strip()
    if host_str in {"localhost", "127.0.0.1"}:
        console.print(
            f"[yellow]{label} host '{host_str}' resolves to the container itself. "
            "Using host.docker.internal instead.[/yellow]"
        )
        return "host.docker.internal"
    return host_str


def select_option(console: Console, title: str, options: List[str], default_index: int = 0) -> str:
    def _plain_prompt_enabled() -> bool:
        raw = os.environ.get("KDCUBE_CLI_PLAIN_PROMPTS", "").strip().lower()
        return raw not in {"", "0", "false", "no"}

    def _use_alt_screen() -> bool:
        raw = os.environ.get("KDCUBE_CLI_ALT_SCREEN", "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return any(os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))

    def _use_manual_redraw() -> bool:
        raw = os.environ.get("KDCUBE_CLI_MANUAL_REDRAW", "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        term = os.environ.get("TERM", "").lower()
        return bool(os.environ.get("STY") or os.environ.get("TMUX") or term.startswith("screen"))

    def _prompt_numbered() -> str:
        console.print(f"[bold]{title}[/bold]")
        for i, option in enumerate(options, start=1):
            marker = " (default)" if i - 1 == default_index else ""
            console.print(f"  {i}. {option}{marker}")
        choice = Prompt.ask(
            "Select option number",
            choices=[str(i) for i in range(1, len(options) + 1)],
            default=str(default_index + 1),
        )
        _abort_if_quit(choice)
        return options[int(choice) - 1]

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _prompt_numbered()
    if _plain_prompt_enabled():
        return _prompt_numbered()
    if not console.is_terminal or console.is_jupyter or os.environ.get("TERM", "").lower() == "dumb":
        return _prompt_numbered()
    try:
        from readchar import readkey, key
    except Exception:
        return _prompt_numbered()

    idx = max(0, min(default_index, len(options) - 1))

    def _render() -> Panel:
        text = Text()
        text.append(title + "\n\n", style="bold")
        for i, option in enumerate(options):
            if i == idx:
                text.append("➤ ", style="bold cyan")
                text.append(option, style="bold cyan")
            else:
                text.append("  " + option)
            text.append("\n")
        text.append("\nUse ↑/↓ and Enter. Press q to exit.", style="dim")
        return Panel(text, title="Select")

    if _use_manual_redraw():
        def _capture() -> tuple[str, int]:
            with console.capture() as capture:
                console.print(_render())
            rendered = capture.get()
            lines = rendered.splitlines()
            return rendered, max(1, len(lines))

        def _rewrite(rendered: str, line_count: int) -> None:
            if line_count > 0:
                sys.stdout.write(f"\x1b[{line_count}F")
            sys.stdout.write(rendered)
            sys.stdout.flush()

        rendered, line_count = _capture()
        sys.stdout.write(rendered)
        sys.stdout.flush()

        while True:
            k = readkey()
            if k in (key.UP, "k"):
                idx = (idx - 1) % len(options)
            elif k in (key.DOWN, "j"):
                idx = (idx + 1) % len(options)
            elif k in (key.ENTER, "\r", "\n"):
                return options[idx]
            elif k in ("q", key.ESC):
                raise KeyboardInterrupt
            elif k in (key.CTRL_C, "\x03"):
                raise KeyboardInterrupt
            rendered, _ = _capture()
            _rewrite(rendered, line_count)

    with Live(
        _render(),
        console=console,
        screen=_use_alt_screen(),
        transient=True,
        auto_refresh=False,
        redirect_stdout=False,
        redirect_stderr=False,
    ) as live:
        while True:
            k = readkey()
            if k in (key.UP, "k"):
                idx = (idx - 1) % len(options)
            elif k in (key.DOWN, "j"):
                idx = (idx + 1) % len(options)
            elif k in (key.ENTER, "\r", "\n"):
                return options[idx]
            elif k in ("q", key.ESC):
                raise KeyboardInterrupt
            elif k in (key.CTRL_C, "\x03"):
                raise KeyboardInterrupt
            live.update(_render(), refresh=True)


def compute_paths(ai_app_root: Path, lib_root: Path, workdir: Path, compose_mode: str) -> Dict[str, str]:
    if compose_mode == "custom-ui-managed-infra":
        docker_dir = ai_app_root / "deployment/docker/custom-ui-managed-infra"
    else:
        docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"
    repo_root = ai_app_root.parent.parent
    defaults: Dict[str, str] = {
        "docker_dir": str(docker_dir),
        "host_kb_storage": str(workdir / "data/kdcube-storage"),
        "host_bundle_storage": str(workdir / "data/bundle-storage"),
        "host_exec_workspace": str(workdir / "data/exec-workspace"),
        "host_bundles": str(workdir / "data/bundles"),
        "ui_dockerfile_path": "",
        "ui_source_path": "",
        "ui_env_build_relative": "",
        "nginx_ui_config": "",
        "frontend_config_json": str((workdir / "config/frontend.config.hardcoded.json").resolve()),
    }
    if compose_mode == "custom-ui-managed-infra":
        defaults["ui_dockerfile_path"] = "deployment/docker/custom-ui-managed-infra/Dockerfile_UI"
        defaults["ui_source_path"] = "ui/chat-web-app"
        defaults["ui_env_build_relative"] = "ui/chat-web-app/.env.sample"
        defaults["nginx_ui_config"] = "deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_ui.conf"
        defaults["nginx_proxy_config"] = "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_cognito.conf"
    else:
        defaults["ui_dockerfile_path"] = "deployment/docker/all_in_one_kdcube/Dockerfile_UI"
        defaults["ui_source_path"] = "ui/chat-web-app"
        defaults["ui_env_build_relative"] = "ui/chat-web-app/.env.sample"
        defaults["nginx_ui_config"] = "deployment/docker/all_in_one_kdcube/nginx/conf/nginx_ui.conf"
        defaults["nginx_proxy_config"] = "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"

    common_parent = repo_root
    defaults["proxy_build_context"] = str(common_parent)
    if compose_mode == "custom-ui-managed-infra":
        defaults["proxy_dockerfile_path"] = str(
            (ai_app_root / "deployment/docker/custom-ui-managed-infra/Dockerfile_ProxyOpenResty").relative_to(common_parent)
        )
    else:
        defaults["proxy_dockerfile_path"] = str(
            (ai_app_root / "deployment/docker/all_in_one_kdcube/Dockerfile_ProxyOpenResty").relative_to(common_parent)
        )
    defaults["ui_build_context"] = str(ai_app_root)
    defaults["ui_env_file_path"] = str(ai_app_root / "ui/chat-web-app/.env")
    return defaults


def should_replace_bundles_config(value: Optional[str]) -> bool:
    if is_placeholder(value):
        return True
    if value and "/config/assembly.yaml" in value:
        return False
    if value and "/config/bundles.yaml" in value:
        return False
    if value and "/config/release.yaml" in value:
        return True
    if value and ("kdcube.demo.1" in value or "<project>" in value):
        return True
    return False


def gather_configuration(
    console: Console,
    ctx: PathsContext,
    *,
    release_descriptor_path: Optional[str] = None,
    release_descriptor: Optional[Dict[str, object]] = None,
    bundles_descriptor_path: Optional[str] = None,
    bundles_descriptor: Optional[Dict[str, object]] = None,
    bundles_secrets_descriptor: Optional[Dict[str, object]] = None,
    gateway_descriptor: Optional[Dict[str, object]] = None,
    secrets_descriptor: Optional[Dict[str, object]] = None,
    compose_mode: str = "all-in-one",
    use_descriptor_bundles: Optional[bool] = None,
    use_descriptor_frontend: Optional[bool] = None,
    use_bundles_descriptor: Optional[bool] = None,
    use_bundles_secrets: Optional[bool] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    force_prompt = os.getenv("KDCUBE_RESET_CONFIG", "").lower() in {"1", "true", "yes", "on"}
    env_main = load_env_file(ctx.config_dir / ".env")
    env_ingress = load_env_file(ctx.config_dir / ".env.ingress")
    env_proc = load_env_file(ctx.config_dir / ".env.proc")
    env_metrics = load_env_file(ctx.config_dir / ".env.metrics")
    env_pg = load_env_file(ctx.config_dir / ".env.postgres.setup")
    env_proxy = load_env_file(ctx.config_dir / ".env.proxylogin")
    runtime_secrets: Dict[str, str] = {}
    assembly_path = Path(release_descriptor_path).expanduser().resolve() if release_descriptor_path else None
    assembly_data: Dict[str, object] = dict(release_descriptor or {})
    bundles_path = Path(bundles_descriptor_path).expanduser().resolve() if bundles_descriptor_path else None
    bundles_data: Dict[str, object] = dict(bundles_descriptor or {})
    bundles_secrets_data: Dict[str, object] = dict(bundles_secrets_descriptor or {})
    secrets_data: Dict[str, object] = dict(secrets_descriptor or {})
    gateway_data: Dict[str, object] = dict(gateway_descriptor or {})
    if isinstance(gateway_data, dict) and isinstance(gateway_data.get("gateway"), dict):
        gateway_data = dict(gateway_data.get("gateway") or {})
    if bundles_path and not bundles_path.exists():
        bundles_path = None
    autosave_envs = (env_main, env_ingress, env_proc, env_metrics, env_pg, env_proxy)
    assembly_user_supplied = parse_bool(os.getenv("KDCUBE_ASSEMBLY_USER_SUPPLIED", "")) is True

    def _secret_pick(*paths: object) -> Optional[str]:
        for path in paths:
            if isinstance(path, str):
                val = secrets_data.get(path)
            elif isinstance(path, (list, tuple)):
                val = _get_nested(secrets_data, *path)
            else:
                continue
            if isinstance(val, str):
                val = val.strip()
                if val and not is_placeholder(val):
                    return val
        return None

    def _flatten_bundle_secrets(data: Dict[str, object]) -> Dict[str, str]:
        flattened: Dict[str, str] = {}

        def _walk(prefix: str, node: object) -> None:
            if node is None:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    if key is None:
                        continue
                    _walk(f"{prefix}.{key}", value)
                return
            if isinstance(node, list):
                for idx, value in enumerate(node):
                    _walk(f"{prefix}.{idx}", value)
                return
            value_str = str(node).strip()
            if not value_str or is_placeholder(value_str):
                return
            flattened[prefix] = value_str

        root = data
        if isinstance(data, dict) and isinstance(data.get("bundles"), dict):
            root = data.get("bundles")
        items = root.get("items") if isinstance(root, dict) else None
        if not isinstance(items, list):
            return flattened
        for item in items:
            if not isinstance(item, dict):
                continue
            bundle_id = item.get("id")
            if not bundle_id:
                continue
            secrets_block = item.get("secrets")
            if secrets_block is None:
                continue
            _walk(f"bundles.{bundle_id}.secrets", secrets_block)
        return flattened

    def _autosave() -> None:
        if assembly_path:
            save_release_descriptor(assembly_path, assembly_data)
        for env in autosave_envs:
            try:
                save_env_file(env)
            except Exception:
                pass

    def _set_env(env: EnvFile, key: str, value: str) -> None:
        if assembly_path:
            update_env_value(env, key, value)
        else:
            update_if_placeholder(env, key, value)

    defaults = compute_paths(ctx.ai_app_root, ctx.lib_root, ctx.workdir, compose_mode)

    if assembly_path:
        update_env_value(env_main, "KDCUBE_ASSEMBLY_DESCRIPTOR_PATH", str(assembly_path))
        _autosave()

    if gateway_data:
        for env in (env_ingress, env_proc, env_metrics):
            replace_multiline_block(env, "GATEWAY_CONFIG_JSON", _format_json_multiline("GATEWAY_CONFIG_JSON", gateway_data))

    # Persist bundle descriptor selection early so Ctrl+C still keeps it.
    existing_tenant, existing_project = _extract_tenant_project(env_ingress)
    if not existing_tenant or not existing_project:
        alt_tenant, alt_project = _extract_tenant_project(env_proc)
        existing_tenant = existing_tenant or alt_tenant
        existing_project = existing_project or alt_project
    if not existing_tenant or not existing_project:
        alt_tenant, alt_project = _extract_tenant_project(env_metrics)
        existing_tenant = existing_tenant or alt_tenant
        existing_project = existing_project or alt_project

    descriptor_tenant = _get_nested(assembly_data, "context", "tenant")
    descriptor_project = _get_nested(assembly_data, "context", "project")
    tenant = ask(
        console,
        "Tenant ID",
        default=(str(descriptor_tenant) if descriptor_tenant else existing_tenant or "demo-tenant"),
    )
    project = ask(
        console,
        "Project name",
        default=(str(descriptor_project) if descriptor_project else existing_project or "demo-project"),
    )
    if is_placeholder(tenant):
        tenant = "demo-tenant"
    if is_placeholder(project):
        project = "demo-project"
    _set_nested(assembly_data, ["context", "tenant"], tenant)
    _set_nested(assembly_data, ["context", "project"], project)
    for env in (env_ingress, env_proc, env_metrics):
        patch_gateway_config_json(env, tenant, project)
    if is_placeholder(env_pg.entries.get("TENANT_ID", (None, None))[1]) or is_default_tenant_project(
        env_pg.entries.get("TENANT_ID", (None, None))[1]
    ):
        update_env_value(env_pg, "TENANT_ID", tenant)
    if is_placeholder(env_pg.entries.get("PROJECT_ID", (None, None))[1]) or is_default_tenant_project(
        env_pg.entries.get("PROJECT_ID", (None, None))[1]
    ):
        update_env_value(env_pg, "PROJECT_ID", project)
    _autosave()

    secrets_provider = normalize_secrets_provider(
        _get_nested(assembly_data, "secrets", "provider"),
        default="secrets-service",
    )
    _set_nested(assembly_data, ["secrets", "provider"], secrets_provider)
    update_env_value(env_ingress, "SECRETS_PROVIDER", secrets_provider)
    update_env_value(env_proc, "SECRETS_PROVIDER", secrets_provider)
    if secrets_provider == "secrets-service":
        update_if_placeholder(env_ingress, "SECRETS_URL", "http://kdcube-secrets:7777")
        update_if_placeholder(env_proc, "SECRETS_URL", "http://kdcube-secrets:7777")
        # Ensure proc can set secrets (bundle secrets admin flow).
        proc_admin = env_proc.entries.get("SECRETS_ADMIN_TOKEN", (None, None))[1]
        if is_placeholder(proc_admin) or not (proc_admin or "").strip():
            update_env_value(env_proc, "SECRETS_ADMIN_TOKEN", "${SECRETS_ADMIN_TOKEN}")
    update_if_placeholder(env_ingress, "LINK_PREVIEW_ENABLED", "0")

    # Auth provider selection
    existing_auth = env_ingress.entries.get("AUTH_PROVIDER", (None, None))[1] or env_proc.entries.get("AUTH_PROVIDER", (None, None))[1]
    auth_descriptor: Dict[str, Any] = {}
    if isinstance(release_descriptor, dict):
        raw_auth = release_descriptor.get("auth")
        if isinstance(raw_auth, dict):
            auth_descriptor = raw_auth
    descriptor_auth_type = (auth_descriptor.get("type") or "").strip().lower()
    auth_options = ["simple", "cognito", "delegated"]
    if descriptor_auth_type in auth_options:
        default_auth = descriptor_auth_type
    else:
        default_auth = "cognito" if (existing_auth or "").strip().lower() == "cognito" else "simple"
    current_proxy_cfg = env_main.entries.get("NGINX_PROXY_RUNTIME_CONFIG_PATH", (None, None))[1] or ""
    if "delegated" in current_proxy_cfg:
        default_auth = "delegated"
    default_idx = auth_options.index(default_auth)
    console.print("[bold]Authentication[/bold]")
    auth_choice = select_option(
        console,
        "Auth type",
        options=auth_options,
        default_index=default_idx,
    )
    auth_mode = auth_choice
    _set_nested(assembly_data, ["auth", "type"], auth_mode)
    auth_provider = "simple" if auth_choice == "simple" else "cognito"
    update_env_value(env_ingress, "AUTH_PROVIDER", auth_provider)
    update_env_value(env_proc, "AUTH_PROVIDER", auth_provider)
    proxy_ssl_env = parse_bool(os.getenv("KDCUBE_PROXY_SSL"))
    proxy_ssl_descriptor = parse_bool(_get_nested(assembly_data, "proxy", "ssl"))
    proxy_ssl_enabled = proxy_ssl_env if proxy_ssl_env is not None else (proxy_ssl_descriptor or False)
    _set_nested(assembly_data, ["proxy", "ssl"], proxy_ssl_enabled)

    if auth_provider == "cognito":
        def _pick(dct: Dict[str, Any], *keys: str) -> str:
            for key in keys:
                val = dct.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            return ""

        cognito_descriptor: Dict[str, Any] = {}
        raw_cognito = auth_descriptor.get("cognito") if auth_descriptor else None
        if isinstance(raw_cognito, dict):
            cognito_descriptor = dict(raw_cognito)

        def _normalize_cognito_block(block: Dict[str, Any]) -> None:
            legacy_map = {
                "user_pool": "user_pool_id",
                "user_pool_name": "user_pool_id",
                "app_client": "app_client_id",
                "app_client_name": "app_client_id",
                "service_client": "service_client_id",
                "service_client_name": "service_client_id",
            }
            for legacy_key, canonical_key in legacy_map.items():
                legacy_val = block.get(legacy_key)
                canonical_val = block.get(canonical_key)
                if (not canonical_val or is_placeholder(str(canonical_val))) and legacy_val and not is_placeholder(str(legacy_val)):
                    block[canonical_key] = legacy_val
            for legacy_key in legacy_map:
                if legacy_key != legacy_map[legacy_key]:
                    block.pop(legacy_key, None)
            block.pop("client_secret", None)

        if cognito_descriptor:
            _normalize_cognito_block(cognito_descriptor)
        descriptor_region = _pick(cognito_descriptor, "region")
        descriptor_pool = _pick(cognito_descriptor, "user_pool_id")
        descriptor_app = _pick(cognito_descriptor, "app_client_id")
        descriptor_service = _pick(cognito_descriptor, "service_client_id")
        use_descriptor_auth = False
        if descriptor_region or descriptor_pool or descriptor_app or descriptor_service:
            use_descriptor_auth = ask_confirm(
                console,
                "Use Cognito settings from assembly descriptor?",
                default=True,
            )

        if auth_mode == "delegated":
            console.print("[dim]Delegated auth uses Cognito for token validation and proxylogin for delegation.[/dim]")
        cognito_region = env_ingress.entries.get("COGNITO_REGION", (None, None))[1]
        if use_descriptor_auth and descriptor_region and not is_placeholder(descriptor_region):
            cognito_region = descriptor_region
            update_env_value(env_ingress, "COGNITO_REGION", cognito_region)
            update_env_value(env_proc, "COGNITO_REGION", cognito_region)
        elif force_prompt or is_placeholder(cognito_region):
            cognito_region = ask(console, "COGNITO_REGION", default="eu-west-1")
        update_env_value(env_ingress, "COGNITO_REGION", cognito_region or "eu-west-1")
        update_env_value(env_proc, "COGNITO_REGION", cognito_region or "eu-west-1")
        _set_nested(assembly_data, ["auth", "cognito", "region"], cognito_region or "eu-west-1")

        for key in ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID", "COGNITO_SERVICE_CLIENT_ID"):
            current_val = env_ingress.entries.get(key, (None, None))[1]
            if use_descriptor_auth:
                if key == "COGNITO_USER_POOL_ID" and descriptor_pool and not is_placeholder(descriptor_pool):
                    current_val = descriptor_pool
                elif key == "COGNITO_APP_CLIENT_ID" and descriptor_app and not is_placeholder(descriptor_app):
                    current_val = descriptor_app
                elif key == "COGNITO_SERVICE_CLIENT_ID" and descriptor_service and not is_placeholder(descriptor_service):
                    current_val = descriptor_service
            if force_prompt or is_placeholder(current_val):
                current_val = ask(console, key, default=current_val or "")
            update_env_value(env_ingress, key, current_val or "")
            update_env_value(env_proc, key, current_val or "")
            if key == "COGNITO_USER_POOL_ID":
                _set_nested(assembly_data, ["auth", "cognito", "user_pool_id"], current_val or "")
            elif key == "COGNITO_APP_CLIENT_ID":
                _set_nested(assembly_data, ["auth", "cognito", "app_client_id"], current_val or "")
            elif key == "COGNITO_SERVICE_CLIENT_ID":
                _set_nested(assembly_data, ["auth", "cognito", "service_client_id"], current_val or "")

        proxy_client_secret = _secret_pick(("auth", "cognito", "client_secret"))
        if auth_mode == "delegated" and not proxy_client_secret:
            proxy_client_secret = ask(
                console,
                "COGNITO_CLIENT_SECRET (leave blank to skip)",
                default="",
            )
        if proxy_client_secret and not is_placeholder(proxy_client_secret):
            update_env_value(env_proxy, "COGNITO_CLIENTSECRET", proxy_client_secret)
            runtime_secrets["auth.cognito.client_secret"] = proxy_client_secret
        elif auth_mode == "delegated":
            update_env_value(env_proxy, "COGNITO_CLIENTSECRET", "")

        proxy_client_id = env_proxy.entries.get("COGNITO_CLIENTID", (None, None))[1]
        if use_descriptor_auth and descriptor_app and not is_placeholder(descriptor_app):
            proxy_client_id = descriptor_app
        if is_placeholder(proxy_client_id):
            proxy_client_id = env_ingress.entries.get("COGNITO_APP_CLIENT_ID", (None, None))[1]
        if proxy_client_id:
            update_env_value(env_proxy, "COGNITO_CLIENTID", proxy_client_id)

        proxy_user_pool = env_proxy.entries.get("COGNITO_USERPOOLID", (None, None))[1]
        if use_descriptor_auth and descriptor_pool and not is_placeholder(descriptor_pool):
            proxy_user_pool = descriptor_pool
        if is_placeholder(proxy_user_pool):
            proxy_user_pool = env_ingress.entries.get("COGNITO_USER_POOL_ID", (None, None))[1]
        if proxy_user_pool:
            update_env_value(env_proxy, "COGNITO_USERPOOLID", proxy_user_pool)

        issuer_region = (cognito_region or "").strip()
        issuer_pool = (proxy_user_pool or "").strip()
        if issuer_region and issuer_pool:
            issuer = f"https://cognito-idp.{issuer_region}.amazonaws.com/{issuer_pool}"
            update_env_value(env_proxy, "COGNITO_JWKSISSUER", issuer)
            update_env_value(env_proxy, "COGNITO_JWKSSIGNINGKEYURL", f"{issuer}/.well-known/jwks.json")

        # Ensure legacy keys are removed from the assembly descriptor after normalization.
        auth_block = assembly_data.get("auth")
        if isinstance(auth_block, dict):
            cognito_block = auth_block.get("cognito")
            if isinstance(cognito_block, dict):
                _normalize_cognito_block(cognito_block)

        if auth_mode == "delegated":
            proxy_login_cfg: Dict[str, Any] = {}
            raw_proxy_login = _get_nested(assembly_data, "auth", "proxy_login")
            if isinstance(raw_proxy_login, dict):
                proxy_login_cfg = dict(raw_proxy_login)

            def _proxy_pick(*keys: str) -> str:
                for key in keys:
                    val = proxy_login_cfg.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                return ""

            domain_raw = _get_nested(assembly_data, "domain")
            proxy_domain = normalize_domain_host(domain_raw, keep_port=False) if isinstance(domain_raw, str) else ""

            if not proxy_domain:
                ui_port = str(
                    _get_nested(assembly_data, "ports", "ui")
                    or env_main.entries.get("KDCUBE_UI_PORT", (None, None))[1]
                    or "5174"
                ).strip()
                if ui_port in ("80", "443"):
                    proxy_domain = "localhost"
                else:
                    proxy_domain = f"localhost:{ui_port}"

            def _apply_domain(value: str) -> str:
                if not value:
                    return value
                return value.replace("YOUR_DOMAIN", proxy_domain).replace("<YOUR_DOMAIN>", proxy_domain)

            keyprefix = _proxy_pick("redis_key_prefix") or env_proxy.entries.get("REDIS_KEYPREFIX", (None, None))[1] or "proxylogin:<TENANT>:<PROJECT>:"
            if "<TENANT>" in keyprefix or "<PROJECT>" in keyprefix:
                keyprefix = keyprefix.replace("<TENANT>", tenant).replace("<PROJECT>", project)
            update_env_value(env_proxy, "REDIS_KEYPREFIX", keyprefix)


            token_masq = proxy_login_cfg.get("token_masquerade")
            if token_masq is None:
                token_masq = proxy_login_cfg.get("token_mascarade")
            if token_masq is not None:
                update_env_value(env_proxy, "TOKEN_MASQUERADE", str(token_masq).lower())

            reset_cfg = proxy_login_cfg.get("password_reset") if isinstance(proxy_login_cfg.get("password_reset"), dict) else {}
            reset_company = str(reset_cfg.get("company") or env_proxy.entries.get("PASSWORD_RESET_COMPANY", (None, None))[1] or "")
            reset_sender = str(reset_cfg.get("sender") or env_proxy.entries.get("PASSWORD_RESET_SENDER", (None, None))[1] or "")
            reset_template = str(reset_cfg.get("template_name") or env_proxy.entries.get("PASSWORD_RESET_TEMPLATENAME", (None, None))[1] or "")
            reset_redirect = str(reset_cfg.get("redirect_url") or env_proxy.entries.get("PASSWORD_RESET_REDIRECTURL", (None, None))[1] or "")
            reset_redirect = _apply_domain(reset_redirect)
            if reset_company:
                update_env_value(env_proxy, "PASSWORD_RESET_COMPANY", reset_company)
            if reset_sender:
                update_env_value(env_proxy, "PASSWORD_RESET_SENDER", reset_sender)
            if reset_template:
                update_env_value(env_proxy, "PASSWORD_RESET_TEMPLATENAME", reset_template)
            if reset_redirect:
                update_env_value(env_proxy, "PASSWORD_RESET_REDIRECTURL", reset_redirect)

            http_urlbase = _proxy_pick("http_urlbase") or env_proxy.entries.get("HTTP_URLBASE", (None, None))[1] or ""
            http_urlbase = _apply_domain(http_urlbase)
            if not http_urlbase:
                scheme = "https" if proxy_ssl_enabled else "http"
                http_urlbase = f"{scheme}://{proxy_domain}/auth"
            update_env_value(env_proxy, "HTTP_URLBASE", http_urlbase)

    _autosave()

    aws_region = _get_nested(assembly_data, "aws", "region")
    aws_profile = _get_nested(assembly_data, "aws", "profile")
    aws_ec2_flag = _get_nested(assembly_data, "aws", "ec2")
    aws_region_val = aws_region.strip() if isinstance(aws_region, str) else ""
    aws_profile_val = aws_profile.strip() if isinstance(aws_profile, str) else ""
    aws_ec2_enabled = parse_bool(str(aws_ec2_flag)) if aws_ec2_flag is not None else False
    if aws_region_val:
        update_env_value(env_ingress, "AWS_REGION", aws_region_val)
        update_env_value(env_ingress, "AWS_DEFAULT_REGION", aws_region_val)
        update_env_value(env_proc, "AWS_REGION", aws_region_val)
        update_env_value(env_proc, "AWS_DEFAULT_REGION", aws_region_val)
        update_env_value(env_metrics, "AWS_REGION", aws_region_val)
        update_env_value(env_metrics, "AWS_DEFAULT_REGION", aws_region_val)
        update_env_value(env_proxy, "AWS_REGION", aws_region_val)
        update_env_value(env_proxy, "AWS_DEFAULT_REGION", aws_region_val)
    if aws_profile_val:
        update_env_value(env_ingress, "AWS_PROFILE", aws_profile_val)
        update_env_value(env_proc, "AWS_PROFILE", aws_profile_val)
        update_env_value(env_metrics, "AWS_PROFILE", aws_profile_val)
        update_env_value(env_proxy, "AWS_PROFILE", aws_profile_val)

    if aws_ec2_enabled:
        update_env_value(env_ingress, "AWS_SDK_LOAD_CONFIG", "1")
        update_env_value(env_proc, "AWS_SDK_LOAD_CONFIG", "1")
        update_env_value(env_metrics, "AWS_SDK_LOAD_CONFIG", "1")
        update_env_value(env_ingress, "AWS_EC2_METADATA_DISABLED", "false")
        update_env_value(env_proc, "AWS_EC2_METADATA_DISABLED", "false")
        update_env_value(env_metrics, "AWS_EC2_METADATA_DISABLED", "false")
        update_env_value(env_ingress, "NO_PROXY", "169.254.169.254,localhost,127.0.0.1")
        update_env_value(env_proc, "NO_PROXY", "169.254.169.254,localhost,127.0.0.1")
        update_env_value(env_metrics, "NO_PROXY", "169.254.169.254,localhost,127.0.0.1")


    pg_user = env_pg.entries.get("POSTGRES_USER", (None, None))[1]
    pg_user_from_assembly = _get_nested(assembly_data, "infra", "postgres", "user")
    pg_db_from_assembly = _get_nested(assembly_data, "infra", "postgres", "database")
    pg_pass_from_assembly = _get_nested(assembly_data, "infra", "postgres", "password")
    pg_pass_from_secrets = _secret_pick(("infra", "postgres", "password"), ("postgres_password",))
    pg_host_from_assembly = _get_nested(assembly_data, "infra", "postgres", "host")
    pg_port_from_assembly = _get_nested(assembly_data, "infra", "postgres", "port")
    pg_ssl_from_assembly = _get_nested(assembly_data, "infra", "postgres", "ssl")
    has_pg_descriptor = bool(
        (pg_user_from_assembly and not is_placeholder(str(pg_user_from_assembly)))
        or (pg_db_from_assembly and not is_placeholder(str(pg_db_from_assembly)))
        or (pg_pass_from_assembly and not is_placeholder(str(pg_pass_from_assembly)))
        or (pg_host_from_assembly and not is_placeholder(str(pg_host_from_assembly)))
        or (pg_port_from_assembly and not is_placeholder(str(pg_port_from_assembly)))
        or pg_ssl_from_assembly is not None
    )
    use_pg_secret = False
    if pg_pass_from_secrets:
        use_pg_secret = ask_confirm(console, "Use Postgres password from secrets descriptor?", default=True)
    use_pg_descriptor = False
    if assembly_path and has_pg_descriptor and assembly_user_supplied:
        if use_pg_secret:
            use_pg_descriptor = True
        else:
            use_pg_descriptor = ask_confirm(console, "Use Postgres settings from assembly descriptor?", default=True)

    if use_pg_descriptor:
        if pg_user_from_assembly and not is_placeholder(str(pg_user_from_assembly)):
            update_env_value(env_pg, "POSTGRES_USER", str(pg_user_from_assembly))
        if pg_db_from_assembly and not is_placeholder(str(pg_db_from_assembly)):
            update_env_value(env_pg, "POSTGRES_DATABASE", str(pg_db_from_assembly))
        if pg_pass_from_assembly and not is_placeholder(str(pg_pass_from_assembly)) and not use_pg_secret:
            update_env_value(env_pg, "POSTGRES_PASSWORD", str(pg_pass_from_assembly))
        if pg_host_from_assembly and not is_placeholder(str(pg_host_from_assembly)):
            update_env_value(env_ingress, "POSTGRES_HOST", str(pg_host_from_assembly))
            update_env_value(env_proc, "POSTGRES_HOST", str(pg_host_from_assembly))
        if pg_port_from_assembly and not is_placeholder(str(pg_port_from_assembly)):
            update_env_value(env_pg, "POSTGRES_PORT", str(pg_port_from_assembly))
        if pg_ssl_from_assembly is not None:
            update_env_value(env_ingress, "POSTGRES_SSL", str(pg_ssl_from_assembly).lower())
            update_env_value(env_proc, "POSTGRES_SSL", str(pg_ssl_from_assembly).lower())
            update_env_value(env_pg, "POSTGRES_SSL", str(pg_ssl_from_assembly).lower())

    pg_user = env_pg.entries.get("POSTGRES_USER", (None, None))[1]
    if force_prompt or is_placeholder(pg_user):
        pg_user_default = (
            str(pg_user_from_assembly)
            if pg_user_from_assembly and not is_placeholder(str(pg_user_from_assembly))
            else (pg_user if not is_placeholder(pg_user) else "postgres")
        )
        pg_user = ask(console, "Postgres user", default=pg_user_default)
        update_env_value(env_pg, "POSTGRES_USER", pg_user)
    if force_prompt:
        update_env_value(env_ingress, "POSTGRES_USER", pg_user or "postgres")
        update_env_value(env_proc, "POSTGRES_USER", pg_user or "postgres")
    else:
        _set_env(env_ingress, "POSTGRES_USER", pg_user or "postgres")
        _set_env(env_proc, "POSTGRES_USER", pg_user or "postgres")

    # If .env.postgres.setup is empty, fall back to .env values
    if use_pg_secret and pg_pass_from_secrets:
        update_env_value(env_pg, "POSTGRES_PASSWORD", pg_pass_from_secrets)
    pg_pass_env = env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1]
    if is_placeholder(pg_pass_env):
        fallback_pg = env_main.entries.get("POSTGRES_PASSWORD", (None, None))[1]
        if is_placeholder(fallback_pg):
            fallback_pg = env_main.entries.get("PGPASSWORD", (None, None))[1]
        if not is_placeholder(fallback_pg):
            update_env_value(env_pg, "POSTGRES_PASSWORD", fallback_pg)
    if (not use_pg_secret) and is_placeholder(env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1]) and pg_pass_from_assembly:
        update_env_value(env_pg, "POSTGRES_PASSWORD", str(pg_pass_from_assembly))

    if use_pg_secret and pg_pass_from_secrets:
        pg_pass = pg_pass_from_secrets
    else:
        current_pass = env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1]
        if is_placeholder(current_pass):
            current_pass = None
        if current_pass and current_pass.strip().lower() == DEFAULT_PG_PASSWORD:
            current_pass = None
        if current_pass:
            options = ["Use existing password", "Unset (no password)", "Enter new password"]
            default_index = 0
        else:
            options = [f"Use default password ({DEFAULT_PG_PASSWORD})", "Unset (no password)", "Enter new password"]
            default_index = 0
        choice = select_option(console, "Postgres password", options, default_index)
        if choice.startswith("Use existing") and current_pass:
            pg_pass = current_pass
        elif choice.startswith("Use default"):
            pg_pass = DEFAULT_PG_PASSWORD
        elif choice.startswith("Unset"):
            pg_pass = ""
        else:
            pg_pass = prompt_secret_value(
                console,
                "Postgres password",
                required=True,
                current=current_pass,
                force_prompt=True,
            ) or ""
        update_env_value(env_pg, "POSTGRES_PASSWORD", pg_pass)
    if not pg_pass:
        pg_pass = env_pg.entries.get("POSTGRES_PASSWORD", (None, None))[1] or ""
        if not pg_pass and pg_pass_from_assembly:
            pg_pass = str(pg_pass_from_assembly)
    if force_prompt:
        update_env_value(env_ingress, "POSTGRES_PASSWORD", pg_pass or "postgres")
        update_env_value(env_proc, "POSTGRES_PASSWORD", pg_pass or "postgres")
    else:
        _set_env(env_ingress, "POSTGRES_PASSWORD", pg_pass or "postgres")
        _set_env(env_proc, "POSTGRES_PASSWORD", pg_pass or "postgres")

    pg_db = env_pg.entries.get("POSTGRES_DATABASE", (None, None))[1]
    if force_prompt or is_placeholder(pg_db):
        pg_db_default = (
            str(pg_db_from_assembly)
            if pg_db_from_assembly and not is_placeholder(str(pg_db_from_assembly))
            else (pg_db if not is_placeholder(pg_db) else "kdcube")
        )
        pg_db = ask(console, "Postgres database", default=pg_db_default)
        update_env_value(env_pg, "POSTGRES_DATABASE", pg_db)
    if force_prompt:
        update_env_value(env_ingress, "POSTGRES_DATABASE", pg_db or "kdcube")
        update_env_value(env_proc, "POSTGRES_DATABASE", pg_db or "kdcube")
        update_env_value(env_main, "PGUSER", pg_user or "postgres")
        update_env_value(env_main, "PGPASSWORD", pg_pass or "postgres")
        update_env_value(env_main, "PGDATABASE", pg_db or "kdcube")
    else:
        _set_env(env_ingress, "POSTGRES_DATABASE", pg_db or "kdcube")
        _set_env(env_proc, "POSTGRES_DATABASE", pg_db or "kdcube")
        _set_env(env_main, "PGUSER", pg_user or "postgres")
        _set_env(env_main, "PGPASSWORD", pg_pass or "postgres")
        _set_env(env_main, "PGDATABASE", pg_db or "kdcube")

    _set_nested(assembly_data, ["infra", "postgres", "user"], pg_user or "postgres")
    if not use_pg_secret:
        _set_nested(assembly_data, ["infra", "postgres", "password"], pg_pass or "")
    _set_nested(assembly_data, ["infra", "postgres", "database"], pg_db or "kdcube")

    redis_pass_from_assembly = _get_nested(assembly_data, "infra", "redis", "password")
    redis_pass_from_secrets = _secret_pick(("infra", "redis", "password"), ("redis_password",))
    redis_host_from_assembly = _get_nested(assembly_data, "infra", "redis", "host")
    redis_port_from_assembly = _get_nested(assembly_data, "infra", "redis", "port")
    has_redis_descriptor = bool(
        _has_nested(assembly_data, "infra", "redis", "password")
        or (redis_host_from_assembly and not is_placeholder(str(redis_host_from_assembly)))
        or (redis_port_from_assembly and not is_placeholder(str(redis_port_from_assembly)))
    )
    use_redis_secret = False
    if redis_pass_from_secrets:
        use_redis_secret = ask_confirm(console, "Use Redis password from secrets descriptor?", default=True)

    use_redis_descriptor = False
    if assembly_path and has_redis_descriptor and assembly_user_supplied:
        if use_redis_secret:
            use_redis_descriptor = True
        else:
            use_redis_descriptor = ask_confirm(console, "Use Redis settings from assembly descriptor?", default=True)

    if use_redis_descriptor:
        if _has_nested(assembly_data, "infra", "redis", "password") and not use_redis_secret:
            if redis_pass_from_assembly is not None and str(redis_pass_from_assembly).strip():
                update_env_value(env_main, "REDIS_PASSWORD", str(redis_pass_from_assembly))
        if redis_host_from_assembly and not is_placeholder(str(redis_host_from_assembly)):
            update_env_value(env_ingress, "REDIS_HOST", str(redis_host_from_assembly))
            update_env_value(env_proc, "REDIS_HOST", str(redis_host_from_assembly))
            update_env_value(env_metrics, "REDIS_HOST", str(redis_host_from_assembly))
        if redis_port_from_assembly and not is_placeholder(str(redis_port_from_assembly)):
            update_env_value(env_ingress, "REDIS_PORT", str(redis_port_from_assembly))
            update_env_value(env_proc, "REDIS_PORT", str(redis_port_from_assembly))
            update_env_value(env_metrics, "REDIS_PORT", str(redis_port_from_assembly))

    if use_redis_secret and redis_pass_from_secrets:
        update_env_value(env_main, "REDIS_PASSWORD", str(redis_pass_from_secrets))
    if (not use_redis_secret) and is_placeholder(env_main.entries.get("REDIS_PASSWORD", (None, None))[1]) and redis_pass_from_assembly:
        update_env_value(env_main, "REDIS_PASSWORD", str(redis_pass_from_assembly))

    if use_redis_secret and redis_pass_from_secrets:
        redis_pass = redis_pass_from_secrets
    elif use_redis_descriptor and _has_nested(assembly_data, "infra", "redis", "password"):
        if redis_pass_from_assembly is None or str(redis_pass_from_assembly).strip() == "":
            redis_pass = ""
        else:
            redis_pass = env_main.entries.get("REDIS_PASSWORD", (None, None))[1]
            if is_placeholder(redis_pass):
                redis_pass = str(redis_pass_from_assembly)
            redis_pass = redis_pass or ""
    else:
        current_pass = env_main.entries.get("REDIS_PASSWORD", (None, None))[1]
        if is_placeholder(current_pass):
            current_pass = None
        if current_pass and current_pass.strip().lower() == DEFAULT_REDIS_PASSWORD:
            current_pass = None
        if current_pass:
            options: List[str] = ["Use existing password", "Unset (no password)", "Enter new password"]
            default_index = 0
        else:
            options = [f"Use default password ({DEFAULT_REDIS_PASSWORD})", "Unset (no password)", "Enter new password"]
            default_index = 0
        choice = select_option(console, "Redis password", options, default_index)
        if choice.startswith("Use existing") and current_pass:
            redis_pass = current_pass
        elif choice.startswith("Use default"):
            redis_pass = DEFAULT_REDIS_PASSWORD
        elif choice.startswith("Unset"):
            redis_pass = ""
        else:
            redis_pass = prompt_secret_value(
                console,
                "Redis password",
                required=False,
                current=current_pass,
                force_prompt=True,
            ) or ""
        update_env_value(env_main, "REDIS_PASSWORD", redis_pass)

    redis_host = (
        env_ingress.entries.get("REDIS_HOST", (None, None))[1]
        or env_proc.entries.get("REDIS_HOST", (None, None))[1]
        or str(redis_host_from_assembly or "redis")
    )
    redis_host = normalize_docker_host(console, redis_host, "Redis")
    redis_port = (
        env_ingress.entries.get("REDIS_PORT", (None, None))[1]
        or env_proc.entries.get("REDIS_PORT", (None, None))[1]
        or str(redis_port_from_assembly or "6379")
    )
    if redis_pass:
        redis_url = f"redis://:{redis_pass}@{redis_host}:{redis_port}/0"
    else:
        redis_url = f"redis://{redis_host}:{redis_port}/0"

    if force_prompt:
        update_env_value(env_ingress, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_proc, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_metrics, "REDIS_PASSWORD", redis_pass)
        update_env_value(env_ingress, "REDIS_URL", redis_url)
        update_env_value(env_proc, "REDIS_URL", redis_url)
        update_env_value(env_metrics, "REDIS_URL", redis_url)
        update_env_value(env_proxy, "REDIS_URL", redis_url)
    else:
        _set_env(env_ingress, "REDIS_PASSWORD", redis_pass)
        _set_env(env_proc, "REDIS_PASSWORD", redis_pass)
        _set_env(env_metrics, "REDIS_PASSWORD", redis_pass)
        _set_env(env_ingress, "REDIS_URL", redis_url)
        _set_env(env_proc, "REDIS_URL", redis_url)
        _set_env(env_metrics, "REDIS_URL", redis_url)
        _set_env(env_proxy, "REDIS_URL", redis_url)

    if not use_redis_secret:
        if redis_pass and str(redis_pass).strip():
            _set_nested(assembly_data, ["infra", "redis", "password"], redis_pass)
        else:
            _delete_nested(assembly_data, ["infra", "redis", "password"])
    _set_nested(assembly_data, ["infra", "redis", "host"], redis_host)
    _set_nested(assembly_data, ["infra", "redis", "port"], str(redis_port))

    if is_placeholder(env_ingress.entries.get("POSTGRES_HOST", (None, None))[1]):
        update_env_value(env_ingress, "POSTGRES_HOST", "postgres-db")
    if is_placeholder(env_proc.entries.get("POSTGRES_HOST", (None, None))[1]):
        update_env_value(env_proc, "POSTGRES_HOST", "postgres-db")

    pg_host_val = env_ingress.entries.get("POSTGRES_HOST", (None, None))[1] or "postgres-db"
    pg_host_val = normalize_docker_host(console, pg_host_val, "Postgres")
    pg_port_val = env_pg.entries.get("POSTGRES_PORT", (None, None))[1] or env_main.entries.get("POSTGRES_PORT", (None, None))[1] or "5432"
    update_env_value(env_pg, "POSTGRES_HOST", pg_host_val)
    update_env_value(env_pg, "POSTGRES_PORT", str(pg_port_val))
    if assembly_path:
        update_env_value(env_ingress, "POSTGRES_HOST", pg_host_val)
        update_env_value(env_proc, "POSTGRES_HOST", pg_host_val)
    if pg_ssl_from_assembly is not None and assembly_path:
        update_env_value(env_ingress, "POSTGRES_SSL", str(pg_ssl_from_assembly).lower())
        update_env_value(env_proc, "POSTGRES_SSL", str(pg_ssl_from_assembly).lower())
        update_env_value(env_pg, "POSTGRES_SSL", str(pg_ssl_from_assembly).lower())
    _set_nested(assembly_data, ["infra", "postgres", "host"], pg_host_val)
    _set_nested(assembly_data, ["infra", "postgres", "port"], str(pg_port_val))
    if pg_ssl_from_assembly is not None:
        _set_nested(assembly_data, ["infra", "postgres", "ssl"], bool(pg_ssl_from_assembly))
    _autosave()

    openai_from_secrets = _secret_pick(
        ("services", "openai", "api_key"),
        ("openai_api_key",),
        ("openai", "api_key"),
        ("providers", "openai", "api_key"),
    )
    anthropic_from_secrets = _secret_pick(
        ("services", "anthropic", "api_key"),
        ("anthropic_api_key",),
        ("anthropic", "api_key"),
        ("providers", "anthropic", "api_key"),
    )
    brave_from_secrets = _secret_pick(
        ("services", "brave", "api_key"),
        ("brave_api_key",),
        ("brave", "api_key"),
        ("search", "brave_api_key"),
    )
    openrouter_from_secrets = _secret_pick(
        ("services", "openrouter", "api_key"),
        ("openrouter_api_key",),
    )
    google_from_secrets = _secret_pick(
        ("services", "google", "api_key"),
        ("google_api_key",),
        ("gemini_api_key",),
    )
    huggingface_from_secrets = _secret_pick(
        ("services", "huggingface", "api_key"),
        ("hugging_face_api_key",),
        ("huggingface_api_key",),
        ("hugging_face_key",),
    )
    aws_access_key_from_secrets = _secret_pick(
        ("aws", "access_key_id"),
        ("aws_access_key_id",),
    )
    aws_secret_key_from_secrets = _secret_pick(
        ("aws", "secret_access_key"),
        ("aws_secret_access_key",),
    )
    stripe_secret_from_secrets = _secret_pick(
        ("services", "stripe", "secret_key"),
        ("stripe_secret_key",),
    )
    stripe_webhook_from_secrets = _secret_pick(
        ("services", "stripe", "webhook_secret"),
        ("stripe_webhook_secret",),
    )
    claude_code_from_secrets = _secret_pick(
        ("services", "anthropic", "claude_code_key"),
        ("anthropic_claude_code_key",),
        ("claude_code_key",),
    )
    openai_key = prompt_secret_value(
        console,
        "OpenAI API key",
        required=False,
        current=openai_from_secrets or env_proc.entries.get("OPENAI_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    anthropic_key = prompt_secret_value(
        console,
        "Anthropic API key",
        required=False,
        current=anthropic_from_secrets or env_proc.entries.get("ANTHROPIC_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    openrouter_key = prompt_secret_value(
        console,
        "OpenRouter API key",
        required=False,
        current=openrouter_from_secrets or env_proc.entries.get("OPENROUTER_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    brave_key = prompt_secret_value(
        console,
        "Brave Search API key",
        required=False,
        current=brave_from_secrets or env_proc.entries.get("BRAVE_API_KEY", (None, None))[1],
        force_prompt=force_prompt,
    )
    if openai_key:
        runtime_secrets["services.openai.api_key"] = openai_key
    if anthropic_key:
        runtime_secrets["services.anthropic.api_key"] = anthropic_key
    if brave_key:
        runtime_secrets["services.brave.api_key"] = brave_key
    if openrouter_key:
        runtime_secrets["services.openrouter.api_key"] = openrouter_key
    elif openrouter_from_secrets:
        runtime_secrets["services.openrouter.api_key"] = openrouter_from_secrets
    if google_from_secrets:
        runtime_secrets["services.google.api_key"] = google_from_secrets
    if huggingface_from_secrets:
        runtime_secrets["services.huggingface.api_key"] = huggingface_from_secrets
    if aws_access_key_from_secrets:
        runtime_secrets["aws.access_key_id"] = aws_access_key_from_secrets
        update_env_value(env_proxy, "AWS_ACCESS_KEY_ID", aws_access_key_from_secrets)
    if aws_secret_key_from_secrets:
        runtime_secrets["aws.secret_access_key"] = aws_secret_key_from_secrets
        update_env_value(env_proxy, "AWS_SECRET_ACCESS_KEY", aws_secret_key_from_secrets)
    if stripe_secret_from_secrets:
        runtime_secrets["services.stripe.secret_key"] = stripe_secret_from_secrets
    if stripe_webhook_from_secrets:
        runtime_secrets["services.stripe.webhook_secret"] = stripe_webhook_from_secrets
    if claude_code_from_secrets:
        runtime_secrets["services.anthropic.claude_code_key"] = claude_code_from_secrets
    if use_bundles_secrets is None:
        use_bundles_secrets = bool(bundles_secrets_data)
    if use_bundles_secrets and bundles_secrets_data:
        flat_bundle_secrets = _flatten_bundle_secrets(bundles_secrets_data)
        runtime_secrets.update(flat_bundle_secrets)
        # Store bundle secret key lists in the sidecar so admin UI can show "known keys"
        # even when secrets were provisioned via bundles.secrets.yaml.
        keys_by_bundle: Dict[str, List[str]] = {}
        for key in flat_bundle_secrets.keys():
            parts = key.split(".")
            if len(parts) >= 4 and parts[0] == "bundles" and parts[2] == "secrets":
                bundle_id = parts[1]
                keys_by_bundle.setdefault(bundle_id, []).append(key)
        for bundle_id, keys in keys_by_bundle.items():
            runtime_secrets[f"bundles.{bundle_id}.secrets.__keys"] = json.dumps(sorted(keys))
    if force_prompt or is_placeholder(env_proc.entries.get("OPENAI_API_KEY", (None, None))[1]):
        update_env_value(env_proc, "OPENAI_API_KEY", "")
    if force_prompt or is_placeholder(env_proc.entries.get("ANTHROPIC_API_KEY", (None, None))[1]):
        update_env_value(env_proc, "ANTHROPIC_API_KEY", "")
    if force_prompt or is_placeholder(env_proc.entries.get("BRAVE_API_KEY", (None, None))[1]):
        update_env_value(env_proc, "BRAVE_API_KEY", "")

    _autosave()

    host_storage_default = _get_nested(assembly_data, "paths", "host_kdcube_storage_path") or defaults.get("host_kb_storage")
    host_storage = ensure_absolute(
        console,
        "Host system storage path",
        env_main.entries.get("HOST_KDCUBE_STORAGE_PATH", (None, None))[1],
        str(host_storage_default) if host_storage_default else None,
        force_prompt=force_prompt,
    )
    host_bundles_current = env_main.entries.get("HOST_BUNDLES_PATH", (None, None))[1]
    agentic_root = env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1] or "/bundles"
    if host_bundles_current:
        normalized = str(host_bundles_current).strip()
        if normalized.startswith("/bundles") or normalized.startswith("/app/") or normalized == agentic_root:
            console.print(
                "[yellow]HOST_BUNDLES_PATH points to a container path; "
                "resetting to the local workdir bundles folder.[/yellow]"
            )
            host_bundles_current = None
    host_bundles_default = _get_nested(assembly_data, "paths", "host_bundles_path") or defaults.get("host_bundles")
    if force_prompt or not is_placeholder(host_bundles_current):
        host_bundles = ensure_absolute(
            console,
            "Host bundles root (git clones)",
            host_bundles_current,
            str(host_bundles_default) if host_bundles_default else None,
            force_prompt=force_prompt,
        )
    else:
        host_bundles = str(host_bundles_default or "")
    host_bundle_storage = ensure_absolute(
        console,
        "Host bundle local storage path",
        env_main.entries.get("HOST_BUNDLE_STORAGE_PATH", (None, None))[1],
        str(_get_nested(assembly_data, "paths", "host_bundle_storage_path") or defaults.get("host_bundle_storage")),
        force_prompt=force_prompt,
    )
    host_exec = ensure_absolute(
        console,
        "Host exec workspace path",
        env_main.entries.get("HOST_EXEC_WORKSPACE_PATH", (None, None))[1],
        str(_get_nested(assembly_data, "paths", "host_exec_workspace_path") or defaults.get("host_exec_workspace")),
        force_prompt=force_prompt,
    )

    update_env_value(env_main, "HOST_KDCUBE_STORAGE_PATH", host_storage)
    update_env_value(env_main, "HOST_BUNDLES_PATH", host_bundles)
    update_env_value(env_main, "HOST_BUNDLE_STORAGE_PATH", host_bundle_storage)
    update_env_value(env_main, "HOST_EXEC_WORKSPACE_PATH", host_exec)
    _set_nested(assembly_data, ["paths", "host_kdcube_storage_path"], host_storage)
    _set_nested(assembly_data, ["paths", "host_bundles_path"], host_bundles)
    _set_nested(assembly_data, ["paths", "host_bundle_storage_path"], host_bundle_storage)
    _set_nested(assembly_data, ["paths", "host_exec_workspace_path"], host_exec)
    # Always align compose paths to the selected workdir.
    update_env_value(env_main, "KDCUBE_CONFIG_DIR", str(ctx.config_dir))
    update_env_value(env_main, "KDCUBE_DATA_DIR", str(ctx.data_dir))
    # Always keep logs in the workdir for compose mounts.
    update_env_value(env_main, "KDCUBE_LOGS_DIR", str(ctx.workdir / "logs"))
    if is_placeholder(env_main.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_main, "AGENTIC_BUNDLES_ROOT", "/bundles")
    if is_placeholder(env_main.entries.get("BUNDLE_STORAGE_ROOT", (None, None))[1]):
        update_env_value(env_main, "BUNDLE_STORAGE_ROOT", "/bundle-storage")

    ports_block = _get_nested(assembly_data, "ports")
    if not isinstance(ports_block, dict):
        ports_block = {}

    def _set_port(env_key: str, port_key: str, default_val: str) -> None:
        current_env = env_main.entries.get(env_key, (None, None))[1]
        asm_val = ports_block.get(port_key)
        if asm_val is not None and not is_placeholder(str(asm_val)):
            update_env_value(env_main, env_key, str(asm_val))
            current_env = str(asm_val)
        if is_placeholder(current_env):
            update_env_value(env_main, env_key, default_val)
            current_env = default_val
        ports_block[port_key] = str(current_env)

    _set_port("CHAT_APP_PORT", "ingress", "8010")
    _set_port("CHAT_PROCESSOR_PORT", "proc", "8020")
    _set_port("METRICS_PORT", "metrics", "8090")
    ui_port_current = env_main.entries.get("KDCUBE_UI_PORT", (None, None))[1]
    ui_port_from_assembly = ports_block.get("ui")
    if assembly_user_supplied and ui_port_from_assembly is not None and not is_placeholder(str(ui_port_from_assembly)):
        update_env_value(env_main, "KDCUBE_UI_PORT", str(ui_port_from_assembly))
        ui_port_current = str(ui_port_from_assembly)
    else:
        ui_default = str(ui_port_current or ui_port_from_assembly or "80")
        ui_port_current = ask(console, "UI port", default=ui_default)
        update_env_value(env_main, "KDCUBE_UI_PORT", str(ui_port_current))
    ports_block["ui"] = str(ui_port_current)
    _set_port("KDCUBE_UI_SSL_PORT", "ui_ssl", "443")
    _set_nested(assembly_data, ["ports"], ports_block)

    _autosave()

    # Routines: apply scheduler settings from assembly descriptor (non-interactive).
    if assembly_path:
        routines_block = _get_nested(assembly_data, "routines")
        if isinstance(routines_block, dict):
            economics = routines_block.get("economics") or {}
            stripe = routines_block.get("stripe") or {}
            opex = routines_block.get("opex") or {}
            _routines_map = [
                (economics, "subscription_rollover_enabled", "SUBSCRIPTION_ROLLOVER_ENABLED"),
                (economics, "subscription_rollover_cron", "SUBSCRIPTION_ROLLOVER_CRON"),
                (economics, "subscription_rollover_lock_ttl_seconds", "SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS"),
                (economics, "subscription_rollover_sweep_limit", "SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT"),
                (stripe, "reconcile_enabled", "STRIPE_RECONCILE_ENABLED"),
                (stripe, "reconcile_cron", "STRIPE_RECONCILE_CRON"),
                (stripe, "reconcile_lock_ttl_seconds", "STRIPE_RECONCILE_LOCK_TTL_SECONDS"),
                (opex, "agg_cron", "OPEX_AGG_CRON"),
            ]
            for block, yaml_key, env_key in _routines_map:
                if not isinstance(block, dict):
                    continue
                val = block.get(yaml_key)
                if val is None or is_placeholder(str(val)):
                    continue
                str_val = str(val).lower() if isinstance(val, bool) else str(val)
                update_env_value(env_ingress, env_key, str_val)

    # Notifications: apply email settings from assembly descriptor (non-interactive).
    if assembly_path:
        email_block = _get_nested(assembly_data, "notifications", "email")
        if isinstance(email_block, dict):
            _email_map = [
                ("enabled", "EMAIL_ENABLED"),
                ("host", "EMAIL_HOST"),
                ("port", "EMAIL_PORT"),
                ("user", "EMAIL_USER"),
                ("from", "EMAIL_FROM"),
                ("to", "EMAIL_TO"),
                ("use_tls", "EMAIL_USE_TLS"),
            ]
            for yaml_key, env_key in _email_map:
                val = email_block.get(yaml_key)
                if val is None or is_placeholder(str(val)):
                    continue
                str_val = str(val).lower() if isinstance(val, bool) else str(val)
                update_env_value(env_ingress, env_key, str_val)

    bundles_descriptor_selected = False
    assembly_descriptor_selected = False

    if use_bundles_descriptor is None and bundles_path:
        use_bundles_descriptor = True

    # bundles.yaml descriptor (preferred when provided)
    if use_bundles_descriptor and bundles_path:
        update_env_value(env_main, "HOST_BUNDLES_DESCRIPTOR_PATH", str(bundles_path))
        update_env_value(env_proc, "AGENTIC_BUNDLES_JSON", "/config/bundles.yaml")
        bundles_descriptor_selected = True
    else:
        current_bundles_descriptor = env_main.entries.get("HOST_BUNDLES_DESCRIPTOR_PATH", (None, None))[1]
        descriptor_value = (current_bundles_descriptor or "").strip().strip("'\"")
        if use_bundles_descriptor is False or force_prompt or is_placeholder(current_bundles_descriptor) or descriptor_value in {"", "/dev/null"}:
            update_env_value(env_main, "HOST_BUNDLES_DESCRIPTOR_PATH", "/dev/null")

    # assembly.yaml bundles section (legacy / fallback)
    if not bundles_descriptor_selected:
        current_descriptor = env_main.entries.get("HOST_BUNDLE_DESCRIPTOR_PATH", (None, None))[1]
        if release_descriptor_path and use_descriptor_bundles is not False:
            current_descriptor = release_descriptor_path
            update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", release_descriptor_path)
            update_env_value(env_proc, "AGENTIC_BUNDLES_JSON", "/config/assembly.yaml")
            assembly_descriptor_selected = True
        descriptor_value = (current_descriptor or "").strip().strip("'\"")
        if use_descriptor_bundles is None:
            if release_descriptor_path:
                update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", release_descriptor_path)
                update_env_value(env_proc, "AGENTIC_BUNDLES_JSON", "/config/assembly.yaml")
                assembly_descriptor_selected = True
            elif force_prompt or is_placeholder(current_descriptor) or descriptor_value in {"", "/dev/null"}:
                update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", "/dev/null")
        elif use_descriptor_bundles:
            if release_descriptor_path:
                update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", release_descriptor_path)
                update_env_value(env_proc, "AGENTIC_BUNDLES_JSON", "/config/assembly.yaml")
                assembly_descriptor_selected = True
            elif is_placeholder(current_descriptor) or descriptor_value in {"", "/dev/null"}:
                update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", "/dev/null")
        else:
            update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", "/dev/null")
    else:
        # Disable assembly bundle descriptor mount when bundles.yaml is active.
        update_env_value(env_main, "HOST_BUNDLE_DESCRIPTOR_PATH", "/dev/null")

    # If any descriptor is set, force a one-time registry sync on startup.
    if bundles_descriptor_selected or assembly_descriptor_selected:
        update_env_value(env_proc, "BUNDLES_FORCE_ENV_ON_STARTUP", "1")
        update_env_value(env_proc, "BUNDLE_GIT_RESOLUTION_ENABLED", "1")
    else:
        update_env_value(env_proc, "BUNDLES_FORCE_ENV_ON_STARTUP", "0")

    # Bundle secrets can be requested long after startup. Disable sidecar token expiry
    # so get_secret() keeps working during runtime/admin updates.
    if use_bundles_secrets or use_bundles_descriptor:
        update_env_value(env_main, "SECRETS_TOKEN_TTL_SECONDS", "0")
        update_env_value(env_main, "SECRETS_TOKEN_MAX_USES", "0")

    _autosave()

    git_token_from_secrets = _secret_pick(("git", "http_token"), ("git_http_token",))
    env_http = env_proc.entries.get("GIT_HTTP_TOKEN", (None, None))[1]
    existing_ssh = env_proc.entries.get("GIT_SSH_KEY_PATH", (None, None))[1]
    if env_http and not is_placeholder(env_http):
        console.print(
            "[yellow]Found GIT_HTTP_TOKEN in .env.proc; it will be cleared and treated as runtime-only.[/yellow]"
        )
        update_env_value(env_proc, "GIT_HTTP_TOKEN", "")
        env_http = None
    if git_token_from_secrets and not is_placeholder(git_token_from_secrets):
        default_auth = "https-token"
    elif not is_placeholder(existing_ssh):
        default_auth = "ssh"
    else:
        default_auth = "skip"

    auth_options = ["ssh", "https-token", "skip"]
    try:
        default_idx = auth_options.index(default_auth)
    except ValueError:
        default_idx = 0
    console.print("[bold]Git bundle authentication[/bold]")
    auth_choice = select_option(
        console,
        "Git auth method for private bundles",
        options=auth_options,
        default_index=default_idx,
    )
    if auth_choice == "ssh":
        if force_prompt or is_placeholder(env_main.entries.get("HOST_GIT_SSH_KEY_PATH", (None, None))[1]):
            ssh_key = prompt_optional(console, "Host SSH key path for git bundles")
            update_env_value(env_main, "HOST_GIT_SSH_KEY_PATH", ssh_key or "/dev/null")
        if force_prompt or is_placeholder(env_main.entries.get("HOST_GIT_KNOWN_HOSTS_PATH", (None, None))[1]):
            known_hosts = prompt_optional(console, "Host known_hosts path for git bundles")
            update_env_value(env_main, "HOST_GIT_KNOWN_HOSTS_PATH", known_hosts or "/dev/null")

        update_if_placeholder(env_proc, "GIT_SSH_KEY_PATH", "/run/secrets/git_ssh_key")
        update_if_placeholder(env_proc, "GIT_SSH_KNOWN_HOSTS", "/run/secrets/git_known_hosts")
        update_if_placeholder(env_proc, "GIT_SSH_STRICT_HOST_KEY_CHECKING", "yes")
        # Clear HTTPS token if placeholder
        if is_placeholder(env_proc.entries.get("GIT_HTTP_TOKEN", (None, None))[1]):
            update_env_value(env_proc, "GIT_HTTP_TOKEN", "")
        if is_placeholder(env_proc.entries.get("GIT_HTTP_USER", (None, None))[1]):
            update_env_value(env_proc, "GIT_HTTP_USER", "")
    elif auth_choice == "https-token":
        console.print("[dim]Create a GitHub token at https://github.com/settings/tokens[/dim]")
        use_git_secret = False
        if git_token_from_secrets and not is_placeholder(git_token_from_secrets):
            use_git_secret = ask_confirm(
                console,
                "Use Git HTTPS token from secrets descriptor?",
                default=True,
            )
        if use_git_secret:
            runtime_secrets["services.git.http_token"] = git_token_from_secrets
        else:
            token = prompt_secret_value(
                console,
                "Git HTTPS token",
                required=True,
                current=None,
                force_prompt=force_prompt,
            )
            if token:
                runtime_secrets["services.git.http_token"] = token
        # Never store the token in env files.
        update_env_value(env_proc, "GIT_HTTP_TOKEN", "")
        # Avoid dangling SSH placeholders if user chose token
        if is_placeholder(env_proc.entries.get("GIT_SSH_KEY_PATH", (None, None))[1]):
            update_env_value(env_proc, "GIT_SSH_KEY_PATH", "")
        if is_placeholder(env_proc.entries.get("GIT_SSH_KNOWN_HOSTS", (None, None))[1]):
            update_env_value(env_proc, "GIT_SSH_KNOWN_HOSTS", "")
        if is_placeholder(env_proc.entries.get("GIT_SSH_STRICT_HOST_KEY_CHECKING", (None, None))[1]):
            update_env_value(env_proc, "GIT_SSH_STRICT_HOST_KEY_CHECKING", "")
        if is_placeholder(env_proc.entries.get("GIT_HTTP_USER", (None, None))[1]):
            update_env_value(env_proc, "GIT_HTTP_USER", "")
        # If host SSH paths are placeholders, disable mounts to avoid missing-path binds.
        if is_placeholder(env_main.entries.get("HOST_GIT_SSH_KEY_PATH", (None, None))[1]):
            update_env_value(env_main, "HOST_GIT_SSH_KEY_PATH", "/dev/null")
        if is_placeholder(env_main.entries.get("HOST_GIT_KNOWN_HOSTS_PATH", (None, None))[1]):
            update_env_value(env_main, "HOST_GIT_KNOWN_HOSTS_PATH", "/dev/null")

    _autosave()

    bundles_json = env_proc.entries.get("AGENTIC_BUNDLES_JSON", (None, None))[1]
    if should_replace_bundles_config(bundles_json):
        update_env_value(env_proc, "AGENTIC_BUNDLES_JSON", "/config/assembly.yaml")

    if is_placeholder(env_proc.entries.get("KDCUBE_STORAGE_PATH", (None, None))[1]):
        update_env_value(env_proc, "KDCUBE_STORAGE_PATH", "/kdcube-storage")
    if is_placeholder(env_proc.entries.get("CB_BUNDLE_STORAGE_URL", (None, None))[1]):
        update_env_value(env_proc, "CB_BUNDLE_STORAGE_URL", "/kdcube-storage")
    if is_placeholder(env_proc.entries.get("BUNDLE_STORAGE_ROOT", (None, None))[1]):
        update_env_value(env_proc, "BUNDLE_STORAGE_ROOT", "/bundle-storage")
    if is_placeholder(env_proc.entries.get("AGENTIC_BUNDLES_ROOT", (None, None))[1]):
        update_env_value(env_proc, "AGENTIC_BUNDLES_ROOT", "/bundles")
    if is_placeholder(env_proc.entries.get("HOST_BUNDLE_STORAGE_PATH", (None, None))[1]):
        update_env_value(env_proc, "HOST_BUNDLE_STORAGE_PATH", host_bundle_storage)

    # For compose installs, always use the container log path.
    update_env_value(env_ingress, "LOG_DIR", "/logs")
    update_env_value(env_proc, "LOG_DIR", "/logs")

    ui_build_context = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    default_ui_context = defaults.get("ui_build_context", "")
    if is_placeholder(ui_build_context):
        update_env_value(env_main, "UI_BUILD_CONTEXT", default_ui_context)
    else:
        normalized_current = _normalize_path(ui_build_context)
        normalized_default = _normalize_path(default_ui_context)
        if normalized_default and normalized_current and normalized_current != normalized_default:
            if ".kdcube/kdcube-ai-app" in normalized_current:
                update_env_value(env_main, "UI_BUILD_CONTEXT", default_ui_context)

    for key, default_key in [
        ("UI_DOCKERFILE_PATH", "ui_dockerfile_path"),
        ("UI_SOURCE_PATH", "ui_source_path"),
        ("UI_ENV_BUILD_RELATIVE", "ui_env_build_relative"),
        ("NGINX_UI_CONFIG_FILE_PATH", "nginx_ui_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            update_env_value(env_main, key, defaults.get(default_key, ""))

    # If the assembly descriptor includes frontend configuration, use it.
    frontend_descriptor: Dict[str, object] = {}
    if use_descriptor_frontend is not False and isinstance(release_descriptor, dict):
        frontend_descriptor = release_descriptor.get("frontend") or {}

    descriptor_dir = None
    if release_descriptor_path:
        try:
            descriptor_dir = Path(release_descriptor_path).expanduser().resolve().parent
        except Exception:
            descriptor_dir = None

    frontend_template_override: Optional[Path] = None
    frontend_root: Optional[Path] = None
    frontend_build: Optional[Dict[str, object]] = None
    frontend_image: Optional[str] = None
    frontend_config_value: Optional[str] = None
    nginx_ui_config_value: Optional[str] = None
    env_build_value: Optional[str] = None

    if isinstance(frontend_descriptor, dict):
        build_section = frontend_descriptor.get("build")
        if isinstance(build_section, dict):
            frontend_build = build_section
        else:
            for legacy_key in ("repo", "dockerfile", "src", "ref"):
                if legacy_key in frontend_descriptor:
                    frontend_build = frontend_descriptor
                    break

        frontend_image = _as_str(frontend_descriptor.get("image"))
        if frontend_image is not None:
            update_env_value(env_main, "KDCUBE_UI_IMAGE", frontend_image.strip())
        elif frontend_build is not None:
            update_env_value(env_main, "KDCUBE_UI_IMAGE", "")

        frontend_config_value = _as_str(frontend_descriptor.get("frontend_config"))
        if not frontend_config_value and isinstance(frontend_build, dict):
            frontend_config_value = _as_str(frontend_build.get("frontend_config"))
        nginx_ui_config_value = _as_str(frontend_descriptor.get("nginx_ui_config"))
        if not nginx_ui_config_value and isinstance(frontend_build, dict):
            nginx_ui_config_value = _as_str(frontend_build.get("nginx_ui_config"))
        env_build_value = _as_str(frontend_descriptor.get("ui_env_build_relative") or frontend_descriptor.get("env_build"))
        if not env_build_value and isinstance(frontend_build, dict):
            env_build_value = _as_str(frontend_build.get("ui_env_build_relative") or frontend_build.get("env_build"))

    if isinstance(frontend_build, dict) and frontend_build.get("repo"):
        frontend_repo = str(frontend_build.get("repo"))
        frontend_ref = frontend_build.get("ref")
        frontend_root = git_clone_or_update(
            console,
            frontend_repo,
            frontend_ref if isinstance(frontend_ref, str) else None,
            ctx.workdir / "frontend",
        )

        update_env_value(env_main, "UI_BUILD_CONTEXT", str(frontend_root))

        dockerfile_path = frontend_build.get("dockerfile")
        if isinstance(dockerfile_path, str) and dockerfile_path:
            update_env_value(env_main, "UI_DOCKERFILE_PATH", dockerfile_path)
        source_path = frontend_build.get("src")
        if isinstance(source_path, str) and source_path:
            update_env_value(env_main, "UI_SOURCE_PATH", source_path)
        if isinstance(env_build_value, str) and env_build_value:
            if is_placeholder(env_build_value) or "path/to/" in env_build_value:
                env_build_value = ".env.ui.build"
            update_env_value(env_main, "UI_ENV_BUILD_RELATIVE", env_build_value)
        else:
            update_env_value(env_main, "UI_ENV_BUILD_RELATIVE", ".env.ui.build")
        if isinstance(nginx_ui_config_value, str) and nginx_ui_config_value:
            update_env_value(env_main, "NGINX_UI_CONFIG_FILE_PATH", nginx_ui_config_value)

    if isinstance(frontend_config_value, str) and frontend_config_value:
        frontend_template_override = _resolve_descriptor_path(
            frontend_config_value,
            repo_root=frontend_root,
            descriptor_dir=descriptor_dir,
        )

    ui_build_context_final = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    ui_env_build_rel_final = env_main.entries.get("UI_ENV_BUILD_RELATIVE", (None, None))[1]
    ui_env_build_rel_final = normalize_env_build_relative(ui_env_build_rel_final)
    if ui_env_build_rel_final:
        update_env_value(env_main, "UI_ENV_BUILD_RELATIVE", ui_env_build_rel_final)
    ensure_ui_env_build_file(console, ui_build_context_final, ui_env_build_rel_final)

    company_name_raw = _get_nested(assembly_data, "company")
    company_name = company_name_raw.strip() if isinstance(company_name_raw, str) else None

    if auth_provider == "simple":
        frontend_template = ctx.ai_app_root / "deployment/docker/all_in_one_kdcube/frontend/config.hardcoded.json"
        compose_ui_config = ctx.config_dir / "frontend.config.hardcoded.json"
        if ctx.docker_dir.name == "custom-ui-managed-infra":
            defaults["nginx_proxy_config"] = (
                "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_hardcoded.conf"
                if proxy_ssl_enabled
                else "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy.conf"
            )
        else:
            defaults["nginx_proxy_config"] = (
                "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_ssl.conf"
                if proxy_ssl_enabled
                else "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"
            )
    else:
        if auth_mode == "delegated":
            frontend_template = ctx.ai_app_root / "deployment/docker/all_in_one_kdcube/frontend/config.delegated.json"
            compose_ui_config = ctx.config_dir / "frontend.config.delegated.json"
        else:
            frontend_template = ctx.ai_app_root / "deployment/docker/all_in_one_kdcube/frontend/config.cognito.json"
            compose_ui_config = ctx.config_dir / "frontend.config.cognito.json"
        if ctx.docker_dir.name == "custom-ui-managed-infra":
            if auth_mode == "delegated":
                defaults["nginx_proxy_config"] = (
                    "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_delegated_auth.conf"
                    if proxy_ssl_enabled
                    else "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_delegated.conf"
                )
            else:
                defaults["nginx_proxy_config"] = (
                    "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_cognito.conf"
                    if proxy_ssl_enabled
                    else "app/ai-app/deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy.conf"
                )
        else:
            if auth_mode == "delegated":
                defaults["nginx_proxy_config"] = (
                    "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_ssl_delegated_auth.conf"
                    if proxy_ssl_enabled
                    else "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_delegated.conf"
                )
            else:
                defaults["nginx_proxy_config"] = (
                    "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_ssl.conf"
                    if proxy_ssl_enabled
                    else "app/ai-app/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"
                )
    if frontend_template_override and frontend_template_override.exists():
        frontend_template = frontend_template_override
    cognito_region_val = env_ingress.entries.get("COGNITO_REGION", (None, None))[1]
    cognito_user_pool_id_val = env_ingress.entries.get("COGNITO_USER_POOL_ID", (None, None))[1]
    cognito_app_client_id_val = env_ingress.entries.get("COGNITO_APP_CLIENT_ID", (None, None))[1]
    proxy_route_prefix_raw = _get_nested(assembly_data, "proxy", "route_prefix")
    proxy_route_prefix = normalize_routes_prefix(proxy_route_prefix_raw) if proxy_route_prefix_raw else ""
    if proxy_route_prefix:
        _set_nested(assembly_data, ["proxy", "route_prefix"], proxy_route_prefix)

    write_frontend_config(
        compose_ui_config,
        tenant,
        project,
        template_path=frontend_template,
        cognito_region=cognito_region_val,
        cognito_user_pool_id=cognito_user_pool_id_val,
        cognito_app_client_id=cognito_app_client_id_val,
        routes_prefix=proxy_route_prefix or None,
        company_name=company_name,
    )
    routes_prefix = proxy_route_prefix or normalize_routes_prefix(_load_json_file(compose_ui_config).get("routesPrefix"))
    runtime_proxy_path = env_main.entries.get("NGINX_PROXY_RUNTIME_CONFIG_PATH", (None, None))[1]
    desired_runtime_path = str((ctx.config_dir / Path(defaults["nginx_proxy_config"]).name).resolve())
    if is_placeholder(runtime_proxy_path) or not runtime_proxy_path:
        runtime_proxy_path = desired_runtime_path
        update_env_value(env_main, "NGINX_PROXY_RUNTIME_CONFIG_PATH", runtime_proxy_path)
    else:
        normalized_current = _normalize_path(runtime_proxy_path)
        normalized_desired = _normalize_path(desired_runtime_path)
        if normalized_current and normalized_desired and normalized_current != normalized_desired:
            runtime_proxy_path = desired_runtime_path
            update_env_value(env_main, "NGINX_PROXY_RUNTIME_CONFIG_PATH", runtime_proxy_path)
    runtime_proxy = Path(runtime_proxy_path).expanduser()
    sync_nginx_proxy_config(runtime_proxy, ctx.ai_app_root, defaults["nginx_proxy_config"])
    update_nginx_routes_prefix(runtime_proxy, routes_prefix)
    if proxy_ssl_enabled:
        ssl_domain = normalize_domain_host(_as_str(_get_nested(assembly_data, "domain")))
        if ssl_domain:
            update_nginx_ssl_domain(runtime_proxy, ssl_domain)
        else:
            console.print(
                "[yellow]proxy.ssl is enabled but assembly.domain is empty; "
                "the generated nginx SSL config will keep YOUR_DOMAIN_NAME placeholders.[/yellow]"
            )
    desired_frontend_path = str(compose_ui_config)
    current_frontend_path = env_main.entries.get("PATH_TO_FRONTEND_CONFIG_JSON", (None, None))[1]
    if is_placeholder(current_frontend_path) or not current_frontend_path:
        current_frontend_path = desired_frontend_path
        update_env_value(env_main, "PATH_TO_FRONTEND_CONFIG_JSON", current_frontend_path)
    else:
        normalized_current = _normalize_path(current_frontend_path)
        normalized_desired = _normalize_path(desired_frontend_path)
        if normalized_current and normalized_desired and normalized_current != normalized_desired:
            current_frontend_path = desired_frontend_path
            update_env_value(env_main, "PATH_TO_FRONTEND_CONFIG_JSON", current_frontend_path)

    # Keep the configured frontend config in sync.
    try:
        write_frontend_config(
            Path(current_frontend_path).expanduser(),
            tenant,
            project,
            template_path=frontend_template,
            cognito_region=cognito_region_val,
            cognito_user_pool_id=cognito_user_pool_id_val,
            cognito_app_client_id=cognito_app_client_id_val,
            routes_prefix=proxy_route_prefix or None,
            company_name=company_name,
        )
    except Exception:
        pass

    if auth_provider == "simple":
        dev_ui_config = ctx.ai_app_root / "ui/chat-web-app/public/private/config.hardcoded.json"
    elif auth_mode == "delegated":
        dev_ui_config = ctx.ai_app_root / "ui/chat-web-app/public/private/config.delegated.json"
    else:
        dev_ui_config = ctx.ai_app_root / "ui/chat-web-app/public/private/config.cognito.demo.json"
    write_frontend_config(
        dev_ui_config,
        tenant,
        project,
        template_path=frontend_template,
        cognito_region=cognito_region_val,
        cognito_user_pool_id=cognito_user_pool_id_val,
        cognito_app_client_id=cognito_app_client_id_val,
        routes_prefix=proxy_route_prefix or None,
        company_name=company_name,
    )

    proxy_build_context = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    default_proxy_context = defaults.get("proxy_build_context", "")
    if is_placeholder(proxy_build_context):
        update_env_value(env_main, "PROXY_BUILD_CONTEXT", default_proxy_context)
    else:
        normalized_current = _normalize_path(proxy_build_context)
        normalized_default = _normalize_path(default_proxy_context)
        if normalized_default and normalized_current and normalized_current != normalized_default:
            if ".kdcube/kdcube-ai-app" in normalized_current:
                update_env_value(env_main, "PROXY_BUILD_CONTEXT", default_proxy_context)

    for key, default_key in [
        ("PROXY_DOCKERFILE_PATH", "proxy_dockerfile_path"),
        ("NGINX_PROXY_CONFIG_FILE_PATH", "nginx_proxy_config"),
    ]:
        value = env_main.entries.get(key, (None, None))[1]
        if is_placeholder(value):
            default_value = defaults.get(default_key, "")
            if default_value:
                update_env_value(env_main, key, default_value)
            else:
                update_env_value(env_main, key, ask(console, f"{key} (relative to PROXY_BUILD_CONTEXT)"))

    _autosave()

    update_env_value(env_main, "KDCUBE_COMPOSE_MODE", compose_mode)

    save_env_file(env_main)
    save_env_file(env_ingress)
    save_env_file(env_proc)
    save_env_file(env_metrics)
    save_env_file(env_pg)
    save_env_file(env_proxy)

    return {
        ".env": str(env_main.path),
        ".env.ingress": str(env_ingress.path),
        ".env.proc": str(env_proc.path),
        ".env.metrics": str(env_metrics.path),
        ".env.postgres.setup": str(env_pg.path),
        ".env.proxylogin": str(env_proxy.path),
    }, runtime_secrets


def run_setup(
    console: Console,
    *,
    repo_root: Optional[Path] = None,
    workdir: Optional[Path] = None,
    install_mode: Optional[str] = None,
    release_ref: Optional[str] = None,
    docker_namespace: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    install_mode = (install_mode or os.getenv("KDCUBE_INSTALL_MODE", "upstream")).strip().lower()
    if not dry_run:
        dry_run = parse_bool(os.getenv("KDCUBE_DRY_RUN", "")) is True
    env_release_ref = os.getenv("KDCUBE_RELEASE_REF", "").strip()
    env_docker_namespace = os.getenv("KDCUBE_DOCKER_NAMESPACE", "").strip()
    if not release_ref and env_release_ref:
        release_ref = env_release_ref
    if not docker_namespace and env_docker_namespace:
        docker_namespace = env_docker_namespace

    if repo_root is not None:
        repo_root = repo_root.expanduser().resolve()
        ai_app_root = repo_root / "app/ai-app"
        if not (ai_app_root / "deployment/docker/all_in_one_kdcube/docker-compose.yaml").exists():
            raise FileNotFoundError(
                f"Could not find deployment/docker/all_in_one_kdcube under {ai_app_root}"
            )
        lib_root = ai_app_root / "services/kdcube-ai-app"
        if not (lib_root / "kdcube_ai_app").exists():
            raise FileNotFoundError(f"Could not locate kdcube_ai_app under {lib_root}")
    else:
        lib_root = discover_lib_root()
        ai_app_root = find_ai_app_root(lib_root)
        if ai_app_root is None:
            ai_app_root = prompt_for_ai_app_root(console)
        if lib_root is None:
            console.print("[yellow]Could not infer lib root; using ai-app root instead.[/yellow]")
            lib_root = ai_app_root

    if workdir is None:
        workdir_env = os.getenv("KDCUBE_WORKDIR", "").strip()
        if workdir_env:
            workdir = Path(workdir_env).expanduser().resolve()
            console.print(f"[dim]Using workdir from environment:[/dim] {workdir}")
        else:
            default_workdir = str(Path.home() / ".kdcube" / "kdcube-runtime")
            workdir = Path(
                ask(console, "Compose workdir (config+data root)", default=default_workdir)
            ).expanduser().resolve()
    else:
        workdir = workdir.expanduser().resolve()

    config_dir = workdir / "config"
    data_dir = workdir / "data"
    logs_dir = workdir / "logs"

    # Resolve compose mode before generating env files.
    compose_mode_env = os.getenv("KDCUBE_COMPOSE_MODE", "").strip()
    compose_mode = compose_mode_env
    release_descriptor_path = None
    release_descriptor = {}
    secrets_descriptor_path = None
    secrets_descriptor: Dict[str, object] = {}
    bundles_descriptor_path = None
    bundles_descriptor: Dict[str, object] = {}
    bundles_secrets_path = None
    bundles_secrets: Dict[str, object] = {}
    gateway_descriptor_path = None
    gateway_descriptor: Dict[str, object] = {}
    env_descriptor = os.getenv("KDCUBE_ASSEMBLY_DESCRIPTOR_PATH", "").strip()
    env_secrets_descriptor = os.getenv("KDCUBE_SECRETS_DESCRIPTOR_PATH", "").strip()
    env_bundles_descriptor = os.getenv("KDCUBE_BUNDLES_DESCRIPTOR_PATH", "").strip()
    env_bundles_secrets = os.getenv("KDCUBE_BUNDLES_SECRETS_PATH", "").strip()
    env_gateway_descriptor = os.getenv("KDCUBE_GATEWAY_DESCRIPTOR_PATH", "").strip()
    skip_assembly_prompt = parse_bool(os.getenv("KDCUBE_ASSEMBLY_SKIP", "")) is True
    def _env_flag(name: str) -> Optional[bool]:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return None
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return None
    env_use_frontend = _env_flag("KDCUBE_ASSEMBLY_USE_FRONTEND")
    use_descriptor_bundles = _env_flag("KDCUBE_ASSEMBLY_USE_BUNDLES")
    use_descriptor_frontend = _env_flag("KDCUBE_ASSEMBLY_USE_FRONTEND")
    use_descriptor_platform = _env_flag("KDCUBE_ASSEMBLY_USE_PLATFORM")
    use_bundles_descriptor = _env_flag("KDCUBE_USE_BUNDLES_DESCRIPTOR")
    use_bundles_secrets = _env_flag("KDCUBE_USE_BUNDLES_SECRETS")
    if (config_dir / ".env").exists():
        env_existing = load_env_file(config_dir / ".env")
        existing_mode = env_existing.entries.get("KDCUBE_COMPOSE_MODE", (None, None))[1]
        if existing_mode:
            compose_mode = existing_mode.strip()
        existing_descriptor = env_existing.entries.get("HOST_BUNDLE_DESCRIPTOR_PATH", (None, None))[1]
        if existing_descriptor and not is_placeholder(existing_descriptor):
            release_descriptor_path = existing_descriptor
        existing_bundles = env_existing.entries.get("HOST_BUNDLES_DESCRIPTOR_PATH", (None, None))[1]
        if existing_bundles and not is_placeholder(existing_bundles):
            bundles_descriptor_path = existing_bundles
    if env_descriptor and (use_descriptor_bundles or use_descriptor_frontend or use_descriptor_platform):
        release_descriptor_path = env_descriptor

    if skip_assembly_prompt:
        release_descriptor_path = None
    elif not compose_mode and (not env_descriptor or (use_descriptor_bundles is None and use_descriptor_frontend is None and use_descriptor_platform is None)):
        default_assembly = str((workdir / "config" / "assembly.yaml").resolve())
        release_descriptor_path = ask(console, "Assembly descriptor path (assembly.yaml)", default=default_assembly)
        source_path_obj = Path(release_descriptor_path).expanduser()
        release_descriptor_path = str(source_path_obj)
        staged = stage_assembly_descriptor(
            Path(default_assembly),
            source_path=source_path_obj,
            ai_app_root=ai_app_root,
        )
        if os.getenv("KDCUBE_ASSEMBLY_USER_SUPPLIED", "") == "":
            user_supplied = source_path_obj.resolve() != Path(default_assembly).resolve()
            os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "1" if user_supplied else "0"
        if staged and Path(default_assembly).exists():
            release_descriptor_path = default_assembly
            # Defer compose mode selection until after descriptor is loaded.
        else:
            release_descriptor_path = None
            compose_mode = "all-in-one"
    elif env_descriptor and not skip_assembly_prompt:
        default_assembly = str((workdir / "config" / "assembly.yaml").resolve())
        staged = stage_assembly_descriptor(
            Path(default_assembly),
            source_path=Path(env_descriptor),
            ai_app_root=ai_app_root,
        )
        if os.getenv("KDCUBE_ASSEMBLY_USER_SUPPLIED", "") == "":
            user_supplied = Path(env_descriptor).expanduser().resolve() != Path(default_assembly).resolve()
            os.environ["KDCUBE_ASSEMBLY_USER_SUPPLIED"] = "1" if user_supplied else "0"
        if staged and Path(default_assembly).exists():
            release_descriptor_path = default_assembly
        else:
            release_descriptor_path = None

    if env_secrets_descriptor:
        secrets_descriptor_path = str(Path(env_secrets_descriptor).expanduser().resolve())

    if env_bundles_descriptor:
        default_bundles = str((workdir / "config" / "bundles.yaml").resolve())
        staged = stage_bundles_descriptor(
            Path(default_bundles),
            source_path=Path(env_bundles_descriptor),
            ai_app_root=ai_app_root,
        )
        if staged and Path(default_bundles).exists():
            bundles_descriptor_path = default_bundles
        else:
            bundles_descriptor_path = None

    if env_bundles_secrets:
        source_path = Path(env_bundles_secrets).expanduser().resolve()
        if source_path.exists():
            bundles_secrets_path = str(source_path)
        else:
            bundles_secrets_path = None

    if env_gateway_descriptor:
        gateway_descriptor_path = str(Path(env_gateway_descriptor).expanduser().resolve())

    if release_descriptor_path:
        descriptor_path = Path(release_descriptor_path).expanduser()
        if descriptor_path.exists():
            release_descriptor = load_release_descriptor(descriptor_path)
            if use_descriptor_frontend is True:
                compose_mode = "custom-ui-managed-infra"
            elif use_descriptor_frontend is False:
                if not compose_mode_env:
                    compose_mode = "all-in-one"
            elif isinstance(release_descriptor, dict) and release_descriptor.get("frontend"):
                compose_mode = "custom-ui-managed-infra"
            elif not compose_mode_env:
                compose_mode = "all-in-one"
            if isinstance(release_descriptor, dict) and not release_descriptor.get("frontend") and use_descriptor_frontend is not True:
                compose_mode = "all-in-one"
    if env_use_frontend is False and not compose_mode_env:
        compose_mode = "all-in-one"

    if bundles_descriptor_path:
        bundles_path = Path(bundles_descriptor_path).expanduser()
        if bundles_path.exists():
            bundles_descriptor = load_release_descriptor(bundles_path)
        else:
            bundles_descriptor_path = None

    if bundles_secrets_path:
        bundles_secrets_file = Path(bundles_secrets_path).expanduser()
        if bundles_secrets_file.exists():
            bundles_secrets = load_release_descriptor(bundles_secrets_file)
        else:
            bundles_secrets_path = None

    if secrets_descriptor_path:
        secrets_path = Path(secrets_descriptor_path).expanduser()
        if secrets_path.exists():
            secrets_descriptor = load_release_descriptor(secrets_path)

    if gateway_descriptor_path:
        gateway_path = Path(gateway_descriptor_path).expanduser()
        if gateway_path.exists():
            gateway_descriptor = load_gateway_descriptor(gateway_path)

    if compose_mode == "custom-ui-managed-infra":
        docker_dir = ai_app_root / "deployment/docker/custom-ui-managed-infra"
    else:
        docker_dir = ai_app_root / "deployment/docker/all_in_one_kdcube"

    sample_env_dir = docker_dir / "sample_env"
    if not sample_env_dir.exists():
        raise FileNotFoundError(f"Missing sample_env at {sample_env_dir}")

    ctx = PathsContext(
        lib_root=lib_root,
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=sample_env_dir,
        workdir=workdir,
        config_dir=config_dir,
        data_dir=data_dir,
    )

    ensure_env_files(config_dir, sample_env_dir)
    ensure_nginx_configs(config_dir, ai_app_root, docker_dir)
    ensure_local_dirs(data_dir, logs_dir)
    # Record installer metadata for future runs.
    try:
        meta = {
            "install_mode": install_mode or "upstream",
            "platform_ref": release_ref or "",
            "dockerhub_namespace": docker_namespace or "",
        }
        (config_dir / "install-meta.json").write_text(json.dumps(meta, indent=2))
    except Exception:
        pass
    console.print("Launching setup wizard...")
    env_paths, runtime_secrets = gather_configuration(
        console,
        ctx,
        release_descriptor_path=release_descriptor_path,
        release_descriptor=release_descriptor,
        bundles_descriptor_path=bundles_descriptor_path,
        bundles_descriptor=bundles_descriptor,
        bundles_secrets_descriptor=bundles_secrets,
        gateway_descriptor=gateway_descriptor,
        secrets_descriptor=secrets_descriptor,
        compose_mode=compose_mode,
        use_descriptor_bundles=use_descriptor_bundles,
        use_descriptor_frontend=use_descriptor_frontend,
        use_bundles_descriptor=use_bundles_descriptor,
        use_bundles_secrets=use_bundles_secrets,
    )
    env_main = load_env_file(config_dir / ".env")
    env_proc = load_env_file(config_dir / ".env.proc")

    console.print("\n[bold]Env files:[/bold]")
    for name, path in env_paths.items():
        console.print(f"  {name}: {path}")
    console.print("\n[dim]Review/edit these files before building images if needed.[/dim]")
    console.print("[dim]Build contexts (from .env):[/dim]")
    ui_ctx = env_main.entries.get("UI_BUILD_CONTEXT", (None, None))[1]
    proxy_ctx = env_main.entries.get("PROXY_BUILD_CONTEXT", (None, None))[1]
    console.print(f"  UI_BUILD_CONTEXT={ui_ctx}")
    console.print(f"  PROXY_BUILD_CONTEXT={proxy_ctx}")

    bundles_host = env_main.entries.get("HOST_BUNDLES_DESCRIPTOR_PATH", (None, None))[1]
    assembly_host = env_main.entries.get("HOST_BUNDLE_DESCRIPTOR_PATH", (None, None))[1]
    if bundles_host or assembly_host:
        console.print("\n[dim]Bundle descriptors (host -> container):[/dim]")
        if bundles_host and not is_placeholder(bundles_host) and bundles_host not in {"", "/dev/null"}:
            exists = "exists" if Path(bundles_host).exists() else "missing"
            console.print(f"  bundles.yaml: {bundles_host} ({exists}) -> /config/bundles.yaml")
        if assembly_host and not is_placeholder(assembly_host) and assembly_host not in {"", "/dev/null"}:
            exists = "exists" if Path(assembly_host).exists() else "missing"
            console.print(f"  assembly.yaml: {assembly_host} ({exists}) -> /config/assembly.yaml")

    console.print("\n[dim]Small coffee break:[/dim] ☕\n")

    if dry_run:
        console.print(f"[bold]Dry run:[/bold] no Docker actions will be executed. Workdir: {workdir}")
        console.print("\n[bold]Env files:[/bold]")
        for name, path in env_paths.items():
            console.print(f"  {name}: {path}")
        if parse_bool(os.getenv("KDCUBE_DRY_RUN_PRINT_ENV", "")) is True:
            for name, path in env_paths.items():
                try:
                    content = Path(path).read_text()
                except Exception as exc:
                    console.print(f"[red]Failed to read {name} ({path}): {exc}[/red]")
                    continue
                console.print(f"\n[bold]{name}[/bold] — {path}\n")
                console.print(content.rstrip())
        if runtime_secrets:
            console.print("\n[bold]Runtime secrets to inject:[/bold]")
            for key in sorted(runtime_secrets.keys()):
                console.print(f"  - {key}")
        return

    if install_mode == "release":
        console.print("[bold]Release mode[/bold]: pull prebuilt images from DockerHub.")
        if not docker_namespace:
            docker_namespace = "kdcube"
        tag = release_ref or ask(console, "Release version (platform.ref)")
        if ask_confirm(console, f"Pull platform images ({docker_namespace}, tag {tag})?", default=True):
            images = [
                "kdcube-chat-ingress",
                "kdcube-chat-proc",
                "kdcube-metrics",
                "kdcube-postgres-setup",
                "kdcube-web-ui",
                "kdcube-web-proxy",
                "kdcube-secrets",
                "proxylogin",
                "py-code-exec",
            ]
            try:
                for image in images:
                    subprocess.run(
                        ["docker", "pull", f"{docker_namespace}/{image}:{tag}"],
                        check=True,
                    )
                    subprocess.run(
                        ["docker", "tag", f"{docker_namespace}/{image}:{tag}", f"{image}:latest"],
                        check=True,
                    )
            except FileNotFoundError:
                console.print("[red]Docker not found. Please install Docker and rerun.[/red]")
            except subprocess.CalledProcessError:
                console.print("[red]Docker pull/tag failed. Check the output and retry.[/red]")
    else:
        if ask_confirm(
            console,
            "Build core platform images (includes py-code-exec)?",
            default=True,
        ):
            missing = missing_build_keys(env_main)
            if missing:
                console.print("[yellow]Skipping build — missing required build settings in .env:[/yellow]")
                for key in missing:
                    console.print(f"  - {key}")
                console.print("[yellow]Fill these in .env and rerun the build step.[/yellow]")
            else:
                try:
                    ui_image_override = env_main.entries.get("KDCUBE_UI_IMAGE", (None, None))[1]
                    build_services = [
                        "chat-ingress",
                        "chat-proc",
                        "metrics",
                        "web-proxy",
                        "postgres-setup",
                        "kdcube-secrets",
                    ]
                    if not (ui_image_override and not is_placeholder(ui_image_override)):
                        build_services.append("web-ui")
                    subprocess.run(
                        [
                            "docker",
                            "compose",
                            "--env-file",
                            str(config_dir / ".env"),
                            "build",
                            *build_services,
                        ],
                        cwd=ctx.docker_dir,
                        check=True,
                        env=compose_env(config_dir / ".env"),
                    )
                except FileNotFoundError:
                    console.print("[red]Docker not found. Please install Docker and rerun the build step.[/red]")
                except subprocess.CalledProcessError:
                    console.print("[red]Docker compose build failed. Check the output and retry.[/red]")
                try:
                    subprocess.run(
                        ["docker", "build", "-t", "py-code-exec:latest", "-f", "Dockerfile_Exec", "../../.."],
                        cwd=ctx.docker_dir,
                        check=True,
                    )
                except FileNotFoundError:
                    console.print("[red]Docker not found. Please install Docker and rerun the build step.[/red]")
                except subprocess.CalledProcessError:
                    console.print("[red]Docker build failed. Check the output and retry.[/red]")

    if ask_confirm(console, "Run docker compose now?", default=True):
        runtime_env = None
        try:
            maybe_remove_legacy_containers(console)
            token_overrides = generate_runtime_tokens()
            runtime_env = write_env_overlay(config_dir / ".env", token_overrides)
            runtime_secrets_provider = normalize_secrets_provider(
                env_proc.entries.get("SECRETS_PROVIDER", (None, None))[1],
                default="secrets-service",
            )
            use_secrets_service_runtime = runtime_secrets_provider == "secrets-service"
            base_cmd = [
                "docker",
                "compose",
                "--env-file",
                str(runtime_env),
            ]
            build_flag = ["--build"] if install_mode != "release" else []
            force_recreate_flag = ["--force-recreate"] if install_mode != "release" else []
            if runtime_secrets and use_secrets_service_runtime:
                # Start secrets service first so secrets are available before ingress/proc boot.
                subprocess.run(
                    [*base_cmd, "up", "-d", "--force-recreate", *build_flag, "kdcube-secrets"],
                    cwd=ctx.docker_dir,
                    check=True,
                    env=compose_env(runtime_env),
                )
                apply_runtime_secrets(console, ctx, runtime_secrets, runtime_env)
            elif runtime_secrets:
                console.print(
                    f"[yellow]Runtime secret injection is only supported for the secrets-service provider; "
                    f"provider is '{runtime_secrets_provider}', so CLI sidecar injection is skipped.[/yellow]"
                )

            services = list_compose_services(ctx, runtime_env)
            if services:
                console.print(f"[dim]Compose services:[/dim] {', '.join(sorted(services))}")
            if runtime_secrets and use_secrets_service_runtime and services:
                filtered = [svc for svc in services if "secret" not in svc.lower()]
                excluded = [svc for svc in services if svc not in filtered]
                services = filtered
                if excluded:
                    console.print(f"[yellow]Excluding services:[/yellow] {', '.join(sorted(excluded))}")
                if services:
                    console.print(f"[dim]Compose services (filtered):[/dim] {', '.join(sorted(services))}")
            no_deps_flag: List[str] = ["--no-deps"] if (runtime_secrets and use_secrets_service_runtime and services) else []
            no_recreate_flag: List[str] = []
            if runtime_secrets and use_secrets_service_runtime and not services:
                console.print(
                    "[yellow]Could not resolve compose services; running without --force-recreate to avoid restarting kdcube-secrets.[/yellow]"
                )
                force_recreate_flag = []
                no_recreate_flag = ["--no-recreate"]
            up_cmd = [*base_cmd, "up", "-d", *force_recreate_flag, *build_flag]
            if services:
                up_cmd = [*base_cmd, "up", "-d", *no_deps_flag, *force_recreate_flag, *build_flag, *services]
            elif no_recreate_flag:
                # Avoid rebuilding/recreating secrets when service list is unknown.
                up_cmd = [*base_cmd, "up", "-d", *no_recreate_flag]
            subprocess.run(
                up_cmd,
                cwd=ctx.docker_dir,
                check=True,
                env=compose_env(runtime_env),
            )
            console.print("[green]Docker compose started.[/green]")
            console.print("Open the UI:")
            ui_port = env_main.entries.get("KDCUBE_UI_PORT", (None, None))[1] or "80"
            if ui_port == "80":
                proxy_url = "http://localhost/chatbot/chat"
            else:
                proxy_url = f"http://localhost:{ui_port}/chatbot/chat"
            console.print(f"  [link={proxy_url}]{proxy_url}[/link]")
        except FileNotFoundError:
            console.print("[red]Docker not found. Please install Docker and rerun.[/red]")
        except subprocess.CalledProcessError:
            console.print("[red]Docker compose up failed. Check the output and retry.[/red]")
        finally:
            if runtime_env and runtime_env.exists():
                runtime_env.unlink(missing_ok=True)
    elif runtime_secrets:
        console.print(
            "[yellow]LLM secrets were provided but docker compose was not started. "
            "If your assembly uses the secrets-service provider, start compose and inject secrets via the sidecar.[/yellow]"
        )


def main() -> None:
    console = Console()
    console.print(
        Panel.fit(
            "KDCube Platform Setup\nQuick-start Docker Compose wizard",
            title="kdcube-cli",
        )
    )
    console.print("[dim]Tip: type 'q' at any prompt to exit.[/dim]\n")

    try:
        run_setup(console)
    except SystemExit as exc:
        console.print(f"[yellow]{exc}[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
