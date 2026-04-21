#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


CURRENT_RELEASE = "2026.4.12.318"
DEFAULT_TENANT = "demo-tenant"
DEFAULT_PROJECT = "demo-project"
DEFAULT_WORKDIR = str(Path.home() / ".kdcube" / "kdcube-runtime")
DEFAULT_PLUGIN_DATA = Path.home() / ".kdcube" / "builder-plugin"
DEFAULT_KDCUBE_REPO = "https://github.com/kdcube/kdcube-ai-app.git"


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _templates_root() -> Path:
    return _plugin_root() / "templates"


def _plugin_data_root() -> Path:
    raw = os.environ.get("CLAUDE_PLUGIN_DATA")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_PLUGIN_DATA.resolve()


def _configured_workdir() -> Path:
    raw = (
        os.environ.get("CLAUDE_PLUGIN_OPTION_KDCUBE_WORKDIR")
        or os.environ.get("KDCUBE_WORKDIR")
        or DEFAULT_WORKDIR
    )
    return Path(raw).expanduser().resolve()


def _configured_repo_root() -> Path | None:
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_KDCUBE_REPO_ROOT") or os.environ.get("KDCUBE_REPO_ROOT")
    if raw:
        candidate = Path(raw).expanduser().resolve()
        if candidate.exists():
            return candidate
    return None


def _kdcube_cmd() -> str:
    return os.environ.get("KDCUBE_CMD", "kdcube")


def _yaml_scalar(value: str) -> str:
    return json.dumps(value)


def _profile_root(profile: str) -> Path:
    return _plugin_data_root() / "profiles" / profile


def _descriptors_dir(profile: str) -> Path:
    return _profile_root(profile) / "descriptors"


def _managed_bundles_dir(profile: str) -> Path:
    return _profile_root(profile) / "managed-bundles"


def _read_template(name: str) -> str:
    return (_templates_root() / name).read_text()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _ensure_cmd_available(cmd: str) -> None:
    if shutil.which(cmd) is None:
        raise SystemExit(
            f"Required command not found in PATH: {cmd}\n"
            f"Run `python3 {Path(__file__).resolve()} install` to install kdcube-cli."
        )


def _resolve_bundle_mapping(bundle_path: Path, host_bundles_path: Path) -> PurePosixPath:
    try:
        relative = bundle_path.relative_to(host_bundles_path)
    except ValueError as exc:
        raise SystemExit(
            f"Bundle path {bundle_path} is not under host bundles root {host_bundles_path}"
        ) from exc
    if not relative.parts:
        raise SystemExit("Bundle path must not be the same directory as host bundles root")
    return PurePosixPath("/bundles", *relative.parts)


def _docker_container_name(match: str) -> str:
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--filter", f"name={match}", "--format", "{{.Names}}"],
            text=True,
            stderr=subprocess.PIPE
        ).strip()
    except FileNotFoundError:
        raise SystemExit("`docker` CLI not found in PATH.")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip() or "is the Docker daemon running?"
        raise SystemExit(f"`docker ps` failed: {detail}")

    names = [n for n in out.splitlines() if n]

    if not names:
        raise SystemExit(f"No running container matching '{match}'. Start the stack first.")
    if len(names) > 1:
        raise SystemExit(f"Multiple containers match '{match}': {names}. Be more specific.")
    return names[0]


