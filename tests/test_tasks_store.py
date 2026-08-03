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
