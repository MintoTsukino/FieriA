import os
import time


def _sid():
    import soul
    return soul.create_soul("検索テスト")


def test_search_finds_wiki_content_by_partial_match():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/UI談義.md", "きょうはインベントリUIの話をみんとちゃんとした。8枠で決着。")
    search.ensure_index(sid)
    hits = search.search(sid, "インベントリ")
    assert len(hits) == 1
    assert hits[0]["source"] == "wiki/UI談義.md"
    assert "インベントリ" in hits[0]["snippet"]


def test_search_finds_readings_content_by_partial_match():
    """readings/（資料層）はwiki等と同じ「soul_dir配下の全.md」走査に乗るだけで
    索引対象に含まれること（search.pyへの専用コード追加は不要な設計）。"""
    import search, soul
    sid = _sid()
    soul.write_file(sid, "readings/技術書A.md", "この資料には非同期処理の要点がまとめてある。")
    search.ensure_index(sid)
    hits = search.search(sid, "非同期処理")
    assert len(hits) == 1
    assert hits[0]["source"] == "readings/技術書A.md"


def test_search_finds_skill_content_by_partial_match():
    """skills/（手続き記憶）もwiki/readingsと同じ「soul_dir配下の全.md」走査に乗るだけで
    索引対象・連想対象に含まれること（search.pyへの専用コード追加は不要な設計）。"""
    import search, soul
    sid = _sid()
    soul.write_skill(sid, "査読手順", "査読の流れ", "まず誤字脱字を洗い出してから内容を見る。")
    search.ensure_index(sid)
    hits = search.search(sid, "誤字脱字")
    assert len(hits) == 1
    assert hits[0]["source"] == "skills/査読手順.md"


def test_search_finds_log_entry_with_who_and_date():
    import search, soul
    sid = _sid()
    soul.append_log(sid, "user", "明日8時に散歩しようね")
    search.ensure_index(sid)
    hits = search.search(sid, "散歩")
    assert len(hits) == 1
    assert hits[0]["who"] == "user"
    assert hits[0]["date"] == time.strftime("%Y-%m-%d")
    assert hits[0]["source"].startswith("logs/")


def test_search_empty_query_returns_empty_list():
    import search
    sid = _sid()
    assert search.search(sid, "") == []
    assert search.search(sid, "   ") == []


def test_search_no_match_returns_empty_list():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/話題A.md", "なんでもない内容")
    search.ensure_index(sid)
    assert search.search(sid, "存在しないはずのフレーズXYZ") == []


def test_search_before_ensure_index_returns_empty_list_without_crash():
    import search
    sid = _sid()
    # ensure_indexを一度も呼ばない = index.sqliteが存在しない状態
    assert search.search(sid, "なにか") == []


def test_ensure_index_lazy_rebuild_picks_up_new_file():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/話題A.md", "最初の内容だけ")
    search.ensure_index(sid)
    assert search.search(sid, "あとから追加した固有の単語") == []

    # mtime/sizeの変化を確実に検出させるため少し間を置く
    time.sleep(0.05)
    soul.write_file(sid, "wiki/話題B.md", "あとから追加した固有の単語がここにある")
    search.ensure_index(sid)
    hits = search.search(sid, "あとから追加した固有の単語")
    assert len(hits) == 1
    assert hits[0]["source"] == "wiki/話題B.md"


def test_ensure_index_no_rebuild_when_nothing_changed():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/話題A.md", "内容")
    search.ensure_index(sid)
    db_path = search._db_path(sid)
    mtime1 = os.path.getmtime(db_path)
    time.sleep(0.05)
    search.ensure_index(sid)  # 何も変わっていないので再構築しない = index.sqlite自体は触らない
    mtime2 = os.path.getmtime(db_path)
    assert mtime1 == mtime2


