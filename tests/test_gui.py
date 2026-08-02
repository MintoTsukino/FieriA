"""tests/test_gui.py — gui.Bridge の SOUL識別まわりの入口ガード。

2026-07-20コードレビュー指摘（Important）: get_soul_identity/update_soul_identityが
存在しないsoul_idを検証せずsoul.pyへ素通ししていたため、実際に
(a) ゴーストディレクトリの無言生成（souls/配下に骨格の無い中途半端なフォルダができる）
(b) soul_id自体へのトラバーサル文字列（"..\\x"等）でSOULS_DIR外へ書き込み
の2件が起きていた（soul._safe_pathはrel_path側のトラバーサルしか見ておらず、
soul_id自体は無防備だったため）。本ファイルはBridge単体（pywebview実ウィンドウ無し）で
その防御を確認する。gui.Bridge()を直接インスタンス化するパターンは
tests/test_env_keys.py（test_get_settings_never_exposes_raw_api_key等）を踏襲。
FIERIA_HOMEはconftest.pyで一時ディレクトリに隔離済み。

2026-07-20 追記（ff30e6b後の続報）: 同種の入口ガード漏れがswitch_soul(soul_id)にも
残っていた。switch_soulはconfigのactive_soulを検証なしで直接書き換えるだけなので、
その場では何も起きないが、後続のsend_message（Engine.process_turn→soul.append_log→
soul._safe_path）がそのactive_soulをsoul_id引数として使うため、存在しない/トラバーサル
soul_idを一度switch_soulに通しておくとSOULS_DIR外への書き込みが起きる（実行確認済み）。
get_soul_identity/update_soul_identityと同じ_soul_existsガードをswitch_soulの先頭
（_maybe_wrapup/config書き換えより前）に追加した。以下のテストはBridge.switch_soul単体の
入口ガードのみを検証する。実際のsend_message/Engine.process_turnは本物のLLM APIを
呼び得るため、このファイルからは一切呼ばない。
"""
import json
import os


def test_get_soul_identity_rejects_unknown_soul_id_without_creating_directory():
    import gui
    import soul
    bridge = gui.Bridge()
    fake_id = "no-such-soul-get"
    d = soul.soul_dir(fake_id)
    assert not os.path.isdir(d)

    result = bridge.get_soul_identity(fake_id)

    assert result["error"]
    assert result["core"] == ""
    assert result["speech_style"] == ""
    assert not os.path.isdir(d)  # ゴーストディレクトリが作られていない


def test_update_soul_identity_rejects_unknown_soul_id_without_creating_directory():
    import gui
    import soul
    bridge = gui.Bridge()
    fake_id = "no-such-soul-update"
    d = soul.soul_dir(fake_id)
    assert not os.path.isdir(d)

    result = bridge.update_soul_identity(fake_id, "勝手に書いた核", "勝手に書いた口調")

    assert result == {"ok": False, "error": "SOULが見つからない"}
    assert not os.path.isdir(d)  # ゴーストディレクトリが作られていない


def test_create_soul_inherit_user_from_rejects_unknown_soul_id_without_creating_directory():
    """create_soulのinherit_user_fromに存在しないsoul_idを渡した場合、_soul_existsで
    弾かれて新SOULのディレクトリすら作られないこと（同種の入口ガード。上のget/updateと
    同じ思想：無効なidを検証なしでsoul.pyへ素通ししない）。"""
    import gui
    bridge = gui.Bridge()
    before = {s["id"] for s in gui.soul_mod.list_souls()}

    result = bridge.create_soul("引き継ぎ失敗テスト", "", "", inherit_user_from="no-such-source")

    assert result.get("error") == "引き継ぎ元のSOULが見つからない"
    after = {s["id"] for s in gui.soul_mod.list_souls()}
    assert after == before  # 新SOULが作られていない


def test_create_soul_inherit_user_from_copies_existing_soul_user_notes():
    import gui
    bridge = gui.Bridge()
    src = bridge.create_soul("引き継ぎ元GUIテスト", "", "")
    src_id = src["active_soul"]
    gui.soul_mod.write_file(src_id, "user.md", "# user\n\nみんとちゃんはASD/ADHD。\n")

    result = bridge.create_soul("引き継ぎ先GUIテスト", "", "", inherit_user_from=src_id)

    new_id = result["active_soul"]
    body = gui.soul_mod.read_file(new_id, "user.md")
    assert "みんとちゃんはASD/ADHD" in body
    assert "引き継いだ" in body


def test_get_scheduled_jobs_returns_all_jobs_with_self_reflection_opt_in():
    """self_reflectionは人格の核（identity）を本人が書き換える機能、wiki_gardeningは
    wiki本文をLLMが書き換える機能なので、どちらもデフォルトOFF（オプトイン）。
    他のジョブは従来どおりデフォルトON。"""
    import gui
    bridge = gui.Bridge()

    jobs = bridge.get_scheduled_jobs()

    ids = {j["id"] for j in jobs}
    assert ids == {"daily_chronicle", "weekly_digest", "monthly_digest", "index_maintenance",
                   "self_reflection", "wiki_gardening"}
    by_id = {j["id"]: j for j in jobs}
    assert by_id["self_reflection"]["enabled"] is False
    assert by_id["wiki_gardening"]["enabled"] is False
    for job_id in ("daily_chronicle", "weekly_digest", "monthly_digest", "index_maintenance"):
        assert by_id[job_id]["enabled"] is True


def test_set_scheduled_job_persists_disabled_state():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.set_scheduled_job("weekly_digest", False)

    weekly = next(j for j in result if j["id"] == "weekly_digest")
    assert weekly["enabled"] is False
    reloaded = config_mod.load_config()
    assert reloaded["scheduled_jobs"]["weekly_digest"] is False


def test_update_soul_identity_rejects_traversal_soul_id_without_escaping_souls_dir():
    """soul_id自体に".."を含む文字列を渡しても、SOULS_DIRの外には一切書き込まれないこと。"""
    import gui
    import soul
    bridge = gui.Bridge()
    traversal_id = "..\\evil-soul"
    escaped_dir = os.path.abspath(os.path.join(soul.SOULS_DIR, "..", "evil-soul"))

    result = bridge.update_soul_identity(traversal_id, "核", "口調")

    assert result == {"ok": False, "error": "SOULが見つからない"}
    assert not os.path.isdir(escaped_dir)
    assert not os.path.isdir(soul.soul_dir(traversal_id))


def test_get_soul_identity_still_works_for_real_soul():
    """存在チェックの追加が正常系を壊していないことの確認（偽陽性ガードの回帰防止）。"""
    import gui
    import soul
    sid = soul.create_soul("ブリッジ正常系テスト", identity_text="核だよ", speech_style="タメ口")
    bridge = gui.Bridge()

    result = bridge.get_soul_identity(sid)

    assert "error" not in result
    assert result["core"] == "核だよ"
    assert result["speech_style"] == "タメ口"


def test_update_soul_identity_still_works_for_real_soul():
    import gui
    import soul
    sid = soul.create_soul("ブリッジ更新正常系テスト", identity_text="旧核")
    bridge = gui.Bridge()

    result = bridge.update_soul_identity(sid, "新核", "新口調")

    assert result == {"ok": True}
    parts = soul.read_identity_parts(sid)
    assert parts["core"] == "新核"
    assert parts["speech_style"] == "新口調"


