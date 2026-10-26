import os

import pytest

import memory_tools as mt
import tool_permissions as permissions
import workspace


def _cfg(tmp_path):
    return {"workspace_dir": str(tmp_path), "llm": {"web_search": True}}


@pytest.mark.parametrize("tool", ["write_doc", "append_doc"])
@pytest.mark.parametrize("approve", [None, lambda request: False, lambda request: "yes"])
def test_document_mutations_fail_closed(tmp_path, tool, approve):
    path = tmp_path / "draft.md"
    path.write_text("original", encoding="utf-8")
    result = mt.execute("unused", {"tool": tool, "path": "draft.md", "content": "changed"},
                        _cfg(tmp_path), approve=approve)
    assert not result["ok"]
    assert path.read_text(encoding="utf-8") == "original"
    assert not (tmp_path / "draft.md.bak").exists()


@pytest.mark.parametrize("tool", ["write_doc", "append_doc"])
def test_approved_write_shows_diff_and_applies_exact_content(tmp_path, tool):
    path = tmp_path / "draft.md"
    path.write_text("old\n", encoding="utf-8")
    seen = []
    def approve(request):
        seen.append(request)
        assert request["kind"] == "workspace_write"
        assert "+new" in request["preview"]
        request["preview"] = "tampered UI data"
        return True
    result = mt.execute("unused", {"tool": tool, "path": "draft.md", "content": "new\n"},
                        _cfg(tmp_path), approve=approve)
    assert result["ok"] and len(seen) == 1
    assert path.read_text(encoding="utf-8") == ("old\nnew\n" if tool == "append_doc" else "new\n")


def test_changed_document_during_confirmation_is_not_overwritten(tmp_path):
    path = tmp_path / "draft.md"
    path.write_text("old", encoding="utf-8")
    def approve(request):
        path.write_text("human edit", encoding="utf-8")
        return True
    result = mt.execute("unused", {"tool": "write_doc", "path": "draft.md", "content": "AI edit"},
                        _cfg(tmp_path), approve=approve)
    assert not result["ok"] and "確認中" in result["detail"]
    assert path.read_text(encoding="utf-8") == "human edit"


def test_new_file_created_during_confirmation_is_not_overwritten(tmp_path):
    def approve(request):
        (tmp_path / "draft.md").write_text("human creation", encoding="utf-8")
        return True
    result = mt.execute("unused", {"tool": "write_doc", "path": "draft.md", "content": "AI edit"},
                        _cfg(tmp_path), approve=approve)
    assert not result["ok"]
    assert (tmp_path / "draft.md").read_text() == "human creation"


def test_oversized_diff_is_denied_without_partial_preview(tmp_path):
    def approve(request):
        pytest.fail("Must not ask for approval with truncated content")
    result = mt.execute("unused", {"tool": "write_doc", "path": "draft.md", "content": "x" * 100_001},
                        _cfg(tmp_path), approve=approve)
    assert not result["ok"] and not (tmp_path / "draft.md").exists()


@pytest.mark.parametrize("enabled,approve", [(False, lambda request: True), (True, None),
                                           (True, lambda request: False)])
def test_search_never_transmits_without_setting_and_approval(monkeypatch, enabled, approve):
    monkeypatch.setattr(mt.websearch, "search_text", lambda query: pytest.fail("Network called"))
    result = mt.execute("unused", {"tool": "web_search", "query": "private draft text"},
                        {"llm": {"web_search": enabled}}, approve=approve)
    assert not result["ok"]


def test_search_sends_only_the_reviewed_query(monkeypatch):
    sent = []
    def search(query):
        sent.append(query)
        return {"ok": True, "results": []}
    monkeypatch.setattr(mt.websearch, "search_text", search)
    def approve(request):
        assert request["preview"] == "public query"
        request["preview"] = "different query"
        return True
    assert mt.execute("unused", {"tool": "web_search", "query": " public query "},
                      {"llm": {"web_search": True}}, approve=approve)["ok"]
    assert sent == ["public query"]


def test_search_setting_rechecked_after_approval(monkeypatch):
    cfg = {"llm": {"web_search": True}}
    def approve(request):
        cfg["llm"]["web_search"] = False
        return True
    monkeypatch.setattr(mt.websearch, "search_text", lambda query: pytest.fail("Network called"))
    assert not mt.execute("unused", {"tool": "web_search", "query": "q"}, cfg, approve=approve)["ok"]


def test_broker_timeout_and_old_id_fail_closed():
    broker = permissions.ApprovalBroker()
    shown, hidden = [], []
    assert broker.request({"preview": "x"}, shown.append, hidden.append, timeout=0) is False
    assert hidden == [shown[0]["id"]]
    assert broker.resolve(shown[0]["id"], True) is False


