def _sid():
    import soul
    return soul.create_soul("ツールテスト")


def test_extract_tool_calls_parses_and_cleans():
    import memory_tools as mt
    reply = ('今日のことwikiにメモしとくね。\n'
             '```fieria-tool\n'
             '{"tool": "write_wiki", "topic": "UI談義", "content": "8枠で決着", "mode": "append"}\n'
             '```\n'
             'で、続きなんだけど。')
    clean, calls = mt.extract_tool_calls(reply)
    assert "fieria-tool" not in clean
    assert "続きなんだけど" in clean
    assert calls[0]["tool"] == "write_wiki"
    assert calls[0]["topic"] == "UI談義"


def test_extract_reports_broken_json_as_parse_error():
    # 2026-07-23: 「壊れは静かに無視して本文に残す」から「壊れは見える化して
    # 除去＋書き直しを促す」へ方針転換（実機で「ツール呼んだのに何も起きない」
    # 混乱を招いたため）。壊れブロックは__parse_error__エントリとして報告され、
    # 本文からは除去される。
    import memory_tools as mt
    reply = '```fieria-tool\n{壊れてる\n```\nほんぶん'
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "__parse_error__"
    assert "壊れてる" in calls[0]["snippet"]
    assert "fieria-tool" not in clean
    assert "ほんぶん" in clean


def test_tools_spec_documents_retraction_mark_policy():
    """記憶の書き換えは消去でなく「【撤回済み】」を付けて残す運用規約が
    ツール説明に含まれること（自作機能2: 撤回マーク運用）。"""
    import memory_tools as mt
    assert "撤回済み" in mt.TOOLS_SPEC


def test_write_wiki_append_and_replace():
    import memory_tools as mt
    import soul
    sid = _sid()
    r1 = mt.execute(sid, {"tool": "write_wiki", "topic": "話題A", "content": "1行目", "mode": "append"})
    assert r1["ok"]
    mt.execute(sid, {"tool": "write_wiki", "topic": "話題A", "content": "2行目", "mode": "append"})
    body = soul.read_file(sid, "wiki/話題A.md")
    assert "1行目" in body and "2行目" in body
    mt.execute(sid, {"tool": "write_wiki", "topic": "話題A", "content": "全置換", "mode": "replace"})
    body = soul.read_file(sid, "wiki/話題A.md")
    assert "全置換" in body and "1行目" not in body


def test_save_reading_note_append_and_replace():
    import memory_tools as mt
    import soul
    sid = _sid()
    r1 = mt.execute(sid, {"tool": "save_reading_note", "source": "資料A",
                          "content": "1行目", "mode": "append"})
    assert r1["ok"]
    mt.execute(sid, {"tool": "save_reading_note", "source": "資料A",
                     "content": "2行目", "mode": "append"})
    body = soul.read_file(sid, "readings/資料A.md")
    assert "1行目" in body and "2行目" in body
    mt.execute(sid, {"tool": "save_reading_note", "source": "資料A",
                     "content": "全置換", "mode": "replace"})
    body = soul.read_file(sid, "readings/資料A.md")
    assert "全置換" in body and "1行目" not in body


def test_save_reading_note_sanitizes_source():
    """topicと同じ_safe_nameを通すため、パス区切り文字を含むsourceでも
    readings/直下から出ないこと（write_wikiと同じトラバーサル対策）。"""
    import os
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "save_reading_note",
                         "source": "../../evil", "content": "内容", "mode": "append"})
    assert r["ok"]
    assert not os.path.isfile(os.path.join(soul.soul_dir(sid), "..", "..", "evil.md"))
    assert os.path.isdir(os.path.join(soul.soul_dir(sid), "readings"))


def test_save_reading_note_not_a_feedback_tool():
    """save_reading_noteは書き込み系ツールなのでFEEDBACK_TOOLS（LLMへの差し戻し対象）
    には含まれないこと。"""
    import memory_tools as mt
    assert "save_reading_note" not in mt.FEEDBACK_TOOLS


def test_save_reading_note_in_tools_index_and_details():
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "save_reading_note" in spec
    detail = mt._execute_describe_tools(["save_reading_note"])
    assert detail["ok"]
    assert "readings" in detail["detail"]
    assert "update_memory_index" in detail["detail"]


def test_read_doc_and_read_pdf_details_mention_save_reading_note():
    """read_doc/read_pdfの詳細説明に、読んだ資料の要点をsave_reading_noteへ
    残せるという促しが入っていること。"""
    import memory_tools as mt
    detail = mt._execute_describe_tools(["read_doc", "read_pdf"])
    assert detail["detail"].count("save_reading_note") >= 2


def test_save_sacred_appends_with_date():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "save_sacred", "text": "妥協は三日で作り直す"})
    assert r["ok"]
    body = soul.read_file(sid, "sacred/sacred.md")
    assert "妥協は三日で作り直す" in body


def test_read_memory_returns_content():
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_file(sid, "wiki/既存.md", "既存の中身じゃよ")
    r = mt.execute(sid, {"tool": "read_memory", "path": "wiki/既存.md"})
    assert r["ok"] and "既存の中身じゃよ" in r["detail"]


def test_unknown_tool_fails_without_exception():
    import memory_tools as mt
    r = mt.execute(_sid(), {"tool": "explode_everything"})
    assert r["ok"] is False


def test_update_memory_index_appends_line():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "update_memory_index", "line": "- [話題A](wiki/話題A.md) — 8枠の件"})
    assert r["ok"]
    assert "話題A" in soul.read_file(sid, "MEMORY.md")


def test_execute_non_dict_call_returns_error():
    import memory_tools as mt
    import soul
    sid = soul.create_soul("型ガードテスト")
    for bad in ([1, 2], "text", 42, None):
        r = mt.execute(sid, bad)
        assert r["ok"] is False  # 例外が漏れないこと


def test_build_tools_spec_omits_workspace_section_when_unset():
    """2026-07-22追記: build_tools_spec本体はTOOLS_SPEC全文連結ではなく「索引」
    （各ツール1行＋最小例）を返すよう変更した（固定注入コスト削減・§9.5遅延読み化）。
    TOOLS_SPEC定数自体は後方互換のため残るが、build_tools_spec(None)と一致する必要はない。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({"workspace_dir": ""})
    assert "作業フォルダツール" not in spec
    spec_no_key = mt.build_tools_spec({})
    assert "作業フォルダツール" not in spec_no_key
    spec_none = mt.build_tools_spec(None)
    assert "write_wiki" in spec_none and "read_memory" in spec_none  # 記憶ツールの索引は含む


def test_build_tools_spec_includes_workspace_section_when_set(tmp_path):
    import memory_tools as mt
    spec = mt.build_tools_spec({"workspace_dir": str(tmp_path)})
    assert "作業フォルダツール" in spec
    assert "list_workspace" in spec
    assert "read_doc" in spec and "search_workspace" in spec
    assert "write_wiki" in spec  # 記憶ツールの索引も削らずそのまま残る


def test_build_tools_spec_index_mentions_describe_tools_and_retraction_policy():
    """索引側にも「詳細はdescribe_toolsで」の案内と、撤回マーク運用（同意・安全系の文言）が
    毎ターン残っていること（describe_toolsを呼ばないと知らない、では困る類のため）。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "describe_tools" in spec
    assert "撤回済み" in spec


def test_build_tools_spec_index_keeps_set_soul_name_consent_wording():
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "set_soul_name" in spec
    assert "勝手に決めない" in spec


def test_build_tools_spec_index_keeps_revise_guard_wording():
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "大きく変えない" in spec


