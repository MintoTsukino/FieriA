"""ネイティブfunction calling（OpenAI互換）。test_llm_web_search.pyと同じ思想:
llm._post_json をmonkeypatchして実urlopenへ到達させず、payloadと戻り値だけ検証。"""
import json

import llm


def _mk():
    return llm.OpenAICompatLLM({"base_url": "http://x", "model": "m"}, "key", 0.7, 400)


TOOLS = [{"type": "function", "function": {"name": "write_wiki", "description": "d",
           "parameters": {"type": "object", "properties": {}, "required": []}}}]


def _capture(monkeypatch, response_message):
    captured = {}

    def fake_post(url, payload, headers=None, timeout=120):
        captured["url"] = url
        captured["payload"] = payload
        return {"choices": [{"message": response_message}]}

    monkeypatch.setattr(llm, "_post_json", fake_post)
    return captured


def test_chat_tools_sends_tools_param(monkeypatch):
    captured = _capture(monkeypatch, {"content": "ok", "tool_calls": None})

    _mk().chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert captured["payload"]["tools"] == TOOLS


def test_chat_tools_parses_tool_calls_arguments_json(monkeypatch):
    _capture(monkeypatch, {
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "write_wiki",
                                       "arguments": '{"topic": "T", "content": "C"}'}}],
    })

    result = _mk().chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert result["text"] == ""
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "write_wiki",
         "arguments": {"topic": "T", "content": "C"}}]


def test_chat_tools_text_and_calls_can_coexist(monkeypatch):
    _capture(monkeypatch, {
        "content": "書いとくじょ",
        "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "save_lesson", "arguments": '{"text": "x"}'}}],
    })

    result = _mk().chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert result["text"] == "書いとくじょ"
    assert result["tool_calls"][0]["name"] == "save_lesson"


def test_chat_tools_broken_arguments_kept_as_raw(monkeypatch):
    """引数JSONが壊れていても例外にせず__rawで返す（engine側が差し戻す素材にする）。"""
    _capture(monkeypatch, {
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "write_wiki", "arguments": '{"topic": 壊れ'}}],
    })

    result = _mk().chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert result["tool_calls"][0]["arguments"] == {"__raw": '{"topic": 壊れ'}


def test_chat_tools_tools_rejection_raises_typed_error(monkeypatch):
    """エラーメッセージにtools/functionを含むHTTP失敗は専用例外。
    engineがフェンス経路へフォールバックする判断に使う（Codex web_search拒否
    リトライと同じ思想）。"""
    def fake_post(url, payload, headers=None, timeout=120):
        raise RuntimeError('HTTP 400: {"error": "tools is not supported for this model"}')

    monkeypatch.setattr(llm, "_post_json", fake_post)

    try:
        _mk().chat_tools([{"role": "user", "content": "hi"}], TOOLS)
        assert False, "例外が出るはず"
    except llm.NativeToolsUnsupported:
        pass


def test_chat_tools_other_errors_pass_through(monkeypatch):
    def fake_post(url, payload, headers=None, timeout=120):
        raise RuntimeError("HTTP 500: server on fire")

    monkeypatch.setattr(llm, "_post_json", fake_post)

    try:
        _mk().chat_tools([{"role": "user", "content": "hi"}], TOOLS)
        assert False
    except llm.NativeToolsUnsupported:
        assert False, "toolsと無関係のエラーを誤分類しない"
    except RuntimeError:
        pass


