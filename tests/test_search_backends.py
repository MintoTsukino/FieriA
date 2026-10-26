import json

import pytest

import llm
import memory_tools
import websearch


def config(backend="auto", provider="gemini"):
    return {"provider": provider, "web_search": True, "web_search_backend": backend,
            "providers": {
                "gemini": {"type": "gemini", "model": "gemini-3.1-flash", "env_key": "GEMINI_API_KEY"},
                "other": {"type": "openai_compat", "model": "other"},
            }}


def grounded_response():
    return {"candidates": [{
        "content": {"parts": [{"text": "private thought", "thought": True}, {"text": "検索した答え"}]},
        "groundingMetadata": {
            "groundingChunks": [{"web": {"title": "公開の出典", "uri": "https://example.org/source"}}],
            "webSearchQueries": ["Geminiが組み立てた公開検索語"],
        },
    }]}


@pytest.mark.parametrize("backend,provider,expected", [
    ("auto", "gemini", "Gemini検索"), ("auto", "other", "アプリ内蔵検索"),
    ("builtin", "gemini", "アプリ内蔵検索"), ("gemini", "other", "Gemini検索"),
])
def test_backend_selection(backend, provider, expected):
    plan = websearch.make_search_plan(config(backend, provider), {"GEMINI_API_KEY": "dummy"})
    assert plan.label.startswith(expected)
    assert "dummy" not in repr(plan)


def test_builtin_does_not_require_gemini_key(monkeypatch):
    monkeypatch.setattr(websearch, "search_text", lambda query: {"ok": True, "results": [], "query": query})
    assert websearch.make_search_plan(config("builtin"), {}).run("q")["query"] == "q"


def test_auto_uses_effective_bound_gemini_model():
    cfg = config()
    cfg["providers"]["bound"] = {"type": "gemini", "model": "gemini-bound", "env_key": "BOUND_KEY"}
    cfg["provider"] = "bound"
    plan = websearch.make_search_plan(cfg, {"BOUND_KEY": "dummy"})
    assert "gemini-bound" in plan.label


def test_gemini_missing_key_or_invalid_backend_fails_closed():
    with pytest.raises(ValueError, match="APIキー"):
        websearch.make_search_plan(config(), {})
    with pytest.raises(ValueError, match="不正"):
        websearch.make_search_plan(config("unknown"), {})


@pytest.mark.parametrize("approved", [None, False, True])
def test_gemini_query_is_reviewed_before_any_network_and_contains_no_history(monkeypatch, approved):
    posted, requests = [], []
    cfg = {"llm": config(), "private_history": "PRIVATE_HISTORY", "private_memory": "PRIVATE_MEMORY"}
    def post(url, payload, **kwargs):
        posted.append(payload)
        assert payload["tools"] == [{"google_search": {}}]
        assert payload["contents"] == [{"role": "user", "parts": [{"text": "approved public request"}]}]
        assert "PRIVATE_HISTORY" not in json.dumps(payload)
        assert "PRIVATE_MEMORY" not in json.dumps(payload)
        assert "temperature" not in payload["generationConfig"]
        return grounded_response()
    monkeypatch.setattr(llm, "_post_json", post)
    def approve(request):
        requests.append(request)
        assert request["preview"] == "approved public request"
        assert "Gemini検索" in request["description"]
        assert "実際の検索語を組み立て" in request["description"]
        return approved
    result = memory_tools.execute("unused", {"tool": "web_search", "query": "approved public request"}, cfg,
                                  approve=approve if approved is not None else None,
                                  search_plan_factory=lambda: websearch.make_search_plan(
                                      cfg["llm"], {"GEMINI_API_KEY": "dummy"}))
    assert result["ok"] is (approved is True)
    assert len(posted) == (1 if approved is True else 0)
    if approved:
        assert "https://example.org/source" in result["detail"]
        assert "Geminiが組み立てた公開検索語" in result["detail"]
        assert "private thought" not in result["detail"]


def test_gemini_failure_does_not_leak_key_or_switch_to_unapproved_backend(monkeypatch):
    def fail(url, payload):
        raise RuntimeError("dummy-sensitive-key-in-error")
    monkeypatch.setattr(llm, "_post_json", fail)
    monkeypatch.setattr(websearch, "search_text", lambda query: pytest.fail("Unexpected fallback"))
    plan = websearch.make_search_plan(config(), {"GEMINI_API_KEY": "dummy-sensitive-key-in-error"})
    result = plan.run("public query")
    assert not result["ok"]
    assert "dummy-sensitive-key" not in result["detail"]


def test_gemini_without_grounding_is_not_reported_as_successful_search(monkeypatch):
    monkeypatch.setattr(llm, "_post_json", lambda *args, **kwargs: {
        "candidates": [{"content": {"parts": [{"text": "検索したつもりの答え"}]}}]})
    plan = websearch.make_search_plan(config(), {"GEMINI_API_KEY": "dummy"})
    assert not plan.run("q")["ok"]


def test_search_plan_keeps_the_reviewed_destination_even_if_settings_change(monkeypatch):
    cfg = config()
    posted = []
    monkeypatch.setattr(llm, "_post_json", lambda url, payload: posted.append(url) or grounded_response())
    plan = websearch.make_search_plan(cfg, {"GEMINI_API_KEY": "dummy"})
    cfg["providers"]["gemini"]["model"] = "different-model"
    assert plan.run("q")["ok"]
    assert "gemini-3.1-flash:generateContent" in posted[0]


def test_gui_uses_soul_effective_provider_for_search_plan(monkeypatch):
    import gui
    bridge = gui.Bridge()
    bridge._cfg = {"active_soul": "s", "llm": config(provider="other"),
                   "soul_llm": {"s": {"provider": "gemini"}}}
    bridge._env = {"GEMINI_API_KEY": "dummy"}
    assert bridge._make_search_plan().label.startswith("Gemini検索")
    assert bridge._cfg["llm"]["provider"] == "other"
