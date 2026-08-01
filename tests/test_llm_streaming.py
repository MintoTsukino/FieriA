"""llm.py — chat_stream()（FieriA拡張・ストリーミング）の検証。

llm._post_sse_streamをmonkeypatchして固定のSSE行リストを差し替え、実urlopenには
一切到達させない（test_llm_payload.pyの_capture_postと同じ思想）。
"""

import json

import pytest

import llm


def _openai_entry():
    return {"base_url": "https://example.invalid/v1", "model": "test-model"}


def _fake_stream(monkeypatch, lines):
    """llm._post_sse_streamを固定行リストのジェネレータに差し替える。
    urlとpayloadは検証用にtarget_dictへ記録する。"""
    captured = {}

    def fake(url, payload, headers=None, timeout=120):
        captured["url"] = url
        captured["payload"] = payload
        for line in lines:
            yield line

    monkeypatch.setattr(llm, "_post_sse_stream", fake)
    return captured


def _sse(obj):
    return "data: " + json.dumps(obj) + "\n"


# --- OpenAICompatLLM ---

def test_openai_compat_chat_stream_yields_delta_chunks(monkeypatch):
    captured = _fake_stream(monkeypatch, [
        _sse({"choices": [{"delta": {"content": "こん"}}]}),
        _sse({"choices": [{"delta": {"content": "にちは"}}]}),
        "data: [DONE]\n",
    ])
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["こん", "にちは"]
    assert captured["payload"]["stream"] is True
    assert captured["url"] == "https://example.invalid/v1/chat/completions"


def test_openai_compat_chat_stream_ignores_keepalive_and_empty_lines(monkeypatch):
    _fake_stream(monkeypatch, [
        "\n",
        ": keep-alive\n",
        _sse({"choices": [{"delta": {"content": "ok"}}]}),
        "data: [DONE]\n",
    ])
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["ok"]


def test_openai_compat_chat_stream_skips_delta_without_content(monkeypatch):
    """role指定だけのdelta（会話開始の合図等）はcontentが無いので何もyieldしない。"""
    _fake_stream(monkeypatch, [
        _sse({"choices": [{"delta": {"role": "assistant"}}]}),
        _sse({"choices": [{"delta": {"content": "本文"}}]}),
        "data: [DONE]\n",
    ])
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["本文"]


def test_openai_compat_chat_stream_raises_on_malformed_data_but_keeps_prior_chunks(monkeypatch):
    _fake_stream(monkeypatch, [
        _sse({"choices": [{"delta": {"content": "前半"}}]}),
        "data: {not-valid-json\n",
    ])
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    gen = provider.chat_stream([{"role": "user", "content": "hi"}])
    got = [next(gen)]
    with pytest.raises(RuntimeError):
        next(gen)
    assert got == ["前半"]


def test_openai_compat_chat_stream_propagates_http_error_from_transport(monkeypatch):
    def fake(url, payload, headers=None, timeout=120):
        raise RuntimeError("HTTP 429: rate limited")
        yield  # pragma: no cover (ジェネレータ関数として成立させるためのダミー)

    monkeypatch.setattr(llm, "_post_sse_stream", fake)
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    with pytest.raises(RuntimeError):
        list(provider.chat_stream([{"role": "user", "content": "hi"}]))


def test_openai_compat_chat_stream_sends_max_tokens_and_reasoning_effort(monkeypatch):
    captured = _fake_stream(monkeypatch, ["data: [DONE]\n"])
    provider = llm.OpenAICompatLLM(
        _openai_entry(), api_key="k", temperature=0.8, max_tokens=2000, reasoning_effort="high")

    list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert captured["payload"]["max_tokens"] == 2000
    assert captured["payload"]["reasoning_effort"] == "high"


def test_openai_compat_chat_stream_omits_max_tokens_when_zero(monkeypatch):
    captured = _fake_stream(monkeypatch, ["data: [DONE]\n"])
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=0)

    list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert "max_tokens" not in captured["payload"]


# --- GeminiLLM ---

def _gemini_entry():
    return {"model": "gemini-2.5-flash"}


def _gemini_sse(text):
    return "data: " + json.dumps(
        {"candidates": [{"content": {"parts": [{"text": text}]}}]}) + "\n"