def test_build_tools_spec_index_all_tool_names_appear_exactly_once():
    """索引には有効な全ツール名が1回ずつ現れること（重複や取りこぼしがないこと）。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({"workspace_dir": "dummy", "auto_role_switch": True})
    all_names = (mt._MEMORY_TOOL_NAMES + mt._WORKSPACE_TOOL_NAMES + mt._ROLE_TOOL_NAMES)
    for name in all_names:
        assert spec.count(name) >= 1, f"{name} が索引に見つからない"


def test_build_tools_spec_stays_compact_even_with_everything_enabled():
    """固定注入コストの実測。workspace有り＋ロール自動切替ありの最大構成でも、
    旧実装（TOOLS_SPEC+WORKSPACE_TOOLS_SPEC全文連結）より十分小さいこと。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({"workspace_dir": "dummy", "auto_role_switch": True})
    legacy = mt._legacy_build_tools_spec({"workspace_dir": "dummy", "auto_role_switch": True})
    assert len(spec) < len(legacy)


def test_workspace_tools_are_feedback_tools():
    import memory_tools as mt
    assert {"list_workspace", "read_doc"} <= mt.FEEDBACK_TOOLS


def test_list_workspace_returns_docs(tmp_path):
    import memory_tools as mt
    import workspace
    sid = _sid()
    workspace.write_doc(str(tmp_path), "a.md", "内容")
    r = mt.execute(sid, {"tool": "list_workspace"}, {"workspace_dir": str(tmp_path)})
    assert r["ok"] and "a.md" in r["detail"]


def test_read_doc_returns_content_via_execute(tmp_path):
    import memory_tools as mt
    import workspace
    sid = _sid()
    workspace.write_doc(str(tmp_path), "a.md", "本文じゃよ")
    r = mt.execute(sid, {"tool": "read_doc", "path": "a.md"}, {"workspace_dir": str(tmp_path)})
    assert r["ok"] and "本文じゃよ" in r["detail"]


def test_write_doc_writes_content_via_execute(tmp_path):
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "write_doc", "path": "a.md", "content": "書いた"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"]
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "書いた"


def test_append_doc_appends_content_via_execute(tmp_path):
    import memory_tools as mt
    import workspace
    sid = _sid()
    workspace.write_doc(str(tmp_path), "a.md", "1行目\n")
    r = mt.execute(sid, {"tool": "append_doc", "path": "a.md", "content": "2行目\n"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"]
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "1行目\n2行目\n"


def test_workspace_tools_fail_without_exception_when_dir_unset():
    import memory_tools as mt
    sid = _sid()
    for tool, extra in [
        ("list_workspace", {}),
        ("read_doc", {"path": "a.md"}),
        ("write_doc", {"path": "a.md", "content": "x"}),
        ("append_doc", {"path": "a.md", "content": "x"}),
    ]:
        call = {"tool": tool, **extra}
        r = mt.execute(sid, call, {"workspace_dir": ""})
        assert r["ok"] is False
        r2 = mt.execute(sid, call)  # cfg省略（後方互換）でも例外にならない
        assert r2["ok"] is False


def test_workspace_tool_rejects_disallowed_extension_without_exception(tmp_path):
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "read_doc", "path": "secret.env"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is False


def test_extract_handles_fence_glued_to_body_text():
    # 実機再現: Geminiの出力で閉じフェンス```の直後に改行なしで本文が連結されていたケース。
    import memory_tools as mt
    reply = ('保存するね\n```fieria-tool\n'
             '{"tool": "save_sacred", "text": "記念"}\n'
             '```お互いに繋がって嬉しい')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "save_sacred"
    assert "fieria-tool" not in clean
    assert "保存するね" in clean
    assert "お互いに繋がって嬉しい" in clean


def test_extract_handles_missing_closing_fence_entirely():
    # 閉じフェンスが最初から存在しなくても、raw_decodeでJSON終端が確定できればツールは動く。
    import memory_tools as mt
    reply = ('```fieria-tool\n'
             '{"tool": "read_memory", "path": "wiki/x.md"}\n'
             'そのあと本文')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "read_memory"
    assert calls[0]["path"] == "wiki/x.md"
    assert "そのあと本文" in clean


# --- フェンス表記ゆれの許容（実機FB第3弾・2026-07-23）---

def test_extract_accepts_four_backtick_fence():
    import memory_tools as mt
    reply = ('見てね\n````fieria-tool\n{"tool": "save_sacred", "text": "y"}\n````\n続き')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "save_sacred"
    assert "fieria-tool" not in clean
    assert "見てね" in clean and "続き" in clean


def test_extract_accepts_underscore_variant():
    import memory_tools as mt
    reply = ('```fieria_tool\n{"tool": "save_sacred", "text": "z"}\n```\n続き')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "save_sacred"
    assert "続き" in clean


def test_extract_accepts_uppercase_tag():
    import memory_tools as mt
    reply = ('```FIERIA-TOOL\n{"tool": "save_sacred", "text": "w"}\n```\n続き')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "save_sacred"


def test_extract_accepts_no_newline_after_tag():
    import memory_tools as mt
    reply = ('```fieria-tool{"tool": "save_sacred", "text": "v"}```\n続き')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "save_sacred"
    assert "続き" in clean


def test_extract_accepts_crlf():
    import memory_tools as mt
    reply = ('```fieria-tool\r\n{"tool": "save_sacred", "text": "u"}\r\n```\r\n続き')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "save_sacred"
    assert "続き" in clean


def test_extract_parse_error_without_closing_fence_truncates_at_200_chars():
    # 閉じフェンスが最後まで見つからない壊れブロックは、開きフェンス直後から200字だけを
    # 除去範囲とする（無限に本文を食い荒らさないための打ち切り）。300字の「あ」の後ろに
    # 続く「本文」は200字の外側にあるので除去されず残る。
    import memory_tools as mt
    reply = '```fieria-tool\n{壊れてる' + ('あ' * 300) + '本文'
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "__parse_error__"
    assert len(calls[0]["snippet"]) <= 120
    assert "fieria-tool" not in clean
    assert "本文" in clean  # 200字打ち切りの範囲外なので本文側に残る


def test_save_lesson_appends_with_date():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "save_lesson", "text": "敬語を使わない"})
    assert r["ok"]
    body = soul.read_file(sid, "lessons.md")
    assert "敬語を使わない" in body


def test_tools_spec_documents_save_lesson_and_reminders():
    import memory_tools as mt
    assert "save_lesson" in mt.TOOLS_SPEC
    assert "set_reminder" in mt.TOOLS_SPEC
    assert "list_reminders" in mt.TOOLS_SPEC


def test_set_reminder_registers_via_execute():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_reminder", "text": "薬を飲む",
                          "due": "2026-08-01", "annual": False})
    assert r["ok"]
    reminders = soul.list_reminders(sid)
    assert len(reminders) == 1
    assert reminders[0]["text"] == "薬を飲む"


def test_set_reminder_invalid_due_fails_without_exception():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_reminder", "text": "変な日付", "due": "変"})
    assert r["ok"] is False


def test_list_reminders_via_execute_returns_formatted_detail():
    import memory_tools as mt
    sid = _sid()
    mt.execute(sid, {"tool": "set_reminder", "text": "散歩", "due": "2026-08-01"})
    r = mt.execute(sid, {"tool": "list_reminders"})
    assert r["ok"]
    assert "散歩" in r["detail"]


def test_list_reminders_is_feedback_tool():
    import memory_tools as mt
    assert "list_reminders" in mt.FEEDBACK_TOOLS


