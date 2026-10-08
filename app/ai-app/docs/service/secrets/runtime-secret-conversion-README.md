---
id: repo:kdcube-ai-app/app/ai-app/docs/service/secrets/runtime-secret-conversion-README.md
title: "Offline Conversion of Copied Runtime Secrets"
summary: "Explicit clone-only conversion with target admission, pinned pure family parsers, whole-inventory size checks and generation-fenced crash replay."
tags: ["service", "secrets", "runtime", "migration", "custody"]
keywords: ["runtime_conversion", "copied vault", "offline conversion", "generation", "crash replay", "inventory", "root keys"]
updated_at: 2026-10-08
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/host-vault-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
---
# Offline Conversion of Copied Runtime Secrets

`kdcube_ai_app.infra.secrets.runtime_conversion` prepares legacy runtime
records for the expiry-aware `RuntimeVaultStore` format in an explicitly
approved **writable copy** of a vault. It preserves the exact inner UTF-8
bytes, reference and original deadline while adding the existing runtime
wrapper. It never selects a different secrets provider, changes the 64 KiB
limit, creates identities, rotates keys or enables a service endpoint.

This is a privileged offline library, not an automatic startup migration.
The source tests use invented records, identities and encryption keys.
Passing those tests does not qualify a deployed provider, establish complete
family coverage or authorize capture, conversion or activation.

## Custodian preparation and target admission

The custodian first captures the complete approved ordinary **and** runtime
scope at a coherent, quiescent cut, retaining an immutable sealed baseline.
The deployed source/provider and every namespace family require independent
review. Copy storage, root-key history, trust, TLS and audit state according
to that reviewed recipe; keep existing identity bindings. Do not substitute
an empty or runtime-only vault for the whole provider.

Conversion uses a second, owner-only writable copy. The custodian creates
`.runtime-conversion-clone.json` **at copy time**, with mode `0600`:

```json
{
  "schema": "kdcube.runtime_conversion_clone.v1",
  "clone_id": "<random 32 lower-case hexadecimal characters>",
  "inventory_sha256": "<trusted inventory SHA-256>",
  "attempt": 1,
  "state": "ready"
}
```

The independently retained `CloneReceipt` pins that canonical target, random
identifier, inventory digest and attempt number. It also supplies complete,
nonempty absolute exclusions for the sealed roots, live vault roots and paths
open by the live vault process. Equality, descendants **and ancestors** of
excluded paths are refused. Missing/wrong markers, links, hard-linked files,
special files, uncommitted candidates, nonprivate modes and concurrent
conversion are refused before constructing a key provider.

The root and its copied directories must be owner-owned `0700`; regular files
must be single-link, owner-owned `0400` or `0600`. Normalize permissions only
in the newly created copy, never in the original. Marker, journal and lock
files require `0600`. The root inode and marker are rechecked at operation
boundaries. This is a mistake-prevention gate for a trusted, quiescent
custodian process, **not isolation against malicious same-UID code**. A
fabricated receipt/marker cannot establish actual custody provenance.

## Pinned inventory and pure family codecs

`Inventory` contains the exact tenant/project/application tuple, up to 10,000
`SourceRecord` entries and a unique namespace-to-parser-source-SHA256 mapping.
Each entry binds namespace, opaque reference, original generation, state and,
when present, the exact inner bytes and deadline. Keep this inventory private
inside the custody process; it contains secrets. The digest includes byte
digests and metadata, not a reserialized inner object.

There are deliberately **no default family parsers**. Trusted composition
supplies reviewed pure `FamilyParser` implementations; their pinned source
digests are verified against the inventory. A parser must close its schema
and fields and verify the exact family, namespace, reference, scope and
positive integer deadline. It must not perform I/O, log values or consult
current service configuration to fill missing fields. A caller-supplied hash
is not a substitute for independent source review of that binding.

If a parser uses an external frozen ownership table, it exposes
`binding_sha256`. `Inventory.binding_pins` must pin every such namespace's
table digest; the inventory digest covers those pins, and preflight checks
the parser's digest before constructing a store. Two tables with the same
parser code cannot be substituted. Trusted composition must also establish
two-way closure: each required binding has its present record, and each
present record has a binding. Absent/tombstoned rows require an explicit
domain-state explanation from the same sealed cut, not a new wall-clock guess.