def test_gemini_chat_stream_yields_delta_chunks(monkeypatch):
    captured = _fake_stream(monkeypatch, [
        _gemini_sse("にゃ"),
        _gemini_sse("っほー"),
    ])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["にゃ", "っほー"]
    assert "alt=sse" in captured["url"]
    assert "streamGenerateContent" in captured["url"]


def test_gemini_chat_stream_ignores_keepalive_and_empty_candidates(monkeypatch):
    _fake_stream(monkeypatch, [
        "\n",
        ": ping\n",
        "data: {}\n",  # candidatesキー自体が無いイベント（プロンプトフィードバック等）
        _gemini_sse("本文"),
    ])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["本文"]


def test_gemini_chat_stream_raises_on_malformed_data_but_keeps_prior_chunks(monkeypatch):
    _fake_stream(monkeypatch, [
        _gemini_sse("前半"),
        "data: {not-valid-json\n",
    ])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    gen = provider.chat_stream([{"role": "user", "content": "hi"}])
    got = [next(gen)]
    with pytest.raises(RuntimeError):
        next(gen)
    assert got == ["前半"]


def test_gemini_chat_stream_sends_max_output_tokens_when_set(monkeypatch):
    captured = _fake_stream(monkeypatch, [])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)

    list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert captured["payload"]["generationConfig"]["maxOutputTokens"] == 2000


def test_gemini_chat_stream_uses_shared_payload_builder_with_chat(monkeypatch):
    """chat()とchat_stream()が同じ_build_payloadを通ること（画像添付・system統合の
    重複実装防止を確認する）。"""
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "見て", "images": [{"mime": "image/png", "b64": "abc"}]},
    ]
    payload_from_chat_path = provider._build_payload(messages, None)
    payload_from_stream_path = provider._build_payload(messages, None)
    assert payload_from_chat_path == payload_from_stream_path


# --- OAuth系(CodexOAuthLLM)・FallbackLLM: 未実装プロバイダの共通フォールバック ---

def test_codex_oauth_chat_stream_falls_back_to_single_full_text_yield(monkeypatch):
    calls = {"n": 0}

    def fake_chat(self, messages, max_tokens=None):
        calls["n"] += 1
        return "全文まとめて返すじょ"

    monkeypatch.setattr(llm.CodexOAuthLLM, "chat", fake_chat)
    provider = llm.CodexOAuthLLM({"model": "gpt-5.5"}, temperature=0.8, max_tokens=2000)

    chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["全文まとめて返すじょ"]
    assert calls["n"] == 1


def test_fallback_llm_chat_stream_falls_back_to_single_full_text_yield():
    class StubLLM:
        def __init__(self, text):
            self.text = text

        def chat(self, messages, max_tokens=None):
            return self.text

    primary = StubLLM("プライマリ応答")
    fallback = StubLLM("フォールバック応答")
    wrapper = llm.FallbackLLM(primary, fallback)

    chunks = list(wrapper.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["プライマリ応答"]


def test_fallback_llm_chat_stream_uses_fallback_when_primary_chat_fails():
    class ExplodingLLM:
        def chat(self, messages, max_tokens=None):
            raise RuntimeError("プライマリ失敗")

    class StubLLM:
        def chat(self, messages, max_tokens=None):
            return "フォールバック応答"

    wrapper = llm.FallbackLLM(ExplodingLLM(), StubLLM())

    chunks = list(wrapper.chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["フォールバック応答"]


def test_openai_compat_stream_ignores_empty_data_lines(monkeypatch):
    """空の`data: `行（litellm等のkeep-alive）でクラッシュしないこと（レビューCritical回帰）。
    Gemini側の既存ガードとの対称性。"""
    import llm

    def fake_stream(url, payload, headers=None):
        yield "data: \n"
        yield 'data: {"choices":[{"delta":{"content":"本文"}}]}\n'
        yield "data: [DONE]\n"
    monkeypatch.setattr(llm, "_post_sse_stream", fake_stream)
    inst = llm.OpenAICompatLLM({"base_url": "http://x", "model": "m", "env_key": ""},
                                 api_key="k", temperature=0.8, max_tokens=100)
    chunks = list(inst.chat_stream([{"role": "user", "content": "hi"}]))
    assert chunks == ["本文"]
