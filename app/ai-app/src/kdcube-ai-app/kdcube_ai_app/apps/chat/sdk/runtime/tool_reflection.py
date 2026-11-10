# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Shared rules for reading tools off a Python module by reflection.

A tool module that exposes neither a Semantic Kernel ``kernel`` nor
``list_tools()`` is introspected by walking ``dir(module)``. Two consumers do
that walk: the tool manager (``ToolSubsystem._introspect_module``), which
builds the catalog rendered into the agent's system prompt, and the agent
inventory (``agent_inventory``), which builds the pickable capability catalog.
Both apply the rules in this module so the two catalogs name the same tools
with the same descriptions.
"""

from __future__ import annotations

from typing import Any, Iterator, Tuple


def tool_defined_in_module(fn: Any, mod: Any) -> bool:
    """True when ``fn`` is defined by ``mod`` rather than imported into it.

    Module-level imports are public and callable, so a module containing
    ``from typing import Optional`` or
    ``from semantic_kernel.functions import kernel_function`` would otherwise
    publish ``Optional`` and ``kernel_function`` as tools, each carrying the
    imported library's docstring.

    A function defined in the module carries that module's ``__module__``.
    ``@kernel_function`` decorates in place (it attaches attributes and returns
    the same function object), so decorated tools keep it. Objects with no
    ``__module__`` are kept: dropping a real tool is a worse failure than
    carrying a stray entry.
    """
    own = getattr(mod, "__name__", None)
    if not own:
        return True
    origin = getattr(fn, "__module__", None)
    if origin is None:
        return True
    return origin == own


def declared_tool_description(fn: Any) -> str:
    """The description a tool declares, in resolution order.

    ``@kernel_function(description=...)`` stores the text on
    ``__kernel_function_description__`` and never sets ``.description``, so
    the kernel attribute is read first, then a plain ``description``
    attribute, then the docstring.
    """
    if fn is None:
        return ""
    return str(
        getattr(fn, "__kernel_function_description__", "")
        or getattr(fn, "description", "")
        or (getattr(fn, "__doc__", "") or "")
    )


def reflected_tool_callables(mod: Any) -> Iterator[Tuple[str, Any]]:
    """Yield ``(name, callable)`` for each tool published by reflection.

    Reflects on ``mod.tools`` when the module exposes one, else on the module
    itself. Private names are skipped. When reflecting on the module itself,
    names imported into it are skipped (see ``tool_defined_in_module``)
    unless the module lists them in ``__all__``: ``__all__`` is the explicit
    way for a tool module to re-export a tool implemented elsewhere. An
    explicit ``tools`` object is taken as declared.
    """
    owner = getattr(mod, "tools", mod)
    reflecting_on_module = owner is mod
    exported = _declared_exports(mod) if reflecting_on_module else frozenset()
    for name in dir(owner):
        if name.startswith("_"):
            continue
        fn = getattr(owner, name, None)
        if not callable(fn):
            continue
        if (
            reflecting_on_module
            and name not in exported
            and not tool_defined_in_module(fn, mod)
        ):
            continue
        yield name, fn


def _declared_exports(mod: Any) -> frozenset:
    names = getattr(mod, "__all__", None)
    if not isinstance(names, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(name) for name in names)
