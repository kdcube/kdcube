from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import (
    _build_iso_injected_header,
    _build_iso_injected_header_step_artifacts,
)


def test_iso_header_uses_shared_dynamic_module_loader():
    header = _build_iso_injected_header(globals_src="", imports_src="")
    assert "from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_from_file" in header
    assert "load_dynamic_module_from_file(_dyn_name, _path)" in header


def test_iso_step_header_uses_shared_dynamic_module_loader():
    header = _build_iso_injected_header_step_artifacts(globals_src="", imports_src="")
    assert "from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_from_file" in header
    assert "load_dynamic_module_from_file(_dyn_name, _path)" in header
