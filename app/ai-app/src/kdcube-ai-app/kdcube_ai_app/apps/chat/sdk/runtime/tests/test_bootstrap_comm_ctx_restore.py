# SPDX-License-Identifier: MIT

"""
Regression tests: bootstrap_bind_all and bootstrap_from_spec must restore
BUNDLE_ID_CV from spec.contextvars["comm_ctx"] so that get_secret("b:...")
resolves bundle-scoped keys inside the subprocess/iso bootstrap path.

Root cause of the original bug: both bootstrap functions explicitly restored
run_ctx and accounting from spec.contextvars but never called
comm_ctx.restore_ctxvars(), leaving BUNDLE_ID_CV as None in the subprocess.
Result: get_secret("b:services.brave.api_key") could not resolve the bundle
context and fell through to the global key (which was unset), triggering DDG
fallback instead of the Brave search backend.
"""

import pytest

import kdcube_ai_app.apps.chat.sdk.runtime.bootstrap as _bootstrap_mod
from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx as _comm_ctx
from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.runtime.portable_spec import PortableSpec, ModelConfigSpec


_BUNDLE_ID = "kdcube.copilot@2026-04-03-19-05"


def _spec_json(bundle_id: str = _BUNDLE_ID) -> str:
    return PortableSpec(
        model_config=ModelConfigSpec(),
        contextvars={
            "comm_ctx": {
                "BUNDLE_ID": bundle_id,
                "REQUEST_CONTEXT": None,
                "BUNDLE_CALL_CONTEXT": {},
                "COMM_PRESENT": False,
            },
        },
    ).to_json()


def _spec_json_no_comm_ctx() -> str:
    return PortableSpec(
        model_config=ModelConfigSpec(),
        contextvars=None,
    ).to_json()


def _patch_heavy(monkeypatch):
    """
    Neutralize the model-service creation step (which requires live infra) so
    that bootstrap can complete without a real secrets/config provider.
    run_ctx and accounting restores are already guarded by try/except, but
    create_workflow_config / ModelServiceBase re-raise on failure, so those
    must be stubbed.
    run_ctx.restore_ctxvars_from_env is also stubbed to suppress "Failed to set
    OUTDIR_CV / WORKDIR_CV" noise — OUTPUT_DIR and WORKDIR are not set in tests.
    """
    from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx as _run_ctx
    from kdcube_ai_app.infra import accounting as _acct

    monkeypatch.setattr(_run_ctx, "restore_ctxvars_from_env", lambda: None, raising=False)
    monkeypatch.setattr(_acct, "restore_ctxvars", lambda snap, **kw: None, raising=False)

    sentinel = object()
    monkeypatch.setattr(_bootstrap_mod, "create_workflow_config", lambda req: sentinel)
    monkeypatch.setattr(_bootstrap_mod, "ModelServiceBase", lambda cfg: sentinel)
    monkeypatch.setattr(_bootstrap_mod, "_make_storage_backend_from_spec", lambda spec: None)
    monkeypatch.setattr(_bootstrap_mod, "make_registry", lambda spec: {})
    monkeypatch.setattr(_bootstrap_mod, "make_chat_comm", lambda spec: None)


class TestBootstrapBindAllCommmCtxRestore:
    def test_bundle_id_cv_is_set_after_bootstrap(self, monkeypatch):
        """BUNDLE_ID_CV must be populated from spec.contextvars["comm_ctx"] after bootstrap."""
        _patch_heavy(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)

        _bootstrap_mod.bootstrap_bind_all(
            _spec_json(), module_names=[], bootstrap_env=False
        )

        assert _comm_ctx.BUNDLE_ID_CV.get() == _BUNDLE_ID

    def test_b_prefix_key_normalizes_to_bundle_scoped_path(self, monkeypatch):
        """After bootstrap, get_secret('b:services.token') must expand using BUNDLE_ID_CV."""
        _patch_heavy(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)

        _bootstrap_mod.bootstrap_bind_all(
            _spec_json(), module_names=[], bootstrap_env=False
        )

        resolved = sdk_config._normalize_secret_lookup_key("b:services.token")
        assert resolved == f"bundles.{_BUNDLE_ID}.secrets.services.token"

    @pytest.mark.asyncio
    async def test_get_secret_b_returns_bundle_value(self, monkeypatch):
        """get_secret('b:services.token') returns the value stored under the bundle-scoped key."""
        _patch_heavy(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)

        _bootstrap_mod.bootstrap_bind_all(
            _spec_json(), module_names=[], bootstrap_env=False
        )

        expected_key = f"bundles.{_BUNDLE_ID}.secrets.services.token"

        class _MockSettings:
            def __getattr__(self, name):
                return None

        class _MockSecretsManager:
            async def get_secret(self, key):
                return "sk-bundle-tok" if key == expected_key else None

        monkeypatch.setattr(sdk_config, "get_settings", lambda: _MockSettings())
        monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _MockSecretsManager())

        assert await sdk_config.get_secret("b:services.token") == "sk-bundle-tok"

    def test_no_comm_ctx_in_spec_does_not_crash(self, monkeypatch):
        """bootstrap_bind_all must not crash when spec.contextvars is absent."""
        _patch_heavy(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)

        _bootstrap_mod.bootstrap_bind_all(
            _spec_json_no_comm_ctx(), module_names=[], bootstrap_env=False
        )

        assert _comm_ctx.BUNDLE_ID_CV.get() is None


