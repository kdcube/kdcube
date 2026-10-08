# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Whole-inventory preflight and exact generation-fenced conversion/replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
from pathlib import Path

from kdcube_ai_app.infra.secrets.runtime_conversion.guard import (
    Admission, CloneReceipt, JOURNAL, read_private, write_private,
)
from kdcube_ai_app.infra.secrets.runtime_conversion.model import (
    ConversionError, FamilyParser, Inventory, Observation, SourceRecord,
    canonical, digest, preflight,
)


class ConversionStore(Protocol):
    """Privileged clone-only port; ordinary runtime routes never expose it."""
    def read(self, source: SourceRecord) -> Observation: ...
    def replace(self, source: SourceRecord, value: bytes) -> int: ...


@dataclass(frozen=True)
class ConversionResult:
    converted: int
    replayed: int
    preserved: int
    attempt: int


def _same(first: Observation, second: Observation | None) -> bool:
    return (type(first) is Observation and type(first.generation) is int
            and type(first.state) is str and (first.value is None or type(first.value) is bytes)
            and second is not None and first == second)


def convert(*, receipt: CloneReceipt, inventory: Inventory,
            parsers: dict[str, FamilyParser],
            store_factory: Callable[[Path], ConversionStore],
            _after_replace: Callable[[], None] | None = None) -> ConversionResult:
    """Use only a custodian-bound clone and source-reviewed pure family codecs.

    The factory receives the checked clone path and is invoked only after the
    entire size/shape preflight. No key material is a parameter. Exceptions
    invalidate an admitted clone; its custodian must discard and re-copy it.
    Crash interruption leaves only exact replay available; Ctrl-C is an abort.
    """
    try:
        admission = Admission(receipt)
    except Exception:
        raise ConversionError("runtime_conversion_target_refused") from None
    with admission.locked():
        try:
            if inventory.sha256() != receipt.inventory_sha256:
                raise ConversionError("runtime_conversion_inventory_pin_mismatch")
            prepared = preflight(inventory, parsers)
            expected = {}
            for item in prepared:
                source = item.source
                if item.wrapper is not None:
                    expected[digest(source.key.encode())] = {
                        "generation": source.generation + 1, "wrapper_sha256": digest(item.wrapper),
                        "inner_sha256": digest(source.value), "expires_at": source.expires_at}
            progress = read_private(admission.root, JOURNAL)
            if progress is None:
                progress = {"schema": "kdcube.runtime_conversion_progress.v1",
                            "clone_id": receipt.clone_id, "attempt": receipt.attempt,
                            "inventory_sha256": receipt.inventory_sha256, "completed": {}}
            if (type(progress) is not dict
                    or set(progress) != {"schema", "clone_id", "attempt", "inventory_sha256", "completed"}
                    or progress["schema"] != "kdcube.runtime_conversion_progress.v1"
                    or progress["clone_id"] != receipt.clone_id
                    or type(progress["attempt"]) is not int or progress["attempt"] != receipt.attempt
                    or progress["inventory_sha256"] != receipt.inventory_sha256
                    or type(progress["completed"]) is not dict
                    or any(key not in expected or canonical(value) != canonical(expected[key])
                           for key, value in progress["completed"].items())):
                raise ConversionError("runtime_conversion_progress_invalid")
            if (admission.check()["state"] == "complete"
                    and set(progress["completed"]) != set(expected)):
                raise ConversionError("runtime_conversion_progress_invalid")
            admission.state("running")
            store = store_factory(admission.root)
            observed = []
            # Validate every actual source incarnation before replacing any.
            for item in prepared:
                admission.check()
                source = item.source
                current = store.read(source)
                admission.check()
                original = Observation(source.generation, source.state, source.value)
                converted = (Observation(source.generation + 1, "present", item.wrapper)
                             if item.wrapper is not None else None)
                if (not (_same(current, original) or _same(current, converted))
                        or digest(source.key.encode()) in progress["completed"] and not _same(current, converted)):
                    raise ConversionError("runtime_conversion_incarnation_conflict")
                observed.append(current)
            written = replayed = preserved = 0
            for item, current in zip(prepared, observed):
                admission.check()
                source = item.source
                if item.wrapper is None:
                    # Recheck absence/tombstone AND byte-exact original indexes;
                    # never create/delete/rewrite any preserved record.
                    if not _same(store.read(source), current):
                        raise ConversionError("runtime_conversion_incarnation_conflict")
                    admission.check()
                    preserved += 1
                    continue
                target = Observation(source.generation + 1, "present", item.wrapper)
                if not _same(current, target):
                    generation = store.replace(source, item.wrapper)
                    if type(generation) is not int or generation != target.generation:
                        raise ConversionError("runtime_conversion_generation_conflict")
                    if _after_replace is not None:
                        _after_replace()
                    written += 1
                else:
                    replayed += 1
                admission.check()
                if not _same(store.read(source), target):
                    raise ConversionError("runtime_conversion_readback_failed")
                admission.check()
                progress["completed"][digest(source.key.encode())] = expected[digest(source.key.encode())]
                write_private(admission.root, JOURNAL, progress)
            admission.state("complete")
            return ConversionResult(written, replayed, preserved, receipt.attempt)
        except (Exception, KeyboardInterrupt):
            # Invalid clones are never usable or repaired in place. Do not
            # delete a possibly-wrong argument or expose provider exceptions.
            try:
                admission.state("invalid")
            except Exception:
                pass
            raise ConversionError("runtime_conversion_discard_clone_required") from None
