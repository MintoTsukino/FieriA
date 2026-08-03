"""tasks_store.py — タスクタブのデータ層（tasks.md / tasks_done.md の書式契約）。

書式契約（v0.9系）:
    # tasks

    ## いまやる
    - 内容 ｜2026-08-12 ｜カテゴリ

    ## これから
    - 内容

- 1行1タスク。｜（全角）または|（半角）区切り。YYYY-MM-DD形式の断片が期限、
  それ以外の断片がカテゴリ（期限・カテゴリとも任意・順不同）
- 規約外の行（旧自由記述）も「内容だけのタスク」として拾う（救済パース）
- ✓を含む行は旧約束『済は✓済で残す』の遺産 → legacy_doneへ隔離し、
  呼び出し側（gui.py）が書き戻し時にtasks_done.mdへ移す（消さない）

完了タスクはtasks.mdから行を消し、tasks_done.mdへ
「- ✓ 内容 ｜カテゴリ ｜完了 YYYY-MM-DD」で追記する＝削除ではなく移動。

このモジュールは純関数のみ（ファイルI/Oはgui.py側）。テストしやすさと、
AI側ツール（update_tasksの全文置換）と書式の正を一箇所で共有するため。
"""
import re

NOW_HEADING = "## いまやる"
FUTURE_HEADING = "## これから"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DONE_DATE_RE = re.compile(r"^完了\s*(\d{4}-\d{2}-\d{2})$")
_BULLET_RE = re.compile(r"^(?:[-*・]\s*)")


def _strip_bullet(s):
    return _BULLET_RE.sub("", s.strip()).strip()


def parse_line(line):
    """1行を{"text","due","category"}へ。タスクとして成立しない行はNone。"""
    s = _strip_bullet(line)
    if not s:
        return None
    parts = [p.strip() for p in re.split(r"[｜|]", s)]
    text = parts[0]
    if not text:
        return None
    due = None
    cats = []
    for p in parts[1:]:
        if not p:
            continue
        if due is None and _DATE_RE.match(p):
            due = p
        else:
            cats.append(p)
    return {"text": text, "due": due, "category": " ".join(cats) or None}


def parse_tasks(md):
    """tasks.md全文を{"now","future","legacy_done"}へ。救済パース込み。

    - ## いまやる → nowセクション。それ以外の##見出し → futureセクション
    - 見出しより前の行・#タイトル行の下の行はfuture扱い
    - ✓を含む行は旧約束の済みタスク → legacy_doneへ生テキストで隔離
    """
    now, future, legacy_done = [], [], []
    section = "future"
    for line in md.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            if s.startswith("##") and "いまやる" in s:
                section = "now"
            elif s.startswith("##"):
                section = "future"
            continue
        if "✓" in s:
            legacy_done.append(_strip_bullet(s))
            continue
        t = parse_line(line)
        if t:
            (now if section == "now" else future).append(t)
    return {"now": now, "future": future, "legacy_done": legacy_done}


def _format_line(t):
    line = "- " + t["text"]
    if t.get("due"):
        line += " ｜" + t["due"]
    if t.get("category"):
        line += " ｜" + t["category"]
    return line


def serialize_tasks(data):
    """正規形へ書き出す。タスク0件でも両見出しを出す（AIが読んだとき
    テンプレが伝わる。prompt._has_bodyは#行を無視するためプロンプトには載らない）。"""
    parts = ["# tasks", "", NOW_HEADING]
    parts += [_format_line(t) for t in data.get("now", [])]
    parts += ["", FUTURE_HEADING]
    parts += [_format_line(t) for t in data.get("future", [])]
    return "\n".join(parts).rstrip() + "\n"


def format_done_line(task, done_date):
    line = "- ✓ " + task["text"]
    if task.get("category"):
        line += " ｜" + task["category"]
    line += " ｜完了 " + done_date
    return line


def parse_done(md):
    """tasks_done.md全文を[{"text","category","date"}...]へ。
    完了日は「完了 YYYY-MM-DD」断片から。無い行（移行遺産）はdate=None。

    行を｜/|で断片に分割してから各断片を分類する（parse_lineと同じ順不同の発想）。
    「完了 YYYY-MM-DD」断片が先頭以外のどこに来てもカテゴリを消さない
    （旧実装は完了断片より後ろの断片を無条件に切り捨てておりデータロスだった）。"""
    out = []
    for line in md.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = _strip_bullet(s)
        if s.startswith("✓"):
            s = s[1:].strip()
        parts = [p.strip() for p in re.split(r"[｜|]", s) if p.strip()]
        if not parts:
            continue
        text = parts[0]
        date = None
        cats = []
        for p in parts[1:]:
            m = _DONE_DATE_RE.match(p)
            if date is None and m:
                date = m.group(1)
            else:
                cats.append(p)
        out.append({"text": text, "category": " ".join(cats) or None, "date": date})
    return out
