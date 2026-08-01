"""max_tokens=0（モデルに任せる）トグルのペイロード検証。

_post_json/_get_jsonをmonkeypatchして実LLM呼び出しを完全に遮断し、
組み立てられたpayloadだけを検証する。実ネットワーク接続は一切発生しない。
"""

import llm


def _capture_post(monkeypatch, target_dict):
    """llm._post_jsonを差し替え、渡されたpayloadをtarget_dict["payload"]に記録して
    最小限のダミー応答を返す。urllib.request.urlopenには一切到達しない。"""
    def fake_post_json(url, payload, headers=None, timeout=120):
        target_dict["payload"] = payload
        target_dict["url"] = url
        return target_dict["response"]

    monkeypatch.setattr(llm, "_post_json", fake_post_json)


def _openai_entry():
    return {"base_url": "https://example.invalid/v1", "model": "test-model"}


def test_openai_compat_sends_max_tokens_when_set(monkeypatch):
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)
    provider.chat([{"role": "user", "content": "hi"}])
    assert captured["payload"]["max_tokens"] == 2000


def test_openai_compat_omits_max_tokens_when_zero(monkeypatch):
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=0)
    provider.chat([{"role": "user", "content": "hi"}])
    assert "max_tokens" not in captured["payload"]


def test_openai_compat_explicit_arg_wins_over_unlimited_instance(monkeypatch):
    """wrapup.write_daily_chronicle等は毎回max_tokensを明示指定して呼ぶ。
    インスタンス側がmax_tokens=0（無制限）でも、明示引数の数値が優先されるべき
    （日記生成の暴走保険を、無制限トグルのONで壊さないため）。"""
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=0)
    provider.chat([{"role": "user", "content": "hi"}], max_tokens=500)
    assert captured["payload"]["max_tokens"] == 500


def test_openai_compat_explicit_arg_zero_omits_max_tokens_even_with_instance_set(monkeypatch):
    """設定GUIの「日記の最大トークン: モデルに任せる」トグルはwrapup.pyから
    毎回 max_tokens=0 を明示引数として渡す想定。インスタンス側のmax_tokensが
    有限値でも、明示引数の0はfalsyとして扱われmax_tokensキーごと送信されない
    こと（llm.py 145行目の `if effective_max_tokens and ...` はintより先に
    真偽値を見るため、0はNoneと同じ経路を通る）。"""
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=2000)
    provider.chat([{"role": "user", "content": "hi"}], max_tokens=0)
    assert "max_tokens" not in captured["payload"]


def test_openai_compat_explicit_none_falls_back_to_instance_value(monkeypatch):
    captured = {"response": {"choices": [{"message": {"content": "ok"}}]}}
    _capture_post(monkeypatch, captured)
    provider = llm.OpenAICompatLLM(_openai_entry(), api_key="k", temperature=0.8, max_tokens=1234)
    provider.chat([{"role": "user", "content": "hi"}], max_tokens=None)
    assert captured["payload"]["max_tokens"] == 1234


def _gemini_entry():
    return {"model": "gemini-2.5-flash"}


def _gemini_response():
    return {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}


def test_gemini_sends_max_output_tokens_when_set(monkeypatch):
    captured = {"response": _gemini_response()}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=2000)
    provider.chat([{"role": "user", "content": "hi"}])
    assert captured["payload"]["generationConfig"]["maxOutputTokens"] == 2000


def test_gemini_omits_max_output_tokens_when_zero(monkeypatch):
    captured = {"response": _gemini_response()}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=0)
    provider.chat([{"role": "user", "content": "hi"}])
    assert "maxOutputTokens" not in captured["payload"]["generationConfig"]


def test_gemini_explicit_arg_wins_over_unlimited_instance(monkeypatch):
    captured = {"response": _gemini_response()}
    _capture_post(monkeypatch, captured)
    provider = llm.GeminiLLM(_gemini_entry(), api_key="k", temperature=0.8, max_tokens=0)
    provider.chat([{"role": "user", "content": "hi"}], max_tokens=500)
    assert captured["payload"]["generationConfig"]["maxOutputTokens"] == 500


# --- プロバイダentry別のreasoning_effort解決とCodexへのreasoning配線 ---