def cmd_bootstrap(args: argparse.Namespace) -> int:
    bundle_path = Path(args.bundle_path).expanduser().resolve()
    if not bundle_path.exists():
        raise SystemExit(f"Bundle path does not exist: {bundle_path}")
    if not bundle_path.is_dir():
        raise SystemExit(f"Bundle path is not a directory: {bundle_path}")

    host_bundles_path = (
        Path(args.host_bundles_path).expanduser().resolve()
        if args.host_bundles_path
        else bundle_path.parent.resolve()
    )
    if not host_bundles_path.exists():
        raise SystemExit(f"Host bundles path does not exist: {host_bundles_path}")
    if not host_bundles_path.is_dir():
        raise SystemExit(f"Host bundles path is not a directory: {host_bundles_path}")

    profile = args.profile
    descriptors_dir = _descriptors_dir(profile)
    managed_bundles_dir = (
        Path(args.host_managed_bundles_path).expanduser().resolve()
        if args.host_managed_bundles_path
        else _managed_bundles_dir(profile)
    )
    managed_bundles_dir.mkdir(parents=True, exist_ok=True)
    descriptors_dir.mkdir(parents=True, exist_ok=True)

    container_bundle_path = _resolve_bundle_mapping(bundle_path, host_bundles_path)
    bundle_name = args.bundle_name or args.bundle_id
    singleton_line = "\n      singleton: true" if args.singleton else ""

    assembly = _read_template("assembly.yaml")
    assembly = assembly.replace('"demo-tenant"', _yaml_scalar(args.tenant), 1)
    assembly = assembly.replace('"demo-project"', _yaml_scalar(args.project), 1)
    assembly = assembly.replace('host_bundles_path: ""', f"host_bundles_path: {_yaml_scalar(str(host_bundles_path))}")
    assembly = assembly.replace(
        'host_managed_bundles_path: ""',
        f"host_managed_bundles_path: {_yaml_scalar(str(managed_bundles_dir))}",
    )
    assembly = assembly.replace(DEFAULT_KDCUBE_REPO, args.platform_repo)
    assembly = assembly.replace(CURRENT_RELEASE, args.platform_ref)

    bundles = f"""bundles:
  version: "1"
  default_bundle_id: {_yaml_scalar(args.bundle_id)}
  items:
    - id: {_yaml_scalar(args.bundle_id)}
      name: {_yaml_scalar(bundle_name)}
      path: {_yaml_scalar(str(container_bundle_path))}
      module: {_yaml_scalar(args.module)}{singleton_line}
"""

    bundles_secrets = f"""bundles:
  version: "1"
  items:
    - id: {_yaml_scalar(args.bundle_id)}
      secrets: {{}}
"""

    gateway = _read_template("gateway.yaml")
    gateway = gateway.replace('"demo-tenant"', _yaml_scalar(args.tenant), 1)
    gateway = gateway.replace('"demo-project"', _yaml_scalar(args.project), 1)

    secrets = _read_template("secrets.yaml")

    _write(descriptors_dir / "assembly.yaml", assembly)
    _write(descriptors_dir / "bundles.yaml", bundles)
    _write(descriptors_dir / "bundles.secrets.yaml", bundles_secrets)
    _write(descriptors_dir / "gateway.yaml", gateway)
    _write(descriptors_dir / "secrets.yaml", secrets)

    print(f"Generated local descriptor profile: {profile}")
    print(f"Descriptors: {descriptors_dir}")
    print(f"Host bundles root: {host_bundles_path}")
    print(f"Container bundle path: {container_bundle_path}")
    print("Next commands:")
    print(f"  python3 {Path(__file__).resolve()} start upstream --profile {profile}")
    print(f"  python3 {Path(__file__).resolve()} start latest-image --profile {profile}")
    print(f"  python3 {Path(__file__).resolve()} reload {args.bundle_id}")
    return 0


def _ensure_descriptors_exist(profile: str) -> Path:
    descriptors_dir = _descriptors_dir(profile)
    required = [
        descriptors_dir / "assembly.yaml",
        descriptors_dir / "bundles.yaml",
        descriptors_dir / "gateway.yaml",
        descriptors_dir / "secrets.yaml",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            "Descriptor profile is not initialized. Run `bootstrap-local` first.\n"
            + "\n".join(missing)
        )
    return descriptors_dir


