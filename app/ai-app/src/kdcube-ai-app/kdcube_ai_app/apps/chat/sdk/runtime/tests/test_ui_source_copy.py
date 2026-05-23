from __future__ import annotations

import shutil

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


def test_ui_copy_ignores_generated_js_shadow_files(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()

    (src / "App.tsx").write_text("export default function App() { return null }\n", encoding="utf-8")
    (src / "App.js").write_text("stale generated jsx residue\n", encoding="utf-8")
    (src / "service.ts").write_text("export const value = 1\n", encoding="utf-8")
    (src / "service.js").write_text("stale generated service residue\n", encoding="utf-8")
    (src / "plain.js").write_text("export const plain = true\n", encoding="utf-8")

    shutil.copytree(src, dst, ignore=BaseEntrypoint._ui_copy_ignore_patterns())

    assert (dst / "App.tsx").exists()
    assert not (dst / "App.js").exists()
    assert (dst / "service.ts").exists()
    assert not (dst / "service.js").exists()
    assert (dst / "plain.js").exists()


def test_ui_signature_ignores_generated_js_shadow_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()

    (src / "App.tsx").write_text("export default function App() { return null }\n", encoding="utf-8")
    generated = src / "App.js"
    generated.write_text("first generated residue\n", encoding="utf-8")

    before = BaseEntrypoint._ui_source_signature(src)
    generated.write_text("changed generated residue\n", encoding="utf-8")
    after = BaseEntrypoint._ui_source_signature(src)

    assert after == before
