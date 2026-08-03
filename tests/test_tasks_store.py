"""tasks_store.py — タスクタブのデータ層（書式契約のパース・正規化）のテスト。"""
import tasks_store as ts


# --- parse_line ---

def test_parse_line_full():
    t = ts.parse_line("- ゲラ戻し ｜2026-08-12 ｜執筆")
    assert t == {"text": "ゲラ戻し", "due": "2026-08-12", "category": "執筆"}


def test_parse_line_content_only():
    t = ts.parse_line("- 定期検診（内科）")
    assert t == {"text": "定期検診（内科）", "due": None, "category": None}


def test_parse_line_order_free():
    """期限とカテゴリは順不同（カテゴリが先でも期限を拾う）"""
    t = ts.parse_line("- ゲラ戻し ｜執筆 ｜2026-08-12")
    assert t["due"] == "2026-08-12"
    assert t["category"] == "執筆"


def test_parse_line_ascii_pipe_and_bullets():
    """半角パイプ・別種の行頭記号（*・・）も受ける（救済パース）"""
    assert ts.parse_line("* 買い物 |2026-08-05")["due"] == "2026-08-05"
    assert ts.parse_line("・洗濯")["text"] == "洗濯"


def test_parse_line_plain_text_is_task():
    """行頭記号なしの地の文も内容だけのタスクとして拾う"""
    assert ts.parse_line("そのうち部屋の掃除")["text"] == "そのうち部屋の掃除"


def test_parse_line_empty_returns_none():
    assert ts.parse_line("") is None
    assert ts.parse_line("- ") is None
    assert ts.parse_line("  ") is None


def test_parse_line_multiple_categories_joined():
    t = ts.parse_line("- 原稿 ｜執筆 ｜急ぎ")
    assert t["category"] == "執筆 急ぎ"


# --- parse_tasks ---

CANON = """# tasks

## いまやる
- ゲラ戻し ｜2026-08-12 ｜執筆

## これから
- 体験版公開 ｜2026-08-23 ｜ゲーム開発
- 定期検診（内科）
"""


def test_parse_tasks_canonical():
    d = ts.parse_tasks(CANON)
    assert [t["text"] for t in d["now"]] == ["ゲラ戻し"]
    assert [t["text"] for t in d["future"]] == ["体験版公開", "定期検診（内科）"]
    assert d["legacy_done"] == []


def test_parse_tasks_freeform_all_future():
    """見出しなしの旧自由記述は全部these「これから」扱い（救済パース）"""
    d = ts.parse_tasks("# tasks\n- 買い物\nメモっぽい行\n")
    assert d["now"] == []
    assert [t["text"] for t in d["future"]] == ["買い物", "メモっぽい行"]


def test_parse_tasks_unknown_heading_is_future():
    d = ts.parse_tasks("## いまやる\n- A\n## 来月\n- B\n")
    assert [t["text"] for t in d["now"]] == ["A"]
    assert [t["text"] for t in d["future"]] == ["B"]


def test_parse_tasks_legacy_done_quarantined():
    """✓を含む行はタスクに混ぜず、legacy_doneに生テキストで隔離"""
    d = ts.parse_tasks("- 原稿を進める（✓済 2026-07-20）\n- 買い物\n")
    assert [t["text"] for t in d["future"]] == ["買い物"]
    assert d["legacy_done"] == ["原稿を進める（✓済 2026-07-20）"]


def test_parse_tasks_placeholder_empty():
    d = ts.parse_tasks("# tasks\n")
    assert d == {"now": [], "future": [], "legacy_done": []}


# --- serialize_tasks ---

def test_serialize_roundtrip():
    d = ts.parse_tasks(CANON)
    md = ts.serialize_tasks(d)
    assert ts.parse_tasks(md) == d


def test_serialize_empty_keeps_headings():
    """空でも両見出しを出す（AIが読んだときテンプレが伝わる。
    prompt._has_bodyは#行を無視するのでプロンプトには載らない）"""
    md = ts.serialize_tasks({"now": [], "future": []})
    assert "## いまやる" in md and "## これから" in md


def test_serialize_line_format():
    md = ts.serialize_tasks({"now": [{"text": "ゲラ戻し", "due": "2026-08-12",
                                       "category": "執筆"}], "future": []})
    assert "- ゲラ戻し ｜2026-08-12 ｜執筆" in md


# --- done側 ---

