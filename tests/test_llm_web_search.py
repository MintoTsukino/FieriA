"""FieriA拡張: Web検索（Gemini Google Search grounding / Grok Live Search）の検証。

test_llm_payload.py/test_llm_streaming.pyと同じ思想: llm._post_json/_post_sse_stream
をmonkeypatchして実urlopenに一切到達させず、組み立てられたpayloadと戻り値だけを検証する。
"""

import json

import llm


def _capture_post(monkeypatch, target_dict):
    def fake_post_json(url, payload, headers=None, timeout=120):
        target_dict["payload"] = payload
        target_dict["url"] = url
        return target_dict["response"]

    monkeypatch.setattr(llm, "_post_json", fake_post_json)


def _fake_stream(monkeypatch, lines):
    captured = {}

    def fake(url, payload, headers=None, timeout=120):
        captured["url"] = url
        captured["payload"] = payload
        for line in lines:
            yield line

    monkeypatch.setattr(llm, "_post_sse_stream", fake)
    return captured


def _gemini_entry():
    return {"model": "gemini-2.5-flash"}


def _gemini_response(text="ok", grounding_chunks=None):
    candidate = {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
    if grounding_chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": grounding_chunks}
    return {"candidates": [candidate]}


def _gemini_sse(text, grounding_chunks=None):
    candidate = {"content": {"parts": [{"text": text}]}}
    if grounding_chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": grounding_chunks}
    return "data: " + json.dumps({"candidates": [candidate]}) + "\n"


# --- Gemini: payload組み立て（tools有無） ---

def test_gemini_chat_adds_google_search_tool_when_web_search_enabled(monkeypatch):
    captured = {"response": _gemini_response()}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    provider.chat([{"role": "user", "content": "hi"}])
    assert captured["payload"]["tools"] == [{"google_search": {}}]


def test_gemini_chat_omits_tools_when_web_search_disabled(monkeypatch):
    captured = {"response": _gemini_response()}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=False)
    provider.chat([{"role": "user", "content": "hi"}])
    assert "tools" not in captured["payload"]


def test_gemini_web_search_defaults_to_disabled(monkeypatch):
    """web_searchキーワードを渡さない既存呼び出し（後方互換）は無効のまま。"""
    captured = {"response": _gemini_response()}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)
    provider.chat([{"role": "user", "content": "hi"}])
    assert "tools" not in captured["payload"]


# --- Gemini: chat()の出典付与 ---

def test_gemini_chat_appends_sources_from_grounding_metadata(monkeypatch):
    chunks = [
        {"web": {"uri": "https://a.example/1", "title": "記事A"}},
        {"web": {"uri": "https://b.example/2", "title": "記事B"}},
    ]
    captured = {"response": _gemini_response(text="本文だよ", grounding_chunks=chunks)}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result.startswith("本文だよ")
    assert "🔎 出典:" in result
    assert "[記事A](https://a.example/1)" in result
    assert "[記事B](https://b.example/2)" in result


def test_gemini_chat_caps_sources_at_three(monkeypatch):
    chunks = [{"web": {"uri": f"https://x.example/{i}", "title": f"記事{i}"}} for i in range(5)]
    captured = {"response": _gemini_response(text="本文", grounding_chunks=chunks)}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result.count("https://x.example/") == 3


def test_gemini_chat_tolerates_missing_title(monkeypatch):
    chunks = [{"web": {"uri": "https://a.example/1"}}]
    captured = {"response": _gemini_response(text="本文", grounding_chunks=chunks)}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert "https://a.example/1" in result


def test_gemini_chat_no_sources_block_when_metadata_absent(monkeypatch):
    captured = {"response": _gemini_response(text="本文のみ")}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result == "本文のみ"
    assert "🔎" not in result


def test_gemini_chat_no_sources_block_when_web_search_disabled_even_with_metadata(monkeypatch):
    """web_search=Falseなら、応答にgroundingMetadataが混ざっていても出典を付けない。"""
    chunks = [{"web": {"uri": "https://a.example/1", "title": "記事A"}}]
    captured = {"response": _gemini_response(text="本文", grounding_chunks=chunks)}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=False)
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result == "本文"


def test_gemini_chat_malformed_grounding_metadata_does_not_break_response(monkeypatch):
    """groundingChunksの形が崩れていても（web無し・非dict要素）例外を握って本文だけ返す。"""
    candidate = {"content": {"parts": [{"text": "本文"}]}, "finishReason": "STOP",
                 "groundingMetadata": {"groundingChunks": ["broken", {"web": "not-a-dict"}, {}]}}
    captured = {"response": {"candidates": [candidate]}}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result == "本文"


# --- Gemini: chat_stream()の出典付与 ---

def test_gemini_chat_stream_yields_sources_block_as_final_chunk(monkeypatch):
    chunks = [{"web": {"uri": "https://a.example/1", "title": "記事A"}}]
    _fake_stream(monkeypatch, [
        _gemini_sse("本文", grounding_chunks=chunks),
    ])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    out = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert out[0] == "本文"
    assert len(out) == 2
    assert "🔎 出典:" in out[-1]
    assert "[記事A](https://a.example/1)" in out[-1]


def test_gemini_chat_stream_dedupes_sources_across_chunks(monkeypatch):
    chunk = {"web": {"uri": "https://a.example/1", "title": "記事A"}}
    _fake_stream(monkeypatch, [
        _gemini_sse("前半", grounding_chunks=[chunk]),
        _gemini_sse("後半", grounding_chunks=[chunk]),
    ])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    out = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert out == ["前半", "後半", "\n\n---\n🔎 出典: [記事A](https://a.example/1)"]