def test_switch_soul_rejects_unknown_soul_id_without_changing_config_or_creating_directory(monkeypatch):
    """存在しないsoul_idでswitch_soulを呼んでも、(a) errorが返る (b) config.jsonの
    active_soulが（メモリ上・ディスク上とも）書き換わらない (c) SOULS_DIR配下にゴースト
    フォルダが作られない、の3点を確認する。switch_soulのガードは_soul_existsの時点で
    即returnするため、本来は_maybe_wrapup（LLM.chat()を呼びうる唯一の経路）にも
    到達しないが、そのガード配置自体に安全性を依存させないよう、念のため
    write_daily_chronicleをこのテストの間だけ無害化しておく（呼ばれない前提を、
    呼ばれても無害という前提に格上げする）。"""
    import config as config_mod
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    bridge = gui.Bridge()
    before_active_soul = config_mod.load_config().get("active_soul")
    fake_id = "no-such-soul-switch"
    d = soul.soul_dir(fake_id)
    assert not os.path.isdir(d)

    result = bridge.switch_soul(fake_id)

    assert result["error"]
    assert bridge._cfg.get("active_soul") == before_active_soul  # メモリ上も不変
    assert config_mod.load_config().get("active_soul") == before_active_soul  # ディスクも不変
    assert not os.path.isdir(d)  # ゴーストディレクトリが作られていない


def test_switch_soul_still_works_for_real_soul(monkeypatch):
    """存在チェックの追加が正常系を壊していないことの確認（偽陽性ガードの回帰防止）。
    switch_soulは内部で_maybe_wrapup→write_daily_chronicle→本物のllm.chat()に
    到達しうる経路を持つ（活性soulに当日ログがある場合）。他テストが共有FIERIA_HOME上に
    残したactive_soul/ログの組み合わせに安全性を依存させたくないため、
    write_daily_chronicle自体をこのテストの間だけ無害化する。"""
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    sid = soul.create_soul("ブリッジ切替正常系テスト", identity_text="核")
    bridge = gui.Bridge()

    result = bridge.switch_soul(sid)

    assert "error" not in result
    assert result["active_soul"] == sid
    assert bridge._cfg["active_soul"] == sid


def test_boot_includes_today_log_for_active_soul():
    """起動時に画面へ復元する当日ログ（表示専用）。boot()はsend_message/process_turnを
    一切経由しない（Engine.chat()を呼ぶ経路が無い）ため、実LLM呼び出しの心配なく直接呼べる。
    active_soulはswitch_soul経由だと_maybe_wrapupが本物のllm.chat()に到達しうる
    （既存テスト参照）ため、ここでは_cfgへ直接セットしてboot()単体の挙動だけを見る。"""
    import gui
    import soul
    sid = soul.create_soul("起動時ログ復元テスト", identity_text="核")
    soul.append_log(sid, "user", "こんにちは")
    soul.append_log(sid, "ai", "にゃっほー")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    data = bridge.boot()

    assert [e["who"] for e in data["today_log"]] == ["user", "ai"]
    assert data["today_log"][0]["text"] == "こんにちは"
    assert data["today_log"][1]["text"] == "にゃっほー"


def test_boot_today_log_empty_when_no_active_soul():
    """active_soul未設定ならtoday_logは空配列（設計判断: gui.pyのboot()コメント参照）。"""
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None

    data = bridge.boot()

    assert data["today_log"] == []


def test_ensure_engine_restores_today_log_into_engine_messages(monkeypatch):
    """_ensure_engine()がEngine生成直後にrestore_todayを呼び、当日ログがengine.messages
    （LLMの実会話コンテキスト）にも入ること。switch_soul経由でトリガーする（_ensure_engineは
    switch_soul内部から呼ばれる）ため、_maybe_wrapupの本物llm.chat()到達を避ける目的で
    他のswitch_soulテストと同じくwrite_daily_chronicleを無害化する。実LLM呼び出しは発生しない
    （FakeLLM/process_turnを一切経由しないため）。"""
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    sid = soul.create_soul("復元コンテキストテスト", identity_text="核")
    soul.append_log(sid, "user", "文脈に残ってほしい発言")
    soul.append_log(sid, "ai", "文脈に残ってほしい返事")
    bridge = gui.Bridge()

    result = bridge.switch_soul(sid)

    assert "error" not in result
    assert bridge._engine is not None
    assert bridge._engine.messages == [
        {"role": "user", "content": "文脈に残ってほしい発言"},
        {"role": "assistant", "content": "文脈に残ってほしい返事"},
    ]


def test_ensure_engine_does_not_restore_when_restore_turns_is_zero(monkeypatch):
    """restore_turns=0ならengine.messagesへの復元をスキップすること。"""
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    sid = soul.create_soul("復元オフテスト", identity_text="核")
    soul.append_log(sid, "user", "残らないはずの発言")
    bridge = gui.Bridge()
    bridge._cfg["restore_turns"] = 0

    result = bridge.switch_soul(sid)

    assert "error" not in result
    assert bridge._engine.messages == []


def test_switch_soul_includes_today_log_for_switched_to_soul(monkeypatch):
    """switch_soul後のtoday_logが「切替先」SOULの当日ログになっていること。switch_soulは
    最終的にself.boot()を返す実装なので、boot()側のtoday_log対応がそのまま効く設計。
    _maybe_wrapupが切替前soulに対し本物のllm.chat()へ到達しうる経路を持つため、
    test_switch_soul_still_works_for_real_soulと同じくwrite_daily_chronicleを無害化する。"""
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    sid = soul.create_soul("切替先ログ復元テスト", identity_text="核")
    soul.append_log(sid, "user", "切替後のひとこと")
    bridge = gui.Bridge()

    result = bridge.switch_soul(sid)

    assert "error" not in result
    assert [e["who"] for e in result["today_log"]] == ["user"]
    assert result["today_log"][0]["text"] == "切替後のひとこと"


# --- 過去の会話ログ閲覧（読むだけ。スレッド再開・編集は思想的に不採用） ---

def test_list_log_dates_returns_newest_first():
    """logs/配下の*.jsonlから日付部分を新しい順で返す。"""
    import gui
    import soul
    sid = soul.create_soul("ログ日付一覧テスト", identity_text="核")
    soul.append_file(sid, os.path.join("logs", "2026-01-01.jsonl"),
                      json.dumps({"who": "user", "text": "元日", "ts": "2026-01-01T00:00:00"},
                                 ensure_ascii=False) + "\n")
    soul.append_file(sid, os.path.join("logs", "2026-02-15.jsonl"),
                      json.dumps({"who": "user", "text": "節分過ぎ", "ts": "2026-02-15T00:00:00"},
                                 ensure_ascii=False) + "\n")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    dates = bridge.list_log_dates()

    assert dates == ["2026-02-15", "2026-01-01"]


def test_list_log_dates_empty_when_no_active_soul():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None

    assert bridge.list_log_dates() == []


