"""FieriA拡張: セマンティック検索の埋め込み層（Ollama /api/embed）の検証。

embed._http_postをmonkeypatchして実urlopen・実Ollamaに一切到達させず、
組み立てられたリクエストと戻り値だけを検証する（test_tts.pyと同じ思想）。
"""

import json

import embed


# --- embed_texts ---

def _capture_post(monkeypatch, responses):
    captured = {"calls": [], "responses": list(responses)}

    def fake_post(url, data=None, headers=None, timeout=30):
        captured["calls"].append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return captured["responses"].pop(0)

    monkeypatch.setattr(embed, "_http_post", fake_post)
    return captured


def test_embed_texts_sends_expected_payload_and_parses_embeddings(monkeypatch):
    response = json.dumps({"embeddings": [[0.1, 0.2], [0.3, 0.4]]}).encode("utf-8")
    captured = _capture_post(monkeypatch, [response])

    result = embed.embed_texts("http://127.0.0.1:11434", "qwen3-embed", ["テキストA", "テキストB"], timeout=20)

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert len(captured["calls"]) == 1
    call = captured["calls"][0]
    assert call["url"] == "http://127.0.0.1:11434/api/embed"
    assert call["timeout"] == 20
    assert call["headers"]["Content-Type"] == "application/json"
    body = json.loads(call["data"].decode("utf-8"))
    assert body == {"model": "qwen3-embed", "input": ["テキストA", "テキストB"]}


def test_embed_texts_strips_trailing_slash_from_engine_url(monkeypatch):
    response = json.dumps({"embeddings": [[1.0]]}).encode("utf-8")
    captured = _capture_post(monkeypatch, [response])

    embed.embed_texts("http://127.0.0.1:11434/", "m", ["x"])

    assert captured["calls"][0]["url"] == "http://127.0.0.1:11434/api/embed"


def test_embed_texts_raises_on_http_failure(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=30):
        raise OSError("connection refused")

    monkeypatch.setattr(embed, "_http_post", fake_post)

    try:
        embed.embed_texts("http://127.0.0.1:11434", "m", ["x"])
        assert False, "例外が送出されるべき"
    except OSError:
        pass


def test_embed_texts_raises_on_malformed_response(monkeypatch):
    response = json.dumps({"unexpected": "shape"}).encode("utf-8")
    _capture_post(monkeypatch, [response])

    try:
        embed.embed_texts("http://127.0.0.1:11434", "m", ["x"])
        assert False, "例外が送出されるべき"
    except (KeyError, TypeError):
        pass


# --- embed_texts_batched ---

def test_embed_texts_batched_splits_into_32_chunks_and_concatenates(monkeypatch):
    calls = []

    def fake_embed_texts(engine_url, model, texts, timeout=30):
        calls.append(list(texts))
        return [[float(len(t))] for t in texts]

    monkeypatch.setattr(embed, "embed_texts", fake_embed_texts)

    texts = [f"t{i}" for i in range(33)]
    result = embed.embed_texts_batched("http://127.0.0.1:11434", "m", texts, batch=32)

    assert len(calls) == 2
    assert len(calls[0]) == 32
    assert len(calls[1]) == 1
    assert len(result) == 33
    assert result == [[float(len(t))] for t in texts]


def test_embed_texts_batched_single_call_when_under_batch_size(monkeypatch):
    calls = []

    def fake_embed_texts(engine_url, model, texts, timeout=30):
        calls.append(list(texts))
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", fake_embed_texts)

    result = embed.embed_texts_batched("http://127.0.0.1:11434", "m", ["a", "b"], batch=32)

    assert len(calls) == 1
    assert result == [[1.0], [1.0]]


def test_embed_texts_batched_empty_input_returns_empty_without_calling(monkeypatch):
    def fake_embed_texts(*a, **kw):
        raise AssertionError("空入力でembed_textsを呼んではいけない")

    monkeypatch.setattr(embed, "embed_texts", fake_embed_texts)

    result = embed.embed_texts_batched("http://127.0.0.1:11434", "m", [], batch=32)

    assert result == []


