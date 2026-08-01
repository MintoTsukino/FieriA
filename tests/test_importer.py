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


def test_split_short_text_single_chunk():
    assert importer._split_text("abc", limit=10) == ["abc"]


def test_split_respects_line_boundaries():
    text = "1234\n5678\nabcd\n"
    chunks = importer._split_text(text, limit=10)
    assert chunks == ["1234\n5678\n", "abcd\n"]
    assert "".join(chunks) == text


def test_split_oversized_single_line_kept_whole():
    text = "x" * 25
    assert importer._split_text(text, limit=10) == [text]


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0)


TOOL_REPLY = (
    "整理するじょ。\n"
    "```fieria-tool\n"
    '{"tool":"write_wiki","topic":"試験トピック","content":"本文の要点","mode":"append"}\n'
    "```\n"
    "```fieria-tool\n"
    '{"tool":"update_memory_index","line":"- [試験トピック](wiki/試験トピック.md) — テスト"}\n'
    "```\n"
)


def _staged_soul(tmp_path, name="取り込み.md", body="# 見出し\n中身じゃ"):
    sid = _soul()
    src = tmp_path / name
    src.write_text(body, encoding="utf-8")
    importer.stage_files(sid, [str(src)])
    return sid


def test_import_one_writes_wiki_and_moves_file(tmp_path):
    sid = _staged_soul(tmp_path)
    res = importer.import_one({}, FakeLLM([TOOL_REPLY]), sid, "取り込み.md")
    assert res["ok"] is True
    assert "本文の要点" in soul.read_file(sid, "wiki/試験トピック.md")
    assert "試験トピック" in soul.read_file(sid, "MEMORY.md")
    assert importer.list_inbox(sid) == []
    imported = os.listdir(os.path.join(soul.soul_dir(sid), "imported"))
    assert imported == ["取り込み.md"]


def test_import_one_system_text_has_name_and_index(tmp_path):
    sid = _staged_soul(tmp_path)
    soul.append_file(sid, "MEMORY.md", "- 既存索引の行\n")
    fake = FakeLLM([TOOL_REPLY])
    importer.import_one({}, fake, sid, "取り込み.md")
    system_text = fake.calls[0][0]["content"]
    assert fake.calls[0][0]["role"] == "system"
    assert "インポータ試験" in system_text
    assert "既存索引の行" in system_text
    user_text = fake.calls[0][1]["content"]
    assert "取り込み.md" in user_text
    assert "中身じゃ" in user_text


def test_import_one_disallowed_tool_not_executed(tmp_path):
    sid = _staged_soul(tmp_path)
    bad = (
        "```fieria-tool\n"
        '{"tool":"archive_memory","target":"wiki/x.md"}\n'
        "```\n" + TOOL_REPLY
    )
    res = importer.import_one({}, FakeLLM([bad]), sid, "取り込み.md")
    assert res["ok"] is True
    rejected = [op for op in res["ops"] if not op["ok"]]
    assert any(op["detail"] == "インポートでは使えないツール" for op in rejected)
    assert not os.path.exists(os.path.join(soul.soul_dir(sid), "archive"))


def test_import_one_no_tools_then_nudge(tmp_path):
    sid = _staged_soul(tmp_path)
    fake = FakeLLM(["読んだだけで何もしない", TOOL_REPLY])
    res = importer.import_one({}, fake, sid, "取り込み.md")
    assert res["ok"] is True
    assert len(fake.calls) == 2
    assert "ツール呼び出しが1つも実行されていない" in fake.calls[1][-1]["content"]


def test_import_one_total_failure_keeps_file(tmp_path):
    sid = _staged_soul(tmp_path)
    res = importer.import_one({}, FakeLLM(["何もしない1", "何もしない2"]), sid, "取り込み.md")
    assert res["ok"] is False
    assert res["detail"] == "記憶への書き込みが行われなかった"
    assert importer.list_inbox(sid) == ["取り込み.md"]


