"""Offline contract tests for the ported-langgraph-agents@2026-07-13 package.

These assert the synchronization invariant: the entrypoint identity, the config
template, the OpenAPI contract, the README, and the release descriptor all agree
that the id is ``ported-langgraph-agents@2026-07-13``; that the app hosts TWO
agents (``lg-solution``, ``lg-react``) each with a declared model picker; that the
only PUBLIC ingress is the Telegram webhook (the reactive chat turn + the webhook
both drive the same ``execute_core``); and that the two-agent scene's chat tiles are
declared as manifest widgets (``chat_lg_solution``, ``chat_lg_react``), one per
agent. They parse the entrypoint via AST (no import), so they run without a DB, an
API key, or the heavy deps.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

BUNDLE_ID = "ported-langgraph-agents@2026-07-13"
BUNDLE_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = BUNDLE_ROOT / "entrypoint.py"
OPENAPI = BUNDLE_ROOT / "interface" / "ported-langgraph-agents.openapi.yaml"
CONFIG_TEMPLATE = BUNDLE_ROOT / "config" / "bundles.template.yaml"
SECRETS_TEMPLATE = BUNDLE_ROOT / "config" / "bundles.secrets.template.yaml"

SOLUTION_ROLE = "lg-solution.answer"
PREBUILT_ROLE = "lg-react.answer"

# `ui_widget` is intentionally NOT forbidden: the scene declares one chat widget
# per agent (see test_entrypoint_declares_the_two_scene_chat_widgets).
FORBIDDEN_SURFACE_DECORATORS = {"mcp", "data_bus_handler", "cron", "on_job"}


def _module_ast() -> ast.Module:
    return ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))


def _module_constants(tree: ast.Module) -> dict[str, object]:
    consts: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    consts[target.id] = ast.literal_eval(node.value)
                except Exception:
                    pass
    return consts


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return target.id if isinstance(target, ast.Name) else ""


def _kw(call: ast.Call, name: str, consts: dict[str, object]):
    for keyword in call.keywords:
        if keyword.arg == name:
            if isinstance(keyword.value, ast.Name):
                return consts.get(keyword.value.id)
            return ast.literal_eval(keyword.value)
    return None


def _class_decorators(tree: ast.Module) -> dict[str, ast.Call]:
    found: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    found[_decorator_name(dec)] = dec
    return found


def test_entrypoint_identity_matches_bundle_id() -> None:
    tree = _module_ast()
    consts = _module_constants(tree)
    decorators = _class_decorators(tree)

    assert consts.get("BUNDLE_ID") == BUNDLE_ID
    assert consts.get("DEFAULT_AGENT_ID") == "lg-solution"

    assert "bundle_id" in decorators
    assert _kw(decorators["bundle_id"], "id", consts) == BUNDLE_ID

    assert "bundle_entrypoint" in decorators
    assert _kw(decorators["bundle_entrypoint"], "name", consts) == "ported-langgraph-agents"
    assert _kw(decorators["bundle_entrypoint"], "version", consts) == "1.0.0"


def test_entrypoint_declares_both_agent_roles() -> None:
    consts = _module_constants(_module_ast())
    assert consts.get("SOLUTION_ANSWER_ROLE") == SOLUTION_ROLE
    assert consts.get("PREBUILT_ANSWER_ROLE") == PREBUILT_ROLE


def _api_decorated_functions(tree: ast.Module) -> dict[str, ast.Call]:
    found: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and _decorator_name(dec) == "api":
                found[node.name] = dec
    return found


def test_no_forbidden_surface_decorators_exist() -> None:
    tree = _module_ast()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            name = _decorator_name(dec)
            if name in FORBIDDEN_SURFACE_DECORATORS:
                offenders.append(f"{node.name} -> @{name}")
    assert not offenders, f"unexpected surface decorators: {offenders}"


def _ui_widget_aliases(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and _decorator_name(dec) == "ui_widget":
                alias = _kw(dec, "alias", {})
                if isinstance(alias, str) and alias:
                    aliases.add(alias)
    return aliases


def test_public_ingress_surface_is_only_the_telegram_webhook() -> None:
    tree = _module_ast()
    consts = _module_constants(tree)
    apis = _api_decorated_functions(tree)

    # The only PUBLIC api (the platform-unauthenticated ingress) is the webhook.
    public = {name for name, dec in apis.items() if _kw(dec, "route", consts) == "public"}
    assert public == {"telegram_webhook"}
    dec = apis["telegram_webhook"]
    assert _kw(dec, "method", consts) == "POST"
    assert _kw(dec, "alias", consts) == "telegram_webhook"

    # Every other api is an operations-route fallback companion of a scene chat
    # widget — nothing else is exposed.
    operations = {name for name, dec in apis.items() if _kw(dec, "route", consts) == "operations"}
    assert operations == {"chat_lg_solution_widget", "chat_lg_react_widget"}


def test_entrypoint_declares_the_two_scene_chat_widgets() -> None:
    # The scene mounts one chat tile per agent at `widgets/<alias>`; a widget is
    # only reachable once declared in the manifest via @ui_widget (the ui.widgets
    # config only BUILDS the assets), so both aliases must be declared here.
    assert _ui_widget_aliases(_module_ast()) == {"chat_lg_solution", "chat_lg_react"}


def test_openapi_declares_both_surfaces_and_the_webhook_path() -> None:
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))

    paths = contract.get("paths") or {}
    assert "/public/telegram_webhook" in paths
    webhook = paths["/public/telegram_webhook"]
    assert "post" in webhook
    assert webhook["post"].get("operationId") == "telegram_webhook"
    assert webhook["post"].get("security") == []

    surfaces = contract.get("x-kdcube-surfaces") or {}
    assert set(surfaces.keys()) == {"chat_turn", "telegram_webhook"}

    chat = surfaces["chat_turn"]
    assert chat.get("kind") == "reactive_chat_turn"
    assert chat.get("entrypoint") == "execute_core"
    assert chat.get("default_chat") is True

    tg = surfaces["telegram_webhook"]
    assert tg.get("route") == "public"
    assert tg.get("method") == "POST"
    assert tg.get("alias") == "telegram_webhook"
    assert tg.get("drives") == "execute_core"


def test_config_template_declares_two_agents_and_both_surfaces() -> None:
    config = yaml.safe_load(CONFIG_TEMPLATE.read_text(encoding="utf-8"))
    items = config["bundles"]["items"]
    item = next(entry for entry in items if entry["id"] == BUNDLE_ID)
    item_config = item["config"]

    assert item["singleton"] is False
    assert config["bundles"]["default_bundle_id"] == BUNDLE_ID

    as_provider = item_config["surfaces"]["as_provider"]
    assert as_provider["bundle"]["default_chat"] is True
    for forbidden in ("api", "mcp", "widget"):
        assert forbidden not in as_provider

    # Two agents, each with its own model picker on its own role.
    as_consumer = item_config["surfaces"]["as_consumer"]
    assert as_consumer["default_agent"] == "lg-solution"
    agents = as_consumer["agents"]
    assert set(agents.keys()) == {"lg-solution", "lg-react"}

    sol = agents["lg-solution"]
    assert sol["capability_provider"] == "simple_model_pick"
    assert sol["capabilities"]["models"]["role"] == SOLUTION_ROLE
    sol_supported = [row["model"] for row in sol["capabilities"]["models"]["supported"]]
    assert sol["capabilities"]["models"]["default"] in sol_supported

    pre = agents["lg-react"]
    assert pre["capability_provider"] == "simple_model_pick"
    assert pre["capabilities"]["models"]["role"] == PREBUILT_ROLE
    pre_supported = [row["model"] for row in pre["capabilities"]["models"]["supported"]]
    assert pre["capabilities"]["models"]["default"] in pre_supported

    # Both provider blocks use real routed ids (provider anthropic).
    for a in (sol, pre):
        assert all(row["provider"] == "anthropic" for row in a["capabilities"]["models"]["supported"])

    # lg-react tools are a per-agent CONNECTION LIST (the standard KDCube shape):
    # the admin ceiling the capabilities picker reads. The vendored plain tools are
    # declared; code_exec/MCP are commented-out (opt-in), so they don't appear here.
    assert isinstance(pre["tools"], list)
    tool_aliases = {str(c.get("alias") or c.get("name")) for c in pre["tools"] if isinstance(c, dict)}
    assert {"calc", "unit_convert", "kb_search"} <= tool_aliases
    assert all(str(c.get("kind") or "python") == "python" for c in pre["tools"])
    # lg-solution has no tool loop, so it carries no tools block.
    assert "tools" not in sol

    # Telegram webhook surface enabled + integration present.
    assert item_config["enabled"]["api"]["public.telegram_webhook.POST"] is True
    telegram = item_config["integrations"]["telegram.default"]
    assert telegram["provider"] == "telegram"
    assert telegram["enabled"] is False
    webhook = telegram["definition"]["webhook"]
    assert BUNDLE_ID in str(webhook["url"])
    assert "integration_id=telegram.default" in str(webhook["url"])

    # No static role_models mapping key: picks are applied at runtime.
    assert "role_models" not in item_config


def test_secrets_template_placeholders_only() -> None:
    secrets = yaml.safe_load(SECRETS_TEMPLATE.read_text(encoding="utf-8"))
    item = next(entry for entry in secrets["bundles"]["items"] if entry["id"] == BUNDLE_ID)
    block = item["secrets"]
    assert set(block.keys()) == {"OPENAI_API_KEY", "DATABASE_URL", "integrations"}
    for key in ("OPENAI_API_KEY", "DATABASE_URL"):
        value = block[key]
        assert str(value).startswith("<") and str(value).endswith(">")

    telegram = block["integrations"]["telegram.default"]["definition"]
    assert set(telegram.keys()) == {"bot_token", "webhook_secret"}
    for value in telegram.values():
        assert str(value).startswith("<") and str(value).endswith(">")


def test_required_package_files_present() -> None:
    required = [
        "README.md",
        "AGENTS.md",
        "release.yaml",
        "requirements.txt",
        "config/bundles.template.yaml",
        "config/bundles.secrets.template.yaml",
        "interface/README.md",
        "interface/ported-langgraph-agents.openapi.yaml",
        "platform/telegram.py",
        "platform/stream_solution.py",
        "platform/stream_prebuilt.py",
        "platform/tools_mcp.py",
        "solution/lg_solution/graph.py",
        "solution/lg_prebuilt/agent.py",
        "docs/integrations/admin-integrational-homework.md",
        "docs/README.md",
        "docs/arch/README.md",
        "docs/storage/README.md",
        "docs/journal/README.md",
        "docs/journal/journal.md",
    ]
    missing = [path for path in required if not (BUNDLE_ROOT / path).is_file()]
    assert not missing, f"missing app package declarations: {missing}"


def test_bundle_id_is_consistent_across_the_package() -> None:
    for rel in ("release.yaml", "interface/ported-langgraph-agents.openapi.yaml", "README.md", "AGENTS.md"):
        text = (BUNDLE_ROOT / rel).read_text(encoding="utf-8")
        assert BUNDLE_ID in text, f"{rel} does not reference {BUNDLE_ID}"