# --- cosine ---

def test_cosine_orthogonal_vectors_is_zero():
    assert embed.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_identical_vectors_is_one():
    assert abs(embed.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_cosine_opposite_vectors_is_minus_one():
    assert abs(embed.cosine([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9


def test_cosine_zero_vector_returns_zero():
    assert embed.cosine([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert embed.cosine([1.0, 2.0], [0.0, 0.0]) == 0.0


def test_cosine_mismatched_length_returns_zero():
    assert embed.cosine([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


# --- EMBED_QUERY_PREFIX / embed_query ---

def test_embed_query_prefix_literal_value():
    assert embed.EMBED_QUERY_PREFIX == "Instruct: Given a query, retrieve relevant memory passages\nQuery: "


def test_embed_query_prepends_prefix_and_returns_single_vector(monkeypatch):
    captured = {}

    def fake_embed_texts(engine_url, model, texts, timeout=30):
        captured["engine_url"] = engine_url
        captured["model"] = model
        captured["texts"] = texts
        return [[0.5, 0.6]]

    monkeypatch.setattr(embed, "embed_texts", fake_embed_texts)

    result = embed.embed_query("http://127.0.0.1:11434", "m", "夕飯なに食べた？")

    assert result == [0.5, 0.6]
    assert captured["texts"] == [embed.EMBED_QUERY_PREFIX + "夕飯なに食べた？"]
    assert captured["engine_url"] == "http://127.0.0.1:11434"
    assert captured["model"] == "m"


def test_embed_query_propagates_exception(monkeypatch):
    def fake_embed_texts(*a, **kw):
        raise RuntimeError("engine down")

    monkeypatch.setattr(embed, "embed_texts", fake_embed_texts)

    try:
        embed.embed_query("http://127.0.0.1:11434", "m", "x")
        assert False, "例外が送出されるべき"
    except RuntimeError:
        pass


# --- config.py: embeddingスキーマ既定 ---

def test_config_default_embedding_schema():
    import config
    assert config.DEFAULT_CONFIG["embedding"] == {
        "enabled": False,
        "engine_url": "http://127.0.0.1:11434",
        "model": "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0",
    }


def test_config_load_backfills_missing_embedding(tmp_path, monkeypatch):
    import importlib
    import config
    original_home = config.HOME
    monkeypatch.setenv("FIERIA_HOME", str(tmp_path / "fieria_home"))
    importlib.reload(config)
    try:
        cfg = config.load_config()
        del cfg["embedding"]
        config.save_config(cfg)

        reloaded = config.load_config()
        assert reloaded["embedding"] == {
            "enabled": False,
            "engine_url": "http://127.0.0.1:11434",
            "model": "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0",
        }
    finally:
        # importlib.reload(config)はモジュール単位でHOME/CONFIG_PATHを書き換えるため、
        # monkeypatchのenv差し戻しだけでは元に戻らない（他テストへの汚染を防ぐため
        # 明示的に元のHOMEへ再readloadする。test_tts.pyの同型テストにも同じ潜在バグが
        # あるが、そちらはalphabetical orderで後続テストに影響しないため未発現——
        # 触るとスコープ外になるのでここでは自テストのみ自衛する）。
        monkeypatch.setenv("FIERIA_HOME", original_home)
        importlib.reload(config)


def test_config_load_backfills_missing_field_in_existing_embedding(tmp_path, monkeypatch):
    import importlib
    import config
    original_home = config.HOME
    monkeypatch.setenv("FIERIA_HOME", str(tmp_path / "fieria_home"))
    importlib.reload(config)
    try:
        cfg = config.load_config()
        cfg["embedding"] = {"enabled": True}
        config.save_config(cfg)

        reloaded = config.load_config()
        assert reloaded["embedding"]["enabled"] is True
        assert reloaded["embedding"]["engine_url"] == "http://127.0.0.1:11434"
        assert reloaded["embedding"]["model"] == "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0"
    finally:
        monkeypatch.setenv("FIERIA_HOME", original_home)
        importlib.reload(config)