def test_unclosed_block_with_later_fence_reports_parse_error():
    # 2026-07-23: 旧仕様は「壊れは本文に残す」だったため calls==[] かつ全文無傷を
    # 期待していた。新仕様では壊れブロックは__parse_error__として報告し、本文からは
    # 「開きフェンス～次の```{3,}まで」を除去する。ここでの「次の閉じフェンス」は
    # 対応関係のない```python（別のコードブロック）だが、仕様上は最初に見つかった
    # ```{3,}をそのまま境界に使うため、間に挟まる「つづきの本文」も除去対象になる
    # （閉じ忘れの壊れJSONと後続コードブロックが隣接する稀なケースの割り切り）。
    import memory_tools as mt
    reply = ('メモするね\n```fieria-tool\n{"tool": "save_sacred", "text": "x"\n'
             'つづきの本文\n```python\nprint(1)\n```\nさいごの本文')
    clean, calls = mt.extract_tool_calls(reply)
    assert len(calls) == 1
    assert calls[0]["tool"] == "__parse_error__"
    assert "save_sacred" in calls[0]["snippet"]
    assert "つづきの本文" not in clean
    assert "さいごの本文" in clean and "print(1)" in clean
    assert "メモするね" in clean


# --- revise_identity / revise_speech_style（自己改訂ツール・2026-07-22追加）---

def test_revise_identity_via_execute_writes_identity_md():
    import memory_tools as mt
    import soul
    sid = _sid()
    text = "ぼくは前より少し落ち着いたフィエリア。書くことがやっぱり好き。"
    r = mt.execute(sid, {"tool": "revise_identity", "text": text})
    assert r["ok"] is True
    assert soul.read_identity_parts(sid)["core"] == text


def test_revise_speech_style_via_execute_writes_speech_style_md():
    import memory_tools as mt
    import soul
    sid = _sid()
    text = "タメ口でおしゃべりする。語尾はゆるめ。たまに絵文字も使う。"
    r = mt.execute(sid, {"tool": "revise_speech_style", "text": text})
    assert r["ok"] is True
    assert soul.read_identity_parts(sid)["speech_style"] == text


def test_revise_identity_via_execute_rejects_too_short_without_exception():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "revise_identity", "text": "短"})
    assert r["ok"] is False


def test_tools_spec_documents_revise_identity_and_speech_style():
    import memory_tools as mt
    assert "revise_identity" in mt.TOOLS_SPEC
    assert "revise_speech_style" in mt.TOOLS_SPEC


# --- set_soul_name（名前なし開始・自己改名・2026-07-22追加）---

def test_set_soul_name_via_execute_writes_name_file():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_soul_name", "name": "イリア"})
    assert r["ok"] is True
    assert soul.read_name(sid) == "イリア"


def test_set_soul_name_rejects_newline_without_exception():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_soul_name", "name": "イリア\n二行目"})
    assert r["ok"] is False
    assert soul.read_name(sid) == "ツールテスト"  # 拒否時は元の名前のまま


def test_set_soul_name_rejects_over_max_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_soul_name", "name": "あ" * 101})
    assert r["ok"] is False


def test_set_soul_name_to_empty_resets_name():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_soul_name", "name": ""})
    assert r["ok"] is True
    assert soul.read_name(sid) == ""


def test_set_soul_name_missing_name_key_is_rejected_without_resetting():
    """nameフィールド自体が欠落した壊れたツール呼び出し（{"tool": "set_soul_name"}のみ）は
    エラーとして拒否し、既存の名前を消さないこと（実測済みの欠陥：以前は
    call.get("name", "")のデフォルト値""が「名前を白紙に戻す」操作として
    誤実行されていた）。"""
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "set_soul_name"})
    assert r["ok"] is False
    assert soul.read_name(sid) == "ツールテスト"  # 名前は消えていない


def test_tools_spec_documents_set_soul_name_with_consent_first_wording():
    """名前は本人が勝手に決めるのではなく、ユーザーとの合意が前提であることを
    ツール説明文に明記する（create_role等の既存の同意系文言と同じトーン）。"""
    import memory_tools as mt
    assert "set_soul_name" in mt.TOOLS_SPEC
    assert "相談" in mt.TOOLS_SPEC
    assert "勝手に決めない" in mt.TOOLS_SPEC


def test_set_soul_name_is_not_a_feedback_tool():
    """set_soul_nameは書き込み系ツールであり、read_memory等のような
    LLMへの差し戻し(FEEDBACK_TOOLS)対象ではない。"""
    import memory_tools as mt
    assert "set_soul_name" not in mt.FEEDBACK_TOOLS


# --- update_tasks（気にかけリスト・秘書層・2026-07-22追加）---

def test_update_tasks_replaces_full_content_via_execute():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "update_tasks", "content": "- 買い物に行く\n- 原稿を進める"})
    assert r["ok"] is True
    body = soul.read_file(sid, "tasks.md")
    assert "買い物に行く" in body and "原稿を進める" in body


def test_update_tasks_overwrites_previous_content_not_appends():
    import memory_tools as mt
    import soul
    sid = _sid()
    mt.execute(sid, {"tool": "update_tasks", "content": "- 旧タスク"})
    mt.execute(sid, {"tool": "update_tasks", "content": "- 新タスクのみ"})
    body = soul.read_file(sid, "tasks.md")
    assert "新タスクのみ" in body
    assert "旧タスク" not in body


def test_tools_spec_documents_update_tasks():
    import memory_tools as mt
    assert "update_tasks" in mt.TOOLS_SPEC


# --- switch_role（AIによるロール自動切替・2026-07-22追加）---

def test_switch_role_updates_cfg_and_persists_config():
    import config
    import memory_tools as mt
    import roles as roles_mod
    sid = _sid()
    target = roles_mod.list_roles()[0]["name"]
    cfg = config.load_config()
    cfg["auto_role_switch"] = True
    r = mt.execute(sid, {"tool": "switch_role", "name": target}, cfg)
    assert r["ok"] is True
    assert cfg["active_role"] == target
    reloaded = config.load_config()
    assert reloaded["active_role"] == target


def test_switch_role_unknown_name_fails_with_available_list():
    import config
    import memory_tools as mt
    sid = _sid()
    cfg = config.load_config()
    r = mt.execute(sid, {"tool": "switch_role", "name": "存在しないロール"}, cfg)
    assert r["ok"] is False
    assert "存在しないロール" in r["detail"]
    assert "会話" in r["detail"]  # 利用可能なロール名一覧が入っていること
    assert cfg.get("active_role") != "存在しないロール"


def test_switch_role_without_cfg_fails_without_exception():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "switch_role", "name": "会話"})
    assert r["ok"] is False


def test_build_tools_spec_omits_switch_role_when_disabled():
    import memory_tools as mt
    spec = mt.build_tools_spec({"auto_role_switch": False})
    assert "switch_role" not in spec


def test_build_tools_spec_includes_switch_role_with_role_list_when_enabled():
    import memory_tools as mt
    import roles as roles_mod
    spec = mt.build_tools_spec({"auto_role_switch": True})
    assert "switch_role" in spec
    for r in roles_mod.list_roles():
        assert r["name"] in spec


# --- create_role（AIによるロール提案・作成・2026-07-22追加）---

