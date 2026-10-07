"""Recovery failures that must propagate rather than select empty history."""


class CheckpointRecoveryError(RuntimeError):
    """An authoritative checkpoint cannot be safely reconstructed."""


class CheckpointIntegrityError(CheckpointRecoveryError):
    """A scope, key, serialization or byte-integrity binding does not match."""


class CheckpointUnavailableError(CheckpointRecoveryError):
    """The exact authoritative cold payload is unavailable."""


class CheckpointAgeUnprovenError(CheckpointRecoveryError):
    """Creation age has not been established for retention eligibility."""
