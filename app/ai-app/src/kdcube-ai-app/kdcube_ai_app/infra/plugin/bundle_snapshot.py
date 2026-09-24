"""Content-addressed snapshots of a local-path bundle, so an activation can name what it loaded.

A ``local-path`` bundle is a bind mount of a work tree, and a reload imports
whatever that tree holds at the instant of import. The receipt cannot say
which version ran, and a clean tree can move from one commit to another
between the check and the import. Here the activation names a commit: the
bundle's subtree at that commit is exported from the repository's object
store into the managed bundles root, verified file by file against the
commit's trees, and marked with the commit and tree ids it came from. The
registry entry's ``path`` then points at that directory, which nothing edits.

What this does not decide: whether a host wants it. A bundle that declares
``activation.commit`` in its descriptor loads from the snapshot. One that
does not keeps the mounted path and only gains evidence about it in the
receipt (``head`` and ``dirty`` at the moment of the read).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from kdcube_ai_app.infra.plugin.git_bundle import (
    _bundle_lock,
    _run_git_capture_async,
    resolve_managed_bundles_root,
)
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

SNAPSHOT_MARKER = ".kdcube-snapshot.json"
SNAPSHOT_MARKER_SCHEMA = 1
SNAPSHOTS_DIR = "snapshots"
# Snapshots kept per bundle after a successful activation: the current one and
# the previous one, so a rollback has something to point at.
SNAPSHOTS_KEPT = 2


class BundleSnapshotError(ValueError):
    """A snapshot could not be made or does not match the commit it names."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details)


