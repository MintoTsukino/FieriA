import os


def test_create_soul_builds_directory_tree():
    import soul
    sid = soul.create_soul("テスト子", identity_text="ぼくはテスト子。")
    d = soul.soul_dir(sid)
    for sub in ("chronicle", "wiki", "sacred", "logs", "readings"):
        assert os.path.isdir(os.path.join(d, sub))
    for f in ("identity.md", "self_notes.md", "user.md", "MEMORY.md"):
        assert os.path.isfile(os.path.join(d, f))
    assert "ぼくはテスト子。" in soul.read_file(sid, "identity.md")


def test_create_soul_builds_tasks_placeholder():
    """tasks.md（気にかけリスト・秘書層）がcreate_soulで生成され、プレースホルダのままであること。"""
    import soul
    sid = soul.create_soul("タスクテスト子")
    d = soul.soul_dir(sid)
    assert os.path.isfile(os.path.join(d, "tasks.md"))
    assert soul.read_file(sid, "tasks.md") == soul.TASKS_PLACEHOLDER


def test_read_tasks_on_existing_soul_without_file_is_empty():
    """tasks.md追加前に作られた既存SOULを模して、ファイルを手動削除しても
    read_fileが例外にならず空文字を返すこと（既存SOULへの後方互換）。"""
    import soul
    sid = soul.create_soul("旧SOUL模擬")
    os.remove(os.path.join(soul.soul_dir(sid), "tasks.md"))
    assert soul.read_file(sid, "tasks.md") == ""


def test_list_souls_returns_created():
    import soul
    sid = soul.create_soul("一覧テスト")
    names = [s["name"] for s in soul.list_souls()]
    assert "一覧テスト" in names
    assert any(s["id"] == sid for s in soul.list_souls())


def test_read_missing_file_returns_empty():
    import soul
    sid = soul.create_soul("空テスト")
    assert soul.read_file(sid, "wiki/nothing.md") == ""


def test_write_and_append():
    import soul
    sid = soul.create_soul("書きテスト")
    soul.write_file(sid, "wiki/topic.md", "# トピック\n1行目\n")
    soul.append_file(sid, "wiki/topic.md", "2行目\n")
    body = soul.read_file(sid, "wiki/topic.md")
    assert "1行目" in body and "2行目" in body


def test_readings_dir_lazily_created_for_existing_soul():
    """readings/はcreate_soulより後に追加された層のため、既にreadings/フォルダを
    持たない「旧SOUL」を模して同フォルダを削除しても、write_file（save_reading_note
    が使う経路）がos.makedirs(exist_ok=True)で自動的に作り直すこと（wiki/等と違い
    移行処理なしで新規追加できる設計）。"""
    import shutil
    import soul
    sid = soul.create_soul("旧SOUL模擬2")
    readings_dir = os.path.join(soul.soul_dir(sid), "readings")
    assert os.path.isdir(readings_dir)
    shutil.rmtree(readings_dir)
    assert not os.path.isdir(readings_dir)
    soul.write_file(sid, "readings/資料.md", "# 資料\n要点\n")
    assert os.path.isdir(readings_dir)
    assert "要点" in soul.read_file(sid, "readings/資料.md")


def test_path_traversal_rejected():
    import soul
    import pytest
    sid = soul.create_soul("安全テスト")
    with pytest.raises(ValueError):
        soul.read_file(sid, "../../config.json")


def test_append_log_and_read_today():
    import soul
    sid = soul.create_soul("ログテスト")
    soul.append_log(sid, "user", "こんにちは")
    soul.append_log(sid, "ai", "にゃっほー")
    entries = soul.read_today_log(sid)
    assert [e["who"] for e in entries] == ["user", "ai"]
    assert entries[0]["text"] == "こんにちは"


def test_append_break_writes_break_row_readable_by_existing_parser():
    """append_breakはappend_logと同じjsonl追記に乗るため、既存のread_today_log
    （_parse_jsonl_lines）がそのまま読める（新しいwho値でパーサが壊れないこと）。"""
    import soul
    sid = soul.create_soul("区切りテスト")
    soul.append_log(sid, "user", "前の話")
    soul.append_break(sid)
    soul.append_log(sid, "user", "後の話")
    entries = soul.read_today_log(sid)
    assert [e["who"] for e in entries] == ["user", "break", "user"]
    assert entries[1]["text"] == ""


def test_recent_chronicle_concatenates():
    import soul
    sid = soul.create_soul("日記テスト")
    soul.write_file(sid, "chronicle/2026-07-18.md", "# 7/18\n昨日のこと\n")
    soul.write_file(sid, "chronicle/2026-07-19.md", "# 7/19\n今日のこと\n")
    text = soul.recent_chronicle(sid, n=2)
    assert "昨日のこと" in text and "今日のこと" in text


def test_latest_chronicle_returns_most_recent_daily_entry():
    import soul
    sid = soul.create_soul("最新日記テスト")
    soul.write_file(sid, "chronicle/2026-07-18.md", "# 7/18\n古い方\n")
    soul.write_file(sid, "chronicle/2026-07-20.md", "# 7/20\n新しい方\n")
    result = soul.latest_chronicle(sid)
    assert result["date"] == "2026-07-20"
    assert "新しい方" in result["text"]


def test_latest_chronicle_ignores_weekly_and_monthly():
    import soul
    sid = soul.create_soul("週次月次除外テスト")
    soul.write_file(sid, "chronicle/2026-07-18.md", "# 7/18\n日次\n")
    soul.write_file(sid, "chronicle/weekly/2026-W30.md", "# 週次\n週次のこと\n")
    soul.write_file(sid, "chronicle/monthly/2026-07.md", "# 月次\n月次のこと\n")
    result = soul.latest_chronicle(sid)
    assert result["date"] == "2026-07-18"
    assert "日次" in result["text"]
    assert "週次" not in result["text"]
    assert "月次" not in result["text"]


def test_latest_chronicle_none_when_empty():
    import soul
    sid = soul.create_soul("日記無しテスト")
    assert soul.latest_chronicle(sid) is None


def test_create_soul_with_identity_and_speech_style_both_present():
    """核はidentity.md、口調はspeech_style.mdへ別ファイルとして保存されること
    （口調をidentity.mdから分離した新形式の回帰確認）。"""
    import soul
    sid = soul.create_soul("口調テスト", identity_text="ぼくはテスト子。",
                            speech_style="語尾に「にゃ」をつける")
    identity_body = soul.read_file(sid, "identity.md")
    speech_body = soul.read_file(sid, "speech_style.md")
    assert "ぼくはテスト子。" in identity_body
    assert "語尾に「にゃ」をつける" not in identity_body
    assert "語尾に「にゃ」をつける" in speech_body