def test_format_and_parse_done_roundtrip():
    line = ts.format_done_line({"text": "ゲラ戻し", "due": None, "category": "執筆"},
                               "2026-08-04")
    assert line == "- ✓ ゲラ戻し ｜執筆 ｜完了 2026-08-04"
    got = ts.parse_done("# tasks done\n" + line + "\n")
    assert got == [{"text": "ゲラ戻し", "category": "執筆", "date": "2026-08-04"}]


def test_parse_done_legacy_line_without_date():
    """移行で入った日付なし行も落とさず拾う（dateはNone）"""
    got = ts.parse_done("- ✓ 昔の済みタスク\n")
    assert got == [{"text": "昔の済みタスク", "category": None, "date": None}]


def test_parse_done_category_after_date_not_lost():
    """完了断片の後ろに来たカテゴリも捨てない（順不同・データロス禁止）"""
    got = ts.parse_done("- ✓ ゲラ戻し ｜完了 2026-08-04 ｜執筆\n")
    assert got == [{"text": "ゲラ戻し", "category": "執筆", "date": "2026-08-04"}]


# --- 操作関数（すべて純関数・照合失敗はNone） ---

def test_add_task_to_now():
    md = ts.add_task(CANON, "now", "新タスク", due="2026-08-20", category="執筆")
    d = ts.parse_tasks(md)
    assert d["now"][-1] == {"text": "新タスク", "due": "2026-08-20",
                            "category": "執筆"}


def test_add_task_normalizes_freeform():
    """自由記述状態への追加でも、書き戻しは正規形になる"""
    md = ts.add_task("- 買い物\n", "now", "新タスク")
    assert md.startswith("# tasks")
    d = ts.parse_tasks(md)
    assert [t["text"] for t in d["future"]] == ["買い物"]
    assert [t["text"] for t in d["now"]] == ["新タスク"]


def test_move_task_now_to_future():
    md = ts.move_task(CANON, "now", 0, "ゲラ戻し")
    d = ts.parse_tasks(md)
    assert d["now"] == []
    assert d["future"][-1]["text"] == "ゲラ戻し"


def test_move_task_mismatch_returns_none():
    """indexズレ（AIが裏で書き換えた等）は照合失敗としてNone"""
    assert ts.move_task(CANON, "now", 0, "別のタスク") is None
    assert ts.move_task(CANON, "now", 99, "ゲラ戻し") is None


def test_delete_task():
    md = ts.delete_task(CANON, "future", 1, "定期検診（内科）")
    d = ts.parse_tasks(md)
    assert [t["text"] for t in d["future"]] == ["体験版公開"]


def test_complete_task_moves_to_done():
    """完了は削除ではなく移動: tasks.mdから消え、tasks_done.mdへ日付つきで残る"""
    md, done = ts.complete_task(CANON, "# tasks done\n", "now", 0, "ゲラ戻し",
                                "2026-08-04")
    assert ts.parse_tasks(md)["now"] == []
    got = ts.parse_done(done)
    assert got == [{"text": "ゲラ戻し", "category": "執筆", "date": "2026-08-04"}]


def test_restore_done_back_to_now():
    _, done = ts.complete_task(CANON, "", "now", 0, "ゲラ戻し", "2026-08-04")
    md2, done2 = ts.restore_done(ts.serialize_tasks({"now": [], "future": []}),
                                 done, "ゲラ戻し", "2026-08-04")
    assert ts.parse_tasks(md2)["now"][0]["text"] == "ゲラ戻し"
    assert ts.parse_done(done2) == []


def test_restore_done_only_today():
    """完了日が今日でない行は復帰対象外（今日の完了だけがUIに出る前提）"""
    done = "- ✓ 昔のタスク ｜完了 2026-08-01\n"
    assert ts.restore_done("# tasks\n", done, "昔のタスク", "2026-08-04") is None


def test_migrate_legacy_moves_and_normalizes():
    md = "- 原稿を進める（✓済 2026-07-20）\n- 買い物\n"
    new_md, new_done, n = ts.migrate_legacy(md, "", "2026-08-04")
    assert n == 1
    assert "✓" not in new_md
    assert "原稿を進める（✓済 2026-07-20）" in new_done
    assert "完了 2026-08-04" in new_done
    assert ts.parse_tasks(new_md)["future"][0]["text"] == "買い物"


def test_migrate_legacy_noop():
    new_md, new_done, n = ts.migrate_legacy(CANON, "", "2026-08-04")
    assert n == 0
    assert new_done == ""
