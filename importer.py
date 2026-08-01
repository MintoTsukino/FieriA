"""
importer.py — Markdown取り込み（inbox方式）

GUIで選んだ .md を souls/<id>/inbox/ に搬入し（stage_files）、
「インポート実行」で1ファイルずつ専用LLM呼び出しに読ませ、
AI本人が write_wiki / update_memory_index で自分の記憶に整理する。
原文は無改変のまま souls/<id>/imported/ へ移す。

会話エンジン（engine.py）とは独立した単発呼び出し。
会話側の固定注入トークンには一切影響しない。
"""

import os
import shutil

import memory_tools
import soul as soul_mod

INBOX_DIR = "inbox"
IMPORTED_DIR = "imported"
STAGE_EXTS = (".md", ".markdown", ".txt")


def _inbox_path(soul_id):
    return os.path.join(soul_mod.soul_dir(soul_id), INBOX_DIR)


def _imported_path(soul_id):
    return os.path.join(soul_mod.soul_dir(soul_id), IMPORTED_DIR)


def list_inbox(soul_id):
    """inbox内のファイル名一覧（昇順）。ディレクトリ未作成なら空。"""
    d = _inbox_path(soul_id)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))


def _unique_dest(dirpath, name):
    """dirpath内で衝突しないファイルパスを返す（同名なら -2, -3 …を付ける）。"""
    base, ext = os.path.splitext(name)
    cand, n = name, 1
    while os.path.exists(os.path.join(dirpath, cand)):
        n += 1
        cand = f"{base}-{n}{ext}"
    return os.path.join(dirpath, cand)


def _stage_one(src, inbox, staged, skipped):
    if not src.lower().endswith(STAGE_EXTS):
        skipped.append(src)
        return
    dest = _unique_dest(inbox, os.path.basename(src))
    try:
        shutil.copy2(src, dest)
        staged.append(os.path.basename(dest))
    except OSError:
        skipped.append(src)


def stage_files(soul_id, paths):
    """ファイル/フォルダのパス列をinboxへコピーする。フォルダは再帰で対象拡張子を拾う。"""
    inbox = _inbox_path(soul_id)
    os.makedirs(inbox, exist_ok=True)
    staged, skipped = [], []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirs, files in os.walk(p):
                dirs.sort()  # サブフォルダの走査順を決定的にする
                for f in sorted(files):
                    _stage_one(os.path.join(dirpath, f), inbox, staged, skipped)
        elif os.path.isfile(p):
            _stage_one(p, inbox, staged, skipped)
        else:
            skipped.append(p)
    return {"ok": True, "staged": staged, "skipped": skipped}


CHUNK_CHARS = 20000


def _split_text(text, limit=CHUNK_CHARS):
    """行境界で limit 文字以下のチャンクに割る。1行が limit を超える場合はその行を丸ごと1チャンクにする。"""
    if len(text) <= limit:
        return [text]
    chunks, buf, size = [], [], 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and buf:
            chunks.append("".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        chunks.append("".join(buf))
    return chunks


ALLOWED_TOOLS = frozenset({"write_wiki", "update_memory_index", "save_reading_note"})
IMPORT_MAX_TOKENS = 4000

IMPORT_SYSTEM = """あなたは「{name}」という名のAI。ユーザーがあなたの記憶として取り込みたい文書を渡してくる。
内容を読み、自分の記憶としてwikiへ整理すること。

手順:
1. 文書の主題を見極め、1〜3個のトピックにまとめる（無理に分割しない）
2. 各トピックを write_wiki でwikiに書く。丸写しでなく整理した本文にする。ただし固有名詞・日付・数値は正確に保つ
3. 新しくwikiを作ったときだけ、update_memory_index で索引に1行登録する。既にある索引行と重複させない

ツール呼び出しの書式（```fieria-tool フェンス、1フェンスに1つのJSON）:
```fieria-tool
{{"tool":"write_wiki","topic":"トピック名","content":"整理した内容","mode":"append"}}
```
```fieria-tool
{{"tool":"update_memory_index","line":"- [トピック名](wiki/トピック名.md) — ひとことの説明"}}
```
```fieria-tool
{{"tool":"save_reading_note","source":"元ファイル名","content":"感想や補足メモ"}}
```
使えるツールはこの3つだけ。原文は別途保存されるので全文コピーは不要。

現在のあなたの記憶索引（MEMORY.md）:
{index}"""

NUDGE = (
    "ツール呼び出しが1つも実行されていない。上の文書を記憶に整理するため、"
    "```fieria-tool フェンスで write_wiki（必要なら update_memory_index も）を実行して。"
)


def _build_system_text(soul_id):
    name = soul_mod.read_name(soul_id) or "AI"
    index = soul_mod.read_file(soul_id, "MEMORY.md").strip() or "（まだ空）"
    return IMPORT_SYSTEM.format(name=name, index=index)


def _chunk_message(filename, idx, total, chunk):
    head = f"次の文書をあなたの記憶として整理して。\n元ファイル名: {filename}"
    if total > 1:
        head += f"（分割 {idx + 1}/{total}）"
    return head + "\n\n---\n\n" + chunk


def import_one(cfg, llm, soul_id, filename, on_log=None):
    """inbox内の1ファイルを取り込む。1つでも書き込みが成功したら原本をimported/へ移す。

    チャンクごとに最大2回LLMを呼ぶ（1回目でツールが呼ばれなければNUDGEで1回だけ促す）。
    """
    src = os.path.join(_inbox_path(soul_id), filename)
    try:
        with open(src, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return {"ok": False, "file": filename, "ops": [], "detail": f"読み込み失敗: {e}"}

    system_text = _build_system_text(soul_id)
    chunks = _split_text(content, CHUNK_CHARS)
    ops = []
    wrote_any = False
    for idx, chunk in enumerate(chunks):
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": _chunk_message(filename, idx, len(chunks), chunk)},
        ]
        wrote = False
        for _attempt in range(2):
            try:
                raw = llm.chat(messages, max_tokens=IMPORT_MAX_TOKENS)
            except Exception as e:
                ops.append({"ok": False, "op": "llm", "detail": str(e)})
                break
            _reply, calls = memory_tools.extract_tool_calls(raw)
            for call in calls:
                tool = call.get("tool", "")
                if tool not in ALLOWED_TOOLS:
                    ops.append({"ok": False, "op": tool or "(不明)",
                                "detail": "インポートでは使えないツール"})
                    continue
                res = memory_tools.execute(soul_id, call, cfg)
                ops.append(res)
                if res.get("ok"):
                    wrote = True
                if on_log:
                    on_log(res)
            if wrote:
                break
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": NUDGE},
            ]
        wrote_any = wrote_any or wrote

    if not wrote_any:
        return {"ok": False, "file": filename, "ops": ops,
                "detail": "記憶への書き込みが行われなかった"}
    imported = _imported_path(soul_id)
    os.makedirs(imported, exist_ok=True)
    dest = _unique_dest(imported, filename)
    try:
        os.replace(src, dest)
    except OSError as e:
        return {"ok": False, "file": filename, "ops": ops, "detail": f"移動失敗: {e}"}
    return {"ok": True, "file": filename, "ops": ops, "detail": ""}
