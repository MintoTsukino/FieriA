"""search.py — 記憶ファイル群のFTS5全文検索。SQLite3.34+のtrigramトークナイザを使う
（日本語はデフォルトunicode61トークナイザだと分かち書きされず検索にならないため必須）。
py -3.10のsqlite3で trigram tokenizer が使えることは実機確認済み(sqlite_version 3.40.1)。

設計原則:「インデックスは派生物——壊れてもファイルから全再構築できる」。
index.sqliteは soul_dir(soul_id)/index.sqlite に置く。消えても・壊れてもensure_indexが
次回呼び出し時に作り直す（正本は常にMarkdown/JSONL）。

索引対象: soul_dir配下の全*.mdファイル（identity/self_notes/user/MEMORY/chronicle一式/
wiki/sacred）と logs/*.jsonl（1エントリ=1行）。index.sqlite自身と"backups"という名の
ディレクトリ配下は除外する。

差分更新はしない。対象ファイルの(パス,mtime,サイズ)集合が前回構築時と変わっていたら
全再構築する（シンプル優先。魂の規模なら全再構築でも高速）。前回状態はindex.sqlite内の
_files_stateテーブルに保存する。

例外は全てここで握って空リスト/無処理にする（会話を壊さない設計書§8-3と同じ思想）。
"""
import json
import os
import sqlite3

import soul as soul_mod

INDEX_FILENAME = "index.sqlite"


def _db_path(soul_id):
    return os.path.join(soul_mod.soul_dir(soul_id), INDEX_FILENAME)


def _target_files(base_dir):
    """soul_dir配下の索引対象ファイル一覧（'/'区切りの相対パス、ソート済み）を返す。"""
    targets = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d != "backups"]
        for fname in files:
            if fname == INDEX_FILENAME or fname.startswith(INDEX_FILENAME + "-"):
                continue  # index.sqlite本体・-journal/-wal等の副産物
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, base_dir).replace(os.sep, "/")
            if fname.endswith(".md") or (rel.startswith("logs/") and fname.endswith(".jsonl")):
                targets.append(rel)
    return sorted(targets)


def _file_state(base_dir, rel_paths):
    state = {}
    for rel in rel_paths:
        full = os.path.join(base_dir, rel)
        try:
            st = os.stat(full)
        except OSError:
            continue
        state[rel] = (st.st_mtime, st.st_size)
    return state


def _log_docs(full_path, rel_path):
    """logs/YYYY-MM-DD.jsonl の1行=1エントリ(who/text/ts)を1文書として返す。"""
    date = os.path.basename(rel_path)
    if date.endswith(".jsonl"):
        date = date[: -len(".jsonl")]
    with open(full_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            text = entry.get("text", "")
            if not (text or "").strip():
                continue
            yield {"source": rel_path, "date": date, "who": entry.get("who", ""),
                   "content": text}


def _md_doc(full_path, rel_path):
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.strip():
        return None
    return {"source": rel_path, "date": "", "who": "", "content": content}


def _iter_docs(full_path, rel_path):
    if rel_path.startswith("logs/") and rel_path.endswith(".jsonl"):
        yield from _log_docs(full_path, rel_path)
    elif rel_path.endswith(".md"):
        doc = _md_doc(full_path, rel_path)
        if doc:
            yield doc


def _ensure_index_inner(soul_id):
    base_dir = soul_mod.soul_dir(soul_id)
    if not os.path.isdir(base_dir):
        return
    rel_paths = _target_files(base_dir)
    new_state = _file_state(base_dir, rel_paths)
    con = sqlite3.connect(_db_path(soul_id))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS _files_state "
            "(path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL)")
        old_state = {path: (mtime, size) for path, mtime, size in
                     con.execute("SELECT path, mtime, size FROM _files_state")}
        if old_state == new_state:
            return  # 索引は最新のまま。再構築不要
        con.execute("DROP TABLE IF EXISTS docs")
        con.execute(
            "CREATE VIRTUAL TABLE docs USING fts5("
            "source UNINDEXED, date UNINDEXED, who UNINDEXED, content, "
            "tokenize='trigram')")
        # trigramトークナイザは3文字未満のMATCHクエリを常に0件で返す仕様（実機確認済み）。
        # 「散歩」等の2文字語が拾えないと実用にならないため、素の平テーブルも並行して
        # 持ち、短いクエリだけLIKEにフォールバックする（_ensure_index_innerが両方を
        # 同じdocから作るので二重管理にはならない）。
        con.execute(
            "CREATE TABLE _docs_raw "
            "(id INTEGER PRIMARY KEY, source TEXT, date TEXT, who TEXT, content TEXT)")
        con.execute("DELETE FROM _files_state")
        for rel in rel_paths:
            full = os.path.join(base_dir, rel)
            for doc in _iter_docs(full, rel):
                con.execute(
                    "INSERT INTO docs(source, date, who, content) VALUES (?,?,?,?)",
                    (doc["source"], doc["date"], doc["who"], doc["content"]))
                con.execute(
                    "INSERT INTO _docs_raw(source, date, who, content) VALUES (?,?,?,?)",
                    (doc["source"], doc["date"], doc["who"], doc["content"]))
        for path, (mtime, size) in new_state.items():
            con.execute(
                "INSERT INTO _files_state(path, mtime, size) VALUES (?,?,?)",
                (path, mtime, size))
        con.commit()
    finally:
        con.close()