def test_read_identity_parts_splits_core_and_speech_style():
    import soul
    sid = soul.create_soul("分割テスト", identity_text="ぼくはテスト子。",
                            speech_style="語尾に「にゃ」をつける")
    parts = soul.read_identity_parts(sid)
    assert parts["core"] == "ぼくはテスト子。"
    assert parts["speech_style"] == "語尾に「にゃ」をつける"


def test_update_identity_rewrites_both_parts():
    import soul
    sid = soul.create_soul("更新テスト", identity_text="旧・核")
    soul.update_identity(sid, "新・核", "タメ口")
    parts = soul.read_identity_parts(sid)
    assert parts["core"] == "新・核"
    assert parts["speech_style"] == "タメ口"
    assert "旧・核" not in soul.read_file(sid, "identity.md")


def test_speech_style_only_keeps_core_empty():
    """口調だけ指定した場合、identity.mdはプレースホルダのままで、タメ口の文言は
    speech_style.mdの側にだけ書かれること（口調分離後の新形式の回帰確認）。"""
    import soul
    sid = soul.create_soul("口調のみテスト", identity_text="", speech_style="タメ口")
    identity_body = soul.read_file(sid, "identity.md")
    speech_body = soul.read_file(sid, "speech_style.md")
    assert identity_body == soul.IDENTITY_PLACEHOLDER
    assert "タメ口" not in identity_body
    assert "タメ口" in speech_body
    parts = soul.read_identity_parts(sid)
    assert parts["core"] == ""
    assert parts["speech_style"] == "タメ口"


def test_save_attachment_writes_file_and_returns_relative_path():
    import base64
    import soul
    sid = soul.create_soul("添付保存テスト")
    data = base64.b64encode(b"fake-png-bytes").decode("ascii")

    rel_path = soul.save_attachment(sid, "image/png", data)

    assert rel_path.startswith("attachments/")
    assert rel_path.endswith(".png")
    full = os.path.join(soul.soul_dir(sid), rel_path.replace("/", os.sep))
    assert os.path.isfile(full)
    with open(full, "rb") as f:
        assert f.read() == b"fake-png-bytes"


def test_save_attachment_extension_by_mime():
    import base64
    import soul
    sid = soul.create_soul("拡張子テスト")
    data = base64.b64encode(b"x").decode("ascii")

    assert soul.save_attachment(sid, "image/jpeg", data).endswith(".jpeg")
    assert soul.save_attachment(sid, "image/webp", data).endswith(".webp")
    assert soul.save_attachment(sid, "image/gif", data).endswith(".gif")


def test_save_attachment_rejects_unsupported_mime():
    import base64
    import pytest
    import soul
    sid = soul.create_soul("不正mimeテスト")
    data = base64.b64encode(b"x").decode("ascii")

    with pytest.raises(ValueError):
        soul.save_attachment(sid, "application/octet-stream", data)


def test_save_attachment_supports_pdf_mime():
    """application/pdf（GeminiネイティブPDF添付・2026-07-22追加）。追加前は他の画像と
    同様に拒否されていたが、engine.process_turnの画像添付フローに乗る以上、PDFも
    他の添付と同じく保存・ログ参照される必要がある（拒否されると毎ターン
    「画像保存失敗」opが積まれる不整合が起きるため）。"""
    import base64
    import soul
    sid = soul.create_soul("PDF添付テスト")
    data = base64.b64encode(b"%PDF-1.4 fake").decode("ascii")

    rel_path = soul.save_attachment(sid, "application/pdf", data)

    assert rel_path.endswith(".pdf")
    full = os.path.join(soul.soul_dir(sid), rel_path.replace("/", os.sep))
    assert os.path.isfile(full)


def test_save_attachment_avoids_filename_collision_with_sequence_number():
    import base64
    import soul
    sid = soul.create_soul("連番テスト")
    data = base64.b64encode(b"x").decode("ascii")

    p1 = soul.save_attachment(sid, "image/png", data)
    p2 = soul.save_attachment(sid, "image/png", data)

    assert p1 != p2
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), p1.replace("/", os.sep)))
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), p2.replace("/", os.sep)))


def test_create_soul_builds_lessons_placeholder():
    import soul
    sid = soul.create_soul("lessons生成テスト")
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), "lessons.md"))
    assert soul.read_file(sid, "lessons.md") == soul.LESSONS_PLACEHOLDER


def test_read_lessons_missing_file_returns_empty():
    """既存SOUL相当（lessons.mdを作らずに直接ディレクトリを触った想定）でも
    read_fileは空文字を返す（移行処理不要の確認）。"""
    import soul
    sid = soul.create_soul("lessons移行不要テスト")
    os.remove(os.path.join(soul.soul_dir(sid), "lessons.md"))
    assert soul.read_file(sid, "lessons.md") == ""


# --- リマインダ ---

def test_add_reminder_and_list_reminders():
    import soul
    sid = soul.create_soul("リマインダ基本テスト")
    rid = soul.add_reminder(sid, "薬を飲む", "2026-08-01")
    reminders = soul.list_reminders(sid)
    assert len(reminders) == 1
    assert reminders[0]["id"] == rid
    assert reminders[0]["text"] == "薬を飲む"
    assert reminders[0]["due"] == "2026-08-01"
    assert reminders[0]["annual"] is False
    assert reminders[0]["done"] is False
    assert reminders[0]["last_fired"] is None


def test_add_reminder_rejects_invalid_due():
    import pytest
    import soul
    sid = soul.create_soul("不正due拒否テスト")
    with pytest.raises(ValueError):
        soul.add_reminder(sid, "変な日付", "2026-13-40")
    with pytest.raises(ValueError):
        soul.add_reminder(sid, "変な日付2", "8月1日")


def test_add_reminder_ids_are_sequential():
    import soul
    sid = soul.create_soul("id連番テスト")
    r1 = soul.add_reminder(sid, "1件目", "2026-08-01")
    r2 = soul.add_reminder(sid, "2件目", "2026-08-02")
    assert r2 == r1 + 1


def test_list_reminders_excludes_done_by_default():
    import soul
    sid = soul.create_soul("done除外テスト")
    rid = soul.add_reminder(sid, "単発", "2020-01-01")
    soul.mark_reminder_fired(sid, rid)
    assert soul.list_reminders(sid) == []
    assert len(soul.list_reminders(sid, include_done=True)) == 1


def test_due_reminders_single_shot_overdue_is_included():
    import soul
    sid = soul.create_soul("単発超過テスト")
    soul.add_reminder(sid, "昔のリマインダ", "2020-01-01")
    due = soul.due_reminders(sid, today="2026-07-22")
    assert len(due) == 1
    assert due[0]["text"] == "昔のリマインダ"


