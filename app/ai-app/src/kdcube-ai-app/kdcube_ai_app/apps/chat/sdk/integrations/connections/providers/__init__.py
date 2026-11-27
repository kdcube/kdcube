"""Built-in ConnectionProvider declarations.

Importing this package registers all bundled providers via their
`@connection_provider(...)` decorators.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.integrations.connections.providers import slack  # noqa: F401  (registers SlackConnection)
from kdcube_ai_app.apps.chat.sdk.integrations.connections.providers import gmail  # noqa: F401  (registers GmailConnection — Google OAuth)

__all__ = ["slack", "gmail"]
