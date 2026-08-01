"""tests/test_gui_scheduler_guard.py — gui.Bridge()がテスト中にSchedulerのdaemon
スレッドを起動しないことの確認。

2026-07-21コードレビュー指摘（Important）: テスト群がgui.Bridge()を37回生成し、
そのたびSchedulerのdaemonスレッドが起動していた（誰もstop()を呼ばない）。
実LLM呼び出しを防いでいたのは「初回tickまで60秒sleepがあり、テストは1.3秒で
終わる」という時限レースのみで、.envの実APIキーはFIERIA_HOME隔離の対象外の
ため、将来テストが60秒を超えると実キーで実APIへ到達しうる状態だった。
FIERIA_TESTING環境変数（conftest.pyで設定）でScheduler.start()自体を
構造的にスキップするガードをgui.Bridge.__init__に追加し、以下でそれを確認する。
"""
import os


def test_bridge_does_not_start_scheduler_thread_during_tests():
    assert os.environ.get("FIERIA_TESTING") == "1"  # conftest.pyでの前提
    import gui

    bridge = gui.Bridge()

    assert bridge._scheduler is not None  # インスタンス自体は生成される
    assert bridge._scheduler._thread is None  # startされていない＝スレッド未起動
