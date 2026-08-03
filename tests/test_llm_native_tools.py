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