def test_to_openai_msg_passes_tool_roles_through(monkeypatch):
    """assistant(tool_calls)とrole:toolのメッセージが変換で壊れないこと。"""
    captured = _capture(monkeypatch, {"content": "ok", "tool_calls": None})
    msgs = [
        {"role": "user", "content": "やって"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "write_wiki", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok: 書いた"},
    ]

    _mk().chat_tools(msgs, TOOLS)

    sent = captured["payload"]["messages"]
    assert sent[1]["tool_calls"][0]["id"] == "c1"
    assert sent[2]["role"] == "tool"
    assert sent[2]["tool_call_id"] == "c1"


def test_supports_native_tools_flags():
    import llm as llm_mod
    assert llm_mod.OpenAICompatLLM.supports_native_tools is True
    assert getattr(llm_mod.GeminiLLM, "supports_native_tools", False) is False
    assert getattr(llm_mod.FallbackLLM, "supports_native_tools", False) is False


def _fake_stream(monkeypatch, lines):
    def fake(url, payload, headers=None, timeout=120):
        fake.payload = payload
        return iter(lines)

    monkeypatch.setattr(llm, "_post_sse_stream", fake)
    return fake


def _sse(obj):
    return "data: " + json.dumps(obj)


def test_chat_tools_stream_assembles_fragmented_arguments(monkeypatch):
    """id/nameは初回チャンクのみ・argumentsは断片で届く、の標準挙動を組み立てる。"""
    _fake_stream(monkeypatch, [
        _sse({"choices": [{"delta": {"content": "書くじょ"}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1",
             "function": {"name": "write_wiki", "arguments": '{"topic"'}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ': "T", "content": "C"}'}}]}}]}),
        "data: [DONE]",
    ])
    deltas = []

    result = _mk().chat_tools_stream([{"role": "user", "content": "hi"}], TOOLS,
                                       on_delta=deltas.append)

    assert result["text"] == "書くじょ"
    assert deltas == ["書くじょ"]
    assert result["tool_calls"] == [
        {"id": "c1", "name": "write_wiki",
         "arguments": {"topic": "T", "content": "C"}}]


def test_chat_tools_stream_two_parallel_calls_by_index(monkeypatch):
    _fake_stream(monkeypatch, [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "a", "function": {"name": "save_lesson",
                                                   "arguments": '{"text": "1"}'}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "b", "function": {"name": "save_sacred",
                                                   "arguments": '{"text": "2"}'}}]}}]}),
        "data: [DONE]",
    ])

    result = _mk().chat_tools_stream([{"role": "user", "content": "hi"}], TOOLS)

    assert [c["name"] for c in result["tool_calls"]] == ["save_lesson", "save_sacred"]


def test_chat_tools_stream_sends_tools_and_stream_true(monkeypatch):
    fake = _fake_stream(monkeypatch, ["data: [DONE]"])

    _mk().chat_tools_stream([{"role": "user", "content": "hi"}], TOOLS)

    assert fake.payload["tools"] == TOOLS
    assert fake.payload["stream"] is True


def test_chat_tools_stream_should_stop_breaks_before_remaining_chunks(monkeypatch):
    """should_stopがTrueになったら以降のチャンクを読まずに打ち切る（既存chat_streamの
    破棄方式と同じ）。fake側でジェネレータの消費数を数え、[DONE]まで消費しきらない
    ことを確認する。"""
    lines = [
        _sse({"choices": [{"delta": {"content": "1"}}]}),
        _sse({"choices": [{"delta": {"content": "2"}}]}),
        _sse({"choices": [{"delta": {"content": "3"}}]}),
        "data: [DONE]",
    ]
    consumed = {"n": 0}

    def gen():
        for line in lines:
            consumed["n"] += 1
            yield line

    def fake(url, payload, headers=None, timeout=120):
        return gen()

    monkeypatch.setattr(llm, "_post_sse_stream", fake)

    stop_calls = {"n": 0}

    def should_stop():
        stop_calls["n"] += 1
        return stop_calls["n"] >= 2  # 2チャンク目の処理後に停止要求ありとする

    result = _mk().chat_tools_stream([{"role": "user", "content": "hi"}], TOOLS,
                                       should_stop=should_stop)

    assert consumed["n"] == 2  # 3チャンク目・[DONE]は消費されていない
    assert result["text"] == "12"


def test_chat_tools_stream_no_should_stop_reads_all_chunks(monkeypatch):
    """should_stop未指定（None）時は従来どおり全チャンクを読み切る（後方互換）。"""
    fake = _fake_stream(monkeypatch, [
        _sse({"choices": [{"delta": {"content": "1"}}]}),
        _sse({"choices": [{"delta": {"content": "2"}}]}),
        "data: [DONE]",
    ])

    result = _mk().chat_tools_stream([{"role": "user", "content": "hi"}], TOOLS)

    assert result["text"] == "12"


# --- CodexOAuthLLM: ネイティブfunction calling（Responses API・test_llm_web_search.py
# のCodexセクションと同じ流儀: llm._post_sse をmonkeypatchし、openai_codex_oauthの
# ログイン状態もfakeする） ---

def _codex_entry():
    return {"model": "gpt-5.5"}


def _fake_codex_login(monkeypatch):
    import openai_codex_oauth
    monkeypatch.setattr(openai_codex_oauth, "get_access_token", lambda: "tok")
    monkeypatch.setattr(openai_codex_oauth, "get_account_id", lambda: "acct")