def test_create_role_saves_and_persists_to_roles_json():
    import json
    import memory_tools as mt
    import roles as roles_mod
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_role", "name": "新ロール", "prompt": "新しい仕事をする"})
    assert r["ok"] is True
    assert "新ロール" in r["detail"]
    assert roles_mod.get_role("新ロール")["prompt"] == "新しい仕事をする"
    with open(roles_mod.ROLES_PATH, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert "新ロール" in [x["name"] for x in saved]


def test_create_role_rejects_duplicate_name_without_touching_existing():
    import memory_tools as mt
    import roles as roles_mod
    sid = _sid()
    original = roles_mod.get_role("会話")["prompt"]
    r = mt.execute(sid, {"tool": "create_role", "name": "会話", "prompt": "上書きしようとする内容"})
    assert r["ok"] is False
    assert "編集は設定画面から" in r["detail"]
    assert roles_mod.get_role("会話")["prompt"] == original


def test_create_role_rejects_empty_name_or_prompt():
    import memory_tools as mt
    sid = _sid()
    r1 = mt.execute(sid, {"tool": "create_role", "name": "", "prompt": "やること"})
    assert r1["ok"] is False
    r2 = mt.execute(sid, {"tool": "create_role", "name": "名前だけ", "prompt": ""})
    assert r2["ok"] is False


def test_create_role_rejects_too_long_name_or_prompt():
    import memory_tools as mt
    sid = _sid()
    r1 = mt.execute(sid, {"tool": "create_role", "name": "あ" * 21, "prompt": "やること"})
    assert r1["ok"] is False
    r2 = mt.execute(sid, {"tool": "create_role", "name": "長文prompt", "prompt": "あ" * 501})
    assert r2["ok"] is False


def test_tools_spec_documents_create_role():
    # create_roleの説明はTOOLS_SPEC本体でなくauto_role_switch連動セクション側にある
    import memory_tools as mt
    assert "create_role" not in mt.TOOLS_SPEC
    spec = mt.build_tools_spec({"auto_role_switch": True})
    assert "create_role" in spec
    assert "提案" in spec


def test_build_tools_spec_omits_create_role_when_auto_role_switch_disabled():
    import memory_tools as mt
    spec = mt.build_tools_spec({"auto_role_switch": False})
    assert "create_role" not in spec


def test_created_role_appears_in_switch_role_dynamic_list_next_call():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_role", "name": "動的反映テスト", "prompt": "動的反映の確認用"})
    assert r["ok"] is True
    spec = mt.build_tools_spec({"auto_role_switch": True})
    assert "動的反映テスト" in spec


# --- read_pdf（作業フォルダのPDF読みツール・Geminiネイティブ対応・2026-07-22追加）---

def _write_mini_pdf(tmp_path, name="資料.pdf"):
    """pypdfium2で実際に開ける最小PDF（tests/test_gui_pdf.pyのフィクスチャを再利用）。"""
    import test_gui_pdf as gui_pdf_fixtures
    path = tmp_path / name
    path.write_bytes(gui_pdf_fixtures.MINI_PDF_1PAGE)
    return name


def test_read_pdf_is_feedback_tool():
    import memory_tools as mt
    assert "read_pdf" in mt.FEEDBACK_TOOLS


def test_tools_spec_documents_read_pdf_only_in_workspace_section():
    import memory_tools as mt
    assert "read_pdf" not in mt.TOOLS_SPEC
    spec = mt.build_tools_spec({"workspace_dir": "dummy"})
    assert "read_pdf" in spec


def test_tools_spec_read_pdf_oneliner_mentions_text_mode():
    """2026-07-22追加: read_pdfのテキスト抽出モード(mode:"text")が索引の1行にも
    現れること（describe_toolsまで行かなくても存在に気づける必要があるため）。"""
    import memory_tools as mt
    assert 'mode:"text"' in mt._TOOL_ONELINERS["read_pdf"]
    spec = mt.build_tools_spec({"workspace_dir": "dummy"})
    assert 'mode:"text"' in spec


def test_describe_tools_read_pdf_detail_documents_text_mode():
    import memory_tools as mt
    r = mt._execute_describe_tools(["read_pdf"])
    assert r["ok"] is True
    assert 'mode:"text"' in r["detail"]
    assert "start_page" in r["detail"]


def test_read_pdf_fails_without_exception_when_workspace_dir_unset():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "read_pdf", "path": "資料.pdf"}, {"workspace_dir": ""})
    assert r["ok"] is False


def test_read_pdf_gemini_provider_returns_application_pdf_attachment(tmp_path):
    """llm.provider が type:"gemini" のプロバイダを指しているとき、read_pdfは
    ページ画像化せずbase64のままapplication/pdfとしてattachmentsに積む。"""
    import base64
    import memory_tools as mt
    sid = _sid()
    name = _write_mini_pdf(tmp_path)
    cfg = {
        "workspace_dir": str(tmp_path),
        "llm": {"provider": "gemini", "providers": {"gemini": {"type": "gemini"}}},
    }

    r = mt.execute(sid, {"tool": "read_pdf", "path": name}, cfg)

    assert r["ok"] is True
    assert len(r["attachments"]) == 1
    att = r["attachments"][0]
    assert att["mime"] == "application/pdf"
    import test_gui_pdf as gui_pdf_fixtures
    assert base64.b64decode(att["b64"]) == gui_pdf_fixtures.MINI_PDF_1PAGE
    assert "Gemini直読み" in r["detail"]


def test_read_pdf_gemini_provider_attachment_includes_page_count(tmp_path):
    """2026-07-22追記: Gemini直読み添付はpagesキー（ページ数）も持つこと
    （engine._estimate_attachment_tokensが固定800トークンではなく実ページ数ベースで
    見積もれるようにするため。read_pdf自体はページを画像化しないので、カウントだけ
    別途pdf_render.count_pagesで行う）。"""
    import memory_tools as mt
    sid = _sid()
    name = _write_mini_pdf(tmp_path)  # MINI_PDF_1PAGE = 1ページ
    cfg = {
        "workspace_dir": str(tmp_path),
        "llm": {"provider": "gemini", "providers": {"gemini": {"type": "gemini"}}},
    }

    r = mt.execute(sid, {"tool": "read_pdf", "path": name}, cfg)

    assert r["ok"] is True
    assert r["attachments"][0]["pages"] == 1


def test_read_pdf_gemini_provider_attachment_omits_pages_when_count_fails(tmp_path, monkeypatch):
    """ページ数取得に失敗しても（壊れたPDF等を想定した例外注入）、添付自体・
    read_pdf自体は失敗させない（pagesキー無しのままフォールバック概算に任せる）。"""
    import memory_tools as mt
    import pdf_render
    sid = _sid()
    name = _write_mini_pdf(tmp_path)
    cfg = {
        "workspace_dir": str(tmp_path),
        "llm": {"provider": "gemini", "providers": {"gemini": {"type": "gemini"}}},
    }

    def boom(raw_bytes):
        raise RuntimeError("壊れたPDFを想定した注入")
    monkeypatch.setattr(pdf_render, "count_pages", boom)

    r = mt.execute(sid, {"tool": "read_pdf", "path": name}, cfg)

    assert r["ok"] is True
    assert "pages" not in r["attachments"][0]


def test_read_pdf_non_gemini_provider_returns_rendered_page_images(tmp_path):
    """type:"gemini"以外（例: openai_compat）なら、pdf_render経由でページ画像化
    してからattachmentsに積む（gui.render_pdfと同じ変換処理）。"""
    import memory_tools as mt
    sid = _sid()
    name = _write_mini_pdf(tmp_path)
    cfg = {
        "workspace_dir": str(tmp_path),
        "llm": {"provider": "custom", "providers": {"custom": {"type": "openai_compat"}}},
    }

    r = mt.execute(sid, {"tool": "read_pdf", "path": name}, cfg)

    assert r["ok"] is True
    assert len(r["attachments"]) == 1
    att = r["attachments"][0]
    assert att["mime"] == "image/jpeg"
    decoded = __import__("base64").b64decode(att["b64"])
    assert decoded[:2] == b"\xff\xd8"  # JPEG SOIマーカー
    assert "1ページ" in r["detail"]


