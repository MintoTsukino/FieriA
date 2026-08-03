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

--- ベクトル索引（セマンティック検索・2026-08-03追加） ---

FTS5の索引（docs/_docs_raw/_files_state）とは完全に独立したテーブル群
（vectors/emb_cache/_emb_meta）を同じindex.sqliteに持つ。_ensure_index_innerの
DROP対象には含めない（全再構築方式のFTSと、差分埋め込み方式のベクトルが
同じファイルの中で共存する設計。docs/plans/2026-08-03-semantic-recall.md 参照）。

- vectors: 断片1件=1行。doc_key = "{source}#{チャンク連番}"
- emb_cache: 内容ハッシュ(sha1)→ベクトルの永続キャッシュ。同一内容の断片が
  複数箇所にあっても・モデルが同じ限りOllama呼び出しは1回で済む
- _emb_meta: 最後にベクトルを構築したときのモデル名。configのモデルと不一致に
  なったらvectors/emb_cacheを全破棄して作り直す（次元・埋め込み空間の混在防止）
"""
import hashlib
import json
import os
import re
import sqlite3
import struct

import embed
import soul as soul_mod

INDEX_FILENAME = "index.sqlite"
_EMBED_BATCH = 32


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
        # _docs_rawもDROPしてから作る。従来はDROPせずCREATEしていたため2回目以降の
        # 再構築が必ず「already exists」で失敗し、ensure_indexの修復経路が
        # index.sqliteをファイルごと削除していた（FTSだけの時代は実害が見えなかったが、
        # vectorsが同居し始めてから「会話のたびに全ベクトル消失」として顕在化。
        # 2026-08-03実環境で発見）。ファイル削除の修復経路は本物の破損専用に戻す。
        con.execute("DROP TABLE IF EXISTS _docs_raw")
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


# --- ベクトル索引（セマンティック検索） ---

_BLANK_LINE_RE = re.compile(r"\n\s*\n")


def _chunk_md(content, target=500):
    """mdの本文を空行区切りの段落に分け、目安target字になるまで結合してチャンク化する。
    見出し行（#始まり）は単独チャンクにせず、次に来る非見出し段落と結合し、
    そのチャンクの先頭に付ける（文脈保持——見出しなしの断片だけ渡すと何の話か
    分からなくなるため）。空チャンクは出さない。"""
    paragraphs = [p.strip() for p in _BLANK_LINE_RE.split(content or "") if p.strip()]
    chunks = []
    current = []
    current_len = 0
    pending_heading = None

    for para in paragraphs:
        if para.startswith("#"):
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            pending_heading = para
            continue
        if current and current_len + len(para) > target:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        if not current and pending_heading:
            current.append(pending_heading)
            current_len += len(pending_heading)
            pending_heading = None
        current.append(para)
        current_len += len(para)

    if current:
        chunks.append("\n\n".join(current))
    elif pending_heading:
        # 見出しの直後に本文が続かないまま文書が終わる場合、見出しだけでも捨てない
        chunks.append(pending_heading)

    return chunks


def _iter_fragments(full_path, rel_path):
    """update_vectors用の断片列挙。(doc_key, source, content)を返す。
    mdは_chunk_mdで段落チャンクへ分割、logsは_log_docsそのまま(1行=1断片、既存の
    粒度を流用)。FTS用の_iter_docsとは独立した関数（FTS側は無変更のため触らない）。"""
    if rel_path.startswith("logs/") and rel_path.endswith(".jsonl"):
        for idx, doc in enumerate(_log_docs(full_path, rel_path)):
            yield f"{rel_path}#{idx}", rel_path, doc["content"]
    elif rel_path.endswith(".md"):
        doc = _md_doc(full_path, rel_path)
        if doc:
            for idx, chunk in enumerate(_chunk_md(doc["content"])):
                yield f"{rel_path}#{idx}", rel_path, chunk


def _pack_vec(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vec(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _ensure_vector_tables(con):
    # 編集追随のためvectorsは断片の内容ハッシュも持つ（同じdoc_keyでも内容が変われば
    # 再埋め込みする）。旧スキーマ（content_hash無し）を見つけたら作り直す
    # （この機能はまだ未リリースのため移行処理は不要・破棄で足りる）。
    try:
        con.execute("SELECT content_hash FROM vectors LIMIT 1")
    except sqlite3.OperationalError:
        con.execute("DROP TABLE IF EXISTS vectors")
    con.execute(
        "CREATE TABLE IF NOT EXISTS vectors "
        "(doc_key TEXT PRIMARY KEY, source TEXT, snippet TEXT, "
        "content_hash TEXT, vec BLOB)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS emb_cache "
        "(content_hash TEXT PRIMARY KEY, model TEXT, vec BLOB)")
    con.execute("CREATE TABLE IF NOT EXISTS _emb_meta (model TEXT)")


def _collect_fragments(base_dir):
    """soul_dir配下の現在の断片を{doc_key: (source, content)}として返す
    （空文字の断片は除外。ensure_index同様_target_filesを流用）。"""
    candidates = {}
    for rel in _target_files(base_dir):
        full = os.path.join(base_dir, rel)
        for doc_key, source, content in _iter_fragments(full, rel):
            if content and content.strip():
                candidates[doc_key] = (source, content)
    return candidates


def update_vectors(soul_id, cfg_embedding, should_stop=None):
    """未ベクトル断片を差分で埋め込む。モデル名がconfigと異なればvectors/emb_cacheを
    全破棄してから作り直す。emb_cacheヒットは即登録（Ollama呼び出し無し）、未ヒットは
    embed.embed_texts_batchedで_EMBED_BATCH件ずつ呼ぶ（should_stopはバッチ境界=次の
    Ollama呼び出しの直前でだけ確認する）。例外（Ollama未起動等）は握って、その時点までの
    途中結果を返す（doneは今回処理できた断片数、pendingは残数）。
    消えた断片（ファイル削除・編集で今回チャンク化に現れなかったdoc_key）のvectors行は
    今回の走査で削除する（記憶の削除・編集への追随）。"""
    done = 0
    pending = 0
    try:
        base_dir = soul_mod.soul_dir(soul_id)
        if not os.path.isdir(base_dir):
            return {"done": 0, "pending": 0}
        model = (cfg_embedding or {}).get("model", "")
        engine_url = (cfg_embedding or {}).get("engine_url", "")

        con = sqlite3.connect(_db_path(soul_id))
        try:
            _ensure_vector_tables(con)

            row = con.execute("SELECT model FROM _emb_meta LIMIT 1").fetchone()
            if row is None or row[0] != model:
                con.execute("DELETE FROM vectors")
                con.execute("DELETE FROM emb_cache")
                con.execute("DELETE FROM _emb_meta")
                con.execute("INSERT INTO _emb_meta(model) VALUES (?)", (model,))
                con.commit()

            candidates = _collect_fragments(base_dir)
            candidate_keys = set(candidates.keys())
            existing = {r[0]: r[1] for r in con.execute(
                "SELECT doc_key, content_hash FROM vectors")}

            stale = set(existing) - candidate_keys
            if stale:
                con.executemany(
                    "DELETE FROM vectors WHERE doc_key = ?", [(k,) for k in stale])
                con.commit()

            # 「無い」だけでなく「内容が変わった」（同じ位置のチャンクの書き換え・追記）
            # も対象にする——編集後に古いベクトルで検索され続けるのを防ぐ
            missing = []
            for k in candidates:
                h = hashlib.sha1(candidates[k][1].encode("utf-8")).hexdigest()
                if k not in existing or existing[k] != h:
                    missing.append(k)
            need_embed_keys = []
            for key in missing:
                source, content = candidates[key]
                content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
                cached = con.execute(
                    "SELECT vec FROM emb_cache WHERE content_hash = ? AND model = ?",
                    (content_hash, model)).fetchone()
                if cached:
                    con.execute(
                        "INSERT OR REPLACE INTO vectors"
                        "(doc_key, source, snippet, content_hash, vec) "
                        "VALUES (?,?,?,?,?)",
                        (key, source, content[:200], content_hash, cached[0]))
                    done += 1
                else:
                    need_embed_keys.append(key)
            con.commit()

            i = 0
            while i < len(need_embed_keys):
                if should_stop and should_stop():
                    break
                batch_keys = need_embed_keys[i:i + _EMBED_BATCH]
                batch_contents = [candidates[k][1] for k in batch_keys]
                try:
                    vecs = embed.embed_texts_batched(
                        engine_url, model, batch_contents, batch=_EMBED_BATCH)
                except Exception:
                    break
                for key, vec in zip(batch_keys, vecs):
                    source, content = candidates[key]
                    content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
                    vec_blob = _pack_vec(vec)
                    con.execute(
                        "INSERT OR REPLACE INTO emb_cache(content_hash, model, vec) "
                        "VALUES (?,?,?)",
                        (content_hash, model, vec_blob))
                    con.execute(
                        "INSERT OR REPLACE INTO vectors"
                        "(doc_key, source, snippet, content_hash, vec) "
                        "VALUES (?,?,?,?,?)",
                        (key, source, content[:200], content_hash, vec_blob))
                    done += 1
                con.commit()
                i += _EMBED_BATCH

            pending = len(missing) - done
        finally:
            con.close()
    except Exception:
        pass
    return {"done": done, "pending": pending}


def semantic_search(soul_id, query_vec, limit=8):
    """vectors全行とquery_vecのコサイン類似度で上位limit件を返す
    ([{"source","snippet","score"}, ...])。vectorsが空・未構築・例外は[]。"""
    try:
        db_path = _db_path(soul_id)
        if not os.path.isfile(db_path):
            return []
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute("SELECT source, snippet, vec FROM vectors").fetchall()
        finally:
            con.close()
        if not rows:
            return []
        scored = [
            {"source": source, "snippet": snippet,
             "score": embed.cosine(query_vec, _unpack_vec(vec_blob))}
            for source, snippet, vec_blob in rows]
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[: int(limit)]
    except Exception:
        return []


def vector_count(soul_id):
    """埋め込み済み断片数（設定画面の「埋め込み済み: N断片」表示用）。
    インデックス未作成・テーブル無し・例外はすべて0を返す（表示専用・会話を壊さない）。"""
    try:
        db_path = _db_path(soul_id)
        if not os.path.isfile(db_path):
            return 0
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        finally:
            con.close()
    except Exception:
        return 0