def test_read_log_returns_parsed_entries():
    import gui
    import soul
    sid = soul.create_soul("ログ内容テスト", identity_text="核")
    soul.append_file(sid, os.path.join("logs", "2026-03-03.jsonl"),
                      json.dumps({"who": "user", "text": "こんにちは", "ts": "2026-03-03T09:00:00"},
                                 ensure_ascii=False) + "\n" +
                      json.dumps({"who": "ai", "text": "にゃっほー", "ts": "2026-03-03T09:00:05"},
                                 ensure_ascii=False) + "\n")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    entries = bridge.read_log("2026-03-03")

    assert [e["who"] for e in entries] == ["user", "ai"]
    assert entries[0]["text"] == "こんにちは"
    assert entries[1]["text"] == "にゃっほー"


def test_read_log_skips_broken_lines():
    import gui
    import soul
    sid = soul.create_soul("壊れた行スキップテスト", identity_text="核")
    good = json.dumps({"who": "user", "text": "正常な行", "ts": "2026-03-04T09:00:00"},
                       ensure_ascii=False)
    soul.append_file(sid, os.path.join("logs", "2026-03-04.jsonl"),
                      "{this is not json\n" + good + "\n")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    entries = bridge.read_log("2026-03-04")

    assert len(entries) == 1
    assert entries[0]["text"] == "正常な行"


def test_read_log_returns_empty_for_nonexistent_date():
    import gui
    import soul
    sid = soul.create_soul("存在しない日付テスト", identity_text="核")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    assert bridge.read_log("2026-12-31") == []


def test_read_log_rejects_traversal_date_without_escaping_souls_dir():
    """dateはファイル名に直接使われるため、トラバーサル文字列を渡しても
    SOULS_DIRの外を読みに行かず、例外にもならず空リストを返すこと。"""
    import gui
    import soul
    sid = soul.create_soul("ログトラバーサルテスト", identity_text="核")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    entries = bridge.read_log("../../../etc/passwd")

    assert entries == []


def test_get_latest_diary_returns_most_recent_entry():
    import gui
    import soul
    sid = soul.create_soul("最新日記Bridgeテスト", identity_text="核")
    soul.write_file(sid, "chronicle/2026-07-18.md", "# 7/18\n古い方\n")
    soul.write_file(sid, "chronicle/2026-07-20.md", "# 7/20\n新しい方\n")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    diary = bridge.get_latest_diary()

    assert diary["date"] == "2026-07-20"
    assert "新しい方" in diary["text"]


def test_get_latest_diary_empty_shape_when_no_entries():
    import gui
    import soul
    sid = soul.create_soul("日記無しBridgeテスト", identity_text="核")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    assert bridge.get_latest_diary() == {"date": None, "text": ""}


def test_get_latest_diary_empty_shape_when_no_active_soul():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None

    assert bridge.get_latest_diary() == {"date": None, "text": ""}


def test_read_log_empty_when_no_active_soul():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None

    assert bridge.read_log("2026-01-01") == []


# --- backup_souls: 「1フォルダ=1魂、丸ごとコピーで引っ越し」の実務手段。
# souls/・config.json・roles.jsonをzipに、.envは絶対に含めないことを検証する
# （2026-07-21追加）。2026-07-22以降 .env/xai_oauth.json/openai_codex_oauth.json は
# 正式にconfig.HOME直下へ移設された（誤って置かれた場合の防御ではなく実運用の配置）が、
# backup_souls()はsouls/・config.json・roles.jsonしか個別追加しない実装のため、
# これらも変わらずzipに含まれないはず。

def test_backup_souls_creates_zip_with_soul_and_config():
    import gui
    import soul
    import config as config_mod
    import roles as roles_mod
    import zipfile

    sid = soul.create_soul("バックアップ対象", identity_text="核データ")
    roles_mod.list_roles()  # roles.jsonを実体化させる
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    result = bridge.backup_souls()

    assert result["ok"] is True
    assert os.path.isfile(result["path"])
    assert result["path"].endswith(".zip")
    assert isinstance(result["size_mb"], float)

    with zipfile.ZipFile(result["path"]) as zf:
        names = zf.namelist()
        # zip内は常に"/"区切り（zipfile.write時にreplace済み）
        assert f"souls/{sid}/identity.md" in names
        assert "config.json" in names
        assert "roles.json" in names

        identity_bytes = zf.read(f"souls/{sid}/identity.md")
        assert "核データ" in identity_bytes.decode("utf-8")


