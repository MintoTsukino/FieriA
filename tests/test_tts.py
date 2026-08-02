"""FieriA拡張: 読み上げ（VOICEVOX互換エンジン）の検証。

tts._http_post/_http_getをmonkeypatchして実urlopen・実winsoundに一切到達させず、
組み立てられたリクエストと戻り値だけを検証する（test_llm_web_search.pyと同じ思想）。
"""

import json

import tts


# --- prepare_text ---

def test_prepare_text_strips_code_block_to_placeholder():
    text = "説明だよ\n```python\nprint(1)\n```\n続き"
    out = tts.prepare_text(text)
    assert "```" not in out
    assert "print(1)" not in out
    assert "（コード略）" in out


def test_prepare_text_strips_markdown_symbols_and_keeps_link_label():
    text = "# 見出し\n**強調**と`コード`と[表示名](https://example.invalid/x)"
    out = tts.prepare_text(text)
    assert "#" not in out
    assert "*" not in out
    assert "`" not in out
    assert "[" not in out and "]" not in out and "(" not in out and ")" not in out
    assert "表示名" in out
    assert "example.invalid" not in out


def test_prepare_text_strips_source_citation_block():
    text = "本文だよ\n\n---\n🔎 出典: [記事A](https://a.example/1)\n（検索語: 「foo」）"
    out = tts.prepare_text(text)
    assert "🔎" not in out
    assert "出典" not in out
    assert "---" not in out
    assert out.strip() == "本文だよ"


def test_prepare_text_strips_bare_urls():
    text = "見てね https://example.invalid/path?q=1 だよ"
    out = tts.prepare_text(text)
    assert "https://" not in out
    assert "見てね" in out and "だよ" in out


def test_prepare_text_clips_to_limit():
    text = "あ" * 1000
    out = tts.prepare_text(text, limit=600)
    assert len(out) == 600


def test_prepare_text_returns_empty_string_when_nothing_left():
    text = "```python\nprint(1)\n```".replace("print(1)", "print(1)")
    # コードブロックのみ→（コード略）が残るのでこれは空にならない例。
    # 本当に空になるケース：URLだけの本文。
    out = tts.prepare_text("https://example.invalid/only-url")
    assert out == ""


# --- synthesize ---

def _capture_http(monkeypatch):
    captured = {"post_calls": [], "responses": []}

    def fake_post(url, data=None, headers=None, timeout=15):
        captured["post_calls"].append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return captured["responses"].pop(0)

    monkeypatch.setattr(tts, "_http_post", fake_post)
    return captured


def test_synthesize_sends_expected_requests(monkeypatch):
    captured = _capture_http(monkeypatch)
    query_response = json.dumps({"speedScale": 1.0, "pitchScale": 0.0}).encode("utf-8")
    wav_response = b"RIFF-fake-wav-bytes"
    captured["responses"] = [query_response, wav_response]

    result = tts.synthesize("http://127.0.0.1:10101", "こんにちは", speaker=3, speed=1.5, timeout=20)

    assert result == wav_response
    assert len(captured["post_calls"]) == 2

    first = captured["post_calls"][0]
    assert first["url"].startswith("http://127.0.0.1:10101/audio_query?")
    assert "text=" in first["url"]
    assert "speaker=3" in first["url"]
    assert first["data"] is None
    assert first["timeout"] == 20

    second = captured["post_calls"][1]
    assert second["url"].startswith("http://127.0.0.1:10101/synthesis?")
    assert "speaker=3" in second["url"]
    sent_body = json.loads(second["data"].decode("utf-8"))
    assert sent_body["speedScale"] == 1.5
    assert sent_body["pitchScale"] == 0.0
    assert second["headers"]["Content-Type"] == "application/json"


def test_synthesize_raises_on_http_failure(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=15):
        raise OSError("connection refused")

    monkeypatch.setattr(tts, "_http_post", fake_post)

    try:
        tts.synthesize("http://127.0.0.1:10101", "hi", speaker=0)
        assert False, "例外が送出されるべき"
    except OSError:
        pass


# --- play_wav_async / stop ---

def test_play_wav_async_returns_false_when_winsound_unavailable(monkeypatch):
    monkeypatch.setattr(tts, "winsound", None)
    assert tts.play_wav_async(b"dummy") is False


def test_play_wav_async_plays_from_temp_file(monkeypatch, tmp_path):
    """winsoundはSND_MEMORY+SND_ASYNCを仕様上サポートしない（実機E2Eで発覚した
    RuntimeError）ため、一時ファイル経由のSND_FILENAME再生であることを固定する。
    順序も重要: PURGE（前の再生停止）→ファイル書き込み→FILENAME再生。"""
    calls = []

    class FakeWinsound:
        SND_FILENAME = 131072
        SND_ASYNC = 1
        SND_PURGE = 64

        def PlaySound(self, data, flags):
            calls.append((data, flags))

    fake = FakeWinsound()
    monkeypatch.setattr(tts, "winsound", fake)
    tmp_wav = str(tmp_path / "t.wav")
    monkeypatch.setattr(tts, "_tts_temp_path", lambda: tmp_wav)
    result = tts.play_wav_async(b"dummy-wav")
    assert result is True
    assert calls == [(None, fake.SND_PURGE),
                     (tmp_wav, fake.SND_FILENAME | fake.SND_ASYNC)]
    with open(tmp_wav, "rb") as f:
        assert f.read() == b"dummy-wav"