def test_due_reminders_single_shot_future_is_excluded():
    import soul
    sid = soul.create_soul("単発未来テスト")
    soul.add_reminder(sid, "未来のリマインダ", "2099-01-01")
    assert soul.due_reminders(sid, today="2026-07-22") == []


def test_due_reminders_single_shot_not_reinjected_after_done():
    import soul
    sid = soul.create_soul("単発既発火テスト")
    rid = soul.add_reminder(sid, "済んだリマインダ", "2020-01-01")
    soul.mark_reminder_fired(sid, rid)
    assert soul.due_reminders(sid, today="2026-07-22") == []


def test_due_reminders_annual_fires_on_matching_month_day():
    import soul
    sid = soul.create_soul("記念日当日テスト")
    soul.add_reminder(sid, "誕生日", "2020-07-22", annual=True)
    due = soul.due_reminders(sid, today="2026-07-22")
    assert len(due) == 1 and due[0]["text"] == "誕生日"


def test_due_reminders_annual_does_not_fire_on_other_days():
    import soul
    sid = soul.create_soul("記念日別日テスト")
    soul.add_reminder(sid, "誕生日", "2020-07-22", annual=True)
    assert soul.due_reminders(sid, today="2026-07-23") == []
    assert soul.due_reminders(sid, today="2026-06-22") == []


def test_due_reminders_annual_not_refired_same_year_after_mark():
    import soul
    sid = soul.create_soul("記念日同年再発火なしテスト")
    rid = soul.add_reminder(sid, "誕生日", "2020-07-22", annual=True)
    soul.mark_reminder_fired(sid, rid)  # 実行時の"今日"の年で発火済み扱いになる
    reminders = soul.list_reminders(sid, include_done=True)
    fired_year = reminders[0]["last_fired"]
    same_year_date = f"{fired_year}-07-22"
    assert soul.due_reminders(sid, today=same_year_date) == []


def test_due_reminders_annual_refires_next_year():
    import soul
    sid = soul.create_soul("記念日翌年再発火テスト")
    rid = soul.add_reminder(sid, "誕生日", "2020-07-22", annual=True)
    soul.mark_reminder_fired(sid, rid)
    reminders = soul.list_reminders(sid, include_done=True)
    fired_year = reminders[0]["last_fired"]
    next_year_date = f"{fired_year + 1}-07-22"
    due = soul.due_reminders(sid, today=next_year_date)
    assert len(due) == 1 and due[0]["text"] == "誕生日"


def test_due_reminders_annual_never_marked_done():
    import soul
    sid = soul.create_soul("記念日done不変テスト")
    rid = soul.add_reminder(sid, "誕生日", "2020-07-22", annual=True)
    soul.mark_reminder_fired(sid, rid)
    reminders = soul.list_reminders(sid, include_done=True)
    assert reminders[0]["done"] is False


def test_mark_reminder_fired_unknown_id_is_noop():
    import soul
    sid = soul.create_soul("不明id無視テスト")
    soul.add_reminder(sid, "何か", "2026-08-01")
    result = soul.mark_reminder_fired(sid, 9999)
    assert result is False
    assert len(soul.list_reminders(sid)) == 1


def test_create_soul_with_neither_stays_exact_placeholder():
    """核も口調も空のときはidentity.md/speech_style.mdともプレースホルダと完全一致の
    ままであること（prompt.pyのbuild_system_textはこの完全一致でプレースホルダを
    プロンプトから除外している。書き込みロジックを経由しても除外条件が崩れていないことの
    回帰確認）。"""
    import soul
    sid = soul.create_soul("プレースホルダ回帰テスト")
    identity_body = soul.read_file(sid, "identity.md")
    speech_body = soul.read_file(sid, "speech_style.md")
    assert identity_body == soul.IDENTITY_PLACEHOLDER
    assert speech_body == soul.SPEECH_STYLE_PLACEHOLDER
    parts = soul.read_identity_parts(sid)
    assert parts == {"core": "", "speech_style": ""}


# --- 口調分離の後方互換移行（2026-07-22追加）。旧形式（identity.md内に核と口調が
# "## 口調"見出しで同居）のSOULをread_identity_parts経由で開いたとき、口調が
# speech_style.mdへ分離され、identity.mdが核だけに書き戻されること。移行は1回きりで、
# 2回目の呼び出しでは何も変化しない（冪等）ことも確認する。

def _make_legacy_soul(name, core, speech):
    """旧形式（口調分離前）のSOULを模擬生成する。当時のcreate_soulが書いていた
    合成規則（"{core}\n\n## 口調\n{speech}\n"）をここで再現し、identity.md 1本に
    直接書き込む（speech_style.mdは作らない＝旧SOULには存在しなかった）。"""
    import soul
    sid = soul.create_soul(name)  # 核・口調とも空でプレースホルダのまま作る
    legacy_body = f"{core}\n\n## 口調\n{speech}\n"
    soul.write_file(sid, "identity.md", legacy_body)
    return sid


def test_read_identity_parts_migrates_legacy_identity_into_two_files():
    import soul
    sid = _make_legacy_soul("旧形式移行テスト", "ぼくは旧核。", "語尾に「にゃ」をつける")

    parts = soul.read_identity_parts(sid)

    assert parts["core"] == "ぼくは旧核。"
    assert parts["speech_style"] == "語尾に「にゃ」をつける"
    # ファイルが実際に2本に分かれていること（identity.mdに口調が残っていない）
    identity_body = soul.read_file(sid, "identity.md")
    speech_body = soul.read_file(sid, "speech_style.md")
    assert "ぼくは旧核。" in identity_body
    assert "語尾に「にゃ」をつける" not in identity_body
    assert "語尾に「にゃ」をつける" in speech_body


def test_legacy_migration_is_idempotent_on_second_read():
    """1回移行した後、2回目のread_identity_partsでも同じ結果のままであること
    （再移行が誤って何かを壊さない・見出しが無いので即returnする経路を通る）。"""
    import soul
    sid = _make_legacy_soul("旧形式再読テスト", "ぼくは旧核。", "タメ口")

    first = soul.read_identity_parts(sid)
    second = soul.read_identity_parts(sid)

    assert first == second == {"core": "ぼくは旧核。", "speech_style": "タメ口"}


def test_legacy_migration_with_core_empty_note_becomes_empty_core():
    """旧形式で核が空だった場合、identity.mdの先頭にはCORE_EMPTY_NOTEが書かれていた。
    移行後、そのnote文言はcoreに残らず空文字になること。"""
    import soul
    sid = _make_legacy_soul("旧形式core空テスト", soul.CORE_EMPTY_NOTE, "敬語なし")

    parts = soul.read_identity_parts(sid)

    assert parts["core"] == ""
    assert parts["speech_style"] == "敬語なし"
    assert soul.CORE_EMPTY_NOTE not in soul.read_file(sid, "identity.md")