def test_short_query_uses_like_fallback_and_finds_two_char_word():
    # trigramトークナイザは3文字未満のMATCHクエリを常に0件で返す仕様（実機確認済み）。
    # 「散歩」のような2文字語はLIKEフォールバック経由でないと見つからない。
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/予定.md", "明日8時に散歩しようねという話をした")
    search.ensure_index(sid)
    hits = search.search(sid, "散歩")
    assert len(hits) == 1
    assert "散歩" in hits[0]["snippet"]


def test_short_query_with_like_wildcard_characters_does_not_crash_or_over_match():
    # LIKEフォールバック経路で query に %/_ が入っても、LIKEのワイルドカードとして
    # 暴発せず、そのままの文字列として扱われること（ESCAPE句のエスケープ確認）。
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/記号短文.md", "8%引きという表現をここに書いておく")
    search.ensure_index(sid)
    hits = search.search(sid, "8%")
    assert len(hits) == 1
    hits2 = search.search(sid, "9%")  # 別の数字なので本来ヒットしない
    assert hits2 == []


def test_corrupted_index_db_does_not_crash_and_self_heals():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/話題A.md", "回復確認用の内容")
    db_path = search._db_path(sid)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with open(db_path, "wb") as f:
        f.write(b"\x00\x01" + "ゴミバイトでは壊れたsqliteファイルを再現する".encode("utf-8") + b"\xff\xfe")

    # 例外を投げずに完走し、自己修復（再構築）できること
    search.ensure_index(sid)
    hits = search.search(sid, "回復確認用")
    assert len(hits) == 1


def test_search_on_broken_index_file_returns_empty_without_ensure_index():
    import search
    sid = _sid()
    db_path = search._db_path(sid)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with open(db_path, "wb") as f:
        f.write(b"\x00not a real sqlite db\xff")
    assert search.search(sid, "なにか") == []


def test_search_query_with_fts5_special_characters_does_not_crash():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/記号.md", "AND OR NOT みたいな記号入りクエリでも壊れないか確認")
    search.ensure_index(sid)
    # FTS5のクエリ構文にひっかかりやすい文字（"-, *, ", 等）を含めても例外を出さない
    for q in ['AND', '"引用符"', "みたいな-記号*入り", "he said \"hi\""]:
        search.search(sid, q)  # 例外を投げなければ良い


def test_search_memory_tool_via_execute_returns_formatted_hits():
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_file(sid, "wiki/約束.md", "8時に散歩する約束をした")
    r = mt.execute(sid, {"tool": "search_memory", "query": "散歩"})
    assert r["ok"]
    assert "約束" in r["detail"] or "散歩" in r["detail"]


def test_search_memory_tool_zero_hits_reports_not_found():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "search_memory", "query": "ぜったいに存在しないはずの単語列XYZ123"})
    assert r["ok"]
    assert "見つからなかった" in r["detail"]


class _FakeLLM:
    """test_engine.pyのFakeLLMと同型（tests/に__init__.pyが無くパッケージ跨ぎimportできない
    ため、ここでも同じ最小実装を持つ）。chat()が予め積んだ応答を順に返す。"""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0)


def test_engine_search_memory_feeds_back_and_rereplies():
    import engine, soul

    sid = soul.create_soul("検索差し戻しテスト", identity_text="テスト用の子。")
    soul.write_file(sid, "wiki/約束.md", "8時に散歩する約束をした")
    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    fake = _FakeLLM([
        '調べるじょ\n```fieria-tool\n{"tool": "search_memory", "query": "散歩"}\n```',
        "散歩の約束、見つけたじょ",
    ])
    eng = engine.Engine(cfg, fake, sid)
    out = eng.process_turn("前に散歩の話してたっけ？")
    assert out["reply"] == "散歩の約束、見つけたじょ"
    second_messages = fake.calls[1]
    assert any("散歩" in m["content"] for m in second_messages)


