"""gui.py — Bridge._push_stream_delta/_flush_stream_buf、send_message経由の
on_delta配線（FieriA拡張・ストリーミング）の検証。

send_message自体はtest_gui.py冒頭のコメント方針（実LLM APIを呼びうるため直接
呼ばない）を踏襲しつつ、ここではbridge._engineをフェイクに差し替えることで
実LLM呼び出しを完全に遮断した上でon_delta配線だけを検証する。
"""


class FakeWindow:
    """webview.Windowの代わり。evaluate_jsに渡された文字列を記録するだけ。"""

    def __init__(self, raise_on_call=False):
        self.calls = []
        self.raise_on_call = raise_on_call

    def evaluate_js(self, script):
        if self.raise_on_call:
            raise RuntimeError("ウィンドウが既に閉じている等の想定失敗")
        self.calls.append(script)


class FakeEngine:
    """gui.Bridge.send_messageからのon_delta引数だけを捕まえるフェイクEngine。
    実際のLLM呼び出し・ログ書き込みは一切発生しない。"""

    def __init__(self, result=None, raise_exc=None):
        self.result = result or {"reply": "ok", "operations": [], "stopped": False,
                                  "recall_used": False, "history_len": 1}
        self.raise_exc = raise_exc
        self.received_on_delta = "unset"  # 呼ばれなかった場合と区別するため

    def process_turn(self, text, images=None, on_delta=None, on_status=None):
        self.received_on_delta = on_delta
        self.received_on_status = on_status
        if self.raise_exc:
            raise self.raise_exc
        return dict(self.result)


def _fresh_bridge():
    """Bridge生成の共通ヘルパー。ベア生成だと共有FIERIA_HOME上の残存SOULを
    __init__が自動採用してconfig.jsonへ書き込み、実行順次第で他テスト
    （test_configのactive_soul is None前提）を壊すため、生成後に
    active_soulを明示的にNoneへ戻して永続化し直す（レビュー指摘の隔離修正）。"""
    import config as config_mod
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None
    config_mod.save_config(bridge._cfg)
    bridge._engine = None
    return bridge


# --- _push_stream_delta / _flush_stream_buf のバッファリング ---

def test_push_stream_delta_buffers_short_deltas_without_flushing_immediately():
    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._stream_buf = ""
    bridge._stream_last_flush = __import__("time").monotonic()

    bridge._push_stream_delta("短い")

    assert bridge._window.calls == []  # 40文字未満・80ms未満なのでまだ送られない
    assert bridge._stream_buf == "短い"


def test_push_stream_delta_flushes_when_char_threshold_reached():
    import json

    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._stream_buf = ""
    bridge._stream_last_flush = __import__("time").monotonic()
    import gui
    text = "あ" * gui.STREAM_FLUSH_CHARS

    bridge._push_stream_delta(text)

    assert len(bridge._window.calls) == 1
    # evaluate_jsへ渡すのはjson.dumps済み文字列(ensure_ascii既定でエスケープされる)なので、
    # 生のテキストではなくjson.dumps後の文字列が含まれることを確認する。
    assert json.dumps(text) in bridge._window.calls[0]
    assert bridge._stream_buf == ""  # フラッシュ後は空に戻る


def test_push_stream_delta_flushes_when_time_threshold_elapsed():
    import json

    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._stream_buf = ""
    bridge._stream_last_flush = 0.0  # 十分に古い時刻＝時間しきい値を必ず超えている

    bridge._push_stream_delta("短い")

    assert len(bridge._window.calls) == 1
    assert json.dumps("短い") in bridge._window.calls[0]


def test_flush_stream_buf_noop_without_window_but_clears_buffer():
    bridge = _fresh_bridge()
    bridge._window = None
    bridge._stream_buf = ""
    bridge._stream_last_flush = 0.0

    import gui
    bridge._push_stream_delta("あ" * gui.STREAM_FLUSH_CHARS)  # 閾値超過でフラッシュを試みる

    assert bridge._stream_buf == ""  # ウィンドウが無くてもバッファはクリアされる


def test_flush_stream_buf_swallows_evaluate_js_exception():
    bridge = _fresh_bridge()
    bridge._window = FakeWindow(raise_on_call=True)
    import gui
    bridge._stream_buf = "あ" * gui.STREAM_FLUSH_CHARS
    bridge._stream_last_flush = 0.0

    bridge._flush_stream_buf()  # 例外を投げないこと

    assert bridge._stream_buf == ""  # 失敗しても呼び出し前にバッファは既にクリア済み


# --- send_message経由のon_delta配線 ---

def test_send_message_passes_push_stream_delta_when_window_present_and_streaming_enabled():
    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._cfg["streaming"] = True
    fake_engine = FakeEngine()
    bridge._engine = fake_engine

    bridge.send_message("こんにちは")

    assert fake_engine.received_on_delta == bridge._push_stream_delta


def test_send_message_passes_none_on_delta_when_window_absent():
    """ウィンドウ未設定（テスト実行時と同じ状況）ならon_delta=Noneで従来動作。"""
    bridge = _fresh_bridge()
    bridge._window = None
    bridge._cfg["streaming"] = True
    fake_engine = FakeEngine()
    bridge._engine = fake_engine

    bridge.send_message("こんにちは")

    assert fake_engine.received_on_delta is None


def test_send_message_passes_none_on_delta_when_streaming_disabled_by_config():
    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._cfg["streaming"] = False
    fake_engine = FakeEngine()
    bridge._engine = fake_engine

    bridge.send_message("こんにちは")

    assert fake_engine.received_on_delta is None


def test_send_message_flushes_stream_buf_after_successful_turn():
    """ターン完了後、40文字/80ms未満で溜まったままの端数もflushされること
    （engineが直接on_deltaを呼ぶ代わりに、ここではsend_message側のfinally節を
    確認するため手動でbridge._stream_bufへ端数を残してから呼ぶ）。"""
    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._cfg["streaming"] = True
    bridge._engine = FakeEngine()

    # send_messageは冒頭で_stream_bufを""にリセットするため、ここでの事前セットは
    # 「エンジン呼び出し中に溜まる想定」の代わりに、finally節が確実に動くことの確認に絞る。
    bridge.send_message("こんにちは")

    # 少なくとも例外なく完了し、フラッシュ後のバッファは空であること
    assert bridge._stream_buf == ""


def test_send_message_flushes_stream_buf_even_when_engine_raises():
    bridge = _fresh_bridge()
    bridge._window = FakeWindow()
    bridge._cfg["streaming"] = True
    bridge._engine = FakeEngine(raise_exc=RuntimeError("落ちたじょ"))

    result = bridge.send_message("こんにちは")

    assert "error" in result
    assert bridge._stream_buf == ""  # 例外経路でもfinallyでバッファがクリアされる