def test_stop_calls_purge_when_winsound_available(monkeypatch):
    calls = []

    class FakeWinsound:
        SND_PURGE = 64

        def PlaySound(self, data, flags):
            calls.append((data, flags))

    fake = FakeWinsound()
    monkeypatch.setattr(tts, "winsound", fake)
    tts.stop()
    assert calls == [(None, fake.SND_PURGE)]


def test_stop_is_noop_when_winsound_unavailable(monkeypatch):
    monkeypatch.setattr(tts, "winsound", None)
    tts.stop()  # 例外を出さずに戻ること


# --- speak ---

def test_speak_returns_false_when_disabled_and_never_calls_synthesize(monkeypatch):
    called = {"n": 0}

    def fake_synthesize(*a, **kw):
        called["n"] += 1
        return b"wav"

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    cfg_tts = {"enabled": False, "engine_url": "http://127.0.0.1:10101", "speaker": 0, "speed": 1.0}

    result = tts.speak(cfg_tts, "こんにちは")

    assert result is False
    assert called["n"] == 0


def test_speak_returns_false_when_synthesize_raises(monkeypatch):
    def fake_synthesize(*a, **kw):
        raise RuntimeError("engine down")

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(tts, "play_wav_async", lambda wav: True)
    cfg_tts = {"enabled": True, "engine_url": "http://127.0.0.1:10101", "speaker": 0, "speed": 1.0}

    result = tts.speak(cfg_tts, "こんにちは")

    assert result is False


def test_speak_returns_false_when_prepared_text_is_empty(monkeypatch):
    called = {"n": 0}

    def fake_synthesize(*a, **kw):
        called["n"] += 1
        return b"wav"

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    cfg_tts = {"enabled": True, "engine_url": "http://127.0.0.1:10101", "speaker": 0, "speed": 1.0}

    result = tts.speak(cfg_tts, "https://example.invalid/only-url")

    assert result is False
    assert called["n"] == 0


def test_speak_calls_synthesize_and_play_on_success(monkeypatch):
    captured = {}

    def fake_synthesize(engine_url, text, speaker, speed=1.0, timeout=15):
        captured["engine_url"] = engine_url
        captured["text"] = text
        captured["speaker"] = speaker
        captured["speed"] = speed
        return b"wav-bytes"

    def fake_play(wav_bytes):
        captured["played"] = wav_bytes
        return True

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(tts, "play_wav_async", fake_play)
    cfg_tts = {"enabled": True, "engine_url": "http://127.0.0.1:10101", "speaker": 5, "speed": 1.2}

    result = tts.speak(cfg_tts, "やっほー")

    assert result is True
    assert captured["engine_url"] == "http://127.0.0.1:10101"
    assert captured["speaker"] == 5
    assert captured["speed"] == 1.2
    assert captured["played"] == b"wav-bytes"


# --- list_speakers ---

def _capture_get(monkeypatch, response_bytes):
    captured = {}

    def fake_get(url, headers=None, timeout=5):
        captured["url"] = url
        captured["timeout"] = timeout
        return response_bytes

    monkeypatch.setattr(tts, "_http_get", fake_get)
    return captured


def test_list_speakers_formats_name_and_styles(monkeypatch):
    raw = json.dumps([
        {"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 3}, {"name": "あまあま", "id": 1}]},
        {"name": "四国めたん", "speaker_uuid": "xxx", "styles": [{"name": "ノーマル", "id": 2}]},
    ]).encode("utf-8")
    captured = _capture_get(monkeypatch, raw)

    result = tts.list_speakers("http://127.0.0.1:10101", timeout=7)

    assert captured["url"] == "http://127.0.0.1:10101/speakers"
    assert captured["timeout"] == 7
    assert result == [
        {"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 3}, {"name": "あまあま", "id": 1}]},
        {"name": "四国めたん", "styles": [{"name": "ノーマル", "id": 2}]},
    ]


def test_list_speakers_raises_on_http_failure(monkeypatch):
    def fake_get(url, headers=None, timeout=5):
        raise OSError("connection refused")

    monkeypatch.setattr(tts, "_http_get", fake_get)

    try:
        tts.list_speakers("http://127.0.0.1:10101")
        assert False, "例外が送出されるべき"
    except OSError:
        pass


# --- config.py: ttsスキーマ既定 ---

def test_config_default_tts_schema():
    import config
    assert config.DEFAULT_CONFIG["tts"] == {
        "enabled": False,
        "engine_url": "http://127.0.0.1:10101",
        "speaker": 0,
        "speed": 1.0,
    }


def test_config_load_backfills_missing_tts(tmp_path, monkeypatch):
    import importlib
    import config
    monkeypatch.setenv("FIERIA_HOME", str(tmp_path / "fieria_home"))
    importlib.reload(config)

    cfg = config.load_config()
    del cfg["tts"]
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["tts"] == {
        "enabled": False,
        "engine_url": "http://127.0.0.1:10101",
        "speaker": 0,
        "speed": 1.0,
    }


def test_config_load_backfills_missing_field_in_existing_tts(tmp_path, monkeypatch):
    import importlib
    import config
    monkeypatch.setenv("FIERIA_HOME", str(tmp_path / "fieria_home"))
    importlib.reload(config)

    cfg = config.load_config()
    cfg["tts"] = {"enabled": True}
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["tts"]["enabled"] is True
    assert reloaded["tts"]["engine_url"] == "http://127.0.0.1:10101"
    assert reloaded["tts"]["speaker"] == 0
    assert reloaded["tts"]["speed"] == 1.0
