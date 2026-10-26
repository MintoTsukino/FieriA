"""Official Anthropic adapter and real Engine integration; no live API or GUI."""
import copy
import io
import json
import urllib.error

import pytest

import anthropic_llm
import config
import engine
import llm
import soul
import websearch


KEY = "dummy-anthropic-private-key"
MESSAGES = [{"role": "system", "content": "SOUL"}, {"role": "user", "content": "こんにちは"}]
TOOLS = [{"type": "function", "function": {"name": "read_doc", "description": "文書を読む",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    # Bridge may choose the first existing SOUL and persist it. Keep this module
    # separate from the pre-existing tests that expect their initial empty home.
    import roles
    test_home = tmp_path / "home"
    monkeypatch.setattr(config, "HOME", str(test_home))
    monkeypatch.setattr(config, "CONFIG_PATH", str(test_home / "config.json"))
    monkeypatch.setattr(soul, "HOME", str(test_home))
    monkeypatch.setattr(soul, "SOULS_DIR", str(test_home / "souls"))
    monkeypatch.setattr(roles, "HOME", str(test_home))
    monkeypatch.setattr(roles, "ROLES_PATH", str(test_home / "roles.json"))


def info(model="claude-sonnet-5-5", ceiling=128000):
    return {"id": model, "max_tokens": ceiling, "lifecycle": "active", "capabilities": {
        "thinking": {"types": {"adaptive": {"supported": True}}},
        "effort": {level: {"supported": True} for level in ("low", "medium", "high", "xhigh", "max")},
    }}


def client(monkeypatch, maximum=2000, effort="medium", metadata=None):
    monkeypatch.setattr(llm, "_get_json", lambda *a, **k: copy.deepcopy(metadata or info()))
    return llm.AnthropicLLM({"model": "claude-sonnet-5-5"}, KEY, 0.8, maximum, effort)


def answer(text="返事", blocks=None, reason=None):
    return {"content": blocks if blocks is not None else [{"type": "text", "text": text}],
            "stop_reason": reason or ("tool_use" if any(b.get("type") == "tool_use" for b in blocks or []) else "end_turn")}


def capture(monkeypatch, response=None):
    posts = []
    def post(url, payload, headers=None, **kwargs):
        posts.append((url, copy.deepcopy(payload), headers))
        return copy.deepcopy(response or answer())
    monkeypatch.setattr(llm, "_post_json", post)
    return posts


def sse_events(body):
    events = [{"type": "message_start", "message": {"content": [], "stop_reason": None}}]
    for idx, original in enumerate(body["content"]):
        block = copy.deepcopy(original)
        deltas = []
        if block["type"] == "text":
            text = block.pop("text")
            block["text"] = ""
            deltas.append({"type": "text_delta", "text": text})
        elif block["type"] == "thinking":
            thinking, signature = block.pop("thinking"), block.pop("signature")
            block.update(thinking="", signature="")
            deltas.extend([{"type": "thinking_delta", "thinking": thinking},
                           {"type": "signature_delta", "signature": signature[:3]},
                           {"type": "signature_delta", "signature": signature[3:]}])
        elif block["type"] == "tool_use":
            args = json.dumps(block.pop("input"), ensure_ascii=False)
            block["input"] = {}
            middle = len(args) // 2
            deltas.extend([{"type": "input_json_delta", "partial_json": args[:middle]},
                           {"type": "input_json_delta", "partial_json": args[middle:]}])
        events.append({"type": "content_block_start", "index": idx, "content_block": block})
        events.extend({"type": "content_block_delta", "index": idx, "delta": d} for d in deltas)
        events.append({"type": "content_block_stop", "index": idx})
    events.extend([{"type": "message_delta", "delta": {"stop_reason": body["stop_reason"]}},
                   {"type": "message_stop"}])
    return events


def lines(events):
    for event in events:
        yield "event: " + event["type"] + "\n"
        yield "data: " + json.dumps(event, ensure_ascii=False) + "\n"
        yield "\n"


def stream_response(monkeypatch, events):
    captured = []
    def stream(url, payload, headers=None):
        captured.append((url, copy.deepcopy(payload), headers))
        yield from lines(events)
    monkeypatch.setattr(llm, "_post_sse_stream", stream)
    return captured


def test_official_payload_auth_and_no_unsupported_sampling(monkeypatch):
    api = client(monkeypatch)
    posts = capture(monkeypatch)
    assert api.chat(MESSAGES) == "返事"
    url, payload, headers = posts[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers == {"Authorization": "Bearer " + KEY, "anthropic-version": "2026-10-21"}
    assert payload["system"] == "SOUL"
    assert payload["messages"] == [{"role": "user", "content": [{"type": "text", "text": "こんにちは"}]}]
    assert payload["max_tokens"] == 2000
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k", "tools", "api_key"}.intersection(payload)
    assert KEY not in json.dumps(payload)


@pytest.mark.parametrize("instance_max,override,expected", [(0, None, 128000), (2000, 0, 128000),
    (0, 600, 600), (999999, None, 128000), (2000, None, 2000)])
def test_output_limit_mapping(monkeypatch, instance_max, override, expected):
    api = client(monkeypatch, maximum=instance_max)
    posts = capture(monkeypatch)
    api.chat(MESSAGES, max_tokens=override)
    assert posts[0][1]["max_tokens"] == expected


def test_unknown_model_limit_fails_before_message_request(monkeypatch):
    api = client(monkeypatch, maximum=0, metadata=info(ceiling=None))
    posts = capture(monkeypatch)
    with pytest.raises(RuntimeError, match="出力上限"):
        api.chat(MESSAGES)
    assert posts == []


def test_metadata_is_cached_and_alias_is_encoded(monkeypatch):
    api = client(monkeypatch)
    api.model = "future/model alias"
    gets = []
    monkeypatch.setattr(llm, "_get_json", lambda url, **k: gets.append(url) or info("resolved-model"))
    capture(monkeypatch)
    api.chat(MESSAGES)
    api.chat(MESSAGES)
    assert gets == ["https://api.anthropic.com/v1/models/future%2Fmodel%20alias"]


@pytest.mark.parametrize("effort", ["", "low", "medium", "high", "xhigh", "max"])
def test_effort_and_adaptive_follow_capabilities(monkeypatch, effort):
    api = client(monkeypatch, effort=effort)
    posts = capture(monkeypatch)
    api.chat(MESSAGES)
    assert posts[0][1].get("output_config") == ({"effort": effort} if effort else None)
    api._models[api.model]["capabilities"] = {}
    api.chat(MESSAGES)
    assert "output_config" not in posts[1][1]
    assert "thinking" not in posts[1][1]


def test_models_pagination_metadata_and_retired_filter(monkeypatch):
    api = client(monkeypatch)
    pages = [{"data": [info(), {**info("old"), "lifecycle": "retired"}],
              "has_more": True, "last_id": "cursor /"},
             {"data": [info("haiku-future"), info()], "has_more": False}]
    urls = []
    monkeypatch.setattr(llm, "_get_json", lambda url, **k: urls.append(url) or pages.pop(0))
    assert api.list_models() == [api.model, "haiku-future"]
    assert "after_id=cursor+%2F" in urls[1]
    assert api._info()["max_tokens"] == 128000


def test_models_broken_pagination_fails(monkeypatch):
    api = client(monkeypatch)
    monkeypatch.setattr(llm, "_get_json", lambda *a, **k: {"data": [], "has_more": True, "last_id": "same"})
    with pytest.raises(RuntimeError, match="続き"):
        api.list_models()


def test_unset_key_never_reaches_network(monkeypatch):
    api = client(monkeypatch)
    api.api_key = ""
    monkeypatch.setattr(llm, "_get_json", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(llm, "_post_json", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(RuntimeError, match="APIキーが未設定"):
        api.chat(MESSAGES)
    assert api.health()[0] is False


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429, 500, 529])
def test_http_error_never_echoes_key_or_body(monkeypatch, status):
    api = client(monkeypatch)
    def post(*a, **k):
        raise urllib.error.HTTPError("https://example.invalid", status, KEY, {}, io.BytesIO(KEY.encode()))
    monkeypatch.setattr(llm, "_post_json", post)
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as caught:
        api.chat(MESSAGES)
    assert KEY not in str(caught.value)
    assert not isinstance(caught.value, llm.NativeToolsUnsupported)


def test_tool_schema_and_signed_content_round_trip(monkeypatch):
    api = client(monkeypatch)
    opaque = [{"type": "thinking", "thinking": "", "signature": "opaque-signature"},
              {"type": "redacted_thinking", "data": "opaque-data"},
              {"type": "tool_use", "id": "a", "name": "read_doc", "input": {"path": "a.md"}}]
    posts = capture(monkeypatch, answer(blocks=opaque))
    result = api.chat_tools(MESSAGES, TOOLS)
    assert result["tool_calls"] == [{"id": "a", "name": "read_doc", "arguments": {"path": "a.md"}}]
    assert result["text"] == ""
    continuation = MESSAGES + [{"role": "assistant", "content": None,
        "provider_content": result["provider_content"]},
        {"role": "tool", "tool_call_id": "a", "content": "error: denied", "is_error": True}]
    api.chat_tools(continuation, TOOLS)
    payload = posts[1][1]
    assert payload["tools"] == [{"name": "read_doc", "description": "文書を読む", "input_schema": TOOLS[0]["function"]["parameters"]}]
    assert payload["tool_choice"] == {"type": "auto"}
    assert payload["messages"][1]["content"] == opaque
    assert payload["messages"][2]["content"][0]["is_error"] is True
    payload["messages"][1]["content"][0]["signature"] = "changed"
    assert result["provider_content"]["anthropic"] == opaque


def test_multiple_tool_results_stay_first_in_user_message_and_keep_images(monkeypatch):
    api = client(monkeypatch)
    posts = capture(monkeypatch)
    calls = [{"id": name, "function": {"name": "read_doc", "arguments": "{}"}} for name in ("a", "b")]
    api.chat_tools(MESSAGES + [{"role": "assistant", "content": "調べる", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "a", "content": "PDF", "images": [{"mime": "image/jpeg", "b64": "AA=="}]},
        {"role": "tool", "tool_call_id": "b", "content": "文書"}], TOOLS)
    result_blocks = posts[0][1]["messages"][-1]["content"]
    assert [b["tool_use_id"] for b in result_blocks] == ["a", "b"]
    assert result_blocks[0]["content"][1]["type"] == "image"


@pytest.mark.parametrize("mime,kind", [("image/png", "image"), ("application/pdf", "document")])
def test_user_attachments(monkeypatch, mime, kind):
    api = client(monkeypatch)
    posts = capture(monkeypatch)
    api.chat([{"role": "user", "content": "見て", "images": [{"mime": mime, "b64": "AA=="}]}])
    assert posts[0][1]["messages"][0]["content"][1] == {"type": kind, "source": {
        "type": "base64", "media_type": mime, "data": "AA=="}}


def test_unsupported_attachment_fails_before_post(monkeypatch):
    api = client(monkeypatch)
    posts = capture(monkeypatch)
    with pytest.raises(ValueError, match="添付形式"):
        api.chat([{"role": "user", "content": "x", "images": [{"mime": "text/html", "b64": "AA=="}]}])
    assert posts == []


def test_stream_fragments_signatures_and_tools(monkeypatch):
    api = client(monkeypatch)
    opaque = [{"type": "thinking", "thinking": "do not display", "signature": "opaque-signature"},
              {"type": "text", "text": "読むね"},
              {"type": "tool_use", "id": "a", "name": "read_doc", "input": {"path": "日本語.md"}}]
    events = sse_events(answer(blocks=opaque))
    events.insert(1, {"type": "future_event"})
    posts = stream_response(monkeypatch, events)
    deltas = []
    result = api.chat_tools_stream(MESSAGES, TOOLS, on_delta=deltas.append)
    assert deltas == ["読むね"]
    assert result["provider_content"]["anthropic"] == opaque
    assert result["tool_calls"][0]["arguments"] == {"path": "日本語.md"}
    assert posts[0][1]["stream"] is True


def test_plain_chat_stream_is_incremental_and_hides_thinking(monkeypatch):
    api = client(monkeypatch)
    stream_response(monkeypatch, sse_events(answer(blocks=[
        {"type": "thinking", "thinking": "private", "signature": "sig"},
        {"type": "text", "text": "こんにちは"}])))
    assert list(api.chat_stream(MESSAGES)) == ["こんにちは"]


@pytest.mark.parametrize("failure", ["eof", "open_block", "bad_json", "error", "max_tokens"])
def test_bad_tool_streams_never_return_executable_calls(monkeypatch, failure):
    api = client(monkeypatch)
    events = sse_events(answer(blocks=[{"type": "tool_use", "id": "a", "name": "write_doc", "input": {"path": "a.md"}}]))
    if failure == "eof":
        events.pop()
    elif failure == "open_block":
        events = [e for e in events if e["type"] != "content_block_stop"]
    elif failure == "bad_json":
        events[2]["delta"]["partial_json"] = "broken"
    elif failure == "error":
        events[-1] = {"type": "error", "error": {"message": KEY}}
    else:
        events[-2]["delta"]["stop_reason"] = "max_tokens"
    stream_response(monkeypatch, events)
    with pytest.raises(RuntimeError) as caught:
        api.chat_tools_stream(MESSAGES, TOOLS)
    assert KEY not in str(caught.value)


def test_stop_while_only_thinking_closes_stream_without_calls(monkeypatch):
    api = client(monkeypatch)
    state = {"stop": False, "closed": False}
    def stream(*a, **k):
        try:
            yield from lines([{"type": "message_start", "message": {"content": []}}])
            state["stop"] = True
            yield from lines([{"type": "ping"}])
            pytest.fail("read after cancellation")
        finally:
            state["closed"] = True
    monkeypatch.setattr(llm, "_post_sse_stream", stream)
    result = api.chat_tools_stream(MESSAGES, TOOLS, should_stop=lambda: state["stop"])
    assert result["tool_calls"] == []
    assert state["closed"]


def test_sse_multiline_records_comments_and_missing_final_blank_line():
    records = [": keep alive\n", "event: ping\n", 'data: {"type":\n',
               'data: "ping"}\n', "\n", 'data: {"type":"message_stop"}\n']
    assert list(anthropic_llm._events(records)) == [{"type": "ping"}, {"type": "message_stop"}]


def test_stream_http_errors_are_redacted_and_not_tool_fallback(monkeypatch):
    api = client(monkeypatch)
    def stream(*a, **k):
        raise RuntimeError("HTTP 400: tools " + KEY)
        yield
    monkeypatch.setattr(llm, "_post_sse_stream", stream)
    with pytest.raises(RuntimeError, match="HTTP 400") as caught:
        api.chat_tools_stream(MESSAGES, TOOLS)
    assert KEY not in str(caught.value)
    assert not isinstance(caught.value, llm.NativeToolsUnsupported)


def test_engine_preserves_pdf_tool_attachments_in_native_exchange(monkeypatch):
    api = client(monkeypatch)
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["llm"]["provider"] = "anthropic"
    cfg["auto_recall"]["enabled"] = False
    sid = soul.create_soul("PDF往復", "SOUL")
    attachment = {"mime": "image/jpeg", "b64": "AA=="}
    monkeypatch.setattr(engine.memory_tools, "execute", lambda *a, **k: {
        "ok": True, "op": "read_pdf", "detail": "PDFを添付", "attachments": [attachment]})
    posts = []
    def post(url, payload, **kwargs):
        posts.append(payload)
        if len(posts) == 1:
            return answer(blocks=[{"type": "tool_use", "id": "pdf", "name": "read_pdf", "input": {"path": "a.pdf"}}])
        result = payload["messages"][-1]["content"][0]
        assert result["content"][1] == {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": "AA=="}}
        return answer("読めた")
    monkeypatch.setattr(llm, "_post_json", post)
    assert engine.Engine(cfg, api, sid).process_turn("PDFを読んで")["reply"] == "読めた"


def test_gui_model_list_uses_official_provider(monkeypatch):
    import gui
    bridge = gui.Bridge()
    bridge._env = {"ANTHROPIC_API_KEY": KEY}
    bridge._cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    gets = []
    monkeypatch.setattr(llm, "_get_json", lambda url, **kwargs: gets.append((url, kwargs)) or {
        "data": [info(), info("future-model")], "has_more": False})
    result = bridge.list_models("anthropic")
    assert result == {"ok": True, "models": ["claude-sonnet-5-5", "future-model"]}
    assert gets[0][0] == "https://api.anthropic.com/v1/models?limit=100"
    assert gets[0][1]["headers"]["Authorization"] == "Bearer " + KEY
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", ["allow", "deny", "auto"])
@pytest.mark.parametrize("action", ["write_doc", "web_search"])
def test_engine_permissions_and_gemini_search_with_real_adapter(monkeypatch, tmp_path, streaming, mode, action):
    api = client(monkeypatch)
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["llm"]["provider"] = "anthropic"
    cfg["llm"].update(web_search=True, web_search_backend="gemini")
    cfg["workspace_dir"] = str(tmp_path)
    cfg["auto_recall"]["enabled"] = False
    if mode == "auto":
        cfg["tool_approvals"] = {"workspace_write": "auto", "web_search": "auto"}
    sid = soul.create_soul("Claude接続テスト", "SOUL")
    args = {"path": "new.md", "content": "原稿"} if action == "write_doc" else {"query": "公開の検索依頼"}
    opaque = [{"type": "thinking", "thinking": "", "signature": "keep-signature"},
              {"type": "tool_use", "id": "a", "name": action, "input": args}]
    posted, gemini_posts, approvals = [], [], []
    def post(url, payload, **kwargs):
        if url.startswith("https://generativelanguage.googleapis.com"):
            gemini_posts.append(copy.deepcopy(payload))
            return {"candidates": [{"content": {"parts": [{"text": "検索結果"}]}, "groundingMetadata": {
                "groundingChunks": [{"web": {"title": "出典", "uri": "https://example.org/source"}}]}}]}
        posted.append(copy.deepcopy(payload))
        if len(posted) == 1:
            return answer(blocks=opaque)
        assert payload["messages"][-2]["content"] == opaque
        assert payload["messages"][-1]["content"][0]["tool_use_id"] == "a"
        assert payload["messages"][-1]["content"][0]["is_error"] is (mode == "deny")
        return answer("操作の結果を伝える")
    monkeypatch.setattr(llm, "_post_json", post)
    monkeypatch.setattr(llm, "_post_sse_stream", lambda url, payload, **kw: lines(sse_events(post(url, payload))))
    def approve(request):
        approvals.append(request)
        return mode == "allow"
    plan_factory = lambda: websearch.make_search_plan(cfg["llm"], {"GEMINI_API_KEY": "dummy-gemini-key"})
    eng = engine.Engine(cfg, api, sid, approve_tool=approve, search_plan_factory=plan_factory)
    result = eng.process_turn("実行して", on_delta=(lambda d: None) if streaming else None)
    assert result["reply"] == "操作の結果を伝える"
    assert len(posted) == 2
    assert len(approvals) == (0 if mode == "auto" else 1)
    assert result["operations"][0]["ok"] is (mode != "deny")
    assert (tmp_path / "new.md").exists() is (action == "write_doc" and mode != "deny")
    assert len(gemini_posts) == int(action == "web_search" and mode != "deny")
    if gemini_posts:
        assert gemini_posts[0]["contents"] == [{"role": "user", "parts": [{"text": "公開の検索依頼"}]}]
    assert all("provider_content" not in m and "tool_calls" not in m for m in eng.messages)
    assert "keep-signature" not in json.dumps(soul.read_today_log(sid))


def test_config_adds_anthropic_without_changing_current_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOME", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["llm"]["provider"] = "ollama"
    del cfg["llm"]["providers"]["anthropic"]
    config.save_config(cfg)
    loaded = config.load_config()
    assert loaded["llm"]["provider"] == "ollama"
    assert loaded["llm"]["providers"]["anthropic"]["reasoning_effort"] == "medium"
    loaded["llm"]["providers"]["anthropic"]["model"] = "my-model"
    config.save_config(loaded)
    assert config.load_config()["llm"]["providers"]["anthropic"]["model"] == "my-model"


def test_factory_and_gui_api_key_metadata(monkeypatch):
    import gui
    api = llm.create_llm({**config.DEFAULT_CONFIG["llm"], "provider": "anthropic", "web_search": True},
                         {"ANTHROPIC_API_KEY": KEY})
    assert isinstance(api, llm.AnthropicLLM)
    assert api.api_key == KEY
    assert api.reasoning_effort == "medium"
    bridge = gui.Bridge()
    bridge._cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    bridge._env = {"ANTHROPIC_API_KEY": KEY}
    record = next(r for r in bridge.list_api_keys() if r["env_key"] == "ANTHROPIC_API_KEY")
    assert record["is_set"] is True
    assert KEY not in json.dumps(bridge.get_settings())
    assert KEY not in json.dumps(record)


def test_gui_anthropic_extended_efforts_do_not_spread_to_other_providers():
    import gui
    bridge = gui.Bridge()
    bridge._cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    draft = copy.deepcopy(bridge._cfg["llm"])
    draft["providers"]["anthropic"]["reasoning_effort"] = "XHIGH"
    draft["providers"]["gemini"]["reasoning_effort"] = "xhigh"
    saved = bridge.save_settings({"llm": draft})
    assert saved["llm"]["providers"]["anthropic"]["reasoning_effort"] == "xhigh"
    assert saved["llm"]["providers"]["gemini"]["reasoning_effort"] == ""