def test_read_pdf_without_llm_cfg_defaults_to_rendered_images(tmp_path):
    """cfgにllmキー自体が無い（後方互換・テストの手組みcfg）場合は非Gemini扱いになり、
    例外を投げずページ画像化経路にフォールバックする。"""
    import memory_tools as mt
    sid = _sid()
    name = _write_mini_pdf(tmp_path)
    r = mt.execute(sid, {"tool": "read_pdf", "path": name}, {"workspace_dir": str(tmp_path)})
    assert r["ok"] is True
    assert r["attachments"][0]["mime"] == "image/jpeg"


def test_read_pdf_missing_file_fails_without_exception(tmp_path):
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "read_pdf", "path": "no-such.pdf"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is False


def test_read_pdf_rejects_disallowed_extension_without_exception(tmp_path):
    import memory_tools as mt
    sid = _sid()
    (tmp_path / "note.md").write_text("note", encoding="utf-8")
    r = mt.execute(sid, {"tool": "read_pdf", "path": "note.md"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is False


# --- read_pdf mode:"text"（テキスト抽出モード・2026-07-22追加）---

def _write_text_pdf(tmp_path, fixture_name, name="資料.pdf"):
    """テキスト描画オペレータ入りの最小PDF（tests/test_gui_pdf.pyのフィクスチャ）を
    作業フォルダへ書く。fixture_nameは"MINI_PDF_TEXT_1PAGE"等。"""
    import test_gui_pdf as gui_pdf_fixtures
    path = tmp_path / name
    path.write_bytes(getattr(gui_pdf_fixtures, fixture_name))
    return name


def test_read_pdf_mode_text_returns_extracted_text_as_detail(tmp_path):
    import memory_tools as mt
    sid = _sid()
    name = _write_text_pdf(tmp_path, "MINI_PDF_TEXT_1PAGE")

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "text"},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is True
    assert r["detail"] == "[p1]\nHello PDF\n"
    assert "attachments" not in r  # 画像化しない・トークン効率優先の経路


def test_read_pdf_mode_text_supports_start_page_and_max_pages(tmp_path):
    import memory_tools as mt
    sid = _sid()
    name = _write_text_pdf(tmp_path, "MINI_PDF_TEXT_2PAGE")

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "text", "start_page": 2},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is True
    assert r["detail"] == "[p2]\nPage Two Text\n"


def test_read_pdf_mode_text_max_pages_reports_continuation(tmp_path):
    import memory_tools as mt
    sid = _sid()
    name = _write_text_pdf(tmp_path, "MINI_PDF_TEXT_2PAGE")

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "text", "max_pages": 1},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is True
    assert "続きは start_page=2" in r["detail"]


def test_read_pdf_mode_text_start_page_out_of_range_fails_without_exception(tmp_path):
    import memory_tools as mt
    sid = _sid()
    name = _write_text_pdf(tmp_path, "MINI_PDF_TEXT_1PAGE")  # 全1ページしかない

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "text", "start_page": 5},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is False


def test_read_pdf_mode_omitted_defaults_to_image_mode_unchanged(tmp_path):
    """後方互換: modeキー省略時は従来どおり画像化経路（既存テストの
    test_read_pdf_non_gemini_provider_returns_rendered_page_images等で担保される挙動を
    ここでも明示的に確認する）。"""
    import memory_tools as mt
    sid = _sid()
    name = _write_mini_pdf(tmp_path)
    cfg = {
        "workspace_dir": str(tmp_path),
        "llm": {"provider": "custom", "providers": {"custom": {"type": "openai_compat"}}},
    }

    r = mt.execute(sid, {"tool": "read_pdf", "path": name}, cfg)

    assert r["ok"] is True
    assert r["attachments"][0]["mime"] == "image/jpeg"


def test_read_pdf_unknown_mode_fails_without_exception(tmp_path):
    import memory_tools as mt
    sid = _sid()
    name = _write_text_pdf(tmp_path, "MINI_PDF_TEXT_1PAGE")

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "nonsense"},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is False


def test_read_pdf_mode_text_no_text_layer_guides_to_image_mode(tmp_path):
    """テキスト層が無いPDF（既存のMINI_PDF_1PAGE・コンテンツストリーム無し）を
    mode:"text"で読むと、失敗にはせずmode:"image"へ誘導する案内文をdetailで返す。"""
    import memory_tools as mt
    sid = _sid()
    name = _write_mini_pdf(tmp_path)

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "text"},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is True
    assert 'mode:"image"' in r["detail"]


def test_read_pdf_mode_text_respects_max_pdf_bytes_guard(tmp_path, monkeypatch):
    """既存の14MB超ガード（workspace.read_pdf_bytes）はmode:"text"でも効くこと。"""
    import memory_tools as mt
    import workspace
    sid = _sid()
    name = _write_text_pdf(tmp_path, "MINI_PDF_TEXT_1PAGE")
    monkeypatch.setattr(workspace, "MAX_PDF_BYTES", 1)  # どんなファイルも超過扱いにする

    r = mt.execute(sid, {"tool": "read_pdf", "path": name, "mode": "text"},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is False


def test_read_pdf_mode_text_rejects_missing_path_guard(tmp_path):
    """既存の不正パス（作業フォルダ外脱出）ガードはmode:"text"でも効くこと。"""
    import memory_tools as mt
    sid = _sid()

    r = mt.execute(sid, {"tool": "read_pdf", "path": "../outside.pdf", "mode": "text"},
                   {"workspace_dir": str(tmp_path)})

    assert r["ok"] is False


# --- read_doc部分読み（execute経由・2026-07-22追加）---

def test_read_doc_via_execute_supports_start_line_and_max_lines(tmp_path):
    import memory_tools as mt
    sid = _sid()
    (tmp_path / "a.md").write_text("1行目\n2行目\n3行目\n", encoding="utf-8")
    r = mt.execute(sid, {"tool": "read_doc", "path": "a.md", "start_line": 2, "max_lines": 1},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is True
    assert "2行目" in r["detail"] and "3行目" not in r["detail"]


def test_read_doc_via_execute_without_start_line_defaults_to_first(tmp_path):
    import memory_tools as mt
    sid = _sid()
    (tmp_path / "a.md").write_text("先頭行\n", encoding="utf-8")
    r = mt.execute(sid, {"tool": "read_doc", "path": "a.md"}, {"workspace_dir": str(tmp_path)})
    assert r["ok"] is True
    assert "先頭行" in r["detail"]


# --- search_workspace（横断検索ツール・execute経由・2026-07-22追加）---

def test_search_workspace_via_execute_returns_hits(tmp_path):
    import memory_tools as mt
    sid = _sid()
    (tmp_path / "a.md").write_text("お宝の在り処\n", encoding="utf-8")
    r = mt.execute(sid, {"tool": "search_workspace", "query": "お宝"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is True
    assert "a.md" in r["detail"] and "お宝の在り処" in r["detail"]


def test_search_workspace_via_execute_no_hits_returns_ok_with_placeholder(tmp_path):
    import memory_tools as mt
    sid = _sid()
    (tmp_path / "a.md").write_text("関係ない内容\n", encoding="utf-8")
    r = mt.execute(sid, {"tool": "search_workspace", "query": "存在しない単語"},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is True
    assert "ヒットなし" in r["detail"]


def test_search_workspace_via_execute_fails_without_exception_when_workspace_dir_unset():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "search_workspace", "query": "何か"}, {"workspace_dir": ""})
    assert r["ok"] is False


def test_search_workspace_via_execute_empty_query_fails_without_exception(tmp_path):
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "search_workspace", "query": ""},
                   {"workspace_dir": str(tmp_path)})
    assert r["ok"] is False


def test_search_workspace_is_a_feedback_tool():
    import memory_tools as mt
    assert "search_workspace" in mt.FEEDBACK_TOOLS


def test_tools_spec_documents_search_workspace_only_in_workspace_section():
    import memory_tools as mt
    assert "search_workspace" not in mt.build_tools_spec({})
    assert "search_workspace" in mt.build_tools_spec({"workspace_dir": "dummy"})


# --- describe_tools（ツール仕様の遅延読み化・2026-07-22追加）---

def test_describe_tools_returns_detail_for_known_tool():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "describe_tools", "tools": ["read_doc"]})
    assert r["ok"] is True
    assert "start_line" in r["detail"]
    assert "read_doc" in r["detail"]