def _expand_descriptors(descriptors_dir: Path) -> Path:
    """Copy descriptors to a temp dir with ~ and $HOME expanded in all yaml values."""
    home = str(Path.home())
    tilde_re = re.compile(r'(:\s*["\']?)~/')

    tmp = Path(tempfile.mkdtemp(prefix="kdcube-descriptors-"))
    for src in descriptors_dir.iterdir():
        if not src.is_file() or src.suffix not in (".yaml", ".yml"):
            continue
        text = src.read_text()
        # expand ~/  →  /home/user/
        text = tilde_re.sub(lambda m: m.group(1) + home + "/", text)
        # expand $HOME/
        text = text.replace("$HOME/", home + "/")
        (tmp / src.name).write_text(text)
    return tmp


def cmd_start(args: argparse.Namespace) -> int:
    _ensure_cmd_available(_kdcube_cmd())
    descriptors_dir = _expand_descriptors(_ensure_descriptors_exist(args.profile))
    cmd = [_kdcube_cmd(), "--descriptors-location", str(descriptors_dir)]

    if args.mode == "upstream":
        cmd.extend(["--build", "--upstream"])
    elif args.mode == "latest":
        cmd.extend(["--build", "--latest"])
    elif args.mode == "latest-image":
        cmd.append("--latest")
    elif args.mode == "release":
        cmd.extend(["--build", "--release", args.release_ref])
    elif args.mode == "release-image":
        cmd.extend(["--release", args.release_ref])
    else:
        raise SystemExit(f"Unsupported start mode: {args.mode}")

    cmd.extend(args.extra_args)
    return subprocess.run(cmd).returncode


def cmd_reload(args: argparse.Namespace) -> int:
    _ensure_cmd_available(_kdcube_cmd())
    cmd = [
        _kdcube_cmd(),
        "--workdir",
        str(_configured_workdir()),
        "--bundle-reload",
        args.bundle_id,
    ]
    cmd.extend(args.extra_args)
    return subprocess.run(cmd).returncode


def cmd_stop(args: argparse.Namespace) -> int:
    _ensure_cmd_available(_kdcube_cmd())
    cmd = [_kdcube_cmd(), "--workdir", str(_configured_workdir()), "--stop"]
    cmd.extend(args.extra_args)
    return subprocess.run(cmd).returncode


def cmd_bundle_tests(args: argparse.Namespace) -> int:
    repo_root = _configured_repo_root()
    if repo_root is None:
        raise SystemExit(
            "Bundle tests require a local kdcube-ai-app checkout. "
            "Set plugin option `kdcube_repo_root` or environment variable `KDCUBE_REPO_ROOT`."
        )
    bundle_path = Path(args.bundle_path).expanduser().resolve()
    if not bundle_path.exists():
        raise SystemExit(f"Bundle path does not exist: {bundle_path}")
    pythonpath_root = repo_root / "app" / "ai-app" / "src" / "kdcube-ai-app"
    if not pythonpath_root.exists():
        raise SystemExit(f"Invalid kdcube repo root, missing {pythonpath_root}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(pythonpath_root)
    cmd = [
        sys.executable,
        "-m",
        "kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite",
        "--bundle-path",
        str(bundle_path),
    ]
    cmd.extend(args.extra_args)
    return subprocess.run(cmd, env=env).returncode


def cmd_verify_reload(args: argparse.Namespace) -> int:
    container = _docker_container_name("chat-proc")

    payload = json.dumps({"bundle_id": args.bundle_id})
    script = (
        "import json,sys,urllib.request;"
        f"data={payload!r}.encode('utf-8');"
        "req=urllib.request.Request("
        "'http://127.0.0.1:8020/internal/bundles/reset-env',"
        "data=data,"
        "headers={'content-type':'application/json'},"
        "method='POST');"
        "resp=urllib.request.urlopen(req);"
        "sys.stdout.write(resp.read().decode('utf-8'))"
    )
    r = subprocess.run(
        ["docker", "exec", container, "python", "-c", script],
        capture_output=True, text=True,
    )

    if r.returncode != 0:
        raise SystemExit(f"docker exec failed: {r.stderr.strip() or r.stdout.strip()}")

    try:
        resp = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"Non-JSON response: {r.stdout!r}")

    if resp.get("status") != "ok":
        raise SystemExit(f"reset-env returned non-ok: {resp}")
    if resp.get("bundle_id") != args.bundle_id:
        raise SystemExit(
            f"bundle_id mismatch: expected {args.bundle_id}, got {resp.get('bundle_id')!r}"
        )
    if resp.get("eviction") is None:
        print(f"WARN: eviction was null — bundle may not have been in proc cache.")

    print(f"Verify OK.")
    print(f"  bundle_id: {args.bundle_id}")
    print(f"  count:     {resp.get('count')}")
    print(f"  eviction:  {resp.get('eviction')}")

    return 0