def test_broker_single_use_and_cancel():
    broker = permissions.ApprovalBroker()
    def show(payload):
        assert broker.resolve("wrong-id", True) is False
        assert broker.resolve(payload["id"], False) is True
        assert broker.resolve(payload["id"], True) is False
    assert broker.request({}, show, lambda request_id: None) is False
    assert broker.request({}, lambda payload: broker.cancel_all(), lambda request_id: None) is False


def test_broker_show_failure_denies():
    broker = permissions.ApprovalBroker()
    def show(payload):
        raise RuntimeError("window unavailable")
    assert broker.request({}, show, lambda request_id: None) is False


def _junction(link, target):
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Cannot create test junction: {error}")


def test_junction_escape_rejected_for_all_workspace_access(tmp_path):
    base, outside = tmp_path / "work", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (outside / "note.md").write_text("DUMMY_OUTSIDE", encoding="utf-8")
    (outside / "note.pdf").write_bytes(b"DUMMY_PDF")
    link = base / "linked"
    _junction(link, outside)
    try:
        for action in [lambda: workspace.read_doc(str(base), "linked/note.md"),
                       lambda: workspace.write_doc(str(base), "linked/note.md", "changed"),
                       lambda: workspace.append_doc(str(base), "linked/note.md", "changed"),
                       lambda: workspace.read_pdf_bytes(str(base), "linked/note.pdf")]:
            with pytest.raises(ValueError):
                action()
        assert workspace.list_docs(str(base)) == []
        assert workspace.search_workspace(str(base), "DUMMY_OUTSIDE") == []
        assert (outside / "note.md").read_text() == "DUMMY_OUTSIDE"
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


def test_hard_link_and_backup_escape_rejected(tmp_path):
    base, outside = tmp_path / "work", tmp_path / "outside.md"
    base.mkdir()
    outside.write_text("DUMMY_OUTSIDE")
    try:
        os.link(outside, base / "alias.md")
        os.link(outside, base / "draft.md.bak")
    except OSError as error:
        pytest.skip(str(error))
    (base / "draft.md").write_text("old")
    with pytest.raises(ValueError):
        workspace.read_doc(str(base), "alias.md")
    with pytest.raises(ValueError):
        workspace.write_doc(str(base), "draft.md", "new")
    assert outside.read_text() == "DUMMY_OUTSIDE"
    assert (base / "draft.md").read_text() == "old"


def test_rejected_action_is_not_reprompted_in_same_turn():
    from engine import Engine
    called = []
    engine = Engine({}, None, "unused", approve_tool=lambda request: called.append(request) or False)
    request = {"kind": "web_search", "preview": "q"}
    assert engine._approve_tool(request) is False
    assert engine._approve_tool(request) is False
    assert len(called) == 1


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
def test_engine_write_approval_on_both_tool_paths(tmp_path, native, allowed):
    import json
    import soul
    from engine import Engine
    call = {"tool": "write_doc", "path": "draft.md", "content": "reviewed\n"}
    class FakeLLM:
        supports_native_tools = native
        count = 0
        def chat(self, messages, max_tokens=None):
            self.count += 1
            return ("```fieria-tool\n" + json.dumps(call) + "\n```"
                    if self.count == 1 else "操作の結果を受け取りました")
        def chat_tools(self, messages, tools, max_tokens=None):
            self.count += 1
            return {"text": "", "tool_calls": [
                {"id": "call-1", "name": "write_doc", "arguments": call}
            ]} if self.count == 1 else {"text": "操作の結果を受け取りました", "tool_calls": []}
    sid = soul.create_soul("確認テスト")
    seen = []
    engine = Engine(_cfg(tmp_path), FakeLLM(), sid,
                    approve_tool=lambda request: seen.append(request) or allowed)
    result = engine.process_turn("テスト文書を書いて")
    assert len(seen) == 1
    assert result["operations"][0]["ok"] is allowed
    assert (tmp_path / "draft.md").exists() is allowed


@pytest.mark.parametrize("ptype", ["gemini", "openai_codex_oauth", "xai_oauth"])
def test_provider_native_search_cannot_bypass_approval(ptype):
    import llm
    cfg = {"provider": "p", "web_search": True,
           "providers": {"p": {"type": ptype, "model": "test"}}}
    provider = llm.create_llm(cfg, {})
    assert provider.web_search is False
    assert "web_search" in {item["function"]["name"]
                            for item in mt.build_native_tools({"llm": cfg})}


