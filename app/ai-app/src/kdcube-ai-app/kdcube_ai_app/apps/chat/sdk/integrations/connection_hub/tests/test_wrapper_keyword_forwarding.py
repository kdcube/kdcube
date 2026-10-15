"""W303: a new keyword on a Connection Hub constructor must not break KDCube's wrapper.

Each KDCube wrapper over a portable Connection Hub class is built with every
keyword its portable class declares, read by ``inspect.signature`` from the
installed package, so a new portable parameter fails here in CI, not at deploy.
A guard also fails when a new wrapper appears that this test does not cover.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

pytest.importorskip("connection_hub")

PACKAGE = "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub"
PACKAGE_DIR = Path(__file__).resolve().parents[1]

# (module under PACKAGE, class): keywords the wrapper itself requires (used only when the portable class does not declare them).
WRAPPERS = {
    ("authenticators.client", "ConnectionHubAuthenticatorsClient"): {},
    ("authority_registry_client", "AuthorityRegistryClient"): {},
    ("connection_edges", "ConnectionEdgesClient"): {},
    ("delegated_credentials.cards.persistence", "DurableCardPersistence"): {},
    ("delegated_credentials.cards.service", "DelegatedCardService"): {},
    ("delegated_credentials.automation_access", "AutomationAccessService"): {},
    ("delegated_to_kdcube.store", "DelegatedToKdcubeStore"): {},
    ("request_auth", "PlatformTokenAuthenticator"): {"auth_manager": object()},
    ("request_auth", "RequestAuthResolver"): {"auth_manager": None},
}

# Wrappers that do not restate the portable constructor, with the reason.
NOT_FORWARDING = {
    # Builds the portable class's only argument (a transport object) from a named-service
    # registry; its own ``transport`` keyword is a transport *name*, a different thing.
    ("client", "ConnectionsClient"): "builds the portable transport",
    # Defines no __init__: the portable constructor is inherited unchanged.
    ("delegated_to_kdcube.client", "DelegatedToKdcubeClient"): "inherits the portable __init__",
}


class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"<{self.name}>"

    def __bool__(self) -> bool:  # truthy, so a wrapper's `value or default` keeps it
        return True


def _portable(wrapper: type) -> type:
    base = wrapper.__mro__[1]
    assert base.__module__.startswith("connection_hub."), (wrapper, base)
    return base


def _portable_keywords(portable: type) -> list[str]:
    parameters = inspect.signature(portable.__init__).parameters.values()
    return [
        p.name
        for p in parameters
        if p.name != "self" and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]


@pytest.mark.parametrize("key", sorted(WRAPPERS), ids=lambda k: k[1])
def test_a_wrapper_accepts_and_forwards_every_portable_keyword(monkeypatch, key):
    module_name, class_name = key
    wrapper = getattr(importlib.import_module(f"{PACKAGE}.{module_name}"), class_name)
    portable = _portable(wrapper)
    received: dict = {}
    keywords = {name: _Sentinel(name) for name in _portable_keywords(portable)}  # read before patching
    positional = [
        p.name for p in inspect.signature(portable.__init__).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name != "self"
    ]

    def record(self, *args, **kwargs):
        received.update(zip(positional, args))  # a keyword forwarded positionally is still forwarded
        received.update(kwargs)

    monkeypatch.setattr(portable, "__init__", record)
    keywords["w303_future_keyword"] = _Sentinel("w303_future_keyword")  # a parameter the portable class adds later

    wrapper_only = {k: v for k, v in WRAPPERS[key].items() if k not in keywords}
    wrapper(**wrapper_only, **keywords)  # TypeError here is the W303 defect

    for name, value in keywords.items():
        assert received.get(name) is value, f"{class_name} did not forward {name}"


def _portable_wrappers_in_source() -> set[tuple[str, str]]:
    """Every class in the package whose base is a connection_hub class, however it was imported."""
    found = set()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if "tests" in path.relative_to(PACKAGE_DIR).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names, modules = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "connection_hub":
                names.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                modules.update(a.asname or a.name for a in node.names if a.name.split(".")[0] == "connection_hub")
            elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                  and getattr(node.value.func, "id", getattr(node.value.func, "attr", "")) == "import_module"
                  and node.value.args and isinstance(node.value.args[0], ast.Constant)
                  and str(node.value.args[0].value).split(".")[0] == "connection_hub"):
                modules.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                portable = (isinstance(base, ast.Name) and base.id in names) or (
                    isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id in modules
                )
                if portable:
                    module = ".".join(path.relative_to(PACKAGE_DIR).with_suffix("").parts)
                    found.add((module, node.name))
    return found


def test_every_wrapper_over_a_portable_class_is_covered():
    assert _portable_wrappers_in_source() == set(WRAPPERS) | set(NOT_FORWARDING)