def test_backup_souls_never_includes_env_file():
    """2026-07-22以降、.envはconfig.HOME直下（backup_soulsがwalkするのと同じ
    ディレクトリ）に正式に置かれるようになった。backup_soulsはsouls/・config.json・
    roles.jsonしか個別追加しない許可リスト方式のため、同じ場所に.envが実在していても
    zipに紛れ込まないことを確認する（ダミー値を置き、実.envの中身には一切触れない）。"""
    import gui
    import soul
    import config as config_mod
    import zipfile

    soul.create_soul("env漏洩防止テスト", identity_text="核")
    env_path = os.path.join(config_mod.HOME, ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("SOME_API_KEY=super-secret-value\n")

    bridge = gui.Bridge()
    result = bridge.backup_souls()

    assert result["ok"] is True
    with zipfile.ZipFile(result["path"]) as zf:
        names = zf.namelist()
        assert ".env" not in names
        for name in names:
            assert not name.endswith(".env")
        for info in zf.infolist():
            data = zf.read(info.filename)
            assert b"super-secret-value" not in data


def test_backup_souls_never_includes_oauth_token_files():
    """xai_oauth.json/openai_codex_oauth.jsonも2026-07-22以降config.HOME直下に
    置かれるようになった。.envと同じ理由で、backup_soulsのzipに紛れ込まないこと
    をダミー値で確認する（実トークンファイルの中身には一切触れない）。"""
    import gui
    import soul
    import config as config_mod
    import zipfile

    soul.create_soul("oauth漏洩防止テスト", identity_text="核")
    xai_path = os.path.join(config_mod.HOME, "xai_oauth.json")
    codex_path = os.path.join(config_mod.HOME, "openai_codex_oauth.json")
    with open(xai_path, "w", encoding="utf-8") as f:
        f.write('{"access_token": "dummy-xai-token-marker"}')
    with open(codex_path, "w", encoding="utf-8") as f:
        f.write('{"access_token": "dummy-codex-token-marker"}')

    bridge = gui.Bridge()
    result = bridge.backup_souls()

    assert result["ok"] is True
    with zipfile.ZipFile(result["path"]) as zf:
        names = zf.namelist()
        assert "xai_oauth.json" not in names
        assert "openai_codex_oauth.json" not in names
        for info in zf.infolist():
            data = zf.read(info.filename)
            assert b"dummy-xai-token-marker" not in data
            assert b"dummy-codex-token-marker" not in data


# --- stop_turn: 考え中の応答を止めるボタンの入口（2026-07-21追加）。
# pywebviewは各API呼び出しを別スレッドで走らせるため、send_message実行中でも
# stop_turnは並行して届きうる。ここではBridge単体でengine有無の分岐のみ検証する。

def test_stop_turn_returns_false_when_no_engine():
    import gui
    bridge = gui.Bridge()
    bridge._engine = None

    result = bridge.stop_turn()

    assert result == {"ok": False}


def test_stop_turn_returns_true_and_requests_stop_when_engine_exists(monkeypatch):
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    sid = soul.create_soul("停止ボタンテスト", identity_text="核")
    bridge = gui.Bridge()
    bridge.switch_soul(sid)
    assert bridge._engine is not None
    assert bridge._engine._stop_requested is False

    result = bridge.stop_turn()

    assert result == {"ok": True}
    assert bridge._engine._stop_requested is True


def test_insert_break_returns_false_when_no_engine():
    import gui
    bridge = gui.Bridge()
    bridge._engine = None

    result = bridge.insert_break()

    assert result == {"ok": False}


def test_insert_break_writes_marker_and_clears_engine_messages(monkeypatch):
    """「区切り」: soul.append_breakでログへマーカーを1本追記し、
    engine.reset_context()でmessagesを空にする。"""
    import gui
    import soul
    monkeypatch.setattr(gui.wrapup_mod, "write_daily_chronicle", lambda cfg, llm, sid: False)
    sid = soul.create_soul("区切りボタンテスト", identity_text="核")
    bridge = gui.Bridge()
    bridge.switch_soul(sid)
    bridge._engine.messages.append({"role": "user", "content": "直前の話"})
    bridge._engine.messages.append({"role": "assistant", "content": "直前の返事"})

    result = bridge.insert_break()

    assert result == {"ok": True}
    assert bridge._engine.messages == []
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["break"]


def test_backup_souls_consecutive_calls_do_not_collide():
    import gui
    import soul

    soul.create_soul("連続バックアップテスト", identity_text="核")
    bridge = gui.Bridge()

    result1 = bridge.backup_souls()
    result2 = bridge.backup_souls()

    assert result1["ok"] is True
    assert result2["ok"] is True
    assert result1["path"] != result2["path"]
    assert os.path.isfile(result1["path"])
    assert os.path.isfile(result2["path"])


# --- import_backup: backup_souls()が作ったzipからの復元（GUI「バックアップから復元」）。
# Zip Slip・symlink・無関係zip・.env混入は全部拒否し、復元前に自動バックアップを
# 取ってからマージ展開することを検証する（2026-07-22追加）。

def test_import_backup_round_trip_restores_soul():
    import gui
    import soul
    import config as config_mod

    sid = soul.create_soul("往復テスト魂", identity_text="往復核データ")
    bridge = gui.Bridge()
    backup = bridge.backup_souls()
    assert backup["ok"] is True

    # 復元前の状態を変える（identityを書き換えて、復元後に元に戻ることを確認する）
    soul.update_identity(sid, "書き換え後の核", "")

    result = bridge.import_backup(backup["path"])

    assert result["ok"] is True
    # FIERIA_HOMEはテストセッション全体で共有される（conftest.py参照）ため、
    # zipには他テストが作った既存soulも入っている可能性がある。ここでは
    # このテストで作ったsoulが含まれることだけを確認する（完全一致は前提できない）。
    assert "往復テスト魂" in result["restored_souls"]
    assert os.path.isfile(result["auto_backup"])
    parts = soul.read_identity_parts(sid)
    assert parts["core"] == "往復核データ"
    # 復元後はconfigを再読込してengineを組み直しているので、boot()相当のキーも返る
    assert "souls" in result
    assert any(s["id"] == sid for s in result["souls"])


def test_import_backup_rejects_zip_slip_and_extracts_nothing(tmp_path):
    import gui
    import config as config_mod
    import zipfile

    malicious_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("config.json", "{}")  # FieriAバックアップに見せかける
        zf.writestr("../zip-slip-outside.txt", "pwned")
        zf.writestr("souls/x/../../../zip-slip-via-souls.txt", "pwned2")

    # 復元前のconfig.jsonの中身を記録しておき、展開が一切起きていないことの根拠にする
    with open(config_mod.CONFIG_PATH, "r", encoding="utf-8") as f:
        config_before = f.read()

    bridge = gui.Bridge()
    result = bridge.import_backup(str(malicious_zip))

    assert result["ok"] is False
    assert "error" in result
    outside_path = os.path.abspath(os.path.join(config_mod.HOME, "..", "zip-slip-outside.txt"))
    assert not os.path.isfile(outside_path)
    via_souls_path = os.path.abspath(
        os.path.join(config_mod.HOME, "..", "..", "zip-slip-via-souls.txt")
    )
    assert not os.path.isfile(via_souls_path)
    with open(config_mod.CONFIG_PATH, "r", encoding="utf-8") as f:
        assert f.read() == config_before  # 1ファイルも展開されていない


def test_import_backup_rejects_absolute_path_entry():
    import gui
    import config as config_mod
    import zipfile
    import tempfile
    import os as os_mod

    tmpdir = tempfile.mkdtemp(prefix="fieria-ziptest-")
    zip_path = os_mod.path.join(tmpdir, "evil-abs.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.json", "{}")
        zf.writestr("/etc/zip-slip-abs.txt", "pwned")

    bridge = gui.Bridge()
    result = bridge.import_backup(zip_path)

    assert result["ok"] is False
    assert not os.path.isfile("/etc/zip-slip-abs.txt")


def test_import_backup_rejects_symlink_entry():
    import gui
    import zipfile
    import tempfile
    import os as os_mod
    import stat as stat_mod

    tmpdir = tempfile.mkdtemp(prefix="fieria-ziptest-")
    zip_path = os_mod.path.join(tmpdir, "evil-symlink.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.json", "{}")
        info = zipfile.ZipInfo("souls/evil-link")
        info.external_attr = (stat_mod.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")  # リンク先(通常は展開時にシンボリックリンクとして書かれる)

    bridge = gui.Bridge()
    result = bridge.import_backup(zip_path)

    assert result["ok"] is False


def test_import_backup_rejects_unrelated_zip():
    import gui
    import zipfile
    import tempfile
    import os as os_mod

    tmpdir = tempfile.mkdtemp(prefix="fieria-ziptest-")
    zip_path = os_mod.path.join(tmpdir, "unrelated.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("random.txt", "hello")

    bridge = gui.Bridge()
    result = bridge.import_backup(zip_path)

    assert result["ok"] is False
    assert result["error"] == "FieriAのバックアップではないようです"


def test_import_backup_auto_backs_up_current_state_before_restoring():
    import gui
    import soul
    import config as config_mod

    soul.create_soul("自動バックアップ確認魂", identity_text="核")
    bridge = gui.Bridge()
    backup = bridge.backup_souls()
    backups_dir = os.path.join(config_mod.HOME, "backups")
    before_count = len(os.listdir(backups_dir))

    result = bridge.import_backup(backup["path"])

    assert result["ok"] is True
    after_count = len(os.listdir(backups_dir))
    assert after_count == before_count + 1  # 復元前の自動バックアップが1件増える
    assert result["auto_backup"] != backup["path"]
    assert os.path.isfile(result["auto_backup"])


def test_import_backup_merges_and_does_not_delete_soul_absent_from_zip():
    import gui
    import soul

    sid_a = soul.create_soul("バックアップに入る魂", identity_text="核A")
    bridge = gui.Bridge()
    backup = bridge.backup_souls()  # この時点でsid_bはまだ存在しない

    sid_b = soul.create_soul("バックアップ後に作った魂", identity_text="核B")

    result = bridge.import_backup(backup["path"])

    assert result["ok"] is True
    ids_after = [s["id"] for s in soul.list_souls()]
    assert sid_a in ids_after
    assert sid_b in ids_after  # zipに無いsoulは消えない（マージ）


def test_import_backup_never_extracts_env_entry():
    import gui
    import config as config_mod
    import zipfile
    import tempfile
    import os as os_mod

    tmpdir = tempfile.mkdtemp(prefix="fieria-ziptest-")
    zip_path = os_mod.path.join(tmpdir, "with-env.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.json", "{}")
        zf.writestr(".env", "SOME_API_KEY=with-env-zip-marker\n")

    env_path = os.path.join(config_mod.HOME, ".env")
    before_content = None
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            before_content = f.read()

    bridge = gui.Bridge()
    result = bridge.import_backup(zip_path)

    assert result["ok"] is True
    # .envは許可リスト外なので展開されない。既存の(他テスト由来かもしれない).envが
    # あってもzipの中身で上書きされていないこと・secretが書き込まれていないことを見る
    # （FIERIA_HOMEはテストセッション共有のため、非存在を前提にできない）。
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            after_content = f.read()
        # 既存.env（他テスト由来かもしれない）が、zip側の内容(このテスト固有の
        # マーカー文字列)で上書きされていないことを見る。before_contentとの完全一致は
        # 「1バイトも書き換わっていない」の直接証拠。
        assert after_content == before_content
        assert "with-env-zip-marker" not in after_content
    else:
        assert before_content is None


def test_import_backup_never_extracts_oauth_token_entries():
    """xai_oauth.json/openai_codex_oauth.jsonも.envと同じ許可リスト外なので、
    zipに仕込まれていても展開されないこと（2026-07-22の秘匿ファイル移設後の回帰防止）。"""
    import gui
    import config as config_mod
    import zipfile
    import tempfile
    import os as os_mod

    tmpdir = tempfile.mkdtemp(prefix="fieria-ziptest-oauth-")
    zip_path = os_mod.path.join(tmpdir, "with-oauth.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.json", "{}")
        zf.writestr("xai_oauth.json", '{"access_token": "xai-zip-marker"}')
        zf.writestr("openai_codex_oauth.json", '{"access_token": "codex-zip-marker"}')

    xai_path = os.path.join(config_mod.HOME, "xai_oauth.json")
    codex_path = os.path.join(config_mod.HOME, "openai_codex_oauth.json")

    def _read_if_exists(path):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    xai_before = _read_if_exists(xai_path)
    codex_before = _read_if_exists(codex_path)

    bridge = gui.Bridge()
    result = bridge.import_backup(zip_path)

    assert result["ok"] is True
    xai_after = _read_if_exists(xai_path)
    codex_after = _read_if_exists(codex_path)
    assert xai_after == xai_before
    assert codex_after == codex_before
    if xai_after is not None:
        assert "xai-zip-marker" not in xai_after
    if codex_after is not None:
        assert "codex-zip-marker" not in codex_after


# --- 2026-07-22コードレビュー指摘の修正確認 ---
# Critical: NTFS代替データストリーム(ADS)形式のzipエントリ名("souls/x.md:ads"のように
# パス途中にコロンを含む)は、_is_unsafe_zip_memberの旧実装（先頭"/"とドライブレター
# 形式(2文字目が":")しか見ていなかった）をすり抜けて展開されてしまい、実機で
# 隠しADSストリームへの書き込みが再現した。コロン全面禁止で塞ぐ。

def test_import_backup_rejects_ads_colon_entry_and_extracts_nothing(tmp_path):
    import gui
    import config as config_mod
    import zipfile

    ads_zip = tmp_path / "evil-ads.zip"
    with zipfile.ZipFile(ads_zip, "w") as zf:
        zf.writestr("config.json", "{}")  # FieriAバックアップに見せかける
        zf.writestr("souls/x.md:ads", "hidden ads stream payload")

    # Bridge()生成でconfig.jsonが必ず保存された状態にしてから「展開前後で1バイトも
    # 変わっていない」ことの基準を取る（他テストの実行順に依存させないため）。
    bridge = gui.Bridge()
    with open(config_mod.CONFIG_PATH, "r", encoding="utf-8") as f:
        config_before = f.read()

    result = bridge.import_backup(str(ads_zip))

    assert result["ok"] is False
    assert "souls/x.md:ads" in result["error"]
    with open(config_mod.CONFIG_PATH, "r", encoding="utf-8") as f:
        assert f.read() == config_before  # 1ファイルも展開されていない（レビュー再現ケース）


# Important: 展開ループ中（自動バックアップ成功後）に例外が起きると、自動バックアップ
# 自体は既にディスク上にあるのに、エラーメッセージがそのパスを案内していなかった。
# ユーザーが復旧経路を知らないまま「失敗した」としか見えない状態になる。

def test_import_backup_error_includes_auto_backup_recovery_path_on_extraction_failure(monkeypatch):
    import builtins
    import gui
    import soul
    import config as config_mod

    soul.create_soul("復旧パス確認魂", identity_text="核")
    bridge = gui.Bridge()
    backup = bridge.backup_souls()
    assert backup["ok"] is True

    real_open = builtins.open

    def failing_open(path, mode="r", *args, **kwargs):
        # 展開ループがconfig.jsonをbase_dir直下へ書き出す瞬間だけを狙って落とす
        # （自動バックアップ完了後・展開完了前、という指摘対象の窓を再現する）。
        if mode == "wb" and os.path.abspath(str(path)) == os.path.abspath(config_mod.CONFIG_PATH):
            raise OSError("injected failure for test: extraction interrupted")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)

    result = bridge.import_backup(backup["path"])

    assert result["ok"] is False
    assert "復元前の状態は" in result["error"]
    assert "fieria_homeに展開し直す" in result["error"]


# --- get_current_prompt: プロンプト透視カード用。実LLM呼び出しはせず、
# prompt.build_system_text()を組んで返すだけであることを確認する（2026-07-22追加）。

def test_get_current_prompt_returns_empty_when_no_active_soul():
    import gui

    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None

    result = bridge.get_current_prompt()

    assert result == {"text": "", "chars": 0, "approx_tokens": 0}


def test_get_current_prompt_includes_fact_layer_and_identity_for_active_soul():
    import gui
    import prompt
    import soul

    sid = soul.create_soul("プロンプト透視テスト", identity_text="わたしはテト。一人称はわたし。")
    bridge = gui.Bridge()
    bridge.switch_soul(sid)
    # fact_layerはFIERIA_HOMEを共有する他テスト（test_config.py等）がconfig.jsonへ
    # 永続化した値を引き継ぎうるため、既定値であることをここで明示的に固定する。
    bridge._cfg["fact_layer"] = {"enabled": True, "custom_text": ""}

    result = bridge.get_current_prompt()

    # 事実層の標準文言の断片が入っていること（fact_layerがデフォルトenabledのため）
    assert prompt.DEFAULT_FACT_TEXT[:20] in result["text"]
    assert "わたしはテト" in result["text"]
    assert result["chars"] == len(result["text"])
    assert result["approx_tokens"] == int(len(result["text"]) * 0.6)


def test_get_current_prompt_does_not_call_llm(monkeypatch):
    """合成だけであり、LLM呼び出し経路(create_llm/Engineのprocess_turn等)には
    一切触れないことを確認する。llm.create_llmを壊れる実装に差し替えても
    get_current_prompt自体は例外を起こさないことで、LLM未到達を裏付ける。"""
    import gui
    import soul

    def _boom(*args, **kwargs):
        raise AssertionError("get_current_promptからLLMが呼ばれてはいけない")

    sid = soul.create_soul("プロンプト透視LLM不到達テスト", identity_text="核")
    bridge = gui.Bridge()
    bridge.switch_soul(sid)
    monkeypatch.setattr(gui, "create_llm", _boom)

    result = bridge.get_current_prompt()

    assert result["text"]


# --- rename_soul / get_soul_identityのname（2026-07-22追加）---

def test_get_soul_identity_includes_raw_name():
    import gui
    import soul
    sid = soul.create_soul("名前確認テスト")
    bridge = gui.Bridge()

    result = bridge.get_soul_identity(sid)

    assert result["name"] == "名前確認テスト"


def test_get_soul_identity_name_is_empty_string_not_unnamed_label_for_nameless_soul():
    """名前未設定SOULのget_soul_identityは、一覧表示用のUNNAMED_LABELではなく
    生の空文字を返すこと（編集フォームの初期値としてラベル文言を書き戻さないため）。"""
    import gui
    import soul
    sid = soul.create_soul("")
    bridge = gui.Bridge()

    result = bridge.get_soul_identity(sid)

    assert result["name"] == ""


def test_rename_soul_updates_name_file():
    import gui
    import soul
    sid = soul.create_soul("旧名前テスト")
    bridge = gui.Bridge()

    result = bridge.rename_soul(sid, "新しい名前")

    assert result == {"ok": True, "name": "新しい名前"}
    assert soul.read_name(sid) == "新しい名前"


def test_rename_soul_keeps_folder_name_unchanged():
    """rename_soulはフォルダ名（soul_id）を変えない——ログ・添付・索引のパスが
    soul_id基準で書かれているため、変えるとそれらが壊れる。"""
    import gui
    import soul
    sid = soul.create_soul("フォルダ名不変テスト")
    bridge = gui.Bridge()

    bridge.rename_soul(sid, "改名後")

    assert os.path.isdir(soul.soul_dir(sid))
    assert soul.soul_dir(sid).endswith(sid)


def test_rename_soul_to_empty_resets_name():
    import gui
    import soul
    sid = soul.create_soul("消される名前")
    bridge = gui.Bridge()

    result = bridge.rename_soul(sid, "")

    assert result == {"ok": True, "name": ""}
    assert soul.read_name(sid) == ""


def test_rename_soul_rejects_unknown_soul_id_without_creating_directory():
    import gui
    import soul
    bridge = gui.Bridge()
    fake_id = "no-such-soul-rename"
    d = soul.soul_dir(fake_id)

    result = bridge.rename_soul(fake_id, "勝手に名付ける")

    assert result == {"ok": False, "error": "SOULが見つからない"}
    assert not os.path.isdir(d)


def test_rename_soul_rejects_traversal_soul_id_without_escaping_souls_dir():
    import gui
    import soul
    bridge = gui.Bridge()
    traversal_id = "..\\evil-rename"
    escaped_dir = os.path.abspath(os.path.join(soul.SOULS_DIR, "..", "evil-rename"))

    result = bridge.rename_soul(traversal_id, "名前")

    assert result == {"ok": False, "error": "SOULが見つからない"}
    assert not os.path.isdir(escaped_dir)


def test_rename_soul_rejects_newline_in_name():
    import gui
    import soul
    sid = soul.create_soul("改行拒否テスト")
    bridge = gui.Bridge()

    result = bridge.rename_soul(sid, "イリア\n二行目")

    assert result["ok"] is False
    assert soul.read_name(sid) == "改行拒否テスト"  # 拒否時は元の名前のまま


# --- import_backupのoverwritten_souls検出（2026-07-22コードレビュー指摘）---
# soul_idはローカル連番のみで生成されるため、別インストール同士のバックアップを
# 同じfieria_homeへ復元すると、名前なしSOUL等のIDが偶然一致して無警告で
# 中身が上書きされうる。マージ展開の挙動自体は変えず、「どのSOULが上書きされたか」を
# 検出してUIに渡せることを確認する。

def test_import_backup_reports_overwritten_soul_when_id_collides():
    import gui
    import soul

    sid = soul.create_soul("上書き前の名前", identity_text="上書き前の核")
    bridge = gui.Bridge()
    backup = bridge.backup_souls()
    assert backup["ok"] is True

    # 同じsoul_idのまま中身が変わった状態（＝IDが衝突している別インストールの
    # バックアップを復元する状況を模する）にしてから復元する。
    soul.update_identity(sid, "衝突後に変わった核", "")

    result = bridge.import_backup(backup["path"])

    assert result["ok"] is True
    overwritten_ids = [s["id"] for s in result["overwritten_souls"]]
    assert sid in overwritten_ids
    entry = next(s for s in result["overwritten_souls"] if s["id"] == sid)
    assert entry["name"] == "上書き前の名前"  # 上書きされた側(展開前)の名前を報告する
    # 中身は実際にzip側(上書き前の核)へ戻っていること
    assert soul.read_identity_parts(sid)["core"] == "上書き前の核"


def test_import_backup_overwritten_souls_empty_when_soul_is_new_to_home():
    """zipに入っていたsoul_idが、復元先にまだ存在しない新規SOULの場合は
    overwritten_soulsに含めない（上書きではなく新規追加のため）。"""
    import gui
    import soul

    sid_new = soul.create_soul("新規追加テスト魂", identity_text="核")
    bridge = gui.Bridge()
    backup = bridge.backup_souls()
    assert backup["ok"] is True

    # 復元先から一旦消して「まだ存在しない新規SOUL」の状態を作る
    import shutil
    shutil.rmtree(soul.soul_dir(sid_new))

    result = bridge.import_backup(backup["path"])

    assert result["ok"] is True
    overwritten_ids = [s["id"] for s in result["overwritten_souls"]]
    assert sid_new not in overwritten_ids
    assert sid_new in [s["id"] for s in soul.list_souls()]  # 復元自体はされている


# --- ペット（ドット絵マスコット）: get_pet_skin / pet_enabled設定 ---
# 2026-07-22追加。souls/<id>/pet/配下のskin PNGをbase64 data URIへ変換して返す
# Bridgeメソッドの検証。既存のget_soul_identity等と同じ_soul_existsガードを通す
# ため、トラバーサルsoul_idのテストも上のtest_update_soul_identity_rejects_...と
# 同じ思想（bridge._cfg["active_soul"]へ直接malicious文字列を入れて呼ぶ）で書く。

def test_get_pet_skin_returns_empty_dict_when_no_pet_folder():
    import gui
    import soul
    sid = soul.create_soul("ペットskin無しテスト")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    assert bridge.get_pet_skin() == {}


def test_get_pet_skin_returns_data_uri_for_placed_png():
    import gui
    import soul
    sid = soul.create_soul("ペットskin有りテスト")
    pet_dir = os.path.join(soul.soul_dir(sid), "pet")
    os.makedirs(pet_dir, exist_ok=True)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # 中身の妥当性は問わない（拡張子とサイズだけ見る仕様）
    with open(os.path.join(pet_dir, "idle.png"), "wb") as f:
        f.write(png_bytes)
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    result = bridge.get_pet_skin()

    assert set(result.keys()) == {"idle"}
    assert result["idle"].startswith("data:image/png;base64,")
    import base64
    b64_part = result["idle"].split(",", 1)[1]
    assert base64.b64decode(b64_part) == png_bytes


def test_get_pet_skin_skips_oversized_png():
    import gui
    import soul
    sid = soul.create_soul("ペットskin超過テスト")
    pet_dir = os.path.join(soul.soul_dir(sid), "pet")
    os.makedirs(pet_dir, exist_ok=True)
    oversized = b"\x00" * (gui.PET_SKIN_MAX_BYTES + 1)
    with open(os.path.join(pet_dir, "idle.png"), "wb") as f:
        f.write(oversized)
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    assert bridge.get_pet_skin() == {}


def test_get_pet_skin_ignores_non_png_files():
    import gui
    import soul
    sid = soul.create_soul("ペットskin非png無視テスト")
    pet_dir = os.path.join(soul.soul_dir(sid), "pet")
    os.makedirs(pet_dir, exist_ok=True)
    with open(os.path.join(pet_dir, "idle.txt"), "wb") as f:
        f.write(b"not a png")
    with open(os.path.join(pet_dir, "thinking.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = sid

    result = bridge.get_pet_skin()

    assert set(result.keys()) == {"thinking"}


def test_get_pet_skin_rejects_traversal_active_soul_without_escaping_souls_dir():
    """active_soulが実在のSOULディレクトリ名と一致しない（トラバーサル文字列含む）場合、
    soul_dir()にそのまま渡さず空dictを返すこと（_soul_existsによる入口ガード）。"""
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = "..\\evil-soul"

    assert bridge.get_pet_skin() == {}


def test_get_pet_skin_returns_empty_dict_when_no_active_soul():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["active_soul"] = None

    assert bridge.get_pet_skin() == {}


def test_get_settings_includes_pet_enabled_default_true():
    import gui
    bridge = gui.Bridge()
    assert bridge.get_settings()["pet_enabled"] is True


def test_save_settings_persists_pet_enabled_false():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_enabled": False})

    assert result["pet_enabled"] is False
    reloaded = config_mod.load_config()
    assert reloaded["pet_enabled"] is False


def test_get_settings_includes_llm_web_search_default_false():
    """FieriA拡張: Web検索。get_settingsはsanitize_llm_cfg経由でllm全体を返すため、
    web_searchも素通しで含まれること（既定False）。"""
    import gui
    bridge = gui.Bridge()
    assert bridge.get_settings()["llm"]["web_search"] is False


def test_save_settings_persists_llm_web_search_true():
    import gui
    import config as config_mod
    bridge = gui.Bridge()
    llm_cfg = bridge.get_settings()["llm"]
    llm_cfg["web_search"] = True

    result = bridge.save_settings({"llm": llm_cfg})

    assert result["llm"]["web_search"] is True
    reloaded = config_mod.load_config()
    assert reloaded["llm"]["web_search"] is True


def test_save_settings_casts_llm_web_search_to_bool():
    """JS側から渡る値の型を保証しない（auto_role_switch等と同じ思想）ため、
    truthyな非bool値（1等）でもbool()化されて保存されること。"""
    import gui
    bridge = gui.Bridge()
    llm_cfg = bridge.get_settings()["llm"]
    llm_cfg["web_search"] = 1

    result = bridge.save_settings({"llm": llm_cfg})

    assert result["llm"]["web_search"] is True


def test_boot_includes_pet_enabled():
    """boot()の戻り値にpet_enabledが含まれること。値そのものは前後のテストが
    save_settingsで書き換えた共有configの状態に依存しうるため、ここではbridge._cfgへ
    明示的にTrueをセットしてから検証する（他のget_pet_skinテストと同じ、_cfg直接
    セットのパターンを踏襲）。"""
    import gui
    bridge = gui.Bridge()
    bridge._cfg["pet_enabled"] = True
    assert bridge.boot()["pet_enabled"] is True


# --- ペットのサイズ変更＋ドラッグ移動: pet_size / pet_pos / save_pet_pos ---
# 2026-07-22追加。get_settings/save_settings/bootへのpet_size・pet_pos追加と、
# ドラッグ終了ごとに呼ばれる軽量Bridge save_pet_pos の検証。


def test_get_settings_includes_pet_size_default_64():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["pet_size"] = 64
    assert bridge.get_settings()["pet_size"] == 64


def test_save_settings_persists_pet_size():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": 96})

    assert result["pet_size"] == 96
    reloaded = config_mod.load_config()
    assert reloaded["pet_size"] == 96


def test_save_settings_clamps_pet_size_above_max():
    """スライダーの上限は128。細工された/バグった値でそれを超えても128にクランプされること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": 999})

    assert result["pet_size"] == 128


def test_save_settings_clamps_pet_size_just_above_max_boundary():
    """境界値129（上限128の1つ上）が128にクランプされること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": 129})

    assert result["pet_size"] == 128


def test_save_settings_clamps_pet_size_below_min():
    """スライダーの下限は48。0や負値・下限未満の値は48にクランプされること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": 1})

    assert result["pet_size"] == 48


def test_save_settings_clamps_pet_size_just_below_min_boundary():
    """境界値47（下限48の1つ下）が48にクランプされること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": 47})

    assert result["pet_size"] == 48


def test_save_settings_pet_size_accepts_numeric_string():
    """JS側からの型は保証されないため、数字文字列もint()化して受け付けること
    （auto_recallのmax_hitsと同じ考え方）。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": "80"})

    assert result["pet_size"] == 80


def test_save_settings_pet_size_falls_back_to_default_on_garbage():
    """int()化に失敗する値（数字に変換できない文字列等）は既定の64にフォールバックすること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"pet_size": "not-a-number"})

    assert result["pet_size"] == 64


def test_boot_includes_pet_size_and_pet_pos():
    """boot()の戻り値にpet_size・pet_posが含まれること（他の値と同じく_cfg直接セットの
    パターンを踏襲、前後のテストの共有config状態に依存しないようにする）。"""
    import gui
    bridge = gui.Bridge()
    bridge._cfg["pet_size"] = 72
    bridge._cfg["pet_pos"] = {"right": 40, "bottom": 110}
    data = bridge.boot()
    assert data["pet_size"] == 72
    assert data["pet_pos"] == {"right": 40, "bottom": 110}


def test_save_pet_pos_persists_position():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.save_pet_pos(300, 150)

    assert result == {"right": 300, "bottom": 150}
    reloaded = config_mod.load_config()
    assert reloaded["pet_pos"] == {"right": 300, "bottom": 150}


def test_save_pet_pos_clamps_negative_to_zero():
    import gui
    bridge = gui.Bridge()

    result = bridge.save_pet_pos(-5, -100)

    assert result == {"right": 0, "bottom": 0}


def test_save_pet_pos_clamps_oversized_value():
    """画面外はるか彼方へペットの座標が飛ばないよう、0〜4000にクランプされること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_pet_pos(99999, 5000)

    assert result == {"right": 4000, "bottom": 4000}


def test_save_pet_pos_falls_back_to_zero_on_garbage():
    """int()化に失敗する値は0扱いになること（save_settingsのpet_sizeとは異なり、
    位置は「わからなければ既定コーナーに寄せる」意味で64ではなく0が自然なフォールバック）。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_pet_pos("not-a-number", None)

    assert result == {"right": 0, "bottom": 0}


def test_save_pet_pos_accepts_numeric_strings():
    import gui
    bridge = gui.Bridge()

    result = bridge.save_pet_pos("30", "45")

    assert result == {"right": 30, "bottom": 45}


# --- プロバイダ別推論エフォート: save_settings時のホワイトリスト正規化 ---
# 2026-08-02追加。Task 1のllm.REASONING_EFFORTSを唯一の正としてGUI/JS由来の
# reasoning_effortをここで確定させる（大文字化・不正値・キー欠落の3パターン）。


def test_save_settings_normalizes_reasoning_effort():
    import gui
    b = gui.Bridge()
    llm_cfg = {"provider": "ollama", "providers": {
        "ollama": {"type": "openai_compat", "base_url": "http://x/v1",
                   "model": "m", "reasoning_effort": "HIGH"},
        "gemini": {"type": "gemini", "model": "g", "env_key": "GEMINI_API_KEY",
                   "reasoning_effort": "turbo"},
        "groq": {"type": "openai_compat", "base_url": "http://y/v1", "model": "m2"},
    }}
    b.save_settings({"llm": llm_cfg})
    provs = b._cfg["llm"]["providers"]
    assert provs["ollama"]["reasoning_effort"] == "high"
    assert provs["gemini"]["reasoning_effort"] == ""
    assert provs["groq"]["reasoning_effort"] == ""


# --- メイン画面QOL Task 1: boot()にllm_summaryを追加 ---
# 2026-08-02追加。画面ヘッダーに現在のプロバイダ/モデル/推論エフォートを常時
# 表示するためのサマリー。異常な設定（存在しないprovider等）でも例外にせず
# 空文字で埋めたdictを返す（gui.py _llm_summary参照）。


def test_boot_includes_llm_summary():
    import gui
    b = gui.Bridge()
    b._cfg["llm"]["provider"] = "gemini"
    b._cfg["llm"]["providers"]["gemini"]["reasoning_effort"] = "high"
    data = b.boot()
    s = data["llm_summary"]
    assert s["provider"] == "gemini"
    assert s["model"] == b._cfg["llm"]["providers"]["gemini"]["model"]
    assert s["reasoning_effort"] == "high"
    assert s["label"]  # 空でない表示名


def test_boot_llm_summary_safe_on_broken_config():
    import gui
    b = gui.Bridge()
    b._cfg["llm"]["provider"] = "存在しないやつ"
    data = b.boot()
    assert data["llm_summary"] == {"provider": "", "label": "", "model": "",
                                   "reasoning_effort": ""}


# --- LLM返信完了時のSE: get_settings/save_settings/boot への reply_se追加 ---
# 2026-08-02追加。pet_character（不正値は既定へ倒す）とは異なり、不正値・空文字は
# 「鳴らさない」("")へ倒す（安全側＝鳴らない方に倒す）。


def test_get_settings_includes_reply_se_default_se_poko():
    import gui
    bridge = gui.Bridge()
    assert bridge.get_settings()["reply_se"] == "se-poko.mp3"


def test_save_settings_persists_reply_se():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.save_settings({"reply_se": "se-kachi.mp3"})

    assert result["reply_se"] == "se-kachi.mp3"
    reloaded = config_mod.load_config()
    assert reloaded["reply_se"] == "se-kachi.mp3"


def test_save_settings_reply_se_accepts_empty_string_as_no_sound():
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"reply_se": ""})

    assert result["reply_se"] == ""