def test_import_one_llm_exception_keeps_file(tmp_path):
    sid = _staged_soul(tmp_path)

    class BoomLLM:
        def chat(self, messages, max_tokens=None):
            raise RuntimeError("接続断")

    res = importer.import_one({}, BoomLLM(), sid, "取り込み.md")
    assert res["ok"] is False
    assert importer.list_inbox(sid) == ["取り込み.md"]


def test_import_one_multichunk_calls_llm_per_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "CHUNK_CHARS", 10)
    sid = _staged_soul(tmp_path, body="1234\n5678\nabcd\n")
    fake = FakeLLM([TOOL_REPLY, TOOL_REPLY])
    res = importer.import_one({}, fake, sid, "取り込み.md")
    assert res["ok"] is True
    assert len(fake.calls) == 2
    assert "分割 1/2" in fake.calls[0][1]["content"]
    assert "分割 2/2" in fake.calls[1][1]["content"]


def test_import_one_non_dict_tool_call_does_not_crash(tmp_path):
    sid = _staged_soul(tmp_path)
    bad = "```fieria-tool\n[1, 2]\n```\n" + TOOL_REPLY
    res = importer.import_one({}, FakeLLM([bad]), sid, "取り込み.md")
    assert res["ok"] is True
    rejected = [op for op in res["ops"] if not op["ok"]]
    assert any(op["detail"] == "ツール呼び出しがオブジェクト形式でない" for op in rejected)


def _staged_two(tmp_path):
    sid = _soul()
    (tmp_path / "a.md").write_text("中身A", encoding="utf-8")
    (tmp_path / "b.md").write_text("中身B", encoding="utf-8")
    importer.stage_files(sid, [str(tmp_path / "a.md"), str(tmp_path / "b.md")])
    return sid


def test_run_import_processes_all_and_reports_progress(tmp_path):
    sid = _staged_two(tmp_path)
    events = []
    summary = importer.run_import({}, FakeLLM([TOOL_REPLY, TOOL_REPLY]), sid,
                                  on_progress=events.append)
    assert summary == {"total": 2, "done": 2, "failed": [], "stopped": False}
    assert [e["kind"] for e in events] == ["file_start", "file_done",
                                           "file_start", "file_done"]
    assert events[0]["file"] == "a.md" and events[0]["total"] == 2
    assert importer.list_inbox(sid) == []


def test_run_import_cancel_at_file_boundary(tmp_path):
    sid = _staged_two(tmp_path)
    summary = importer.run_import({}, FakeLLM([]), sid, should_stop=lambda: True)
    assert summary == {"total": 2, "done": 0, "failed": [], "stopped": True}
    assert importer.list_inbox(sid) == ["a.md", "b.md"]


def test_run_import_failure_continues_to_next(tmp_path):
    sid = _staged_two(tmp_path)
    fake = FakeLLM(["だめ1", "だめ2", TOOL_REPLY])
    summary = importer.run_import({}, fake, sid)
    assert summary["total"] == 2
    assert summary["done"] == 1
    assert summary["failed"] == [{"file": "a.md",
                                  "detail": "記憶への書き込みが行われなかった"}]
    assert importer.list_inbox(sid) == ["a.md"]


def test_run_import_exception_in_import_one_continues(tmp_path, monkeypatch):
    sid = _staged_two(tmp_path)
    real_import_one = importer.import_one

    def boom_then_real(cfg, llm, soul_id, filename, on_log=None):
        if filename == "a.md":
            raise OSError("ディスク満杯")
        return real_import_one(cfg, llm, soul_id, filename, on_log)

    monkeypatch.setattr(importer, "import_one", boom_then_real)
    summary = importer.run_import({}, FakeLLM([TOOL_REPLY]), sid)
    assert summary["total"] == 2
    assert summary["done"] == 1
    assert summary["failed"][0]["file"] == "a.md"
    assert "予期しない例外" in summary["failed"][0]["detail"]
    assert importer.list_inbox(sid) == ["a.md"]
