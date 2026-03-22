# ── events_resources.py ──
# User-facing messages for economics rate-limit SSE events.
#
# All text that reaches the client via `user_message` fields in
# rate_limit.* events is defined here so it can be found and updated
# in one place without touching entrypoint logic.
#
# Static messages are plain string constants.
# Dynamic messages (those that embed runtime values) are small functions.
#
# Consumers:
#   entrypoint_with_economic.py  –  imports from this module


# ── rate_limit.denied ────────────────────────────────────────────────────────

def msg_denied_quota_reset(reset_text: str) -> str:
    """Quota exhausted; reset time is known."""
    return f"You've reached your usage limit. Your quota resets {reset_text}."


MSG_DENIED_LOCK_TIMEOUT = (
    "Too many requests are being processed right now. Please try again in a moment."
)

MSG_DENIED_CONCURRENCY = (
    "You have too many requests running at once. Please wait for one to complete."
)

MSG_DENIED_TOKEN_LIMIT = (
    "You've reached your token limit. Try again later or upgrade your plan."
)

MSG_DENIED_REQUEST_LIMIT = (
    "You've reached your request limit. Try again later or upgrade your plan."
)

MSG_DENIED_GENERIC = "You've reached your usage limit. Please try again later."


# ── rate_limit.warning ───────────────────────────────────────────────────────

def msg_warning_last_msg_reset(reset_text: str) -> str:
    """Last message used; reset time is known."""
    return f"You've used your last message. Your quota resets {reset_text}."


MSG_WARNING_LAST_MSG_SOON = (
    "You've used your last message. Your quota will reset soon."
)

MSG_WARNING_ONE_REQUEST_REMAINING = (
    "You have 1 message remaining in your current quota."
)


def msg_warning_low_tokens(tokens_k: int) -> str:
    """Token budget is the binding constraint; quota is not exhausted yet."""
    return f"You're running low on tokens (~{tokens_k}K remaining). Consider upgrading."


MSG_WARNING_APPROACHING = "You're approaching your usage limit."


# ── rate_limit.no_funding ────────────────────────────────────────────────────

MSG_NO_FUNDING = (
    "This service is not available for your account type. Please contact support."
)


# ── rate_limit.subscription_exhausted ────────────────────────────────────────

MSG_SUBSCRIPTION_EXHAUSTED = (
    "Your subscription balance is exhausted. "
    "Please top up your subscription to continue."
)


# ── rate_limit.project_exhausted ─────────────────────────────────────────────

MSG_PROJECT_EXHAUSTED = (
    "Project budget exhausted. Please contact your administrator to add funds."
)