@pytest.mark.parametrize("tool", ["write_doc", "append_doc"])
def test_auto_document_write_skips_dialog_but_keeps_file_checks(tmp_path, tool):
    path = tmp_path / "draft.md"
    path.write_text("old\n", encoding="utf-8")
    cfg = {**_cfg(tmp_path), "tool_approvals": {"workspace_write": "auto"}}
    def unexpected_dialog(request):
        pytest.fail("Automatic mode must not open an approval dialog")
    result = mt.execute("unused", {"tool": tool, "path": "draft.md", "content": "new\n"},
                        cfg, approve=unexpected_dialog)
    assert result["ok"]
    assert path.read_text(encoding="utf-8") == ("old\nnew\n" if tool == "append_doc" else "new\n")
    if tool == "write_doc":
        assert (tmp_path / "draft.md.bak").read_text(encoding="utf-8") == "old\n"
    assert not mt.execute("unused", {"tool": tool, "path": "../outside.md", "content": "x"},
                          cfg, approve=unexpected_dialog)["ok"]


@pytest.mark.parametrize("policy", [None, True, "auto", {}, {"workspace_write": True},
                                     {"workspace_write": "AUTO"}])
def test_only_explicit_auto_value_skips_confirmation(tmp_path, policy):
    result = mt.execute("unused", {"tool": "write_doc", "path": "draft.md", "content": "new"},
                        {**_cfg(tmp_path), "tool_approvals": policy})
    assert not result["ok"] and not (tmp_path / "draft.md").exists()


def test_auto_search_still_requires_web_search_enabled(monkeypatch):
    sent = []
    monkeypatch.setattr(mt.websearch, "search_text", lambda query: sent.append(query) or {"ok": True, "results": []})
    cfg = {"llm": {"web_search": True}, "tool_approvals": {"web_search": "auto"}}
    def unexpected_dialog(request):
        pytest.fail("Automatic search must not open a dialog")
    call = {"tool": "web_search", "query": "public query"}
    assert mt.execute("unused", call, cfg, approve=unexpected_dialog)["ok"]
    assert sent == ["public query"]
    cfg["llm"]["web_search"] = False
    assert not mt.execute("unused", call, cfg, approve=unexpected_dialog)["ok"]
    assert sent == ["public query"]


def test_auto_categories_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(mt.websearch, "search_text", lambda query: pytest.fail("Unexpected network"))
    cfg = {**_cfg(tmp_path), "tool_approvals": {"workspace_write": "auto"}}
    assert not mt.execute("unused", {"tool": "web_search", "query": "q"}, cfg)["ok"]
    cfg["tool_approvals"] = {"web_search": "auto"}
    assert not mt.execute("unused", {"tool": "write_doc", "path": "draft.md", "content": "x"}, cfg)["ok"]


@pytest.mark.parametrize("tool", ["write_doc", "append_doc", "web_search"])
def test_stop_request_is_respected_even_in_auto_mode(tmp_path, monkeypatch, tool):
    monkeypatch.setattr(mt.websearch, "search_text", lambda query: pytest.fail("Cancelled network call"))
    cfg = {**_cfg(tmp_path), "tool_approvals": {"workspace_write": "auto", "web_search": "auto"}}
    call = {"tool": tool, "path": "draft.md", "content": "new", "query": "q"}
    assert not mt.execute("unused", call, cfg, is_cancelled=lambda: True)["ok"]
    assert not (tmp_path / "draft.md").exists()


def test_model_arguments_cannot_enable_automatic_execution(tmp_path):
    call = {"tool": "write_doc", "path": "draft.md", "content": "new",
            "tool_approvals": {"workspace_write": "auto"}, "approve": True}
    assert not mt.execute("unused", call, _cfg(tmp_path))["ok"]


def test_gui_saves_independent_auto_modes_and_rejects_invalid_modes(monkeypatch):
    import gui
    bridge = gui.Bridge()
    monkeypatch.setattr(bridge, "_ensure_engine", lambda: None)
    monkeypatch.setattr(gui.config_mod, "save_config", lambda cfg: None)
    result = bridge.save_settings({"tool_approvals": {"workspace_write": "auto", "web_search": "ask"}})
    assert result["tool_approvals"] == {"workspace_write": "auto", "web_search": "ask"}
    result = bridge.save_settings({"tool_approvals": {"web_search": "auto"}})
    assert result["tool_approvals"] == {"workspace_write": "auto", "web_search": "auto"}
    result = bridge.save_settings({"tool_approvals": {"workspace_write": True, "web_search": "yes"}})
    assert result["tool_approvals"] == {"workspace_write": "ask", "web_search": "ask"}


def test_prompt_explains_auto_mode_without_overriding_search_off():
    import prompt
    import soul
    sid = soul.create_soul("自動設定テスト")
    cfg = {"tool_approvals": {"workspace_write": "auto", "web_search": "auto"},
           "llm": {"web_search": False}}
    text = prompt.build_system_text(cfg, sid)
    assert "作業文書の作成・上書き・追記: 確認なしで自動実行" in text
    assert "Web検索: 無効。実行しない" in text
    cfg["llm"]["web_search"] = True
    assert "Web検索: 確認なしで自動実行" in prompt.build_system_text(cfg, sid)