def test_search_finds_notes_history_content():
    """notes_history/（self_notes/user.mdの全文置換前の退避先）はwiki/readingsと同じ
    「soul_dir配下の全.md」走査に乗るだけで索引対象に含まれること（search.pyへの
    専用コード追加は不要な設計。2026-07-22 notes_history新設に伴う回帰テスト）。"""
    import search, soul
    sid = _sid()
    soul.update_self_notes(sid, "最近は落ち着いて執筆に向き合えている。\n")
    soul.update_self_notes(sid, "改訂後の自己認識。\n")

    search.ensure_index(sid)
    hits = search.search(sid, "落ち着いて執筆")

    assert len(hits) == 1
    assert hits[0]["source"].startswith("notes_history/self_notes-")


# --- ベクトル索引（セマンティック検索・2026-08-03追加） ---
# embed層(embed.embed_texts_batched)はmonkeypatchし、実Ollama・実ネットワークには触れない。


def _fake_embed_batched(vec_for=None, calls=None):
    """embed.embed_texts_batchedの差し替え用フェイク。calls(list)を渡すと
    呼び出しごとの入力textsを記録する。vec_forはtext->vecの関数（既定は長さベース）。"""
    def fake(engine_url, model, texts, batch=32):
        if calls is not None:
            calls.append(list(texts))
        if vec_for:
            return [vec_for(t) for t in texts]
        return [[float(len(t)), 0.0] for t in texts]
    return fake


def test_chunk_md_combines_paragraphs_up_to_about_500_chars():
    import search
    content = "\n\n".join(["あ" * 200] * 3)
    chunks = search._chunk_md(content)
    assert chunks == ["あ" * 200 + "\n\n" + "あ" * 200, "あ" * 200]


def test_chunk_md_attaches_heading_to_following_chunk():
    import search
    content = "# 見出し\n\n本文1つ目の段落。\n\n本文2つ目の段落。"
    chunks = search._chunk_md(content)
    assert len(chunks) == 1
    assert chunks[0].startswith("# 見出し")
    assert "本文1つ目の段落。" in chunks[0]
    assert "本文2つ目の段落。" in chunks[0]


def test_chunk_md_produces_no_empty_chunks():
    import search
    assert search._chunk_md("") == []
    assert search._chunk_md("\n\n\n\n   \n\n") == []


def test_update_vectors_second_call_only_embeds_new_fragments(monkeypatch):
    # create_soul直後のsoul_dirには既定生成ファイル群(identity.md等)があるため、
    # まず1回全部片付けてから「新規分だけ」の差分動作を検証する。
    import search, soul
    sid = _sid()
    calls = []
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched(calls=calls))
    cfg = {"engine_url": "http://127.0.0.1:11434", "model": "m"}
    search.update_vectors(sid, cfg)  # 既定ファイル群を先に埋め込み切る
    calls.clear()

    soul.write_file(sid, "wiki/話題A.md", "本文その1。ここに文章がある。")
    result1 = search.update_vectors(sid, cfg)
    assert result1 == {"done": 1, "pending": 0}
    assert len(calls) == 1

    # 何も変わっていない2回目呼び出し = 新規embed呼び出しなし
    result2 = search.update_vectors(sid, cfg)
    assert result2 == {"done": 0, "pending": 0}
    assert len(calls) == 1

    # 新規ファイル追加分だけ埋め込まれる
    soul.write_file(sid, "wiki/話題B.md", "本文その2。別の文章がある。")
    result3 = search.update_vectors(sid, cfg)
    assert result3 == {"done": 1, "pending": 0}
    assert len(calls) == 2