def _codex_final_sse(output, status="completed"):
    """response.completedイベント1個だけのSSE文字列。output配列に任意のアイテム
    （function_call等）を積めるようにしたテスト用ヘルパー。"""
    return "data: " + json.dumps({"response": {"status": status, "output": output}}) + "\n\n"


def _mk_codex(**kwargs):
    return llm.CodexOAuthLLM(_codex_entry(), temperature=0.8, max_tokens=2000, **kwargs)


def test_codex_chat_tools_sends_flat_tool_definitions(monkeypatch):
    """chat.completions形（type/function入れ子）→ Responses APIのフラット形へ変換される。"""
    _fake_codex_login(monkeypatch)
    captured = {}

    def fake_post_sse(url, payload, headers=None, timeout=120):
        captured["payload"] = payload
        return _codex_final_sse([])

    monkeypatch.setattr(llm, "_post_sse", fake_post_sse)

    _mk_codex().chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert captured["payload"]["tools"] == [
        {"type": "function", "name": "write_wiki", "description": "d",
         "parameters": {"type": "object", "properties": {}, "required": []}}]


def test_codex_chat_tools_parses_function_call_output_into_tool_calls(monkeypatch):
    """response.completedのoutput配列のfunction_callアイテム→chat.completions形tool_calls。"""
    _fake_codex_login(monkeypatch)

    def fake_post_sse(url, payload, headers=None, timeout=120):
        return _codex_final_sse([
            {"type": "function_call", "call_id": "call_1", "name": "write_wiki",
             "arguments": '{"topic": "T", "content": "C"}'},
        ])

    monkeypatch.setattr(llm, "_post_sse", fake_post_sse)

    result = _mk_codex().chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert result["tool_calls"] == [
        {"id": "call_1", "name": "write_wiki",
         "arguments": {"topic": "T", "content": "C"}}]
    assert result["text"] == ""


def test_codex_chat_tools_converts_tool_role_message_to_function_call_output(monkeypatch):
    """exchangeのrole:"tool"メッセージがinput配列のfunction_call_outputアイテムへ、
    assistant(tool_calls)がfunction_callアイテムへ変換されて送られる。"""
    _fake_codex_login(monkeypatch)
    captured = {}

    def fake_post_sse(url, payload, headers=None, timeout=120):
        captured["payload"] = payload
        return _codex_final_sse([])

    monkeypatch.setattr(llm, "_post_sse", fake_post_sse)
    msgs = [
        {"role": "user", "content": "やって"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "write_wiki", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok: 書いた"},
    ]

    _mk_codex().chat_tools(msgs, TOOLS)

    sent = captured["payload"]["input"]
    assert {"type": "function_call", "call_id": "c1", "name": "write_wiki",
            "arguments": "{}"} in sent
    assert {"type": "function_call_output", "call_id": "c1", "output": "ok: 書いた"} in sent


def test_codex_chat_tools_web_search_coexists_with_function_tools(monkeypatch):
    """web_search ONのとき、toolsにweb_searchとfunction定義が両方入る。"""
    _fake_codex_login(monkeypatch)
    captured = {}

    def fake_post_sse(url, payload, headers=None, timeout=120):
        captured["payload"] = payload
        return _codex_final_sse([])

    monkeypatch.setattr(llm, "_post_sse", fake_post_sse)

    _mk_codex(web_search=True).chat_tools([{"role": "user", "content": "hi"}], TOOLS)

    assert {"type": "web_search"} in captured["payload"]["tools"]
    assert {"type": "function", "name": "write_wiki", "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": []}
            } in captured["payload"]["tools"]


def test_codex_supports_native_tools_flag():
    assert llm.CodexOAuthLLM.supports_native_tools is True


def test_codex_chat_tools_tools_rejection_raises_typed_error(monkeypatch):
    """function tools起因の拒否は、web_searchのような黙ったリトライではなく
    NativeToolsUnsupportedを投げる（呼び出しが消えたまま気づかず進むのを防ぐ）。"""
    _fake_codex_login(monkeypatch)

    def fake_post_sse(url, payload, headers=None, timeout=120):
        raise RuntimeError('HTTP 400: {"error": "tools is not supported for this model"}')

    monkeypatch.setattr(llm, "_post_sse", fake_post_sse)

    try:
        _mk_codex().chat_tools([{"role": "user", "content": "hi"}], TOOLS)
        assert False, "例外が出るはず"
    except llm.NativeToolsUnsupported:
        pass