def cmd_use_descriptors(args: argparse.Namespace) -> int:
    src = Path(args.descriptors_dir).expanduser().resolve()

    required = ["assembly.yaml", "bundles.yaml", "gateway.yaml", "secrets.yaml"]
    missing = [n for n in required if not (src / n).exists()]

    if not src.exists():
        raise SystemExit(f"Descriptors dir does not exist: {src}")
    if not src.is_dir():
        raise SystemExit(f"Not a directory: {src}")
    if missing:
        raise SystemExit(f"Missing in {src}: {', '.join(missing)}")

    profile = args.profile
    profile_dir = _profile_root(profile)
    profile_dir.mkdir(parents=True, exist_ok=True)
    link = profile_dir / "descriptors"

    if link.is_symlink() or link.exists():
        if link.is_symlink():
            link.unlink()
        else:
            raise SystemExit(
                f"{link} is a real directory, not a symlink. "
                f"Refusing to overwrite. Remove it manually if you want to switch."
            )

    link.symlink_to(src, target_is_directory=True)

    print(f"Linked profile '{profile}' -> {src}")
    print(f"Descriptors link:   {link}")
    print("Next commands:")
    print(f"  python3 {Path(__file__).resolve()} start latest-image --profile {profile}")
    print(f"  python3 {Path(__file__).resolve()} status --profile {profile}")

    return 0


