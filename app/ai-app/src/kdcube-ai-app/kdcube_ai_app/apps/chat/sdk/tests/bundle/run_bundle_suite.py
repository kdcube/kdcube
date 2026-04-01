from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

import pytest


def _shared_suite_root() -> Path:
    return Path(__file__).resolve().parent


def _resolve_bundle_dir(raw: str | None) -> Path:
    if not raw:
        raise SystemExit(
            "Bundle suite runner requires a bundle folder. "
            "Set BUNDLE_UNDER_TEST=/abs/path/to/bundle or pass --bundle-path=/abs/path/to/bundle."
        )
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Bundle under test does not exist: {path}")
    if not path.is_dir():
        raise SystemExit(f"Bundle under test is not a directory: {path}")
    if not (path / "entrypoint.py").exists():
        raise SystemExit(f"Bundle under test must contain entrypoint.py: {path}")
    return path


def _bundle_tests_root(bundle_dir: Path) -> Path:
    return bundle_dir / "tests"


def build_test_targets(
    bundle_dir: Path,
    *,
    include_shared: bool = True,
    include_bundle_local: bool = True,
) -> list[Path]:
    targets: list[Path] = []
    if include_shared:
        targets.append(_shared_suite_root())
    if include_bundle_local:
        bundle_tests = _bundle_tests_root(bundle_dir)
        if bundle_tests.is_dir():
            targets.append(bundle_tests)
    return targets


def _parse_args(argv: Sequence[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run the shared SDK bundle pytest suite and, when present, bundle-local tests "
            "from <bundle>/tests against the selected bundle folder."
        )
    )
    parser.add_argument(
        "--bundle-path",
        default=os.environ.get("BUNDLE_UNDER_TEST"),
        help="Absolute or cwd-relative path to the bundle under test.",
    )
    parser.add_argument(
        "--shared-only",
        action="store_true",
        help="Run only the shared SDK bundle suite.",
    )
    parser.add_argument(
        "--bundle-only",
        action="store_true",
        help="Run only bundle-local tests under <bundle>/tests.",
    )
    return parser.parse_known_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args, pytest_args = _parse_args(argv)
    if args.shared_only and args.bundle_only:
        raise SystemExit("--shared-only and --bundle-only cannot be used together.")

    bundle_dir = _resolve_bundle_dir(args.bundle_path)
    include_shared = not args.bundle_only
    include_bundle_local = not args.shared_only
    targets = build_test_targets(
        bundle_dir,
        include_shared=include_shared,
        include_bundle_local=include_bundle_local,
    )

    if not targets:
        raise SystemExit("No test targets selected.")
    if args.bundle_only and not _bundle_tests_root(bundle_dir).is_dir():
        raise SystemExit(f"Bundle-local tests directory not found: {_bundle_tests_root(bundle_dir)}")

    os.environ["BUNDLE_UNDER_TEST"] = str(bundle_dir)
    pytest_cmd = [str(target) for target in targets] + list(pytest_args)
    return int(pytest.main(pytest_cmd))


if __name__ == "__main__":
    raise SystemExit(main())