def test_save_settings_reply_se_falls_back_to_empty_on_invalid_value():
    """不正値は既定のse-poko.mp3ではなく""へ倒す（鳴らない方に倒す＝安全側）。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"reply_se": "../../etc/passwd"})

    assert result["reply_se"] == ""


def test_boot_includes_reply_se():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["reply_se"] = "se-pichon.mp3"
    assert bridge.boot()["reply_se"] == "se-pichon.mp3"


# --- 入力欄フォーカス時のIME自動切替: get_settings/save_settings/ensure_ime_japanese ---
# 2026-08-02追加。実IME切替（Win32 SendMessage）はテスト環境で検証不能なため、
# ここでは「設定の出し入れが正しいこと」と「ensure_ime_japaneseが例外を漏らさず
# dictを返すこと」だけを網にかける（実害なし優先の設計どおり）。


def test_get_settings_includes_ime_auto_ja_default_true():
    import gui
    bridge = gui.Bridge()
    assert bridge.get_settings()["ime_auto_ja"] is True


def test_save_settings_persists_ime_auto_ja_false():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.save_settings({"ime_auto_ja": False})

    assert result["ime_auto_ja"] is False
    reloaded = config_mod.load_config()
    assert reloaded["ime_auto_ja"] is False


def test_save_settings_ime_auto_ja_coerces_truthy_value_to_bool():
    """auto_role_switch等と同じく、JS側から来る値の型を保証せずここでbool確定させる。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"ime_auto_ja": 0})

    assert result["ime_auto_ja"] is False


