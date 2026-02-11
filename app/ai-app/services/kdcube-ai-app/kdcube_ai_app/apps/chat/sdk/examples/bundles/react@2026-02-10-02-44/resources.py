import random
from typing import Dict, List

from kdcube_ai_app.infra.service_hub.errors import ServiceError

# Friendly error messages for different scenarios
FRIENDLY_ERROR_MESSAGES: Dict[str, List[str]] = {
    "usage_limit": [
        """I apologize, but I'm temporarily unable to process requests due to high usage. 

Our team has been notified and we expect service to resume within the hour.

In the meantime, you can:
- Check our status page at status.yourapp.com
- Email us at info@nestlogic.com and we'll respond promptly
- Try again in a few minutes

We're sorry for the inconvenience!""",

        """Oops! I seem to be getting a bit overloaded right now.

I've let the team know, and they're on it. Please try again in a few minutes - 
I should be back to my usual helpful self shortly!

Need immediate help? Reach out to info@nestlogic.com""",

        """We're experiencing temporary capacity limits and can't process your request at this moment.

Our team is actively working to restore full service. Please try again shortly.

For urgent assistance: info@nestlogic.com""",

        """I'm temporarily at capacity and need a quick breather.

The good news: Our team is already on it and working to get me back up to speed.
The even better news: This usually resolves in just a few minutes.

Try again soon, or reach out to info@nestlogic.com if you need immediate help.""",

        """My apologies - I've hit a temporary service limit and can't complete your request right now.

What's happening: High usage has temporarily maxed out my capacity
What we're doing: Our team is actively resolving this
What you can do: Try again in 5-10 minutes or contact info@nestlogic.com

Thanks for your patience!"""
    ],

    "rate_limit": [
        """Whoa, slow down there. You're making requests faster than I can handle.

Please wait a moment and try again. I'll be ready for you shortly!""",

        """I need just a brief moment to catch up with your requests.

Please try again in a few seconds - I'll be ready!""",

        """You're moving fast! To ensure quality responses, I need you to slow down just a bit.

Try again in a moment, and I'll be happy to help."""
    ],

    "server_error": [
        """Oops! Something unexpected happened on my end. 

This isn't your fault - I've logged the error and our team will investigate.

Please try again, or contact info@nestlogic.com if the issue persists.""",

        """I've encountered an unexpected error while processing your request.

Our team has been automatically notified and will investigate. 
Please try again - if the problem continues, reach out to info@nestlogic.com""",

        """Something went wrong on our end, and I couldn't complete your request.

The technical team has been alerted. Please try again in a moment!"""
    ],

    "timeout": [
        """Your request is taking longer than expected to process.

This might be due to high load. Please try again - sometimes a fresh start does the trick!""",

        """I wasn't able to complete your request in time.

This usually happens during peak usage. Give it another try, and it should work!""",
    ]
}


def get_friendly_error_message(error_code: str, fallback: bool = True) -> str:
    """
    Randomly sample a friendly error message for the given error code.

    Args:
        error_code: The error type (e.g., 'usage_limit', 'rate_limit', 'server_error')
        fallback: If True, return a generic message when error_code not found

    Returns:
        A randomly selected friendly error message
    """
    messages = FRIENDLY_ERROR_MESSAGES.get(error_code)

    if messages:
        return random.choice(messages)

    if fallback:
        return """We're experiencing a temporary issue and couldn't process your request.

Our team has been notified. Please try again in a few moments.

Contact info@nestlogic.com if you need immediate assistance."""

    raise ValueError(f"Unknown error code: {error_code}")


# Usage examples:

# Example 1: In your error handler
def handle_service_error(error: ServiceError) -> str:
    """Convert ServiceError to user-friendly message."""

    # Map service error codes to message categories
    error_code_map = {
        "usage_limit": "usage_limit",
        "rate_limit": "rate_limit",
        "quota_exceeded": "usage_limit",
        "too_many_requests": "rate_limit",
        "timeout": "timeout",
        "internal_error": "service_error",
        "server_error": "server_error",
    }

    # Get the friendly message
    message_code = error_code_map.get(error.code, "usage_limit")
    return get_friendly_error_message(message_code)
