# SPDX-License-Identifier: MIT
"""Resolve local Compose file selectors without invoking Docker."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping, Sequence

import yaml


class ComposeSourceError(ValueError):
    """A refresh cannot establish its platform base and runtime overlays."""

    def __init__(self, file: object, reason: str) -> None:
        super().__init__(f"error[compose_source_mismatch]: {file}: {reason}")


_DEFAULT_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
_DEFAULT_OVERRIDES = (
    "compose.override.yml", "compose.override.yaml",
    "docker-compose.override.yml", "docker-compose.override.yaml",
)
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z0-9_.\[\]-]+)\s*[:=]\s*(.*)$")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _options(command: Sequence[str], long: str, short: str = "") -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(command):
        token = command[index]
        if token == long or (short and token == short):
            index += 1
            if index == len(command):
                raise ComposeSourceError(long, "missing option value")
            values.append(command[index])
        elif token.startswith(long + "="):
            values.append(token[len(long) + 1:])
        elif short and token.startswith(short) and token != short:
            values.append(token[len(short):])
        index += 1
    return values


def _interpolate(value: str, lookup, file: Path) -> str:
    """Compose's dollar substitutions; no shell execution or value diagnostics."""
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "$":
            result.append(value[index])
            index += 1
            continue
        index += 1
        if index < len(value) and value[index] == "$":
            result.append("$")
            index += 1
            continue
        if index < len(value) and value[index] == "{":
            start = index + 1
            end, depth = start, 1
            while end < len(value) and depth:
                depth += (value[end] == "{") - (value[end] == "}")
                end += 1
            if depth:
                raise ComposeSourceError(file, "unclosed selector interpolation")
            expression = value[start:end - 1]
            match = _VARIABLE.match(expression)
            if match is None:
                raise ComposeSourceError(file, "invalid selector interpolation")
            name = match.group()
            suffix = expression[len(name):]
            current = lookup(name)
            if not suffix:
                replacement = current or ""
            else:
                operator = next((op for op in (":-", ":+", ":?", "-", "+", "?") if suffix.startswith(op)), None)
                if operator is None:
                    raise ComposeSourceError(file, "unsupported selector interpolation")
                present = current is not None and (current != "" or not operator.startswith(":"))
                argument = suffix[len(operator):]
                if operator.endswith("?") and not present:
                    raise ComposeSourceError(file, "required selector variable is unset")
                if operator.endswith("-"):
                    replacement = current if present else _interpolate(argument, lookup, file)
                elif operator.endswith("+"):
                    replacement = _interpolate(argument, lookup, file) if present else ""
                else:
                    replacement = current or ""
            result.append(replacement)
            index = end
            continue
        match = _VARIABLE.match(value, index)
        if match is None:
            raise ComposeSourceError(file, "invalid selector interpolation")
        result.append(lookup(match.group()) or "")
        index = match.end()
    return "".join(result)


def _quoted_end(value: str) -> int | None:
    escaped = False
    for index in range(1, len(value)):
        char = value[index]
        if char == value[0] and not escaped:
            return index
        escaped = char == "\\" and not escaped
    return None