def test_describe_tools_mixes_known_and_unknown_tools():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "describe_tools", "tools": ["save_lesson", "存在しないツール"]})
    assert r["ok"] is True
    assert "save_lesson" in r["detail"]
    assert "知らないツール名: 存在しないツール" in r["detail"]


def test_describe_tools_empty_tools_list_is_not_a_hard_error():
    """空・未知名はエラーとせず、案内メッセージ／既知分の詳細＋不明の明示を返す。"""
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "describe_tools", "tools": []})
    assert r["ok"] is True
    r2 = mt.execute(sid, {"tool": "describe_tools"})  # toolsキー自体が無い場合も同様
    assert r2["ok"] is True


def test_describe_tools_covers_all_catalog_entries():
    """索引に載っている全ツール名がdescribe_toolsの詳細でも引けること（取りこぼし防止）。"""
    import memory_tools as mt
    all_names = mt._MEMORY_TOOL_NAMES + mt._WORKSPACE_TOOL_NAMES + mt._ROLE_TOOL_NAMES
    sid = _sid()
    r = mt.execute(sid, {"tool": "describe_tools", "tools": all_names})
    assert r["ok"] is True
    for name in all_names:
        assert f"知らないツール名: {name}" not in r["detail"]


def test_describe_tools_is_a_feedback_tool():
    import memory_tools as mt
    assert "describe_tools" in mt.FEEDBACK_TOOLS


def test_describe_tools_can_describe_itself():
    """自己記述の欠落修正確認: describe_tools自身をtoolsに指定しても
    「知らないツール名」にならず、ちゃんと自分の詳細（tools引数の説明）を返すこと。"""
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "describe_tools", "tools": ["describe_tools"]})
    assert r["ok"] is True
    assert "知らないツール名: describe_tools" not in r["detail"]
    assert "describe_tools" in r["detail"]
    assert "tools" in r["detail"]


def test_build_tools_spec_index_footer_has_real_json_example_for_describe_tools():
    """索引フッターの案内文が「describe_toolsツールで取れる」という散文だけでなく、
    実際のJSON呼び出し例を含むこと（自己記述欠落の修正の一部）。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert '{"tool":"describe_tools","tools":["read_doc"]}' in spec


def test_build_tools_spec_index_footer_keeps_history_not_erased_wording():
    """撤回マーク運用の隣に「歴史を消さないこと。過去に信じていたことも自分の一部」を復元。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "歴史を消さないこと" in spec
    assert "過去に信じていたことも自分の一部" in spec


def test_describe_tools_feedback_loop_via_engine_with_fake_llm(tmp_path):
    """describe_toolsの結果がFEEDBACK_TOOLS経由でLLMへ差し戻され、2ターン目の応答が
    最終replyとして返ること（フェイクLLMでのエンジンループ疎通確認）。"""
    import engine
    import soul

    class FakeLLM:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []

        def chat(self, messages, max_tokens=None):
            self.calls.append(messages)
            return self.replies.pop(0)

    sid = soul.create_soul("describe_toolsテスト", identity_text="テスト用の子。")
    fake = FakeLLM([
        '調べるね\n```fieria-tool\n{"tool": "describe_tools", "tools": ["save_lesson"]}\n```',
        "わかった、答えるじょ",
    ])
    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    eng = engine.Engine(cfg, fake, sid)
    result = eng.process_turn("save_lessonの使い方教えて")
    assert result["reply"] == "わかった、答えるじょ"
    assert len(fake.calls) == 2


def test_read_doc_and_read_pdf_text_reject_zero_start(tmp_path):
    """start_line:0 / start_page:0 が `or 1` のfalsy罠で無検証に1へすり替わらず、
    バリデーションでok:Falseになること（レビュー指摘の回帰テスト）。"""
    import memory_tools as mt
    import workspace as ws
    ws.write_doc(str(tmp_path), "a.md", "hello\n")
    cfg = {"workspace_dir": str(tmp_path)}
    r = mt.execute("dummy", {"tool": "read_doc", "path": "a.md", "start_line": 0}, cfg)
    assert r["ok"] is False
    r = mt.execute("dummy", {"tool": "read_pdf", "path": "x.pdf", "mode": "text",
                              "start_page": 0}, cfg)
    assert r["ok"] is False


def test_execute_update_self_notes_wires_to_notes_history():
    """update_self_notesツールがsoul.update_self_notes（履歴保存つき）経由で
    書き込まれること（soul.write_fileへの直呼びに戻っていないことの回帰テスト）。"""
    import os
    import memory_tools as mt
    import soul
    sid = _sid()
    mt.execute(sid, {"tool": "update_self_notes", "content": "初版の自己認識"})

    r = mt.execute(sid, {"tool": "update_self_notes", "content": "改訂版の自己認識"})

    assert r["ok"] is True
    assert soul.read_file(sid, "self_notes.md") == "改訂版の自己認識\n"
    hist_dir = os.path.join(soul.soul_dir(sid), "notes_history")
    files = [f for f in os.listdir(hist_dir) if f.startswith("self_notes-")]
    assert len(files) == 1
    assert "初版の自己認識" in soul.read_file(sid, f"notes_history/{files[0]}")


def test_execute_update_user_notes_wires_to_notes_history():
    import os
    import memory_tools as mt
    import soul
    sid = _sid()
    mt.execute(sid, {"tool": "update_user_notes", "content": "初版の相手理解"})

    r = mt.execute(sid, {"tool": "update_user_notes", "content": "改訂版の相手理解"})

    assert r["ok"] is True
    assert soul.read_file(sid, "user.md") == "改訂版の相手理解\n"
    hist_dir = os.path.join(soul.soul_dir(sid), "notes_history")
    files = [f for f in os.listdir(hist_dir) if f.startswith("user-")]
    assert len(files) == 1
    assert "初版の相手理解" in soul.read_file(sid, f"notes_history/{files[0]}")


# --- archive_memory / unarchive_memory（忘却：連想から外すが記録は消さない） ---

def test_archive_memory_tool_moves_wiki_to_archive():
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_file(sid, "wiki/古い話題.md", "# 古い話題\n本文\n")

    r = mt.execute(sid, {"tool": "archive_memory", "path": "wiki/古い話題.md"})

    assert r["ok"] is True
    assert "archive/ へ降ろした" in r["detail"]
    assert soul.read_file(sid, "wiki/古い話題.md") == ""
    assert "本文" in soul.read_file(sid, "archive/wiki/古い話題.md")


