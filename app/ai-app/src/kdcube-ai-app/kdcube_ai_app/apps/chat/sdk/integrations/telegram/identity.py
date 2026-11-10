"""Telegram actor id construction.

A Telegram chatter is stored, accounted, and addressed under an actor user id
derived from the Telegram user id. The default form is
``telegram_{telegram_user_id}``. A deployment that federates identities across
surfaces can choose another stable namespace with the integration definition
prop ``actor_id_template``; every SDK site that builds or matches a Telegram
actor id goes through this module so the forms never diverge.
"""

from __future__ import annotations

import string
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import integration_definition_value

DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE = "telegram_{telegram_user_id}"
TELEGRAM_ACTOR_ID_TEMPLATE_PROP = "actor_id_template"
_PLACEHOLDER = "telegram_user_id"


def validate_telegram_actor_id_template(template: Any) -> str:
    """Return the template when it is usable, raise ``ValueError`` otherwise.

    A usable template is a string that contains the ``{telegram_user_id}``
    placeholder (so ids stay distinct per user) and no other replacement field.
    """
    if not isinstance(template, str) or not template.strip():
        raise ValueError("telegram actor_id_template must be a non-empty string")
    value = template.strip()
    fields: list[str] = []
    try:
        for _literal, field_name, format_spec, conversion in string.Formatter().parse(value):
            if field_name is None:
                continue
            if field_name != _PLACEHOLDER or format_spec or conversion:
                raise ValueError(
                    "telegram actor_id_template may only use the {telegram_user_id} placeholder; "
                    f"got {{{field_name}{'!' + conversion if conversion else ''}{':' + format_spec if format_spec else ''}}}"
                )
            fields.append(field_name)
    except ValueError as exc:
        if "actor_id_template" in str(exc):
            raise
        raise ValueError(f"telegram actor_id_template is not a valid format string: {exc}") from exc
    if not fields:
        raise ValueError("telegram actor_id_template must contain the {telegram_user_id} placeholder")
    return value


def format_telegram_actor_user_id(telegram_user_id: Any, template: Any = None) -> str:
    """Build the actor user id for one Telegram user id."""
    subject = str(telegram_user_id or "").strip()
    if not subject:
        return ""
    effective = DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE if template in (None, "") else template
    return validate_telegram_actor_id_template(effective).format(telegram_user_id=subject)


def telegram_actor_id_template(entrypoint: Any, *, integration_id: str = "") -> str:
    """Resolve the configured actor id template for a Telegram integration."""
    configured = integration_definition_value(
        entrypoint,
        provider="telegram",
        key=TELEGRAM_ACTOR_ID_TEMPLATE_PROP,
        default=None,
        integration_id=integration_id,
    )
    if configured in (None, ""):
        return DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE
    return validate_telegram_actor_id_template(configured)


def telegram_actor_user_id(entrypoint: Any, telegram_user_id: Any, *, integration_id: str = "") -> str:
    """Actor user id for ``telegram_user_id`` under the integration's template."""
    return format_telegram_actor_user_id(
        telegram_user_id,
        telegram_actor_id_template(entrypoint, integration_id=integration_id),
    )


def telegram_actor_id_template_from_definition(definition: Mapping[str, Any] | None) -> str:
    """Resolve the template from an already-selected integration definition mapping."""
    if not isinstance(definition, Mapping):
        return DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE
    configured = definition.get(TELEGRAM_ACTOR_ID_TEMPLATE_PROP)
    if configured in (None, ""):
        webhook = definition.get("webhook")
        if isinstance(webhook, Mapping):
            configured = webhook.get(TELEGRAM_ACTOR_ID_TEMPLATE_PROP)
    if configured in (None, ""):
        return DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE
    return validate_telegram_actor_id_template(configured)
