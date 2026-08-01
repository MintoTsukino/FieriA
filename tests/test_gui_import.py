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