def test_migrate_legacy_identity_writes_speech_style_before_identity(monkeypatch):
    """移行の書き込み順は speech_style.md 保存 → identity.md 書き戻し（この逆はやらない）。
    1回目のwrite_file呼び出し（=speech_style.md保存）をクラッシュさせても、
    identity.mdは旧形式のまま無傷であること（＝次回また再移行を試せる安全側の失敗。
    もしidentity.mdを先に上書きしていたら、この時点で口調が恒久的に失われていた）。"""
    import pytest
    import soul
    sid = _make_legacy_soul("移行順序テスト", "ぼくは旧核。", "語尾に「にゃ」をつける")
    legacy_raw = soul.read_file(sid, "identity.md")

    original_write_file = soul.write_file
    calls = []

    def flaky_write_file(soul_id, rel_path, content):
        calls.append(rel_path)
        if len(calls) == 1:
            raise RuntimeError("模擬クラッシュ（1回目の書き込みで死ぬ）")
        return original_write_file(soul_id, rel_path, content)

    monkeypatch.setattr(soul, "write_file", flaky_write_file)

    with pytest.raises(RuntimeError):
        soul._migrate_legacy_identity(sid)

    assert calls == ["speech_style.md"]  # identity.mdへの書き込みには到達していない
    assert soul.read_file(sid, "identity.md") == legacy_raw  # 旧形式のまま無傷
    assert soul._SPEECH_STYLE_RE.search(soul.read_file(sid, "identity.md"))  # 見出しが残っている＝再移行可能


def test_list_reminders_skips_corrupted_lines():
    """reminders.jsonlに壊れた行（不正JSON・dict以外の正当なJSON）が混じっても、
    正常な行だけが返ること。1行の破損で毎ターンbuild_system_text/process_turnが
    JSONDecodeErrorで死んで会話が恒久停止する事故（実機再現済み）の回帰確認。"""
    import soul
    sid = soul.create_soul("reminders破損耐性テスト")
    rid = soul.add_reminder(sid, "正常なリマインダ", "2026-08-01")
    soul.append_file(sid, soul.REMINDERS_FILE, "{not valid json\n")
    soul.append_file(sid, soul.REMINDERS_FILE, "[1, 2, 3]\n")
    soul.append_file(sid, soul.REMINDERS_FILE, "42\n")

    reminders = soul.list_reminders(sid)
    assert len(reminders) == 1
    assert reminders[0]["id"] == rid
    assert reminders[0]["text"] == "正常なリマインダ"


# --- 名前なし作成・改名（2026-07-22追加）---

def test_create_soul_with_empty_name_writes_empty_name_file():
    """名前を空欄で作成できる（ユーザーがAIと相談して後から名前を決めたいケース）。
    name.txtは空のまま作られ、フォルダスラッグは時刻ベース（soul-<timestamp>）になる
    （固定の"soul"連番だと別インストール同士でIDが衝突しうるため）。"""
    import soul
    sid = soul.create_soul("")
    assert soul.read_file(sid, "name.txt") == ""
    assert sid.startswith("soul-")


def test_create_soul_with_empty_name_twice_does_not_collide():
    """名前なしでcreate_soulを2回呼んでも、soul_idが衝突しないこと（実測済みの
    欠陥の再発防止）。固定スラッグ"soul"では別インストールを想定した衝突耐性が
    ローカル連番だけに頼っていたため、時刻ベースのスラッグへ変えた。
    同一秒内に呼ばれても既存のwhileループによる連番衝突回避が効くため、
    最低限「2回呼んで同じIDにならない」ことをここで確認する。"""
    import soul
    sid1 = soul.create_soul("")
    sid2 = soul.create_soul("")
    assert sid1 != sid2
    assert sid1 != "soul"
    assert sid2 != "soul"


def test_list_souls_shows_unnamed_label_for_empty_name():
    """name.txtが空のSOULは一覧でsoul_id剥き出しではなくUNNAMED_LABELになること。"""
    import soul
    sid = soul.create_soul("")
    entry = next(s for s in soul.list_souls() if s["id"] == sid)
    assert entry["name"] == soul.UNNAMED_LABEL
    assert entry["name"] != sid


def test_read_name_returns_raw_empty_string_not_fallback():
    """read_nameはlist_soulsと違い、フォールバック文言を返さず生の空文字のまま返す
    （編集フォームの初期値用。フォールバック文言を編集フォームに入れると、無変更保存で
    それが実際の名前としてname.txtへ書き込まれてしまうため、生値が必要）。"""
    import soul
    sid = soul.create_soul("")
    assert soul.read_name(sid) == ""


def test_read_name_returns_trimmed_existing_name():
    import soul
    sid = soul.create_soul("  イリア  ")
    assert soul.read_name(sid) == "イリア"


def test_validate_name_accepts_empty_string():
    """空文字は名前を白紙に戻す正当な操作として許可する。"""
    import soul
    ok, result = soul.validate_name("")
    assert ok is True
    assert result == ""


def test_validate_name_trims_whitespace():
    import soul
    ok, result = soul.validate_name("  イリア  ")
    assert ok is True
    assert result == "イリア"


def test_validate_name_rejects_newline():
    import soul
    ok, result = soul.validate_name("イリア\n二行目")
    assert ok is False
    assert result


def test_validate_name_rejects_over_max_chars():
    import soul
    ok, result = soul.validate_name("あ" * (soul.NAME_MAX_CHARS + 1))
    assert ok is False
    assert result


def test_validate_name_accepts_exactly_max_chars():
    import soul
    ok, result = soul.validate_name("あ" * soul.NAME_MAX_CHARS)
    assert ok is True
    assert result == "あ" * soul.NAME_MAX_CHARS


def test_validate_name_rejects_null_control_char():
    """改行以外の制御文字（Cc）、例えばNUL(\\x00)も拒否する（実測済みの欠陥：
    改行だけをチェックしていたため素通りしていた）。"""
    import soul
    ok, result = soul.validate_name("イリア\x00不可視")
    assert ok is False
    assert result


def test_validate_name_rejects_escape_control_char():
    """ESC(\\x1b)のような改行以外の制御文字も拒否する。"""
    import soul
    ok, result = soul.validate_name("イリア\x1b[31m")
    assert ok is False
    assert result


def test_validate_name_accepts_emoji_and_surrogate_pair_characters():
    """絵文字（サロゲートペアを要する文字含む）は制御文字ではないので許可する。"""
    import soul
    ok, result = soul.validate_name("イリア🎉🐱")
    assert ok is True
    assert result == "イリア🎉🐱"