class TestBootstrapFromSpecCommmCtxRestore:
    def test_bundle_id_cv_is_set_after_bootstrap_from_spec(self, monkeypatch):
        """bootstrap_from_spec must also restore BUNDLE_ID_CV from spec.contextvars["comm_ctx"]."""
        _patch_heavy(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)

        import types, sys
        fake_tool_module = types.ModuleType("fake_tool_module")
        sys.modules["fake_tool_module"] = fake_tool_module

        try:
            _bootstrap_mod.bootstrap_from_spec(
                _spec_json(), tool_module=fake_tool_module, bootstrap_env=False
            )
        except Exception:
            pass  # model service or bind step may fail; BUNDLE_ID_CV is set before those

        assert _comm_ctx.BUNDLE_ID_CV.get() == _BUNDLE_ID

    def test_b_prefix_key_normalizes_after_bootstrap_from_spec(self, monkeypatch):
        """After bootstrap_from_spec, get_secret('b:services.token') must use BUNDLE_ID_CV."""
        _patch_heavy(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)

        import types, sys
        fake_tool_module = types.ModuleType("fake_tool_module2")
        sys.modules["fake_tool_module2"] = fake_tool_module

        try:
            _bootstrap_mod.bootstrap_from_spec(
                _spec_json(), tool_module=fake_tool_module, bootstrap_env=False
            )
        except Exception:
            pass

        resolved = sdk_config._normalize_secret_lookup_key("b:services.brave.api_key")
        assert resolved == f"bundles.{_BUNDLE_ID}.secrets.services.brave.api_key"


class TestBuildPortableSpecEnvPassthrough:
    """
    Hardening: build_portable_spec must include KDCUBE_BUNDLE_ID in env_passthrough
    so _resolve_current_bundle_id() has an env-var fallback even if comm_ctx
    restoration fails completely in the child bootstrap.
    """

    def _patch_snapshot(self, monkeypatch):
        """Stub out the config/settings/cv parts of build_portable_spec that need live infra."""
        from kdcube_ai_app.apps.chat.sdk.runtime import snapshot as snap_mod
        from kdcube_ai_app.apps.chat.sdk.runtime.portable_spec import ModelConfigSpec

        class _MockSettings:
            STORAGE_PATH = None
            def __getattr__(self, name):
                return None

        monkeypatch.setattr(snap_mod, "_config_to_model_config_spec",
                            lambda cfg: ModelConfigSpec())
        monkeypatch.setattr(snap_mod, "get_settings", lambda: _MockSettings())
        monkeypatch.setattr(snap_mod, "snapshot_all_contextvars", lambda: {"entries": []})

    def _make_stub_svc(self):
        from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase
        svc = object.__new__(ModelServiceBase)
        svc.config = object()
        return svc

    def test_bundle_id_included_in_env_passthrough_when_set(self, monkeypatch):
        """KDCUBE_BUNDLE_ID must appear in env_passthrough when BUNDLE_ID_CV is populated."""
        from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec

        self._patch_snapshot(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(_BUNDLE_ID)
        try:
            spec = build_portable_spec(svc=self._make_stub_svc(), chat_comm=None)
            assert spec.env_passthrough.get("KDCUBE_BUNDLE_ID") == _BUNDLE_ID
        finally:
            _comm_ctx.BUNDLE_ID_CV.set(None)

    def test_bundle_id_absent_from_env_passthrough_when_not_set(self, monkeypatch):
        """KDCUBE_BUNDLE_ID must not appear in env_passthrough when BUNDLE_ID_CV is None."""
        from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec

        self._patch_snapshot(monkeypatch)
        _comm_ctx.BUNDLE_ID_CV.set(None)
        spec = build_portable_spec(svc=self._make_stub_svc(), chat_comm=None)
        assert "KDCUBE_BUNDLE_ID" not in spec.env_passthrough

    def test_env_var_fallback_resolves_bundle_id_when_cv_is_none(self, monkeypatch):
        """
        _resolve_current_bundle_id() must return the bundle ID from KDCUBE_BUNDLE_ID
        env var when BUNDLE_ID_CV is None — this is the fallback that apply_env()
        activates in the child process from env_passthrough.
        """
        _comm_ctx.BUNDLE_ID_CV.set(None)
        monkeypatch.setenv("KDCUBE_BUNDLE_ID", _BUNDLE_ID)

        resolved = sdk_config._resolve_current_bundle_id()
        assert resolved == _BUNDLE_ID