def _env_selectors(files: Sequence[Path], process: Mapping[str, str]) -> dict[str, str]:
    # Evaluate only the two selectors and their dependencies. Unrelated values
    # (including secrets) are never expanded or included in a diagnostic.
    entries: list[tuple[str, str, Path]] = []
    for file in files:
        try:
            # Compose's unquoted extractor ends a value at LF only. Preserve
            # bare CR and Unicode separators inside values: splitlines() or
            # universal-newline conversion could invent a later selector.
            with file.open(encoding="utf-8-sig", newline="") as stream:
                lines = stream.read().split("\n")
        except OSError as exc:
            raise ComposeSourceError(file, "cannot read selected env file") from exc
        remaining = iter(lines)
        for line in remaining:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = _ASSIGNMENT.match(line)
            if match is None:
                # A bare name inherits the process value (already first in
                # lookup). Refuse other syntax rather than guess a selector.
                if re.fullmatch(r"\s*(?:export\s+)?[A-Za-z0-9_.\[\]-]+\s*", line):
                    continue
                raise ComposeSourceError(file, "unsupported env assignment syntax")
            raw = match[2]
            if raw.startswith(("'", '"')):
                while _quoted_end(raw) is None:
                    following = next(remaining, None)
                    if following is None:
                        raise ComposeSourceError(file, "unclosed quoted env value")
                    raw += "\n" + following
                trailing = raw[_quoted_end(raw) + 1:].strip()
                if trailing and not trailing.startswith("#"):
                    raise ComposeSourceError(file, "unsupported trailing env assignment syntax")
            entries.append((match[1], raw, file))

    def lookup(key: str, before: int = len(entries)) -> str | None:
        if key in process:
            return process[key]
        for index in range(before - 1, -1, -1):
            name, raw, file = entries[index]
            if name != key:
                continue
            quoted = raw.startswith(("'", '"'))
            if quoted:
                quote = raw[0]
                closing = _quoted_end(raw)
                if closing is None:
                    raise ComposeSourceError(file, "unsupported quoted selector value")
                trailing = raw[closing + 1:].strip()
                if trailing and not trailing.startswith("#"):
                    raise ComposeSourceError(file, "unsupported quoted selector value")
                value = raw[1:closing].replace("\\" + quote, quote)
                if quote == "'":
                    return value
                escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "$": "$$", '"': '"'}
                value = re.sub(r'\\([abfnrtv\\$"]|0[0-7]{1,3})', lambda m: chr(int(m[1][1:], 8)) if m[1].startswith("0") else escapes[m[1]], value)
            else:
                value = raw.split(" #", 1)[0].strip()
            return _interpolate(value, lambda name: lookup(name, index), file)
        return None

    result: dict[str, str] = {}
    for key in ("COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR"):
        value = lookup(key)
        if value is not None:
            result[key] = value
    return result