def test_update_vectors_cache_hit_skips_embed_call(monkeypatch):
    import search, soul
    sid = _sid()
    calls = []
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched(calls=calls))
    cfg = {"engine_url": "http://127.0.0.1:11434", "model": "m"}
    search.update_vectors(sid, cfg)  # 既定ファイル群を先に埋め込み切る
    calls.clear()

    soul.write_file(sid, "wiki/A.md", "重複するテキスト内容です")
    search.update_vectors(sid, cfg)
    assert len(calls) == 1
    calls.clear()

    # 全く同じ内容の別ファイル = 内容ハッシュが一致しキャッシュヒットするはず
    soul.write_file(sid, "wiki/B.md", "重複するテキスト内容です")
    result = search.update_vectors(sid, cfg)
    assert result == {"done": 1, "pending": 0}
    assert len(calls) == 0  # embed_texts_batchedは呼ばれていない（キャッシュヒット）


def test_update_vectors_model_change_discards_and_reembeds(monkeypatch):
    import search, soul
    sid = _sid()
    calls = []
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched(calls=calls))
    search.update_vectors(sid, {"engine_url": "u", "model": "model-1"})  # 既定ファイル群
    calls.clear()

    soul.write_file(sid, "wiki/A.md", "内容A")
    result1 = search.update_vectors(sid, {"engine_url": "u", "model": "model-1"})
    assert result1 == {"done": 1, "pending": 0}
    assert len(calls) == 1
    calls.clear()

    # モデル名が変わればvectors/emb_cacheごと全破棄——既存ファイル分も含めて丸ごと再埋め込み
    total_fragments = len(search._collect_fragments(soul.soul_dir(sid)))
    result2 = search.update_vectors(sid, {"engine_url": "u", "model": "model-2"})
    assert result2 == {"done": total_fragments, "pending": 0}
    assert len(calls) == 1


def test_update_vectors_removes_vectors_for_deleted_fragments(monkeypatch):
    import search, soul
    sid = _sid()
    soul.write_file(sid, "wiki/A.md", "内容A")
    soul.write_file(sid, "wiki/B.md", "内容B")
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched())
    cfg = {"engine_url": "u", "model": "m"}
    search.update_vectors(sid, cfg)

    con = __import__("sqlite3").connect(search._db_path(sid))
    keys_before = {r[0] for r in con.execute("SELECT doc_key FROM vectors")}
    con.close()
    assert any(k.startswith("wiki/B.md#") for k in keys_before)

    os.remove(os.path.join(soul.soul_dir(sid), "wiki", "B.md"))
    result = search.update_vectors(sid, cfg)
    assert result == {"done": 0, "pending": 0}

    con = __import__("sqlite3").connect(search._db_path(sid))
    keys_after = {r[0] for r in con.execute("SELECT doc_key FROM vectors")}
    con.close()
    assert not any(k.startswith("wiki/B.md#") for k in keys_after)
    assert any(k.startswith("wiki/A.md#") for k in keys_after)


def test_update_vectors_should_stop_halts_at_batch_boundary(monkeypatch):
    import search, soul
    sid = _sid()
    calls = []
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched(calls=calls))
    search.update_vectors(sid, {"engine_url": "u", "model": "m"})  # 既定ファイル群を先に片付ける
    calls.clear()

    for i in range(5):
        soul.write_file(sid, f"wiki/話題{i}.md", f"内容その{i}番目のテキストです")
    monkeypatch.setattr(search, "_EMBED_BATCH", 2)

    state = {"n": 1}

    def should_stop():
        state["n"] -= 1
        return state["n"] < 0

    result = search.update_vectors(
        sid, {"engine_url": "u", "model": "m"}, should_stop=should_stop)

    assert len(calls) == 1  # 最初のバッチ(2件)だけ処理され、2バッチ目の直前で停止
    assert result == {"done": 2, "pending": 3}


def test_update_vectors_swallows_embed_exception_and_returns_partial_result(monkeypatch):
    import search, soul
    sid = _sid()
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched())
    search.update_vectors(sid, {"engine_url": "u", "model": "m"})  # 既定ファイル群を先に片付ける
    soul.write_file(sid, "wiki/A.md", "内容A")

    def raising(engine_url, model, texts, batch=32):
        raise OSError("ollama not running")

    monkeypatch.setattr(search.embed, "embed_texts_batched", raising)
    result = search.update_vectors(sid, {"engine_url": "u", "model": "m"})
    assert result == {"done": 0, "pending": 1}


