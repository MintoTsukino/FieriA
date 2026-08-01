"""importer.py のテスト。FIERIA_HOMEはconftest.pyが一時ディレクトリに設定済み。"""
import os

import importer
import soul


def _soul():
    return soul.create_soul("インポータ試験", identity_text="テスト用。")


def test_list_inbox_empty_when_no_dir():
    sid = _soul()
    assert importer.list_inbox(sid) == []


def test_stage_files_copies_md_into_inbox(tmp_path):
    sid = _soul()
    src = tmp_path / "メモ.md"
    src.write_text("# メモ\n本文", encoding="utf-8")
    out = importer.stage_files(sid, [str(src)])
    assert out["staged"] == ["メモ.md"]
    assert out["skipped"] == []
    assert importer.list_inbox(sid) == ["メモ.md"]
    inbox_file = os.path.join(soul.soul_dir(sid), "inbox", "メモ.md")
    with open(inbox_file, encoding="utf-8") as f:
        assert f.read() == "# メモ\n本文"


def test_stage_folder_recurses_and_skips_non_md(tmp_path):
    sid = _soul()
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "sub" / "b.markdown").write_text("B", encoding="utf-8")
    (tmp_path / "sub" / "c.png").write_bytes(b"\x89PNG")
    out = importer.stage_files(sid, [str(tmp_path)])
    assert sorted(out["staged"]) == ["a.md", "b.markdown"]
    assert any(p.endswith("c.png") for p in out["skipped"])


def test_stage_same_name_gets_suffix(tmp_path):
    sid = _soul()
    one = tmp_path / "d1"
    two = tmp_path / "d2"
    one.mkdir()
    two.mkdir()
    (one / "同名.md").write_text("1", encoding="utf-8")
    (two / "同名.md").write_text("2", encoding="utf-8")
    importer.stage_files(sid, [str(one / "同名.md")])
    out = importer.stage_files(sid, [str(two / "同名.md")])
    assert out["staged"] == ["同名-2.md"]
    assert importer.list_inbox(sid) == ["同名-2.md", "同名.md"]


def test_stage_nonexistent_path_skipped():
    sid = _soul()
    out = importer.stage_files(sid, ["Z:/存在しない/道.md"])
    assert out["staged"] == []
    assert out["skipped"] == ["Z:/存在しない/道.md"]