def test_create_llm_uses_entry_level_reasoning_effort():
    cfg = {"provider": "opencode_go", "providers": {
        "opencode_go": {"type": "openai_compat", "base_url": "http://x/v1",
                        "model": "deepseek-v4", "env_key": "K",
                        "reasoning_effort": "high"}}}
    inst = llm.create_llm(cfg, {"K": "dummy"})
    assert inst.reasoning_effort == "high"


def test_create_llm_falls_back_to_global_reasoning_effort():
    cfg = {"provider": "opencode_go", "reasoning_effort": "low", "providers": {
        "opencode_go": {"type": "openai_compat", "base_url": "http://x/v1",
                        "model": "m", "env_key": "K"}}}
    inst = llm.create_llm(cfg, {"K": "dummy"})
    assert inst.reasoning_effort == "low"


def test_entry_reasoning_effort_beats_global():
    cfg = {"provider": "opencode_go", "reasoning_effort": "low", "providers": {
        "opencode_go": {"type": "openai_compat", "base_url": "http://x/v1",
                        "model": "m", "env_key": "K",
                        "reasoning_effort": "medium"}}}
    inst = llm.create_llm(cfg, {"K": "dummy"})
    assert inst.reasoning_effort == "medium"


def test_fallback_provider_uses_its_own_entry_effort():
    cfg = {"provider": "a", "fallback_provider": "b", "providers": {
        "a": {"type": "openai_compat", "base_url": "http://x/v1", "model": "m",
              "env_key": "K", "reasoning_effort": "high"},
        "b": {"type": "openai_compat", "base_url": "http://y/v1", "model": "m2",
              "env_key": "K", "reasoning_effort": "low"}}}
    inst = llm.create_llm(cfg, {"K": "dummy"})
    assert inst.primary.reasoning_effort == "high"
    assert inst.fallback.reasoning_effort == "low"


def _capture_post_sse(monkeypatch, target_dict):
    """llm._post_sseを差し替え、渡されたpayloadをtarget_dict["payload"]に記録して
    target_dict["raw"]（SSE生テキスト）を返す。CodexOAuthLLM._headersも
    openai_codex_oauth（未ログイン環境で例外を出す）を経由しないダミーに差し替える
    ——認証状態に依存せずchat()のpayload組み立てだけを検証するため
    （test_llm_streaming.pyのCodex系テストと同様、認証まわりはバイパスする）。"""
    def fake_post_sse(url, payload, headers=None, timeout=120):
        target_dict["payload"] = payload
        target_dict["url"] = url
        return target_dict["raw"]

    monkeypatch.setattr(llm, "_post_sse", fake_post_sse)
    monkeypatch.setattr(llm.CodexOAuthLLM, "_headers", lambda self: {})


def _codex_sse_ok():
    return (
        "event: response.output_text.delta\n"
        'data: {"type": "response.output_text.delta", "delta": "ok"}\n\n'
    )


def test_codex_chat_includes_reasoning_effort_in_payload_when_set(monkeypatch):
    captured = {"raw": _codex_sse_ok()}
    _capture_post_sse(monkeypatch, captured)
    provider = llm.CodexOAuthLLM({"model": "gpt-5.5"}, temperature=0.8, max_tokens=2000,
                                  reasoning_effort="high")
    provider.chat([{"role": "user", "content": "hi"}])
    assert captured["payload"]["reasoning"] == {"effort": "high"}


def test_codex_chat_omits_reasoning_key_when_effort_unset(monkeypatch):
    captured = {"raw": _codex_sse_ok()}
    _capture_post_sse(monkeypatch, captured)
    provider = llm.CodexOAuthLLM({"model": "gpt-5.5"}, temperature=0.8, max_tokens=2000)
    provider.chat([{"role": "user", "content": "hi"}])
    assert "reasoning" not in captured["payload"]


def test_entry_explicit_empty_effort_overrides_global():
    """entryに空文字（UIの「指定なし」）が明示されていれば、旧グローバルキーに落ちない。"""
    cfg = {"provider": "opencode_go", "reasoning_effort": "high", "providers": {
        "opencode_go": {"type": "openai_compat", "base_url": "http://x/v1",
                        "model": "m", "env_key": "K", "reasoning_effort": ""}}}
    inst = llm.create_llm(cfg, {"K": "dummy"})
    assert inst.reasoning_effort == ""
