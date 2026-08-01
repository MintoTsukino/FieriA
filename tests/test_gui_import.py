"""インポートのGUIブリッジのテスト。ネイティブダイアログ部分は対象外（pick_backup_fileと同方針）。"""
import gui
import importer
import soul


def _bridge_with_soul(name="GUIインポート試験"):
    b = gui.Bridge()
    sid = soul.create_soul(name, identity_text="テスト用。")
    b._cfg["active_soul"] = sid
    return b, sid


def test_get_import_status_without_soul():
    b = gui.Bridge()
    b._cfg["active_soul"] = None
    st = b.get_import_status()
    assert st == {"count": 0, "files": [], "importing": False}


def test_get_import_status_lists_inbox(tmp_path):
    b, sid = _bridge_with_soul()
    (tmp_path / "x.md").write_text("X", encoding="utf-8")
    importer.stage_files(sid, [str(tmp_path / "x.md")])
    st = b.get_import_status()
    assert st["count"] == 1
    assert st["files"] == ["x.md"]
    assert st["importing"] is False


import time


class FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


def test_start_import_refuses_without_soul():
    b = gui.Bridge()
    b._cfg["active_soul"] = None
    assert "error" in b.start_import()


def test_start_import_refuses_empty_inbox():
    b, _sid = _bridge_with_soul("空inbox試験")
    assert "error" in b.start_import()


def test_start_import_refuses_when_chat_busy(tmp_path):
    b, sid = _bridge_with_soul("会話中試験")
    (tmp_path / "y.md").write_text("Y", encoding="utf-8")
    importer.stage_files(sid, [str(tmp_path / "y.md")])
    b._busy_turns = 1
    assert "error" in b.start_import()
    b._busy_turns = 0


def test_start_import_runs_and_pushes_done(monkeypatch, tmp_path):
    b, sid = _bridge_with_soul("実行試験")
    (tmp_path / "y.md").write_text("Y", encoding="utf-8")
    importer.stage_files(sid, [str(tmp_path / "y.md")])
    b._window = FakeWindow()

    def fake_run(cfg, llm, soul_id, on_progress=None, should_stop=None):
        assert soul_id == sid
        on_progress({"kind": "file_start", "file": "y.md", "index": 0, "total": 1})
        return {"total": 1, "done": 1, "failed": [], "stopped": False}

    monkeypatch.setattr(gui.importer, "run_import", fake_run)
    out = b.start_import()
    assert out == {"ok": True, "total": 1}
    for _ in range(200):
        if not b._importing:
            break
        time.sleep(0.05)
    assert b._importing is False
    joined = "\n".join(b._window.scripts)
    assert "onImportProgress" in joined
    assert '"kind": "file_start"' in joined
    assert '"kind": "done"' in joined


def test_cancel_import_sets_flag():
    b, _sid = _bridge_with_soul("キャンセル試験")
    b._import_stop = False
    assert b.cancel_import() == {"ok": True}
    assert b._import_stop is True


def test_send_message_refused_during_import():
    b, _sid = _bridge_with_soul("会話ガード試験")
    b._importing = True
    out = b.send_message("やあ")
    assert "error" in out
    b._importing = False


def test_start_import_rechecks_busy_under_lock_after_inbox_io(tmp_path, monkeypatch):
    """list_inbox（I/O窓）の最中に会話ターンが始まったケース: ロック下の再チェックで拒否される。"""
    b, sid = _bridge_with_soul("競合窓試験1")
    (tmp_path / "r.md").write_text("R", encoding="utf-8")
    importer.stage_files(sid, [str(tmp_path / "r.md")])
    real_list_inbox = importer.list_inbox

    def list_inbox_with_race(soul_id):
        b._busy_turns = 1  # I/O中にsend_messageが滑り込んだ状況を再現
        return real_list_inbox(soul_id)

    monkeypatch.setattr(gui.importer, "list_inbox", list_inbox_with_race)
    out = b.start_import()
    assert out == {"error": "会話の処理中はインポートできない"}
    assert b._importing is False
    b._busy_turns = 0


def test_send_message_rechecks_importing_under_lock(monkeypatch):
    """冒頭ガード通過後にインポートが始まったケース: 加算直前のロック下再チェックで拒否される。"""
    b, _sid = _bridge_with_soul("競合窓試験2")

    class MustNotRunEngine:
        def process_turn(self, text, images=None, on_delta=None, on_status=None):
            raise AssertionError("競合窓: インポート中にターンが走った")

    b._engine = MustNotRunEngine()

    def attach_with_race(images):
        b._importing = True  # ガード通過後にstart_importが滑り込んだ状況を再現
        return images

    monkeypatch.setattr(gui, "_attach_pdf_page_counts", attach_with_race)
    out = b.send_message("やあ")
    assert "error" in out and "インポート処理中" in out["error"]
    assert b._busy_turns == 0
    b._importing = False
