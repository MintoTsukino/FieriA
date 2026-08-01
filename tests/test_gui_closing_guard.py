"""tests/test_gui_closing_guard.py — LLM処理中(チャット応答中・定期ジョブ実行中)に
ウィンドウを閉じようとしたら確認ダイアログを出す機能の検証。

- Bridge._busy_turns（チャット応答中カウンタ）がsend_messageの成功/例外いずれの
  経路でも正しく増減すること
- Bridge.is_llm_busy()がチャット中/ジョブ実行中/どちらも無しの3パターンを
  正しく判定すること
- gui._make_on_closing()が組み立てるon_closingハンドラ相当のロジック（busyでなければ
  素通り・busyかつ確認キャンセルでFalse・確認OKで素通り・内部例外で素通り）

実ウィンドウ・実LLMは一切使わない（FakeWindow/FakeEngine/FakeSchedulerで代替）。
"""


class FakeWindow:
    """webview.Windowの代わり。create_confirmation_dialogの戻り値を差し替えられる。"""

    def __init__(self, confirm_result=True, raise_on_dialog=False):
        self.confirm_result = confirm_result
        self.raise_on_dialog = raise_on_dialog
        self.dialog_calls = []

    def create_confirmation_dialog(self, title, message):
        self.dialog_calls.append((title, message))
        if self.raise_on_dialog:
            raise RuntimeError("ダイアログ表示に失敗（想定内の異常系）")
        return self.confirm_result

    def evaluate_js(self, script):
        pass  # busy判定・確認ダイアログのテストでは使わないが、他経路との整合のため用意


class FakeEngine:
    """gui.Bridge.send_messageのbusyカウンタ検証用。process_turn実行中の
    bridge._busy_turnsの値を記録できる。"""

    def __init__(self, result=None, raise_exc=None, on_call=None):
        self.result = result or {"reply": "ok", "operations": [], "stopped": False,
                                  "recall_used": False, "history_len": 1}
        self.raise_exc = raise_exc
        self.on_call = on_call  # 呼ばれた時点でのbridgeを覗き見るためのフック

    def process_turn(self, text, images=None, on_delta=None, on_status=None):
        if self.on_call:
            self.on_call()
        if self.raise_exc:
            raise self.raise_exc
        return dict(self.result)


def _fresh_bridge():
    """test_gui_streaming.pyと同じ隔離パターン（活性soulを汚さない）。"""
    import config as config_mod
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None
    config_mod.save_config(bridge._cfg)
    bridge._engine = None
    return bridge


# --- Bridge._busy_turns / is_llm_busy ---

def test_busy_turns_increments_during_send_message_and_decrements_after_success():
    bridge = _fresh_bridge()
    observed = {}

    def capture():
        observed["busy_turns_during_call"] = bridge._busy_turns

    bridge._engine = FakeEngine(on_call=capture)

    assert bridge._busy_turns == 0
    bridge.send_message("こんにちは")

    assert observed["busy_turns_during_call"] == 1  # 呼び出し中はカウント済み
    assert bridge._busy_turns == 0  # 成功後はデクリメントされて0に戻る


def test_busy_turns_decrements_after_engine_raises():
    bridge = _fresh_bridge()
    bridge._engine = FakeEngine(raise_exc=RuntimeError("落ちたじょ"))

    result = bridge.send_message("こんにちは")

    assert "error" in result
    assert bridge._busy_turns == 0  # 例外経路でもfinallyでデクリメントされる


def test_is_llm_busy_true_while_chat_turn_in_progress():
    bridge = _fresh_bridge()
    observed = {}

    def capture():
        observed["busy_during_call"] = bridge.is_llm_busy()

    bridge._engine = FakeEngine(on_call=capture)

    bridge.send_message("こんにちは")

    assert observed["busy_during_call"] is True
    assert bridge.is_llm_busy() is False  # ターン終了後はFalseに戻る


def test_is_llm_busy_true_when_scheduler_job_running():
    bridge = _fresh_bridge()
    bridge._scheduler._running_job = True

    assert bridge.is_llm_busy() is True


def test_is_llm_busy_false_when_neither_chat_nor_job_running():
    bridge = _fresh_bridge()

    assert bridge.is_llm_busy() is False


# --- gui._make_on_closing ---
#
# end_sessionはハンドラ内でthreading.Thread(daemon=True).start()により非同期に
# 呼ばれるため、bridge.end_sessionをスタブに差し替えてthreading.Eventで
# 「呼ばれたかどうか」を待ち受ける（daemonスレッドへの直接ハンドルは
# _make_on_closingの外から取得できないため）。

def _stub_end_session(bridge):
    """bridge.end_sessionを差し替え、呼ばれたら即座にsetされるEventを返す。"""
    import threading
    called = threading.Event()

    def fake_end_session():
        called.set()
        return {"ok": True}

    bridge.end_session = fake_end_session
    return called


def test_on_closing_does_nothing_when_not_busy_and_still_ends_session():
    import gui
    bridge = _fresh_bridge()
    called = _stub_end_session(bridge)
    window = FakeWindow()
    handler = gui._make_on_closing(bridge, window)

    result = handler()

    assert result is None  # 確認なしで通常クローズ
    assert window.dialog_calls == []  # ダイアログ自体呼ばれていない
    assert called.wait(timeout=2)  # 通常クローズ時はend_sessionが呼ばれる


def test_on_closing_shows_dialog_and_returns_false_when_busy_and_cancelled():
    """キャンセル時はクローズ中止・end_sessionも呼ばれない
    （「終了しない」を選んだのにwrapupだけ確定してしまう事故を防ぐ検証）。"""
    import gui
    bridge = _fresh_bridge()
    called = _stub_end_session(bridge)
    bridge._busy_turns = 1  # 応答中を模す
    window = FakeWindow(confirm_result=False)  # キャンセル選択
    handler = gui._make_on_closing(bridge, window)

    result = handler()

    assert result is False  # クローズを中止
    assert len(window.dialog_calls) == 1
    assert not called.wait(timeout=0.3)  # end_sessionは呼ばれていない


def test_on_closing_returns_none_and_ends_session_when_busy_and_confirmed():
    import gui
    bridge = _fresh_bridge()
    called = _stub_end_session(bridge)
    bridge._busy_turns = 1
    window = FakeWindow(confirm_result=True)  # OK選択
    handler = gui._make_on_closing(bridge, window)

    result = handler()

    assert result is None  # 通常どおりクローズさせる
    assert len(window.dialog_calls) == 1
    assert called.wait(timeout=2)  # 確認OKならend_sessionも呼ばれる


def test_on_closing_swallows_internal_exception_and_still_ends_session():
    """確認ダイアログ自体の実装不備でアプリが閉じられなくなる事故の方が実害が
    大きいため、ハンドラ内の例外は握って通常クローズ(None)側に倒す。
    その場合もend_session（通常クローズ時と同じ後始末）は呼ばれる。"""
    import gui
    bridge = _fresh_bridge()
    called = _stub_end_session(bridge)
    bridge._busy_turns = 1
    window = FakeWindow(raise_on_dialog=True)
    handler = gui._make_on_closing(bridge, window)

    result = handler()  # 例外を投げないこと

    assert result is None
    assert called.wait(timeout=2)