def ensure_index(soul_id):
    """索引を最新化する（lazy再構築）。壊れたindex.sqliteに当たったら1回だけ
    ファイルごと消して作り直す。それでも失敗したら諦める（会話を壊さない）。"""
    try:
        _ensure_index_inner(soul_id)
        return
    except Exception:
        pass
    try:
        db_path = _db_path(soul_id)
        if os.path.exists(db_path):
            os.remove(db_path)
        _ensure_index_inner(soul_id)
    except Exception:
        pass


def _like_escape(query):
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _manual_snippet(content, query, context=20):
    """LIKEフォールバック用の素朴なスニペット抽出。snippet()同等の見た目に揃える。"""
    idx = content.find(query)
    if idx == -1:
        return content[:context * 2]
    start = max(0, idx - context)
    end = min(len(content), idx + len(query) + context)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return (prefix + content[start:idx] + "「" + content[idx:idx + len(query)] + "」"
            + content[idx + len(query):end] + suffix)


def search(soul_id, query, limit=8):
    """全文検索する。例外・0件・索引未構築は全て空リストで返す（会話を壊さない）。

    trigramトークナイザは3文字未満のMATCHクエリを常に0件で返す仕様のため、
    3文字未満のクエリだけ平テーブル(_docs_raw)へのLIKE検索にフォールバックする。
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        db_path = _db_path(soul_id)
        if not os.path.isfile(db_path):
            return []
        con = sqlite3.connect(db_path)
        try:
            if len(query) < 3:
                pattern = "%" + _like_escape(query) + "%"
                rows = con.execute(
                    "SELECT source, date, who, content FROM _docs_raw "
                    "WHERE content LIKE ? ESCAPE '\\' ORDER BY id LIMIT ?",
                    (pattern, int(limit))).fetchall()
                results = [
                    {"source": source, "date": date or "", "who": who or "",
                     "snippet": _manual_snippet(content, query)}
                    for source, date, who, content in rows]
            else:
                # FTS5クエリ構文（-, ", * 等）に引っかからないよう、常にフレーズリテラルとして渡す
                escaped = '"' + query.replace('"', '""') + '"'
                rows = con.execute(
                    "SELECT source, date, who, "
                    "snippet(docs, 3, '「', '」', '…', 40) "
                    "FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
                    (escaped, int(limit))).fetchall()
                results = [
                    {"source": source, "date": date or "", "who": who or "", "snippet": snip}
                    for source, date, who, snip in rows]
        finally:
            con.close()
        return results
    except Exception:
        return []