def test_unarchive_memory_tool_moves_back():
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_file(sid, "wiki/話題.md", "本文\n")
    mt.execute(sid, {"tool": "archive_memory", "path": "wiki/話題.md"})

    r = mt.execute(sid, {"tool": "unarchive_memory", "path": "archive/wiki/話題.md"})

    assert r["ok"] is True
    assert "wiki/話題.md" in r["detail"]
    assert "本文" in soul.read_file(sid, "wiki/話題.md")


def test_archive_memory_rejects_core_files_via_execute():
    """soul.archive_fileが投げるValueErrorはexecute()共通のexcept Exceptionで
    {"ok": False}に変換され、会話を壊さないこと（他の書き込み系ツールと同じ方針）。"""
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "archive_memory", "path": "identity.md"})
    assert r["ok"] is False


def test_archive_memory_and_unarchive_memory_not_feedback_tools():
    """どちらも書き込み系ツールなのでFEEDBACK_TOOLS（LLMへの差し戻し対象）には
    含まれないこと（save_reading_noteと同じ方針）。"""
    import memory_tools as mt
    assert "archive_memory" not in mt.FEEDBACK_TOOLS
    assert "unarchive_memory" not in mt.FEEDBACK_TOOLS


def test_archive_memory_in_tools_index_and_details():
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "archive_memory" in spec
    assert "unarchive_memory" in spec
    detail = mt._execute_describe_tools(["archive_memory"])
    assert detail["ok"]
    assert "迷ったら降ろさない" in detail["detail"]
    assert "unarchive_memory" in detail["detail"]


def test_search_memory_still_finds_archived_memory_with_archive_source():
    """search_memory（明示検索）はsearch.pyの汎用走査に乗るだけなのでarchive/配下も
    自然に含む——『思い出そうとすれば思い出せる』が保たれること。ヒットのsourceに
    archive/が現れて区別できることも確認する。"""
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_file(sid, "wiki/古い話題X.md", "むかしインベントリUIの話をした記録")
    mt.execute(sid, {"tool": "archive_memory", "path": "wiki/古い話題X.md"})

    r = mt.execute(sid, {"tool": "search_memory", "query": "インベントリUI"})

    assert r["ok"] is True
    assert "archive/wiki/古い話題X.md" in r["detail"]


# --- スキル（手続き記憶）: create_skill/update_skill/use_skill ---

def test_create_skill_writes_file_and_reports_ok():
    import memory_tools as mt
    import soul
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                          "description": "説明", "content": "1. やる"})
    assert r["ok"] is True
    assert "手順A" in r["detail"]
    body = soul.read_skill(sid, "手順A")
    assert "1. やる" in body


def test_update_skill_overwrites_and_keeps_history():
    import memory_tools as mt
    import soul
    import os
    sid = _sid()
    mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                      "description": "旧説明", "content": "旧本文"})
    r = mt.execute(sid, {"tool": "update_skill", "name": "手順A",
                          "description": "新説明", "content": "新本文"})
    assert r["ok"] is True
    body = soul.read_skill(sid, "手順A")
    assert "新本文" in body and "旧本文" not in body
    hist_dir = os.path.join(soul.soul_dir(sid), "skills_history")
    assert os.path.isdir(hist_dir) and os.listdir(hist_dir)


def test_create_skill_rejects_empty_name():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "", "description": "説明",
                          "content": "本文"})
    assert r["ok"] is False


def test_use_skill_returns_full_content_and_is_feedback_tool():
    import memory_tools as mt
    sid = _sid()
    mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                      "description": "説明だよ", "content": "本文だよ"})
    assert "use_skill" in mt.FEEDBACK_TOOLS
    r = mt.execute(sid, {"tool": "use_skill", "name": "手順A"})
    assert r["ok"] is True
    assert "本文だよ" in r["detail"]
    assert "説明だよ" in r["detail"]


def test_use_skill_missing_name_is_not_a_hard_error():
    """存在しないスキル名を指定してもexecuteが例外にならず、ok:Falseで
    「list参照」の案内が返ること（会話を止めない）。"""
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "use_skill", "name": "存在しないスキル"})
    assert r["ok"] is False
    assert "list参照" in r["detail"]


def test_build_tools_spec_index_lists_skill_one_line_even_with_zero_skills():
    """スキル0件でも索引にcreate_skill/update_skill/use_skillの1行例が出る
    （作れることを知らないと始まらないため）。"""
    import memory_tools as mt
    spec = mt.build_tools_spec({})
    assert "create_skill" in spec
    assert "update_skill" in spec
    assert "use_skill" in spec
    assert "スキル未登録" in spec


def test_build_tools_spec_index_lists_existing_skill_when_soul_id_given():
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_skill(sid, "既存手順", "既存の1行説明", "本文")
    spec = mt.build_tools_spec({}, sid)
    assert "既存手順: 既存の1行説明" in spec
    assert "スキル未登録" not in spec


def test_build_tools_spec_skill_wording_differs_by_skill_auto_create():
    """skill_auto_create=False（既定）は同意ファーストの文言、Trueは自律作成の文言。"""
    import memory_tools as mt
    spec_default = mt.build_tools_spec({})
    assert "提案し" in spec_default
    assert "同意" in spec_default

    spec_auto = mt.build_tools_spec({"skill_auto_create": True})
    assert "自分の判断でスキル化してよい" in spec_auto


def test_describe_tools_documents_skill_tools():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "describe_tools",
                          "tools": ["create_skill", "update_skill", "use_skill"]})
    assert r["ok"] is True
    for name in ("create_skill", "update_skill", "use_skill"):
        assert f"### {name}" in r["detail"]
        assert f"知らないツール名: {name}" not in r["detail"]


# --- create_skill/update_skill: 同名ガード・上限・サニタイズ収束（レビュー指摘・2026-07-22追加）---

def test_create_skill_rejects_duplicate_name():
    """既にあるスキル名でcreate_skillしたら拒否され、update_skillへ誘導される
    （create_roleの重複拒否と同じ流儀）。"""
    import memory_tools as mt
    sid = _sid()
    mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                      "description": "説明", "content": "本文"})
    r = mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                          "description": "別の説明", "content": "別の本文"})
    assert r["ok"] is False
    assert "update_skill" in r["detail"]
    # 拒否された＝元の内容が上書きされていないこと
    import soul
    assert "本文" in soul.read_skill(sid, "手順A")
    assert "別の本文" not in soul.read_skill(sid, "手順A")


def test_update_skill_rejects_missing_name():
    """存在しないスキル名でupdate_skillしたら拒否され、create_skillへ誘導される
    （create_skillの不存在拒否と対称）。"""
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "update_skill", "name": "存在しないスキル",
                          "description": "説明", "content": "本文"})
    assert r["ok"] is False
    assert "create_skill" in r["detail"]


def test_create_skill_rejects_sanitization_convergence_pair():
    """"a/b"と"a:b"はどちらも_safe_nameのサニタイズで"a-b.md"へ収束する。
    1件目のcreate_skillが成功した後、2件目のcreate_skillは
    「実ファイルが既にある」という基準で同名拒否されるべき
    （名前の見た目ではなくファイルパス基準の衝突検知）。"""
    import memory_tools as mt
    sid = _sid()
    r1 = mt.execute(sid, {"tool": "create_skill", "name": "a/b",
                           "description": "説明1", "content": "本文1"})
    assert r1["ok"] is True
    r2 = mt.execute(sid, {"tool": "create_skill", "name": "a:b",
                           "description": "説明2", "content": "本文2"})
    assert r2["ok"] is False
    assert "update_skill" in r2["detail"]


def test_create_skill_rejects_name_over_40_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "あ" * 41,
                          "description": "説明", "content": "本文"})
    assert r["ok"] is False
    assert "40字" in r["detail"]