def test_validate_name_rejects_unnamed_label_itself():
    """トリム後の名前がUNNAMED_LABEL（一覧表示の予約フォールバック文言）と
    完全一致する場合は拒否する。許してしまうと、実際の名前とフォールバック表示が
    見分けられなくなる。"""
    import soul
    ok, result = soul.validate_name(soul.UNNAMED_LABEL)
    assert ok is False
    assert result


def test_validate_name_accepts_unnamed_label_with_extra_text():
    """UNNAMED_LABELと完全一致しない限りは通常通り許可する（前後に文字が付けば別物）。"""
    import soul
    ok, result = soul.validate_name(soul.UNNAMED_LABEL + "改")
    assert ok is True
    assert result == soul.UNNAMED_LABEL + "改"


def test_set_name_writes_name_file():
    import soul
    sid = soul.create_soul("旧名前")
    soul.set_name(sid, "新しい名前")
    assert soul.read_name(sid) == "新しい名前"


def test_set_name_to_empty_resets_to_unnamed():
    """一度つけた名前を空文字にする=白紙に戻す操作が通ること。"""
    import soul
    sid = soul.create_soul("旧名前")
    soul.set_name(sid, "")
    assert soul.read_name(sid) == ""
    entry = next(s for s in soul.list_souls() if s["id"] == sid)
    assert entry["name"] == soul.UNNAMED_LABEL


def test_list_reminders_corrupted_lines_do_not_break_firing():
    """壊れた行があっても、他のリマインダの発火判定（due_reminders）は正常に働くこと。"""
    import soul
    sid = soul.create_soul("reminders破損時発火テスト")
    soul.add_reminder(sid, "期限切れ単発", "2020-01-01")
    soul.append_file(sid, soul.REMINDERS_FILE, "{broken\n")

    due = soul.due_reminders(sid, today="2026-07-22")
    assert len(due) == 1
    assert due[0]["text"] == "期限切れ単発"


def test_add_reminder_still_works_after_corrupted_line():
    """既存のadd_reminderはlist_reminders(include_done=True)でnext_idを決めるため、
    壊れた行が混ざっていてもValueErrorやKeyErrorで死なずid採番できること。"""
    import soul
    sid = soul.create_soul("reminders破損時追記テスト")
    rid1 = soul.add_reminder(sid, "1件目", "2026-08-01")
    soul.append_file(sid, soul.REMINDERS_FILE, "not json at all\n")
    rid2 = soul.add_reminder(sid, "2件目", "2026-08-02")
    assert rid2 == rid1 + 1


def test_read_log_for_skips_corrupted_lines():
    """logs/YYYY-MM-DD.jsonlに壊れた行が混じっても、正常な行だけが返ること
    （search.py _log_docsと同じ耐性方針をsoul.read_log_forにも揃える）。"""
    import soul
    sid = soul.create_soul("logs破損耐性テスト")
    soul.append_log(sid, "user", "こんにちは")
    day = __import__("datetime").date.today().isoformat()
    soul.append_file(sid, f"logs/{day}.jsonl", "{broken line\n")
    soul.append_file(sid, f"logs/{day}.jsonl", "[1, 2]\n")
    soul.append_log(sid, "ai", "にゃっほー")

    entries = soul.read_today_log(sid)
    assert [e["who"] for e in entries] == ["user", "ai"]
    assert entries[0]["text"] == "こんにちは"
    assert entries[1]["text"] == "にゃっほー"


# --- revise_identity_file（自己改訂・安全弁3枚）---

_CORE_A = "ぼくは元気なフィエリア。書くことが好き。相手の話をよく聞く。"


def test_revise_identity_first_time_from_placeholder_is_free():
    """旧文がプレースホルダ（未定義）の場合、ガード2（変化量制限）はスキップされ、
    初回の自己定義は自由に通ること。"""
    import soul
    sid = soul.create_soul("初回自由テスト")

    r = soul.revise_identity_file(sid, "identity", _CORE_A)

    assert r["ok"] is True
    assert soul.read_identity_parts(sid)["core"] == _CORE_A


def test_revise_identity_saves_old_version_to_history_before_writing():
    """改訂前に旧版がidentity_history/へ保存されてから新しい内容が書き込まれること。"""
    import os
    import soul
    sid = soul.create_soul("履歴保存テスト", identity_text=_CORE_A)

    new_core = _CORE_A + " 最近は少し落ち着いてきた。"
    r = soul.revise_identity_file(sid, "identity", new_core)

    assert r["ok"] is True
    hist_dir = os.path.join(soul.soul_dir(sid), "identity_history")
    files = [f for f in os.listdir(hist_dir) if f.endswith("-identity.md")]
    assert len(files) == 1
    assert _CORE_A in soul.read_file(sid, f"identity_history/{files[0]}")
    assert soul.read_identity_parts(sid)["core"] == new_core


def test_revise_identity_rejects_large_change_and_keeps_file_intact():
    """旧文の50%未満しか保持しない改訂はガード2で拒否され、ファイルが無傷であること。"""
    import soul
    sid = soul.create_soul("変化量拒否テスト", identity_text=_CORE_A)

    r = soul.revise_identity_file(sid, "identity", "まったく無関係などうでもいい別の文章です。")

    assert r["ok"] is False
    assert "半分" in r["detail"]
    assert soul.read_identity_parts(sid)["core"] == _CORE_A


def test_revise_identity_allows_change_keeping_over_half_similar():
    """旧文の50%以上を保つ改訂は許可されること。"""
    import soul
    sid = soul.create_soul("変化量許容テスト", identity_text=_CORE_A)

    new_core = _CORE_A + " それに加えて、最近は絵を描くのも好きになった。"
    r = soul.revise_identity_file(sid, "identity", new_core)

    assert r["ok"] is True
    assert soul.read_identity_parts(sid)["core"] == new_core


def test_revise_identity_allows_large_append_that_old_ratio_guard_would_reject():
    """旧SequenceMatcher.ratio()ベースのガードは、旧文を一切消さない純粋な追記でも
    追記量が旧文の2倍を超えると誤って拒否してしまう（実測: 旧10字+加筆36字→ratio=0.357で拒否）。
    保持率ベース（旧文のうちnewに残っている量/len(old)）のガードでは、旧文が全部そのまま
    残っている追記は量に関わらず通ること。"""
    import soul
    old_core = "十字ぶんの短い核だよ"
    assert len(old_core) == 10
    sid = soul.create_soul("追記パラドックステスト", identity_text=old_core)
    addition = "追加の文章をここにたくさん書いて旧文の何倍にもなるくらい長く加筆してみますよ〜"
    assert len(addition) > len(old_core) * 2  # 旧ratioガードなら拒否される条件（追記>旧文の2倍）
    new_core = old_core + addition

    r = soul.revise_identity_file(sid, "identity", new_core)

    assert r["ok"] is True
    assert soul.read_identity_parts(sid)["core"] == new_core


