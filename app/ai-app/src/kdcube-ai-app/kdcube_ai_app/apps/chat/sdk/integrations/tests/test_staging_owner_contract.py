"""Caller contract: staging ownership cannot be omitted by an integration."""
import ast
from pathlib import Path


def test_every_staged_file_consumer_supplies_an_explicit_owner():
    sdk = Path(__file__).resolve().parents[2]
    calls = []
    for root in (sdk / "integrations", sdk / "examples" / "bundles"):
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            aliases = {}
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if str(node.module).endswith("integrations.file_staging"):
                        aliases.update({a.asname or a.name: a.name for a in node.names})
                    elif str(node.module).endswith("integrations"):
                        modules.update(a.asname or a.name for a in node.names if a.name == "file_staging")
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = aliases.get(node.func.id) if isinstance(node.func, ast.Name) else None
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in modules:
                    name = node.func.attr
                if name not in {"load_staged", "save_staged", "delete_staged"}:
                    continue
                owner = next((k.value for k in node.keywords if k.arg == "owner"), None)
                assert owner is not None, f"{path.relative_to(sdk)}:{node.lineno} omits staging owner"
                if isinstance(owner, ast.Constant) and owner.value is None:
                    if name == "load_staged":
                        assert path.name == "provider_fetch.py"
                    else:
                        assert name == "save_staged" and path.name == "entrypoint.py"
                        route = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "integration_file_upload")
                        assert route.lineno <= node.lineno <= route.end_lineno
                calls.append((path, name))
    assert any(name == "load_staged" for _, name in calls)
    assert any(name == "delete_staged" for _, name in calls)