def test_gemini_chat_stream_no_sources_chunk_when_metadata_absent(monkeypatch):
    _fake_stream(monkeypatch, [_gemini_sse("本文のみ")])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=True)
    out = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert out == ["本文のみ"]


def test_gemini_chat_stream_no_sources_chunk_when_web_search_disabled(monkeypatch):
    chunks = [{"web": {"uri": "https://a.example/1", "title": "記事A"}}]
    _fake_stream(monkeypatch, [_gemini_sse("本文", grounding_chunks=chunks)])
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000,
                              web_search=False)
    out = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert out == ["本文"]


# --- XaiOAuthLLM: search_parameters ---

def _xai_entry():
    return {"base_url": "https://api.x.ai/v1", "model": "grok-4.3"}


def test_xai_oauth_chat_adds_search_parameters_when_enabled(monkeypatch):
    import xai_oauth
    monkeypatch.setattr(xai_oauth, "get_access_token", lambda: "tok")
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.XaiOAuthLLM(_xai_entry(), api_key="", temperature=0.8, max_tokens=2000,
                                web_search=True)
    provider.chat([{"role": "user", "content": "hi"}])
    assert captured["payload"]["search_parameters"] == {"mode": "auto"}


def test_xai_oauth_chat_omits_search_parameters_when_disabled(monkeypatch):
    import xai_oauth
    monkeypatch.setattr(xai_oauth, "get_access_token", lambda: "tok")
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.XaiOAuthLLM(_xai_entry(), api_key="", temperature=0.8, max_tokens=2000,
                                web_search=False)
    provider.chat([{"role": "user", "content": "hi"}])
    assert "search_parameters" not in captured["payload"]


def test_xai_oauth_chat_stream_adds_search_parameters_when_enabled(monkeypatch):
    import xai_oauth
    monkeypatch.setattr(xai_oauth, "get_access_token", lambda: "tok")
    captured = _fake_stream(monkeypatch, ["data: [DONE]\n"])
    provider = llm.XaiOAuthLLM(_xai_entry(), api_key="", temperature=0.8, max_tokens=2000,
                                web_search=True)
    list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert captured["payload"]["search_parameters"] == {"mode": "auto"}


# --- build_provider / create_llm 配線 ---

def test_build_provider_passes_web_search_to_gemini():
    provider = llm.build_provider({"type": "gemini", "model": "gemini-2.5-flash"}, {},
                                   temperature=0.8, max_tokens=2000, web_search=True)
    assert isinstance(provider, llm.GeminiLLM)
    assert provider.web_search is True


def test_build_provider_ignores_web_search_for_openai_compat():
    """openai_compatは対応外プロバイダなので、web_search=Trueを渡しても無視される
    （コンストラクタ自体がキーワードを受け付けない設計）。"""
    provider = llm.build_provider(
        {"type": "openai_compat", "base_url": "https://example.invalid/v1", "model": "m"}, {},
        temperature=0.8, max_tokens=2000, web_search=True)
    assert isinstance(provider, llm.OpenAICompatLLM)
    assert not hasattr(provider, "web_search")


def test_create_llm_reads_web_search_from_llm_cfg():
    cfg = {
        "provider": "gemini",
        "fallback_provider": "",
        "fallback_model": "",
        "web_search": True,
        "providers": {
            "gemini": {"type": "gemini", "model": "gemini-2.5-flash", "env_key": "GEMINI_API_KEY"},
        },
        "max_tokens": 2000,
    }
    created = llm.create_llm(cfg, {})
    assert isinstance(created, llm.GeminiLLM)
    assert created.web_search is True


def test_create_llm_web_search_defaults_to_false_when_absent():
    cfg = {
        "provider": "gemini",
        "fallback_provider": "",
        "fallback_model": "",
        "providers": {
            "gemini": {"type": "gemini", "model": "gemini-2.5-flash", "env_key": "GEMINI_API_KEY"},
        },
        "max_tokens": 2000,
    }
    created = llm.create_llm(cfg, {})
    assert isinstance(created, llm.GeminiLLM)
    assert created.web_search is False


def test_create_llm_applies_web_search_to_fallback_provider_too():
    """web_searchはllm直下のグローバルトグルなので、フォールバック側にも同じ値が渡る。"""
    cfg = {
        "provider": "opencode_zen",
        "fallback_provider": "gemini",
        "fallback_model": "",
        "web_search": True,
        "providers": {
            "opencode_zen": {"type": "openai_compat", "base_url": "https://example.invalid/v1",
                              "model": "m", "env_key": ""},
            "gemini": {"type": "gemini", "model": "gemini-2.5-flash", "env_key": "GEMINI_API_KEY"},
        },
        "max_tokens": 2000,
    }
    created = llm.create_llm(cfg, {})
    assert isinstance(created, llm.FallbackLLM)
    assert created.fallback.web_search is True


def test_sources_block_includes_search_queries_when_present():
    """webSearchQueries（実際の検索語）が出典ブロックに表示される（透明性・実機FB対応）。"""
    import llm
    cand = {"groundingMetadata": {
        "groundingChunks": [{"web": {"title": "記事A", "uri": "https://a.example/1"}}],
        "webSearchQueries": ["みんと 小説家", "  ", "FieriA", "4個目は上限外", "5個目"],
    }}
    sources = llm._extract_grounding_sources(cand)
    queries = llm._extract_search_queries(cand)
    block = llm._format_sources_block(sources, queries)
    assert "（検索語: 「みんと 小説家」 / 「FieriA」 / 「4個目は上限外」）" in block
    assert "[記事A](https://a.example/1)" in block


def test_sources_block_omits_query_line_when_absent():
    import llm
    block = llm._format_sources_block([("記事A", "https://a.example/1")], [])
    assert "検索語" not in block