def test_ensure_ime_japanese_returns_disabled_reason_when_setting_off():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["ime_auto_ja"] = False

    result = bridge.ensure_ime_japanese()

    assert result == {"ok": False, "reason": "disabled"}


def test_ensure_ime_japanese_never_raises_when_enabled():
    """テスト環境（フォアグラウンドウィンドウ無し等）でも例外を漏らさずdictを返す。
    実IME切替の成否は検証不能なので、例外安全であることだけを確認する。"""
    import gui
    bridge = gui.Bridge()
    bridge._cfg["ime_auto_ja"] = True

    result = bridge.ensure_ime_japanese()

    assert isinstance(result, dict)
    assert "ok" in result


# --- テーマ登録制への一般化: save_settingsのホワイトリスト正規化 ---
# 2026-08-02追加。着せ替えテーマ8案（docs/plans/2026-08-02-theme-skins.md）Task 1。
# themeは自由文字列として保存されていたため、pet_character等と同じくconfig_mod.THEME_IDS
# によるホワイトリスト正規化を追加した（既知id以外は既定"light"へ倒す）。


def test_save_settings_persists_known_theme():
    import gui
    import config as config_mod
    bridge = gui.Bridge()

    result = bridge.save_settings({"theme": "neon"})

    assert result["theme"] == "neon"
    reloaded = config_mod.load_config()
    assert reloaded["theme"] == "neon"


def test_save_settings_normalizes_unknown_theme_to_light():
    """改ざん・旧設定の残骸等で未知のtheme値が来ても、既定の"light"へ正規化されること。"""
    import gui
    bridge = gui.Bridge()

    result = bridge.save_settings({"theme": "hacker9000"})

    assert result["theme"] == "light"


# --- ペットキャラの選択（組み込み3種＋不正値フォールバック） ---
# 2026-08-02追加。BUILTIN_PET_SKINS（ui/index.html）のキーと対応する値だけを
# 受け付け、未知の値は既定コノハへ倒す（存在しないスキンを指したまま保存されると
# ペットが表示できなくなるため）。

def test_save_settings_accepts_builtin_pet_characters():
    import gui
    b = gui.Bridge()
    for name in ("konoha", "mokora", "suzuna", "hanapo", "nejiro", "pachiri"):
        b.save_settings({"pet_character": name})
        assert b._cfg["pet_character"] == name


def test_save_settings_rejects_unknown_pet_character():
    import gui
    b = gui.Bridge()
    b.save_settings({"pet_character": "ドラゴン"})
    assert b._cfg["pet_character"] == "konoha"