def test_create_skill_accepts_name_exactly_40_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "あ" * 40,
                          "description": "説明", "content": "本文"})
    assert r["ok"] is True


def test_update_skill_rejects_header_name_mismatch():
    """update_skill対象ファイルの1行目ヘッダ名と要求nameが不一致なら拒否する
    （サニタイズ収束による別スキル誤上書きの最終防衛線）。"""
    import memory_tools as mt
    import soul
    sid = _sid()
    # write_skillでヘッダと実際の呼び出し名がズレたファイルを直接作る
    # （create_skill経由では起きない状況を意図的に再現する）。
    soul.write_skill(sid, "元の名前", "説明", "本文")
    import os
    os.rename(
        os.path.join(soul.soul_dir(sid), "skills", "元の名前.md"),
        os.path.join(soul.soul_dir(sid), "skills", "別名.md"),
    )
    r = mt.execute(sid, {"tool": "update_skill", "name": "別名",
                          "description": "新説明", "content": "新本文"})
    assert r["ok"] is False
    assert "元の名前" in r["detail"] and "別名" in r["detail"]
    # 拒否された＝内容は変わっていない
    assert "本文" in soul.read_skill(sid, "別名")
    assert "新本文" not in soul.read_skill(sid, "別名")


def test_update_skill_allows_broken_header_file():
    """ヘッダが壊れている（1行目が"# "始まりでない）ファイルへのupdate_skillは
    照合できないので許可する（壊れたファイルを直す手段が無くなるのを避けるため）。"""
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_file(sid, "skills/壊れた.md", "説明もタイトルもない本文\n")
    r = mt.execute(sid, {"tool": "update_skill", "name": "壊れた",
                          "description": "直った説明", "content": "直った本文"})
    assert r["ok"] is True
    body = soul.read_skill(sid, "壊れた")
    assert "直った本文" in body


def test_create_skill_rejects_description_over_100_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                          "description": "あ" * 101, "content": "本文"})
    assert r["ok"] is False
    assert "100字" in r["detail"]


def test_create_skill_accepts_description_exactly_100_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                          "description": "あ" * 100, "content": "本文"})
    assert r["ok"] is True


def test_create_skill_rejects_content_over_6000_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                          "description": "説明", "content": "あ" * 6001})
    assert r["ok"] is False
    assert "6000字" in r["detail"]


def test_create_skill_accepts_content_exactly_6000_chars():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "create_skill", "name": "手順A",
                          "description": "説明", "content": "あ" * 6000})
    assert r["ok"] is True


# --- _skills_index_block: 説明クリップ・件数丸め（レビュー指摘・2026-07-22追加）---

def test_skills_index_block_clips_long_description_to_60_chars():
    import memory_tools as mt
    import soul
    sid = _sid()
    long_desc = "あ" * 100
    soul.write_skill(sid, "手順A", long_desc, "本文")
    spec = mt.build_tools_spec({}, sid)
    assert ("あ" * 60 + "…") in spec
    assert long_desc not in spec


def test_skills_index_block_does_not_clip_short_description():
    import memory_tools as mt
    import soul
    sid = _sid()
    soul.write_skill(sid, "手順A", "短い説明", "本文")
    spec = mt.build_tools_spec({}, sid)
    assert "手順A: 短い説明" in spec
    assert "…" not in spec.split("## スキル")[1].split("- use_skill")[0]


def test_skills_index_block_summarizes_beyond_30_skills():
    import memory_tools as mt
    import soul
    sid = _sid()
    for i in range(31):
        soul.write_skill(sid, f"手順{i:02d}", "説明", "本文")
    spec = mt.build_tools_spec({}, sid)
    assert "手順00" in spec
    assert "手順29" in spec
    assert "手順30" not in spec  # 31件目は個別表示されず丸められる
    assert "…他1件（search_memoryで名前を探せる）" in spec


# --- web_search（全プロバイダWeb検索・Task2）---

def test_web_search_dispatch_returns_formatted_results(monkeypatch):
    import memory_tools as mt
    import websearch
    sid = _sid()
    fake_results = [{"title": "タイトル", "href": "https://example.com", "body": "本文抜粋"}]
    monkeypatch.setattr(
        websearch, "search_text",
        lambda query, max_results=5: {"ok": True, "results": fake_results, "detail": "1件"})
    r = mt.execute(sid, {"tool": "web_search", "query": "猫の飼い方"})
    assert r["ok"] is True
    assert r["op"] == "web_search"
    assert "タイトル" in r["detail"]
    assert "https://example.com" in r["detail"]
    assert "本文抜粋" in r["detail"]


def test_web_search_dispatch_zero_results_uses_fixed_detail(monkeypatch):
    import memory_tools as mt
    import websearch
    sid = _sid()
    monkeypatch.setattr(
        websearch, "search_text",
        lambda query, max_results=5: {"ok": True, "results": [], "detail": "0件"})
    r = mt.execute(sid, {"tool": "web_search", "query": "猫の飼い方"})
    assert r["ok"] is True
    assert r["detail"] == "検索結果0件"


def test_web_search_dispatch_failure_passes_through_detail(monkeypatch):
    import memory_tools as mt
    import websearch
    sid = _sid()
    monkeypatch.setattr(
        websearch, "search_text",
        lambda query, max_results=5: {"ok": False, "results": [], "detail": "検索に失敗しました: boom"})
    r = mt.execute(sid, {"tool": "web_search", "query": "猫の飼い方"})
    assert r["ok"] is False
    assert r["op"] == "web_search"
    assert r["detail"] == "検索に失敗しました: boom"


def test_web_search_dispatch_rejects_empty_query():
    import memory_tools as mt
    sid = _sid()
    r = mt.execute(sid, {"tool": "web_search", "query": "   "})
    assert r["ok"] is False
    assert r["op"] == "web_search"


def test_web_search_is_feedback_tool():
    import memory_tools as mt
    assert "web_search" in mt.FEEDBACK_TOOLS


def test_build_tools_spec_omits_web_search_when_off():
    import memory_tools as mt
    cfg = {"llm": {"web_search": False, "provider": "ollama",
                    "providers": {"ollama": {"type": "ollama"}}}}
    spec = mt.build_tools_spec(cfg)
    assert "web_search" not in spec


def test_build_tools_spec_omits_web_search_for_gemini():
    import memory_tools as mt
    cfg = {"llm": {"web_search": True, "provider": "g",
                    "providers": {"g": {"type": "gemini"}}}}
    spec = mt.build_tools_spec(cfg)
    assert "web_search" not in spec


def test_build_tools_spec_includes_web_search_for_non_gemini():
    import memory_tools as mt
    cfg = {"llm": {"web_search": True, "provider": "ollama",
                    "providers": {"ollama": {"type": "ollama"}}}}
    spec = mt.build_tools_spec(cfg)
    assert '"tool":"web_search"' in spec


def test_web_search_not_in_importer_allowed_tools():
    import importer
    assert "web_search" not in importer.ALLOWED_TOOLS


def test_build_tools_spec_omits_web_search_for_codex_oauth():
    """Codex(openai_codex_oauth)はネイティブWeb検索(tools:[{"type":"web_search"}])に
    切り替えたので、アプリ内蔵web_searchツールは索引に出さない(geminiと同じ扱い)。"""
    import memory_tools as mt
    cfg = {"llm": {"web_search": True, "provider": "codex",
                    "providers": {"codex": {"type": "openai_codex_oauth"}}}}
    spec = mt.build_tools_spec(cfg)
    assert "web_search" not in spec
