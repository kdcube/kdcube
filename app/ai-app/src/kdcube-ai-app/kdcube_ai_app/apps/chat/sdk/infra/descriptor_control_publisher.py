# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Ask the platform to publish one agent's descriptor Control now.

Connection Hub shows and edits an agent's shared descriptor Control, but the
descriptor belongs to the agent's application and only that application's
deployment publishes it. When an administrator opens the Control, the Hub asks
the platform, through this port, to publish the agent's current descriptor
first, so the administrator never edits an older one.

The platform binds a publisher only for requests it routes to Connection Hub;
anywhere else the port is unbound and :func:`get_current_descriptor_control_publisher`
returns ``None``. The publisher answers a plain outcome:

``published`` / ``unchanged``
    the Hub now holds the application's current descriptor for that agent;
``busy``
    a deployment of the application is publishing right now; the stored Control stands;
``unavailable``
    the application is not ready in this process; nothing was published;
``refused:<reason>``
    the Hub or the application refused (``refused:agent_not_declared`` when the
    application no longer declares that agent).

Nothing here loads an application or reads its props: the platform's
publisher does, as the application's deployment would.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable, Iterator, Optional

DescriptorControlPublisher = Callable[[str, str], Awaitable[str]]

_PUBLISHER: ContextVar[Optional[DescriptorControlPublisher]] = ContextVar(
    "descriptor_control_publisher", default=None
)


@contextmanager
def bind_descriptor_control_publisher(
    publisher: Optional[DescriptorControlPublisher],
) -> Iterator[Optional[DescriptorControlPublisher]]:
    token = _PUBLISHER.set(publisher)
    try:
        yield publisher
    finally:
        _PUBLISHER.reset(token)


def get_current_descriptor_control_publisher() -> Optional[DescriptorControlPublisher]:
    """The publisher the platform bound for this request, or ``None``."""
    return _PUBLISHER.get()


__all__ = [
    "DescriptorControlPublisher",
    "bind_descriptor_control_publisher",
    "get_current_descriptor_control_publisher",
]