def resolve_compose_files(
    command: Sequence[str], *, cwd: Path, environment: Mapping[str, str],
) -> tuple[Path, ...]:
    """Command -f wins, then process/env-file COMPOSE_FILE, then discovery.

    Relative file and --env-file paths use the subprocess working directory,
    not the env file's directory. Explicit --env-file ignores COMPOSE_ENV_FILES.
    """
    files = _options(command, "--file", "-f")
    env_files = _options(command, "--env-file")
    if not env_files:
        env_files = environment.get("COMPOSE_ENV_FILES", "").split(",") if environment.get("COMPOSE_ENV_FILES") else []
    if not env_files and environment.get("COMPOSE_DISABLE_ENV_FILE", "").lower() not in {"1", "true"}:
        if (cwd / ".env").is_file():
            env_files = [str(cwd / ".env")]
    if not files:
        selectors = _env_selectors([cwd / file for file in env_files], environment)
        if "COMPOSE_FILE" in selectors:
            selected = selectors["COMPOSE_FILE"]
            if re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", selected):
                raise ComposeSourceError("<remote Compose file>", "refresh requires local checkout files")
            files = selected.split(selectors.get("COMPOSE_PATH_SEPARATOR") or os.pathsep)
    if not files:
        directories = _options(command, "--project-directory")
        directory = (cwd / directories[-1]).resolve() if directories else cwd.resolve()
        for parent in (directory, *directory.parents):
            base = next((parent / name for name in _DEFAULT_FILES if (parent / name).is_file()), None)
            if base is not None:
                files = [str(base)]
                override = next((parent / name for name in _DEFAULT_OVERRIDES if (parent / name).is_file()), None)
                if override is not None:
                    files.append(str(override))
                break
    if not files:
        raise ComposeSourceError(cwd, "no Compose file found")
    resolved: list[Path] = []
    for file in files:
        if file == "-" or re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", file):
            raise ComposeSourceError("<non-local Compose file>", "refresh requires local checkout files")
        if not file:
            raise ComposeSourceError("<empty Compose file>", "empty file selector")
        try:
            resolved.append((cwd / file).resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise ComposeSourceError("<unresolved Compose file>", "cannot resolve file path") from exc
    return tuple(resolved)


class _RuntimeOverlayLoader(yaml.SafeLoader):
    """Safe YAML construction with unique explicit keys in every mapping."""

    def __init__(self, stream) -> None:
        super().__init__(stream)
        self._checked_mappings: set[int] = set()
        self._merge_key = object()

    def flatten_mapping(self, node) -> None:
        # Check before merge expansion: an explicit override of an inherited
        # key is valid YAML, but repeated explicit keys are ambiguous. Aliased
        # nodes may be flattened more than once, so check their original form
        # only once. SafeLoader still constructs every nested mapping.
        if id(node) not in self._checked_mappings:
            self._checked_mappings.add(id(node))
            keys: set[object] = set()
            for key_node, _ in node.value:
                key = self._merge_key if key_node.tag == "tag:yaml.org,2002:merge" else self.construct_object(key_node)
                try:
                    if key in keys:
                        raise yaml.YAMLError("duplicate mapping key")
                    keys.add(key)
                except TypeError:
                    raise yaml.YAMLError("unsupported mapping key") from None
        super().flatten_mapping(node)


def _require_runtime_overlay(file: Path) -> None:
    """Runtime-owned overlays configure services without selecting their source."""
    try:
        with file.open(encoding="utf-8-sig") as stream:
            document = yaml.load(stream, Loader=_RuntimeOverlayLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        # Parser exceptions can include file contents. Only the safe diagnostic
        # below is exposed, never the YAML exception or its values.
        raise ComposeSourceError(file, "cannot parse runtime-config overlay safely; stack was not stopped") from None
    if not isinstance(document, dict):
        raise ComposeSourceError(file, "runtime-config overlay must be a mapping; stack was not stopped")
    if "include" in document:
        raise ComposeSourceError(file, "runtime-config overlay contains include; stack was not stopped")
    services = document.get("services", {})
    if not isinstance(services, dict):
        raise ComposeSourceError(file, "runtime-config overlay services must be a mapping; stack was not stopped")
    for service in services.values():
        if not isinstance(service, dict):
            raise ComposeSourceError(file, "runtime-config overlay service must be a mapping; stack was not stopped")
        for key in ("build", "image", "extends"):
            if key in service:
                raise ComposeSourceError(file, f"runtime-config overlay contains service {key}; stack was not stopped")


def require_compose_source(
    repo_root: Path, command: Sequence[str], *, cwd: Path,
    environment: Mapping[str, str], runtime_workdir: Path | None = None,
) -> tuple[Path, ...]:
    """Require a checkout base plus checkout or source-neutral runtime overlays."""
    root = repo_root.resolve()
    files = resolve_compose_files(command, cwd=cwd, environment=environment)
    if not files[0].is_relative_to(root):
        raise ComposeSourceError(files[0], f"outside selected checkout {root}; base Compose file must be in checkout; stack was not stopped")
    config: Path | None = None
    if runtime_workdir is not None:
        workdir = runtime_workdir.resolve()
        candidate = (workdir / "config").resolve()
        if candidate.is_relative_to(workdir):
            config = candidate
    for file in files:
        in_checkout = file.is_relative_to(root)
        if not in_checkout and (config is None or not file.is_relative_to(config)):
            raise ComposeSourceError(file, f"outside selected checkout {root}; stack was not stopped")
        if not file.is_file():
            raise ComposeSourceError(file, "selected Compose file does not exist or is not a regular file")
        if not in_checkout:
            _require_runtime_overlay(file)
    return files
