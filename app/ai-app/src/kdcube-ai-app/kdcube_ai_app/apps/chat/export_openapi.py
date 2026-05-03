from __future__ import annotations

import argparse
import importlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _safe_import_env() -> None:
    """Keep schema export from writing logs/secrets into local runtime paths."""
    tmp_dir = tempfile.gettempdir()
    os.environ.setdefault("KDCUBE_CONFIG_DIR", str(Path(tmp_dir) / "kdcube-openapi-no-env"))
    os.environ.setdefault("LOG_DIR", tmp_dir)
    os.environ.setdefault("LOG_FILE_PREFIX", "kdcube-openapi-export")
    os.environ.setdefault("GATEWAY_COMPONENT", "openapi")


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "operation"


def _dedupe_operation_ids(schema: dict[str, Any]) -> None:
    seen: dict[str, int] = {}
    paths = schema.get("paths") if isinstance(schema.get("paths"), dict) else {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            op_id = str(operation.get("operationId") or f"{method}_{path}")
            if op_id not in seen:
                seen[op_id] = 1
                continue
            seen[op_id] += 1
            operation["operationId"] = f"{op_id}_{_slug(method)}_{_slug(path)}"


def _annotate(schema: dict[str, Any], *, component: str, source_module: str, notes: list[str]) -> None:
    schema.setdefault("servers", [{"url": "/", "description": f"{component} service root"}])
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes.setdefault(
        "BearerAuth",
        {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Gateway bearer token, when the service route requires platform authentication.",
        },
    )
    security_schemes.setdefault(
        "IdTokenHeader",
        {
            "type": "apiKey",
            "in": "header",
            "name": "X-ID-Token",
            "description": "Platform id-token header. The configured runtime header name may differ.",
        },
    )
    schema["x-kdcube-openapi-source"] = {
        "component": component,
        "source_module": source_module,
        "generated_from_fastapi_app": True,
        "notes": notes,
    }


def _ensure_schema(schema: dict[str, Any], name: str, value: dict[str, Any]) -> None:
    schema.setdefault("components", {}).setdefault("schemas", {}).setdefault(name, value)


def _patch_dynamic_bundle_routes(schema: dict[str, Any]) -> None:
    _ensure_schema(
        schema,
        "BundleOperationPostEnvelope",
        {
            "type": "object",
            "required": ["data"],
            "properties": {
                "data": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Operation parameters. The processor passes this object as keyword args to the decorated bundle API method.",
                }
            },
            "additionalProperties": False,
            "description": "Generic KDCube bundle operation POST envelope.",
        },
    )
    _ensure_schema(
        schema,
        "BundleOperationResponseEnvelope",
        {
            "type": "object",
            "required": ["status", "tenant", "project", "bundle_id"],
            "properties": {
                "status": {"type": "string", "enum": ["ok"]},
                "tenant": {"type": "string"},
                "project": {"type": "string"},
                "bundle_id": {"type": "string"},
            },
            "additionalProperties": True,
            "description": "Platform wrapper. The actual bundle result is returned under a property named exactly like the operation alias.",
        },
    )
    paths = schema.get("paths") if isinstance(schema.get("paths"), dict) else {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        is_operation_dispatch = (
            "/operations/{operation}" in path
            or "/public/{operation}" in path
        )
        if not is_operation_dispatch:
            continue
        for method in ("post", "get"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation["description"] = "\n\n".join(
                item
                for item in (
                    str(operation.get("description") or "").strip(),
                    "Dynamic bundle operation dispatch. POST requests must use `{ \"data\": { ... } }`; GET requests pass operation parameters as query params. The response is the KDCube platform wrapper, with the operation result under the operation alias key.",
                )
                if item
            )
            operation.setdefault("responses", {}).setdefault("200", {}).setdefault("content", {}).setdefault(
                "application/json", {}
            )["schema"] = {"$ref": "#/components/schemas/BundleOperationResponseEnvelope"}
            if method == "post":
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/BundleOperationPostEnvelope"}
                        }
                    },
                }


def _load_ingress_app():
    source_module = "kdcube_ai_app.apps.chat.ingress.web_app"
    mod = importlib.import_module(source_module)
    app = mod.app

    # In production this router is mounted during lifespan after gateway/Redis
    # startup. The static OpenAPI export mounts it without starting those
    # services so the contract still includes the public SSE controller.
    if not any(getattr(route, "path", "") == "/sse/chat" for route in app.routes):
        from kdcube_ai_app.apps.chat.ingress.sse.chat import create_sse_router

        sse_router = create_sse_router(
            app=app,
            gateway_adapter=object(),
            chat_queue_manager=object(),
            instance_id=os.environ.get("INSTANCE_ID", "openapi-ingress"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        )
        sse_router.state = app.state
        app.include_router(sse_router, prefix="/sse", tags=["SSE"])
        app.openapi_schema = None
    return app, source_module, [
        "Import-time routers are mounted from ingress/web_app.py.",
        "SSE routes are mounted during lifespan in production and are explicitly mounted by this exporter for documentation.",
        "Socket.IO is an ASGI mount and is documented separately in ingress/socketio.asyncapi.yaml.",
    ]


def _load_proc_app():
    source_module = "kdcube_ai_app.apps.chat.proc.web_app"
    mod = importlib.import_module(source_module)
    return mod.app, source_module, [
        "Import-time routers are mounted from proc/web_app.py.",
        "Bundle-specific decorated APIs are dynamic; this schema documents the processor gateway routes that dispatch them.",
        "Dynamic bundle operation request/response shapes live in each bundle interface contract when available.",
    ]


def build_schema(component: str) -> dict[str, Any]:
    _safe_import_env()
    if component == "ingress":
        app, source_module, notes = _load_ingress_app()
    elif component == "proc":
        app, source_module, notes = _load_proc_app()
    else:
        raise ValueError(f"Unknown component: {component}")

    schema = app.openapi()
    _dedupe_operation_ids(schema)
    if component == "proc":
        _patch_dynamic_bundle_routes(schema)
    _annotate(schema, component=component, source_module=source_module, notes=notes)
    return schema


def write_schema(component: str, output: Path) -> None:
    schema = build_schema(component)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(schema, sort_keys=False, allow_unicode=False, width=120),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Export KDCube chat FastAPI OpenAPI schemas.")
    parser.add_argument("component", choices=("ingress", "proc"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_schema(args.component, args.output)


if __name__ == "__main__":
    main()