def test_semantic_search_ranks_by_cosine_similarity(monkeypatch):
    import search, soul

    def vec_for(text):
        return [1.0, 0.0] if "A" in text else [0.0, 1.0]

    sid = _sid()
    soul.write_file(sid, "wiki/A.md", "テキストA")
    soul.write_file(sid, "wiki/B.md", "テキストB")
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched(vec_for=vec_for))
    search.update_vectors(sid, {"engine_url": "u", "model": "m"})

    # 既定ファイル群にも"A"という文字が含まれない前提で、queryに最も近いのはwiki/A.mdのはず
    results = search.semantic_search(sid, [1.0, 0.0], limit=1)
    assert results[0]["source"] == "wiki/A.md"
    assert results[0]["score"] == 1.0


def test_semantic_search_returns_empty_when_no_vectors_built():
    import search
    sid = _sid()
    assert search.semantic_search(sid, [1.0, 0.0]) == []


def test_update_vectors_reembeds_changed_chunk_same_key(monkeypatch):
    """同じdoc_key（=同じ位置のチャンク）でも内容が変わったら再埋め込みされること。
    wikiは追記・書き換えが日常のため、これが無いと編集後も古いベクトルで検索され続ける
    （Task 2レビューで発見された編集追随の穴・2026-08-03）。"""
    import embed
    import search
    import soul as soul_mod
    sid = soul_mod.create_soul("編集追随テスト", "コア")
    soul_mod.write_file(sid, "wiki/対象.md", "最初の内容で埋める")

    calls = []

    def fake_batched(engine_url, model, texts, batch=32):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts_batched", fake_batched)
    cfg = {"enabled": True, "engine_url": "http://x", "model": "m1"}
    search.update_vectors(sid, cfg)
    n_first = sum(len(c) for c in calls)
    assert n_first >= 1

    # 内容を書き換える（doc_keyは同じ位置のまま）
    soul_mod.write_file(sid, "wiki/対象.md", "書き換えた新しい内容")
    calls.clear()
    search.update_vectors(sid, cfg)
    embedded = [t for c in calls for t in c]
    assert any("書き換えた新しい内容" in t for t in embedded), \
        "内容変更が再埋め込みされていない（古いベクトルが残る）"


def test_fts_rebuild_preserves_vectors(monkeypatch):
    """FTS再構築（記憶ファイルの変化で毎回起きる）がベクトルを道連れにしないこと。
    従来は_docs_rawをDROPせずCREATEしていたため2回目以降の再構築が必ず失敗し、
    修復経路がindex.sqliteをファイルごと削除→vectorsが全滅していた
    （実環境で発見・2026-08-03。会話1メッセージごとに全ベクトル消失）。"""
    import embed
    import search
    import soul as soul_mod
    sid = soul_mod.create_soul("再構築生存テスト", "コア")

    monkeypatch.setattr(embed, "embed_texts_batched",
                        lambda u, m, texts, batch=32: [[1.0, 0.0] for _ in texts])
    cfg = {"enabled": True, "engine_url": "http://x", "model": "m1"}
    search.ensure_index(sid)
    search.update_vectors(sid, cfg)

    import sqlite3
    con = sqlite3.connect(search._db_path(sid))
    n_before = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    con.close()
    assert n_before > 0

    # 記憶ファイルを追加してFTS再構築を誘発（実運用の「会話でログが増えた」に相当）
    soul_mod.write_file(sid, "wiki/新規.md", "再構築の引き金になる新しい記憶")
    search.ensure_index(sid)
    # さらにもう一回（「必ず失敗する2回目」を確実に踏む）
    soul_mod.write_file(sid, "wiki/新規2.md", "もう一度引き金")
    search.ensure_index(sid)

    con = sqlite3.connect(search._db_path(sid))
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "vectors" in tables, "再構築でvectorsテーブルごと消えた"
    n_after = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    con.close()
    assert n_after >= n_before, "再構築でベクトルが減った"
    # FTS側も正しく再構築されている（新ファイルが検索できる）
    hits = search.search(sid, "引き金になる新しい記憶")
    assert any(h["source"] == "wiki/新規.md" for h in hits)