@dataclass(frozen=True)
class BundleSnapshot:
    bundle_id: str
    commit: str
    tree: str
    subdir: str
    repository: str
    path: pathlib.Path
    created_at: str

    def source(self) -> Dict[str, Any]:
        return {
            "mode": "snapshot",
            "commit": self.commit,
            "tree": self.tree,
            "subdir": self.subdir,
            "repository": self.repository,
            "path": str(self.path),
            "created_at": self.created_at,
        }

    def marker(self) -> Dict[str, Any]:
        return {"schema": SNAPSHOT_MARKER_SCHEMA, "bundle_id": self.bundle_id, **self.source()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _git(repository: pathlib.Path, *args: str) -> str:
    proc = await _run_git_capture_async(["git", "-C", str(repository), *args], check=True)
    return proc.stdout


async def repository_of(path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """The work tree holding ``path`` and the path's subdir inside it, or a refusal."""

    try:
        top = (await _git(path, "rev-parse", "--show-toplevel")).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, TimeoutError) as exc:
        raise BundleSnapshotError(
            "bundle_path_not_a_repository",
            f"{path} is not inside a git work tree, so no commit can name it.",
            path=str(path),
        ) from exc
    repository = pathlib.Path(top).resolve()
    try:
        subdir = str(pathlib.Path(path).resolve().relative_to(repository)).replace(os.sep, "/")
    except ValueError as exc:
        raise BundleSnapshotError(
            "bundle_path_outside_repository",
            f"{path} resolves outside its work tree {repository}.",
            path=str(path),
            repository=str(repository),
        ) from exc
    return repository, subdir


async def resolve_commit(repository: pathlib.Path, ref: str) -> str:
    """The full sha ``ref`` names in ``repository``. A branch or tag is pinned here."""

    clean = str(ref or "").strip()
    if not clean or clean.startswith("-"):
        raise BundleSnapshotError("bundle_commit_invalid", "A commit, tag or branch name is required.", ref=clean)
    try:
        return (await _git(repository, "rev-parse", "--verify", "--quiet", f"{clean}^{{commit}}")).strip()
    except subprocess.CalledProcessError as exc:
        raise BundleSnapshotError(
            "bundle_commit_unknown",
            f"{clean} does not name a commit in {repository}.",
            ref=clean,
            repository=str(repository),
        ) from exc


async def describe_mounted_source(path: pathlib.Path) -> Dict[str, Any]:
    """Evidence about a mounted path at this instant: head and dirty flag, never a gate.

    A reload that imports the mounted tree cannot say which version ran, but
    it can say what the tree looked like when it looked. Never raises: a path
    outside a repository is reported as such.
    """

    source: Dict[str, Any] = {"mode": "local-path", "path": str(path), "observed_at": _now()}
    try:
        repository, subdir = await repository_of(path)
    except BundleSnapshotError as exc:
        source["repository"] = ""
        source["error"] = exc.code
        return source
    source["repository"] = str(repository)
    source["subdir"] = subdir
    try:
        source["head"] = (await _git(repository, "rev-parse", "HEAD")).strip()
        status = await _git(repository, "status", "--porcelain", "--", subdir or ".")
        changed = [line[3:] for line in status.splitlines() if line.strip()]
        source["dirty"] = bool(changed)
        source["changed_paths"] = changed[:50]
    except (subprocess.CalledProcessError, TimeoutError) as exc:
        source["error"] = "git_unavailable"
        source["detail"] = str(exc)[:200]
    return source


def bundle_source_identity(
    source: Mapping[str, Any] | None,
    *,
    entry: Mapping[str, Any] | Any | None = None,
) -> Dict[str, Any]:
    """Return the stable fields that identify bundle code loaded by a process.

    Resolution receipts also contain observation timestamps, changed-path
    samples, and diagnostics. Those are useful evidence, but they are not a
    source identity and would make an unchanged widget appear different from
    its server. ``entry`` fills fields carried by the registry rather than the
    local-path activation receipt (notably git repo/ref/commit).
    """

    raw_source = dict(source or {})
    if entry is None:
        raw_entry: Dict[str, Any] = {}
    elif isinstance(entry, Mapping):
        raw_entry = dict(entry)
    else:
        dump = getattr(entry, "model_dump", None)
        raw_entry = dict(dump(mode="python", exclude_none=True)) if callable(dump) else {}

    mode = str(raw_source.get("mode") or "").strip()
    if not mode:
        mode = "git" if raw_entry.get("repo") else "local-path"
    identity: Dict[str, Any] = {"mode": mode}

    candidates = {
        "repository": raw_source.get("repository") or raw_entry.get("repo"),
        "subdir": raw_source.get("subdir") or raw_entry.get("subdir"),
        "ref": raw_source.get("ref") or raw_entry.get("ref"),
        "commit": raw_source.get("commit"),
        "tree": raw_source.get("tree"),
        "head": raw_source.get("head"),
        "git_commit": raw_source.get("git_commit") or raw_entry.get("git_commit"),
        "path": raw_source.get("path") or raw_entry.get("path"),
        "mounted_path": raw_source.get("mounted_path") or raw_entry.get("mounted_path"),
        "error": raw_source.get("error"),
    }
    for key, value in candidates.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        identity[key] = value
    if "dirty" in raw_source:
        identity["dirty"] = bool(raw_source.get("dirty"))
    return identity


async def _expected_blobs(repository: pathlib.Path, commit: str, subdir: str) -> Dict[str, str]:
    listing = await _git(repository, "ls-tree", "-r", "-z", commit, "--", subdir or ".")
    blobs: Dict[str, str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _tab, relative = entry.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        blobs[relative] = parts[2]
    return blobs


def _blob_id(path: pathlib.Path) -> str:
    data = path.read_bytes()
    digest = hashlib.sha1()
    digest.update(f"blob {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


def verify_snapshot_files(snapshot_root: pathlib.Path, subdir: str, expected: Dict[str, str]) -> list[str]:
    """Every file the commit holds under the subdir, present with the commit's content. Returns mismatches."""

    prefix = f"{subdir}/" if subdir else ""
    mismatched: list[str] = []
    for relative, blob in expected.items():
        inner = relative[len(prefix):] if prefix and relative.startswith(prefix) else relative
        target = snapshot_root / inner
        if target.is_symlink() or not target.is_file() or _blob_id(target) != blob:
            mismatched.append(relative)
    return mismatched


def read_snapshot(snapshot_root: pathlib.Path) -> Optional[BundleSnapshot]:
    marker_path = snapshot_root / SNAPSHOT_MARKER
    if not marker_path.is_file():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(marker, dict) or marker.get("schema") != SNAPSHOT_MARKER_SCHEMA or not marker.get("commit"):
        return None
    return BundleSnapshot(
        bundle_id=str(marker.get("bundle_id") or ""),
        commit=str(marker["commit"]),
        tree=str(marker.get("tree") or ""),
        subdir=str(marker.get("subdir") or ""),
        repository=str(marker.get("repository") or ""),
        path=snapshot_root,
        created_at=str(marker.get("created_at") or ""),
    )


def snapshots_root(bundle_id: str, managed_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    root = managed_root or resolve_managed_bundles_root()
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".", "@") else "-" for ch in bundle_id)
    return root / safe / SNAPSHOTS_DIR


async def _extract(repository: pathlib.Path, commit: str, subdir: str, destination: pathlib.Path) -> None:
    """``git archive`` of the subtree into ``destination``, refusing anything but files and directories."""

    with tempfile.TemporaryDirectory(prefix="kdcube-snapshot-") as scratch:
        archive_path = pathlib.Path(scratch) / "snapshot.tar"
        # Paths after the tree-ish filter the archive, and entries keep the subdir prefix.
        args = ["git", "-C", str(repository), "archive", "--format=tar", "-o", str(archive_path), commit]
        if subdir:
            args.append(subdir)
        proc = await _run_git_capture_async(args, check=False)
        if proc.returncode != 0:
            raise BundleSnapshotError(
                "bundle_snapshot_export_failed",
                f"git archive failed for {commit[:12]}: {proc.stderr.strip()[:300]}",
                commit=commit,
                subdir=subdir,
            )
        prefix = f"{subdir}/" if subdir else ""
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive:
                if not (member.isfile() or member.isdir()):
                    raise BundleSnapshotError(
                        "bundle_snapshot_member_refused",
                        f"{member.name} is not a regular file or directory.",
                        member=member.name,
                    )
                name = pathlib.PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise BundleSnapshotError(
                        "bundle_snapshot_member_refused",
                        f"{member.name} escapes the snapshot directory.",
                        member=member.name,
                    )
                inner = member.name[len(prefix):] if prefix and member.name.startswith(prefix) else member.name
                if prefix and not member.name.startswith(prefix):
                    continue
                if not inner or inner == ".":
                    continue
                target = destination / inner
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                with extracted, open(target, "wb") as handle:
                    shutil.copyfileobj(extracted, handle)


async def materialize_snapshot(
    *,
    bundle_id: str,
    mounted_path: pathlib.Path,
    commit: str,
    managed_root: Optional[pathlib.Path] = None,
    logger: Optional[AgentLogger] = None,
) -> BundleSnapshot:
    """Export the bundle subtree at ``commit`` into the managed root, verified, and return it.

    ``commit`` may be any ref. It is pinned to a sha first and the sha is what
    the snapshot is named by. An existing snapshot for the same sha is reused
    when its files still match the commit's trees, and replaced when they do
    not, whatever its marker says.
    """

    log = logger or AgentLogger("bundle.snapshot")
    repository, subdir = await repository_of(pathlib.Path(mounted_path))
    sha = await resolve_commit(repository, commit)
    try:
        tree = (await _git(repository, "rev-parse", "--verify", "--quiet", f"{sha}:{subdir}" if subdir else f"{sha}^{{tree}}")).strip()
    except subprocess.CalledProcessError as exc:
        raise BundleSnapshotError(
            "bundle_subdir_missing_at_commit",
            f"{subdir or '.'} does not exist at {sha[:12]}.",
            commit=sha,
            subdir=subdir,
        ) from exc
    expected = await _expected_blobs(repository, sha, subdir)
    root = snapshots_root(bundle_id, managed_root)
    root.mkdir(parents=True, exist_ok=True)
    final = root / sha

    with _bundle_lock(bundle_id=bundle_id, git_ref=f"snapshot-{sha}", bundles_root=root.parent):
        existing = read_snapshot(final)
        if existing is not None and existing.tree == tree and not verify_snapshot_files(final, subdir, expected):
            log.log(f"[bundle.snapshot] reuse bundle={bundle_id} commit={sha} path={final}", level="INFO")
            return existing
        if final.exists():
            shutil.rmtree(final)
        stage = root / f".{sha}.tmp-{os.getpid()}"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            await _extract(repository, sha, subdir, stage)
            mismatched = verify_snapshot_files(stage, subdir, expected)
            if mismatched:
                raise BundleSnapshotError(
                    "bundle_snapshot_mismatch",
                    f"{len(mismatched)} exported file(s) do not match {sha[:12]}.",
                    commit=sha,
                    paths=mismatched[:20],
                )
            snapshot = BundleSnapshot(
                bundle_id=bundle_id,
                commit=sha,
                tree=tree,
                subdir=subdir,
                repository=str(repository),
                path=final,
                created_at=_now(),
            )
            (stage / SNAPSHOT_MARKER).write_text(json.dumps(snapshot.marker(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.rename(stage, final)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    log.log(
        f"[bundle.snapshot] materialized bundle={bundle_id} commit={sha} tree={tree} subdir={subdir or '.'} path={final}",
        level="INFO",
    )
    return snapshot


def prune_snapshots(bundle_id: str, keep: tuple[str, ...], managed_root: Optional[pathlib.Path] = None) -> list[str]:
    """Remove snapshot directories not named in ``keep`` and any abandoned stage. Returns what went."""

    root = snapshots_root(bundle_id, managed_root)
    if not root.is_dir():
        return []
    removed: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name in keep or not entry.is_dir() or entry.is_symlink():
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed


def activation_block(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The entry's ``activation`` block as a plain dict (a model is accepted too)."""

    raw = entry.get("activation")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    dump = getattr(raw, "model_dump", None)
    return dict(dump()) if callable(dump) else {}


async def resolve_activation_entry(entry: Dict[str, Any], *, logger: Optional[AgentLogger] = None) -> Dict[str, Any]:
    """Resolve a registry entry's source: a snapshot when it declares ``activation.commit``, evidence otherwise.

    Returns the entry with ``path`` rewritten to the snapshot when one applies,
    ``mounted_path`` kept, and a ``source`` block either way. Raises
    ``BundleSnapshotError`` when a declared commit cannot be materialized: an
    entry that asked for a commit must not silently load the mutable tree.
    """

    out = dict(entry)
    mounted = str(entry.get("mounted_path") or entry.get("path") or "").strip()
    commit = str(activation_block(entry).get("commit") or "").strip()
    if not mounted or entry.get("repo"):
        return out
    if commit:
        snapshot = await materialize_snapshot(
            bundle_id=str(entry.get("id") or ""),
            mounted_path=pathlib.Path(mounted),
            commit=commit,
            logger=logger,
        )
        out["mounted_path"] = mounted
        out["path"] = str(snapshot.path)
        source = snapshot.source()
        source["ref"] = commit
        source["mounted_path"] = mounted
        source["origin"] = "descriptor"
        source["durable"] = True
        out["source"] = source
        return out
    out["source"] = await describe_mounted_source(pathlib.Path(mounted))
    return out


@dataclass(frozen=True)
class PinnedCommit:
    repository: pathlib.Path
    subdir: str
    commit: str


async def pin_commit(mounted_path: pathlib.Path, ref: str) -> PinnedCommit:
    """Resolve ``ref`` to a sha in the repository holding ``mounted_path``, without exporting anything."""

    repository, subdir = await repository_of(pathlib.Path(mounted_path))
    return PinnedCommit(repository=repository, subdir=subdir, commit=await resolve_commit(repository, ref))


async def prepare_activation(
    entry: Dict[str, Any],
    *,
    commit: Optional[str],
    expected_commit: Optional[str],
    managed_root: Optional[pathlib.Path] = None,
    logger: Optional[AgentLogger] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """Decide and prepare what an activation of ``entry`` loads, before anything is evicted.

    ``commit`` is the request's. When absent the descriptor's
    ``activation.commit`` applies. The ref is pinned in the mounted
    repository and, when ``expected_commit`` is given, the pin must equal it
    (``bundle_activation_commit_mismatch``): this is the fence against a
    branch that moved between the caller's look and this call. With a commit,
    the snapshot is materialized here so a refusal or an export failure
    leaves the running bundle untouched. Without one, an entry declaring
    ``activation.require_commit`` is refused
    (``bundle_activation_commit_required``), and otherwise the mounted tree
    is described as evidence.

    Returns ``(commit_sha_or_None, source)``. ``source`` is what the receipt
    reports and what the loader will import for this activation.
    """

    block = activation_block(entry)
    mounted = str(entry.get("mounted_path") or entry.get("path") or "").strip()
    bundle_id = str(entry.get("id") or "")
    requested = str(commit or "").strip()
    declared = str(block.get("commit") or "").strip()
    effective = requested or declared
    origin = "request" if requested else ("descriptor" if declared else "")
    expected = str(expected_commit or "").strip()

    if entry.get("repo"):
        if requested or expected:
            raise BundleSnapshotError(
                "bundle_activation_not_local_path",
                f"Bundle '{bundle_id}' is git-backed (repo/ref). Its version is its descriptor ref, not an activation commit.",
                bundle_id=bundle_id,
            )
        return None, {"mode": "git", "path": mounted, "ref": entry.get("ref"), "git_commit": entry.get("git_commit")}

    mounted_path = pathlib.Path(mounted) if mounted else None
    if mounted_path is None or not mounted_path.is_dir():
        raise BundleSnapshotError(
            "bundle_path_unreachable",
            f"Bundle '{bundle_id}' mounted path is not a directory in this runtime: "
            f"{mounted or '<unset>'}. Nothing was evicted.",
            bundle_id=bundle_id,
            path=mounted,
        )

    if not effective:
        if bool(block.get("require_commit")):
            raise BundleSnapshotError(
                "bundle_activation_commit_required",
                f"Bundle '{bundle_id}' declares activation.require_commit in its descriptor entry: "
                "name a commit for this activation, or remove the flag from that entry.",
                bundle_id=bundle_id,
            )
        if expected:
            raise BundleSnapshotError(
                "bundle_activation_expect_without_commit",
                "expected_commit fences a commit. Name the commit to activate as well.",
                bundle_id=bundle_id,
            )
        source = await describe_mounted_source(mounted_path)
        source["origin"] = ""
        return None, source

    pinned = await pin_commit(mounted_path, effective)
    if expected and pinned.commit != expected:
        raise BundleSnapshotError(
            "bundle_activation_commit_mismatch",
            f"{effective} resolves to {pinned.commit[:12]} here, not the expected {expected[:12]}: "
            "the ref moved after it was read. Nothing was evicted.",
            bundle_id=bundle_id,
            ref=effective,
            resolved=pinned.commit,
            expected=expected,
        )
    snapshot = await materialize_snapshot(
        bundle_id=bundle_id,
        mounted_path=mounted_path,
        commit=pinned.commit,
        managed_root=managed_root,
        logger=logger,
    )
    source = snapshot.source()
    source["origin"] = origin
    source["ref"] = effective
    source["mounted_path"] = mounted
    # A request commit lives in this process's registry until the next
    # restart. The descriptor is the operator's and is not written here.
    source["durable"] = origin == "descriptor"
    return pinned.commit, source