def cmd_install(args: argparse.Namespace) -> int:
    kdcube = _kdcube_cmd()
    if shutil.which(kdcube) is not None:
        print(f"kdcube-cli is already installed: {shutil.which(kdcube)}")
        return 0

    if shutil.which("pipx") is None:
        raise SystemExit("pipx not found in PATH. Install it first: https://pipx.pypa.io/stable/installation/")
    installer = ["pipx", "install", "kdcube-cli"]

    print(f"Installing kdcube-cli via: {' '.join(installer)}")
    r = subprocess.run(installer)
    if r.returncode != 0:
        raise SystemExit("Installation failed. Check the output above.")

    if shutil.which(kdcube) is None:
        raise SystemExit(
            "Installation succeeded but `kdcube` is still not in PATH. "
            "You may need to add the install location to PATH and restart your shell."
        )

    print(f"kdcube-cli installed: {shutil.which(kdcube)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    profile = args.profile

    # kdcube CLI
    kdcube = _kdcube_cmd()
    kdcube_available = shutil.which(kdcube) is not None
    print(f"kdcube CLI:     {'ok (' + kdcube + ')' if kdcube_available else 'NOT FOUND (' + kdcube + ')'}")

    # descriptor profile
    link = _profile_root(profile) / "descriptors"
    if link.is_symlink():
        target = link.resolve()
        exists = target.exists()
        print(f"Profile:        {profile}")
        print(f"Descriptors:    {link} -> {target}{'' if exists else '  (TARGET MISSING)'}")
    elif link.exists():
        print(f"Profile:        {profile}")
        print(f"Descriptors:    {link}  (real dir, not a symlink)")
    else:
        print(f"Profile:        {profile}  (no descriptors linked — run use-descriptors or bootstrap first)")

    # workdir
    workdir = _configured_workdir()
    print(f"Workdir:        {workdir}{'' if workdir.exists() else '  (not found)'}")

    # docker containers
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--filter", "name=kdcube", "--format", "{{.Names}}\t{{.Status}}"],
            text=True, stderr=subprocess.PIPE,
        ).strip()
        containers = [line for line in out.splitlines() if line]
        if containers:
            print(f"Containers ({len(containers)}):")
            for c in containers:
                print(f"  {c}")
        else:
            print("Containers:     none running (filter: name=kdcube)")
    except FileNotFoundError:
        print("Containers:     docker CLI not found")
    except subprocess.CalledProcessError as e:
        print(f"Containers:     docker ps failed — {(e.stderr or '').strip() or 'is daemon running?'}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KDCube Claude plugin local runtime helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="Generate a clean local descriptor set for one bundle.")
    bootstrap.add_argument("bundle_id")
    bootstrap.add_argument("bundle_path")
    bootstrap.add_argument("--bundle-name")
    bootstrap.add_argument("--module", default="entrypoint")
    bootstrap.add_argument("--tenant", default=DEFAULT_TENANT)
    bootstrap.add_argument("--project", default=DEFAULT_PROJECT)
    bootstrap.add_argument("--profile", default="default")
    bootstrap.add_argument("--host-bundles-path")
    bootstrap.add_argument("--host-managed-bundles-path")
    bootstrap.add_argument("--platform-ref", default=CURRENT_RELEASE)
    bootstrap.add_argument("--platform-repo", default=DEFAULT_KDCUBE_REPO)
    bootstrap.add_argument("--singleton", action="store_true")
    bootstrap.set_defaults(func=cmd_bootstrap)

    start = sub.add_parser("start", help="Start local KDCube from a generated descriptor profile.")
    start.add_argument(
        "mode",
        choices=["upstream", "latest", "latest-image", "release", "release-image"],
    )
    start.add_argument("release_ref", nargs="?")
    start.add_argument("--profile", default="default")
    start.add_argument("extra_args", nargs=argparse.REMAINDER)
    start.set_defaults(func=cmd_start)

    reload_cmd = sub.add_parser("reload", help="Reload one bundle in an existing local runtime.")
    reload_cmd.add_argument("bundle_id")
    reload_cmd.add_argument("extra_args", nargs=argparse.REMAINDER)
    reload_cmd.set_defaults(func=cmd_reload)

    stop = sub.add_parser("stop", help="Stop the local KDCube runtime.")
    stop.add_argument("extra_args", nargs=argparse.REMAINDER)
    stop.set_defaults(func=cmd_stop)

    tests = sub.add_parser("bundle-tests", help="Run the shared bundle suite.")
    tests.add_argument("bundle_path")
    tests.add_argument("extra_args", nargs=argparse.REMAINDER)
    tests.set_defaults(func=cmd_bundle_tests)

    use_d = sub.add_parser("use-descriptors", help="Point a profile at an existing descriptor directory.")
    use_d.add_argument("descriptors_dir")
    use_d.add_argument("--profile", default="default")
    use_d.set_defaults(func=cmd_use_descriptors)

    vr = sub.add_parser("verify-reload",
                        help="Verify that a bundle's cache was evicted and registry accepts it.")
    vr.add_argument("bundle_id")
    vr.set_defaults(func=cmd_verify_reload)

    status = sub.add_parser("status", help="Show runtime status: CLI, descriptor profile, workdir, containers.")
    status.add_argument("--profile", default="default")
    status.set_defaults(func=cmd_status)

    install = sub.add_parser("install", help="Install kdcube-cli via pipx or pip if not already present.")
    install.set_defaults(func=cmd_install)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {"start"} and args.mode in {"release", "release-image"} and not args.release_ref:
        parser.error(f"{args.mode} requires <release_ref>")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