def test_vector_count_zero_without_table_and_counts_after_update(monkeypatch):
    """埋め込み状況表示（設定画面の「埋め込み済み: N断片」）用。テーブル未作成でも
    例外を出さず0を返し、update_vectors後は断片数を返す。"""
    import embed
    import search
    import soul as soul_mod
    sid = soul_mod.create_soul("断片数テスト", "コア")
    assert search.vector_count(sid) == 0  # インデックス自体まだ無い
    monkeypatch.setattr(embed, "embed_texts_batched",
                        lambda u, m, texts, batch=32: [[1.0, 0.0] for _ in texts])
    search.update_vectors(sid, {"enabled": True, "engine_url": "http://x", "model": "m"})
    assert search.vector_count(sid) > 0


# --- imported/（インポートの原本控え）は検索対象から除外 2026-08-04 ---
# importer.py: 「原文は無改変のままsouls/<id>/imported/へ移す」。この原本と、
# AIが整理したwiki側の内容は同じ話題が2重に索引される（実機フィードバック:
# 連想記憶が原本の生テキストと整理済みwikiの両方をヒットさせてノイズになる）。
# backupsと同じ扱いで、imported/配下は索引対象から外す。

def test_search_excludes_imported_originals():
    import search, soul
    sid = _sid()
    soul.write_file(sid, "imported/むかしの日記.md", "きょうはインベントリUIの話をみんとちゃんとした。8枠で決着。")
    search.ensure_index(sid)
    hits = search.search(sid, "インベントリ")
    assert hits == []


def test_search_excludes_nested_imported_originals():
    """importer.pyのimported/は常にフラットだが、diary_import等の別経路がサブフォルダ
    （imported/diary/等）を作る場合にも同じ扱いにする（`d != "backups"`と同じ深さ非依存の除外）。"""
    import search, soul
    sid = _sid()
    soul.write_file(sid, "imported/diary/2026-07-01.md", "インベントリUIの話が出た日。")
    search.ensure_index(sid)
    assert search.search(sid, "インベントリ") == []


def test_search_still_finds_wiki_when_imported_has_same_topic():
    """除外の副作用でwiki側まで消えないこと（今回の狙いは重複解消であって全消しではない）。"""
    import search, soul
    sid = _sid()
    soul.write_file(sid, "imported/むかしの日記.md", "インベントリUIの生の記録。")
    soul.write_file(sid, "wiki/UI談義.md", "インベントリUIについて整理した内容。8枠で決着。")
    search.ensure_index(sid)
    hits = search.search(sid, "インベントリ")
    assert [h["source"] for h in hits] == ["wiki/UI談義.md"]


def test_update_vectors_skips_imported_originals(monkeypatch):
    """FTSだけでなく意味検索（埋め込み）側もimported/を対象外にする
    （_target_filesを両者が共用しているため、片方だけ直すと片手落ちになる）。"""
    import search, soul
    sid = _sid()
    calls = []
    monkeypatch.setattr(search.embed, "embed_texts_batched", _fake_embed_batched(calls=calls))
    cfg = {"engine_url": "http://127.0.0.1:11434", "model": "m"}
    search.update_vectors(sid, cfg)  # 既定ファイル群を先に埋め込み切る
    calls.clear()

    soul.write_file(sid, "imported/むかしの日記.md", "本文その1。ここに文章がある。")
    result = search.update_vectors(sid, cfg)

    assert result == {"done": 0, "pending": 0}
    assert calls == []