def test_revise_identity_rejects_deleting_over_half_of_old_text():
    """旧文の半分以上を削除する改訂（追記の有無に関わらず）はガード2で拒否され、
    ファイルが無傷であること。拒否メッセージも保持率ベースの新文言に更新されている。"""
    import soul
    old_core = "ぼくは元気なフィエリアだよ。書くことがとても好きで、人の話をよく聞く。丁寧に暮らしたい。"
    sid = soul.create_soul("大量削除拒否テスト", identity_text=old_core)
    new_core = old_core[: len(old_core) // 3]  # 旧文の3分の1程度だけ残す＝削除が半分を超える

    r = soul.revise_identity_file(sid, "identity", new_core)

    assert r["ok"] is False
    assert "半分" in r["detail"]
    assert soul.read_identity_parts(sid)["core"] == old_core


def test_revise_identity_rejects_too_short():
    import soul
    sid = soul.create_soul("下限拒否テスト", identity_text=_CORE_A)

    r = soul.revise_identity_file(sid, "identity", "短い")

    assert r["ok"] is False
    assert soul.read_identity_parts(sid)["core"] == _CORE_A


def test_revise_identity_rejects_too_long():
    import soul
    sid = soul.create_soul("上限拒否テスト", identity_text=_CORE_A)

    too_long = "あ" * 3001
    r = soul.revise_identity_file(sid, "identity", too_long)

    assert r["ok"] is False
    assert soul.read_identity_parts(sid)["core"] == _CORE_A


def test_revise_speech_style_independent_of_identity():
    """whichで対象ファイルが切り替わり、片方の改訂がもう片方に影響しないこと。"""
    import soul
    sid = soul.create_soul("口調改訂テスト", identity_text=_CORE_A,
                            speech_style="敬語で丁寧に話す。落ち着いた口調。")

    new_speech = "敬語で丁寧に話す。落ち着いた口調だが、たまに冗談を言う。"
    r = soul.revise_identity_file(sid, "speech_style", new_speech)

    assert r["ok"] is True
    parts = soul.read_identity_parts(sid)
    assert parts["speech_style"] == new_speech
    assert parts["core"] == _CORE_A  # identityは無傷


def test_revise_identity_unknown_which_rejected():
    import soul
    sid = soul.create_soul("未知対象テスト")

    r = soul.revise_identity_file(sid, "nonsense", "そこそこの長さのテキストです。")

    assert r["ok"] is False


# --- inherit_user_from（SOUL作成時のuser.md引き継ぎ） ---

def test_create_soul_inherit_user_notes_copies_content_with_prefix_line():
    import soul
    src = soul.create_soul("引き継ぎ元太郎")
    soul.write_file(src, "user.md", "# user\n\nみんとちゃんは小説家。顔面麻痺持ち。\n")

    new_sid = soul.create_soul("引き継ぎ先次郎", inherit_user_from=src)

    body = soul.read_file(new_sid, "user.md")
    assert "みんとちゃんは小説家" in body
    assert "SOUL『引き継ぎ元太郎』から引き継いだ" in body
    assert "以後は自分の目で更新していくこと" in body
    # 冒頭の注記の後に元の本文が続くこと
    assert body.index("引き継いだ") < body.index("みんとちゃんは小説家")


def test_create_soul_inherit_skipped_when_source_user_is_empty_placeholder():
    """引き継ぎ元のuser.mdが未編集（プレースホルダのまま）なら、コピー扱いにせず
    従来どおりの空user.mdにする（空を「引き継いだ」と偽らない）。"""
    import soul
    src = soul.create_soul("空っぽ太郎")  # user.mdはプレースホルダのまま

    new_sid = soul.create_soul("引き継ぎ先花子", inherit_user_from=src)

    assert soul.read_file(new_sid, "user.md") == soul.USER_PLACEHOLDER
    assert "引き継いだ" not in soul.read_file(new_sid, "user.md")


def test_create_soul_inherit_invalid_soul_id_rejected():
    import pytest
    import soul
    with pytest.raises(ValueError):
        soul.create_soul("不正引き継ぎテスト", inherit_user_from="../../etc")


def test_create_soul_without_inherit_param_is_backward_compatible():
    """inherit_user_from省略時は従来どおり空のuser.mdで始まること（後方互換）。"""
    import soul
    sid = soul.create_soul("後方互換テスト")
    assert soul.read_file(sid, "user.md") == soul.USER_PLACEHOLDER


# --- notes_history（self_notes.md/user.mdの全文置換前の履歴化） ---

def test_update_self_notes_saves_old_version_to_history_before_overwrite():
    import os
    import soul
    sid = soul.create_soul("自己メモ履歴テスト")
    soul.update_self_notes(sid, "初版の自己認識。\n")

    soul.update_self_notes(sid, "改訂版の自己認識。\n")

    hist_dir = os.path.join(soul.soul_dir(sid), "notes_history")
    files = [f for f in os.listdir(hist_dir) if f.startswith("self_notes-")]
    assert len(files) == 1
    assert "初版の自己認識" in soul.read_file(sid, f"notes_history/{files[0]}")
    assert soul.read_file(sid, "self_notes.md") == "改訂版の自己認識。\n"


def test_update_user_notes_saves_old_version_to_history_before_overwrite():
    import os
    import soul
    sid = soul.create_soul("相手理解履歴テスト")
    soul.update_user_notes(sid, "初版の相手理解。\n")

    soul.update_user_notes(sid, "改訂版の相手理解。\n")

    hist_dir = os.path.join(soul.soul_dir(sid), "notes_history")
    files = [f for f in os.listdir(hist_dir) if f.startswith("user-")]
    assert len(files) == 1
    assert "初版の相手理解" in soul.read_file(sid, f"notes_history/{files[0]}")
    assert soul.read_file(sid, "user.md") == "改訂版の相手理解。\n"


def test_update_self_notes_no_history_when_previous_is_placeholder():
    """初回書き込み（旧内容がプレースホルダ）では履歴を作らない（ゴミ防止）。"""
    import os
    import soul
    sid = soul.create_soul("初回書き込みテスト")

    soul.update_self_notes(sid, "はじめての自己認識。\n")

    hist_dir = os.path.join(soul.soul_dir(sid), "notes_history")
    assert not os.path.isdir(hist_dir) or not os.listdir(hist_dir)


def test_update_self_notes_history_prunes_beyond_max_generations():
    """同一ファイルの履歴が上限(50世代)を超えたら、古いものから削除されること。

    history保存は「上書きされる前の内容」を退避するため、N回のupdate_self_notes
    呼び出し（1回目は旧内容がプレースホルダなので履歴なし）でN-1件の履歴が積まれる。
    ここではMAX+2回呼んで履歴をMAX+1件積み、上限を1件だけ超えさせる
    （最古の1件＝"第0版"だけが消えることを厳密に検証するため）。"""
    import os
    import soul
    sid = soul.create_soul("世代上限テスト")
    soul.update_self_notes(sid, "第0版\n")
    for i in range(1, soul.NOTES_HISTORY_MAX_GENERATIONS + 2):
        soul.update_self_notes(sid, f"第{i}版\n")

    hist_dir = os.path.join(soul.soul_dir(sid), "notes_history")
    files = [f for f in os.listdir(hist_dir) if f.startswith("self_notes-")]
    assert len(files) == soul.NOTES_HISTORY_MAX_GENERATIONS
    # 最も古い版（第0版）は削除され、その次の版（第1版）は残っていること
    contents = [soul.read_file(sid, f"notes_history/{f}") for f in files]
    assert not any("第0版" in c for c in contents)
    assert any("第1版" in c for c in contents)


# --- archive_file / unarchive_file（忘却：連想から外すが記録は消さない） ---

def test_archive_file_moves_wiki_preserving_structure():
    import soul
    sid = soul.create_soul("archive移動テスト")
    soul.write_file(sid, "wiki/古い話題.md", "# 古い話題\n本文\n")

    dest = soul.archive_file(sid, "wiki/古い話題.md")

    assert dest == "archive/wiki/古い話題.md"
    assert soul.read_file(sid, "wiki/古い話題.md") == ""
    assert "本文" in soul.read_file(sid, "archive/wiki/古い話題.md")


def test_archive_file_moves_readings_preserving_structure():
    import soul
    sid = soul.create_soul("archive移動readingsテスト")
    soul.write_file(sid, "readings/資料A.md", "# 資料A\n要点\n")

    dest = soul.archive_file(sid, "readings/資料A.md")

    assert dest == "archive/readings/資料A.md"
    assert soul.read_file(sid, "readings/資料A.md") == ""
    assert "要点" in soul.read_file(sid, "archive/readings/資料A.md")


def test_archive_then_unarchive_round_trip_preserves_content():
    import soul
    sid = soul.create_soul("往復テスト")
    original = "# 話題\n中身は無傷であること\n"
    soul.write_file(sid, "wiki/話題.md", original)

    archived_path = soul.archive_file(sid, "wiki/話題.md")
    restored_path = soul.unarchive_file(sid, archived_path)

    assert restored_path == "wiki/話題.md"
    assert soul.read_file(sid, archived_path) == ""
    assert soul.read_file(sid, "wiki/話題.md") == original


def test_archive_file_collision_appends_suffix_without_overwriting():
    """既にarchive/wiki/x.mdが存在する状態で同名を再度archiveしたら、上書きせず
    連番(-2)を振った別ファイルとして退避すること（退避先行・上書き禁止の家訓）。"""
    import soul
    sid = soul.create_soul("衝突テスト")
    soul.write_file(sid, "wiki/x.md", "1回目の内容\n")
    first_dest = soul.archive_file(sid, "wiki/x.md")
    assert first_dest == "archive/wiki/x.md"

    soul.write_file(sid, "wiki/x.md", "2回目の内容\n")
    second_dest = soul.archive_file(sid, "wiki/x.md")

    assert second_dest == "archive/wiki/x-2.md"
    assert "1回目の内容" in soul.read_file(sid, "archive/wiki/x.md")
    assert "2回目の内容" in soul.read_file(sid, "archive/wiki/x-2.md")


def test_unarchive_file_collision_appends_suffix_without_overwriting():
    import soul
    sid = soul.create_soul("unarchive衝突テスト")
    soul.write_file(sid, "wiki/y.md", "現行版\n")
    soul.write_file(sid, "archive/wiki/y.md", "退避されていた旧版\n")

    dest = soul.unarchive_file(sid, "archive/wiki/y.md")

    assert dest == "wiki/y-2.md"
    assert "現行版" in soul.read_file(sid, "wiki/y.md")
    assert "退避されていた旧版" in soul.read_file(sid, "wiki/y-2.md")


def test_archive_file_rejects_sacred():
    import soul
    import pytest
    sid = soul.create_soul("sacred拒否テスト")
    soul.append_file(sid, "sacred/sacred.md", "- 大事な言葉（2026-01-01）\n")
    with pytest.raises(ValueError):
        soul.archive_file(sid, "sacred/sacred.md")
    assert "大事な言葉" in soul.read_file(sid, "sacred/sacred.md")  # 無傷


def test_archive_file_rejects_core_files():
    """identity.md/speech_style.md/self_notes.md/user.md/tasks.md/lessons.mdは
    「忘れる」対象外（wiki/・readings/配下のみ許可）。"""
    import soul
    import pytest
    sid = soul.create_soul("core拒否テスト")
    for rel in ("identity.md", "speech_style.md", "self_notes.md", "user.md",
                "tasks.md", "lessons.md", "MEMORY.md"):
        with pytest.raises(ValueError):
            soul.archive_file(sid, rel)


def test_archive_file_rejects_logs_and_chronicle():
    import soul
    import pytest
    sid = soul.create_soul("logs拒否テスト")
    soul.append_log(sid, "user", "こんにちは")
    today = soul.datetime.date.today().isoformat()
    with pytest.raises(ValueError):
        soul.archive_file(sid, f"logs/{today}.jsonl")
    soul.write_file(sid, "chronicle/2026-01-01.md", "# 日記\n")
    with pytest.raises(ValueError):
        soul.archive_file(sid, "chronicle/2026-01-01.md")


def test_archive_file_rejects_path_traversal():
    import soul
    import pytest
    sid = soul.create_soul("archiveトラバーサル拒否テスト")
    with pytest.raises(ValueError):
        soul.archive_file(sid, "wiki/../../../config.json.md")


def test_archive_file_rejects_missing_file():
    import soul
    import pytest
    sid = soul.create_soul("archive存在しないテスト")
    with pytest.raises(ValueError):
        soul.archive_file(sid, "wiki/存在しない.md")


def test_unarchive_file_rejects_non_archive_prefix():
    import soul
    import pytest
    sid = soul.create_soul("unarchive拒否テスト")
    soul.write_file(sid, "wiki/z.md", "本文\n")
    with pytest.raises(ValueError):
        soul.unarchive_file(sid, "wiki/z.md")  # archive/で始まらない


def test_unarchive_file_rejects_missing_file():
    import soul
    import pytest
    sid = soul.create_soul("unarchive存在しないテスト")
    with pytest.raises(ValueError):
        soul.unarchive_file(sid, "archive/wiki/存在しない.md")


# --- スキル（手続き記憶） ---

def test_write_and_read_skill_round_trip():
    import soul
    sid = soul.create_soul("スキル往復テスト")
    soul.write_skill(sid, "手順A", "1行説明だよ", "1. やる\n2. 終わる")
    body = soul.read_skill(sid, "手順A")
    assert body.startswith("# 手順A\n> 1行説明だよ\n")
    assert "1. やる" in body and "2. 終わる" in body


def test_read_skill_missing_is_empty():
    import soul
    sid = soul.create_soul("スキル未存在テスト")
    assert soul.read_skill(sid, "無いスキル") == ""


def test_list_skills_parses_name_and_description():
    import soul
    sid = soul.create_soul("スキル索引テスト")
    soul.write_skill(sid, "手順A", "説明A", "本文A")
    soul.write_skill(sid, "手順B", "説明B", "本文B")
    skills = soul.list_skills(sid)
    names = {s["name"] for s in skills}
    assert names == {"手順A", "手順B"}
    by_name = {s["name"]: s["description"] for s in skills}
    assert by_name["手順A"] == "説明A"
    assert by_name["手順B"] == "説明B"


def test_list_skills_empty_when_no_skills_dir():
    import soul
    sid = soul.create_soul("スキル空テスト")
    assert soul.list_skills(sid) == []


def test_list_skills_handles_broken_header_gracefully():
    """1行目が"# "始まりでない壊れた形式でも、ファイル名をフォールバック名にして
    説明は空のまま拾う（1本の壊れたファイルで索引全体が死なない）。"""
    import soul
    sid = soul.create_soul("スキル壊れ形式テスト")
    soul.write_file(sid, "skills/壊れた.md", "説明もタイトルもない本文\n")
    skills = soul.list_skills(sid)
    assert len(skills) == 1
    assert skills[0]["name"] == "壊れた"
    assert skills[0]["description"] == ""


def test_write_skill_overwrite_saves_history():
    import os
    import soul
    sid = soul.create_soul("スキル履歴テスト")
    soul.write_skill(sid, "手順A", "旧説明", "旧本文")
    soul.write_skill(sid, "手順A", "新説明", "新本文")

    hist_dir = os.path.join(soul.soul_dir(sid), "skills_history")
    assert os.path.isdir(hist_dir)
    files = os.listdir(hist_dir)
    assert len(files) == 1
    old_content = soul.read_file(sid, f"skills_history/{files[0]}")
    assert "旧本文" in old_content
    assert "旧説明" in old_content
    # 現行ファイルは新版に置き換わっていること
    current = soul.read_skill(sid, "手順A")
    assert "新本文" in current and "旧本文" not in current


def test_write_skill_first_write_creates_no_history():
    import os
    import soul
    sid = soul.create_soul("スキル初回書き込みテスト")
    soul.write_skill(sid, "手順A", "説明", "本文")
    hist_dir = os.path.join(soul.soul_dir(sid), "skills_history")
    assert not os.path.isdir(hist_dir) or not os.listdir(hist_dir)


def test_write_skill_history_prunes_beyond_max_generations():
    import os
    import soul
    sid = soul.create_soul("スキル世代上限テスト")
    soul.write_skill(sid, "手順A", "説明", "第0版")
    for i in range(1, soul.SKILLS_HISTORY_MAX_GENERATIONS + 2):
        soul.write_skill(sid, "手順A", "説明", f"第{i}版")

    hist_dir = os.path.join(soul.soul_dir(sid), "skills_history")
    files = os.listdir(hist_dir)
    assert len(files) == soul.SKILLS_HISTORY_MAX_GENERATIONS
    contents = [soul.read_file(sid, f"skills_history/{f}") for f in files]
    assert not any("第0版" in c for c in contents)
    assert any("第1版" in c for c in contents)


def test_skill_name_sanitizes_traversal_and_reserved_chars():
    import soul
    sid = soul.create_soul("スキル名サニタイズテスト")
    soul.write_skill(sid, "../../evil", "説明", "本文")
    d = soul.soul_dir(sid)
    # 実体ファイルはskills/配下（サニタイズ後の名前）にしかできず、soul_dirの外へは出ない
    import os
    skills_dir = os.path.join(d, "skills")
    assert os.path.isdir(skills_dir)
    for f in os.listdir(skills_dir):
        assert os.path.isfile(os.path.join(skills_dir, f))


# --- スキル名の40字上限・skill_exists・skill_header_name（レビュー指摘・2026-07-22追加）---

def test_write_skill_rejects_name_over_40_chars():
    """41字超はValueErrorで拒否する（切り詰めによる別名衝突を避けるため。
    _safe_name自体は他用途を壊さないよう40字打ち切りのまま据え置き、
    スキル専用の検証は_skill_path経由のwrite_skill側に足す）。"""
    import pytest
    import soul
    sid = soul.create_soul("スキル名上限テスト")
    with pytest.raises(ValueError):
        soul.write_skill(sid, "あ" * 41, "説明", "本文")


def test_write_skill_accepts_name_exactly_40_chars():
    import soul
    sid = soul.create_soul("スキル名上限境界テスト")
    soul.write_skill(sid, "あ" * 40, "説明", "本文")
    assert soul.skill_exists(sid, "あ" * 40)


def test_skill_exists_true_after_write_false_before():
    import soul
    sid = soul.create_soul("スキル存在確認テスト")
    assert soul.skill_exists(sid, "手順A") is False
    soul.write_skill(sid, "手順A", "説明", "本文")
    assert soul.skill_exists(sid, "手順A") is True


def test_skill_exists_detects_sanitization_convergence():
    """"a/b"と"a:b"は_safe_nameのサニタイズで同じファイルへ収束するため、
    片方を書いた後はもう片方の名前でもskill_existsがTrueを返す
    （名前の文字列一致ではなく実ファイルパス基準の判定であることの確認）。"""
    import soul
    sid = soul.create_soul("サニタイズ収束存在確認テスト")
    soul.write_skill(sid, "a/b", "説明", "本文")
    assert soul.skill_exists(sid, "a:b") is True


def test_skill_header_name_returns_parsed_name_when_well_formed():
    import soul
    sid = soul.create_soul("ヘッダ名取得テスト")
    soul.write_skill(sid, "手順A", "説明", "本文")
    assert soul.skill_header_name(sid, "手順A") == "手順A"


def test_skill_header_name_none_when_missing():
    import soul
    sid = soul.create_soul("ヘッダ名不存在テスト")
    assert soul.skill_header_name(sid, "無いスキル") is None


def test_skill_header_name_none_when_header_broken():
    """1行目が"# "始まりでない壊れたファイルは、フォールバック名と区別が付かないため
    Noneを返す（update_skillのヘッダ照合をスキップさせる合図）。"""
    import soul
    sid = soul.create_soul("ヘッダ名壊れテスト")
    soul.write_file(sid, "skills/壊れた.md", "説明もタイトルもない本文\n")
    assert soul.skill_header_name(sid, "壊れた") is None