Every table-bound namespace requires an explicit `IndexRecord` in
`Inventory.index_records` for `platform.runtime.<namespace>.__keys`. Record
its original present bytes/generation, or explicit absence/tombstone. Those
indexes are pinned in the inventory and read/rechecked byte-exact; they are
never rewritten, rebuilt or silently skipped. Composition separately verifies
index/member closure against the original cut. Ordinary-key preservation
remains whole-provider acceptance, not automatic filtering by this library.

Whole-inventory preflight rejects unknown coverage, duplicates, malformed
UTF-8/JSON, duplicate object fields, nonstandard constants, invalid state or
generation, parser/binding/deadline mismatches and outer wrapper expansion
beyond the **existing** 64 KiB byte limit. This happens before any key/store
factory is invoked, so a valid first record cannot be written before a bad
later record is discovered. No payload truncation or new reference is allowed.

```python
from kdcube_ai_app.infra.secrets.runtime_conversion import convert
from kdcube_ai_app.infra.secrets.runtime_conversion.file_store import CopiedFileVault

# receipt, inventory and reviewed_parsers come from trusted custody composition.
# Never accept them from an ordinary HTTP request or an untrusted descriptor.
result = convert(
    receipt=receipt,
    inventory=inventory,
    parsers=reviewed_parsers,
    store_factory=lambda admitted_root: CopiedFileVault(
        admitted_root, scope=inventory.scope),
)
```

The file adapter reads existing material from `root-keys/` and `store/` only
after admission and checks the existing provider's native custody
qualification. Its privileged tombstone read reuses the durable store's
validated codec under one OS lock. It never calls `rotate()` or enrollment.
Root/TLS keys stay inside the sealed-custody process: never pass them in
arguments, environment variables or logs, and never write key-bearing files
outside the approved copy. Process/container isolation, retention and mount
lifecycle still require their own installation proof.

## Exact replacement, interruption and refusal

Every actual source incarnation is read and validated before the first
replacement. Present records are replaced using their **original** generation
as the CAS condition. The resulting generation must be exactly `old + 1`,
and exact read-back must match the expected wrapper. Absent records remain
generation-zero absence; tombstones retain their deleted generation. No
create/delete is issued for either. Expired records retain their original
deadlines and remain unreadable through the normal runtime adapter.

The private, atomically committed progress journal records only identity
digests, generations, deadlines and byte digests. A separate reviewer must
recompute authoritative inner-byte and ordinary-key preservation digests;
comparing one converter receipt to another is not independent verification.
Only counts and fixed error codes are suitable for public reports. Avoid
logging inventories, object locals, raw provider errors or tracebacks.

There are two distinct recovery cases:

- A **crash/kill interruption**, with no refusal or abort verdict, may resume
  in the same copy. Reconstruct from the pinned sealed inventory. Accept only
  the exact original incarnation or exact `old + 1` wrapper; fill a missing
  journal entry without double-wrapping or incrementing again.
  A `complete` marker with a missing or incomplete progress journal is refused
  before key/store construction; it is not a known crash-interrupted attempt.
- Any **refusal/abort**, including preflight failure, conflict, mismatched
  replay, changed generation/value, ambiguous acknowledgement or explicit
  operator abort, invalidates the entire copy. The custodian discards that
  exact execution copy and re-copies the sealed baseline before retrying,
  using a fresh clone id and incremented attempt. Never repair it in place.
  `KeyboardInterrupt` is treated as an explicit abort.

The engine never deletes an arbitrary rejected target. A failed target
admission leaves it untouched; safe disposal belongs to the custodian. An
in-process refusal marks an admitted copy `invalid` where possible. Even if
that write fails, the external custody receipt must record refusal and bar
reuse. Abrupt death with an uncommitted candidate also requires re-copy, not
automatic recovery of an ambiguous candidate.

## Source test gate

Run the focused suite with the prepared SDK interpreter and explicit source
overlay, after proving interpreter and first-party import origins:

```bash
python -m pytest --import-mode=importlib -q \
  kdcube_ai_app/infra/secrets/runtime_conversion/tests/test_conversion.py
```

`importlib` mode avoids confusing the SDK's namespace directories with
Python's standard `secrets` module. Tests cover target-before-key refusal,
whole-batch preflight, exact bytes/deadlines, absence/tombstones, conflicts,
explicit abort, locked root identity and post-commit/pre-journal crash replay.
The encrypted-file tests use native storage/envelope qualification and the
normal broker/runtime adapter with synthetic certificates and keys. They do
not touch a deployed vault or prove restored-cohort acceptance.

Activation remains a separate reviewed decision after full namespace closure,
provider selection/credential qualification, independent read-back and the
specific execution window have all been established.
