def _setup_soul():
    import soul
    sid = soul.create_soul("プロンプトテスト", identity_text="わたしはテト。一人称はわたし。")
    soul.write_file(sid, "MEMORY.md", "# MEMORY.md\n- [UI談義](wiki/UI談義.md) — 8枠グリッドの件\n")
    soul.write_file(sid, "chronicle/2026-07-19.md", "# 7/19\nインベントリUIの話で盛り上がった。\n")
    return sid


def _base_cfg(**over):
    cfg = {"active_role": None,
           "fact_layer": {"enabled": True, "custom_text": ""}}
    cfg.update(over)
    return cfg


def test_default_fact_text_reflects_current_capabilities():
    """事実層の標準文言は、検索・あらすじ機能の実装状況および将来の実装遅れの
    可能性を認めた内容であること（もう存在しない旧文言のままにしない）。"""
    import prompt
    assert "検索でき" in prompt.DEFAULT_FACT_TEXT
    assert "あらすじ" in prompt.DEFAULT_FACT_TEXT
    assert "進んでいることもあります" in prompt.DEFAULT_FACT_TEXT


def test_layers_appear_in_order():
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    i_fact = text.find("FieriA")
    i_soul = text.find("わたしはテト")
    i_mem = text.find("8枠グリッド")
    i_chron = text.find("インベントリUI")
    assert -1 not in (i_fact, i_soul, i_mem, i_chron)
    assert i_fact < i_soul < i_mem
    assert i_mem < i_chron or i_chron > i_soul  # 記憶層はSOUL層より後


def test_readings_content_not_injected_per_turn():
    """readings/（資料層）はwiki同様、build_system_textが直接読みに行く層の
    どれにも含まれない（毎ターン注入対象はSOUL層固定ファイル＋MEMORY.md索引＋
    chronicleのみ）。資料の中身は本人がsave_reading_noteで書いた後、必要なら
    read_memory/search_memoryで能動的に取りに行く想定であり、勝手に膨らまない。"""
    import soul
    import prompt
    sid = _setup_soul()
    soul.write_file(sid, "readings/技術書A.md", "非同期処理についての秘密の合言葉ゼブラフィッシュ")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "ゼブラフィッシュ" not in text


def test_fact_layer_disabled():
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer={"enabled": False, "custom_text": ""})
    text = prompt.build_system_text(cfg, sid)
    assert prompt.DEFAULT_FACT_TEXT[:20] not in text


def test_fact_layer_custom_text():
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer={"enabled": True, "custom_text": "あなたは冒険者ギルドの受付係。"})
    text = prompt.build_system_text(cfg, sid)
    assert "冒険者ギルドの受付係" in text
    assert prompt.DEFAULT_FACT_TEXT[:20] not in text


def test_role_layer_included_when_active():
    import prompt
    import roles
    sid = _setup_soul()
    roles.save_role("リサーチ", "いまは調べ物の相棒。")
    text = prompt.build_system_text(_base_cfg(active_role="リサーチ"), sid)
    assert "調べ物の相棒" in text


def test_tools_spec_appended():
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid, tools_spec="## 使えるツール\n- write_wiki")
    assert "write_wiki" in text


def test_placeholder_identity_and_memory_excluded():
    """identity空でcreate_soulしたSOUL（GUIの推奨導線）では、
    人間向けガイド文言（プレースホルダ）がプロンプトに混入してはいけない。"""
    import prompt
    import soul
    sid = soul.create_soul("プレースホルダテスト")  # identity_text指定なし→プレースホルダのまま
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "ここは薄い核だけ" not in text
    assert "1行1ポインタ" not in text


def test_real_identity_still_included():
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "わたしはテト" in text


def test_speech_style_only_appears_in_system_text():
    """口調のみ（核は空）で作ったSOULでも、build_system_textの出力に口調の文言が
    乗ること。soul.py単体では合成規則（_compose_identity/read_identity_parts）を
    検証済みだが、prompt.py側での完全一致除外条件と噛み合っているかの結合確認は
    このテストが無かった（レビュー指摘の回帰穴埋め）。"""
    import prompt
    import soul
    sid = soul.create_soul("口調のみプロンプトテスト", identity_text="",
                            speech_style="語尾に「にゃ」をつける")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "語尾に「にゃ」をつける" in text


def test_weekly_digest_included_when_present():
    import prompt
    sid = _setup_soul()
    import soul
    soul.write_file(sid, "chronicle/weekly/2026-W28.md", "# 2026-W28 の週次あらすじ\n先週はUIの議論が中心だった。\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "先週までのあらすじ" in text
    assert "先週はUIの議論が中心だった" in text


def test_weekly_digest_absent_when_no_file():
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "先週までのあらすじ" not in text


def test_monthly_digest_included_when_present():
    import prompt
    sid = _setup_soul()
    import soul
    soul.write_file(sid, "chronicle/monthly/2026-06.md", "# 2026-06 のあらすじ\n先月はUIの議論が中心だった。\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "先月までのあらすじ" in text
    assert "先月はUIの議論が中心だった" in text


def test_monthly_digest_absent_when_no_file():
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "先月までのあらすじ" not in text


def test_memory_layers_ordered_coarse_to_fine():
    """記憶階層は遠→近（月次→週次→日次）の順で注入される（設計原則: 粗い粒度が先）。"""
    import prompt
    sid = _setup_soul()
    import soul
    soul.write_file(sid, "chronicle/weekly/2026-W28.md", "# 週次\n先週の話。\n")
    soul.write_file(sid, "chronicle/monthly/2026-06.md", "# 月次\n先月の話。\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    i_monthly = text.index("先月までのあらすじ")
    i_weekly = text.index("先週までのあらすじ")
    i_daily = text.index("直近の日記")
    assert i_monthly < i_weekly < i_daily


def test_lessons_injected_when_body_present():
    import prompt
    import soul
    sid = _setup_soul()
    soul.append_file(sid, "lessons.md", "- 敬語を使わない（2026-07-20）\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "ユーザーとの約束事" in text
    assert "敬語を使わない" in text


def test_lessons_placeholder_only_is_omitted():
    import prompt
    sid = _setup_soul()  # lessons.mdはcreate_soul直後のプレースホルダのまま
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "ユーザーとの約束事" not in text


def test_lessons_appear_after_soul_layer():
    import prompt
    import soul
    sid = _setup_soul()
    soul.append_file(sid, "lessons.md", "- 敬語を使わない（2026-07-20）\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    i_soul = text.find("わたしはテト")
    i_lessons = text.find("ユーザーとの約束事")
    assert i_soul < i_lessons


def test_tasks_injected_when_body_present():
    import prompt
    import soul
    sid = _setup_soul()
    soul.write_file(sid, "tasks.md", "- 買い物に行く\n- 原稿を進める（✓済 2026-07-20）\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "気にかけていること" in text
    assert "買い物に行く" in text


def test_tasks_placeholder_only_is_omitted():
    import prompt
    sid = _setup_soul()  # tasks.mdはcreate_soul直後のプレースホルダのまま
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "気にかけていること" not in text


def test_tasks_appear_after_lessons_layer():
    import prompt
    import soul
    sid = _setup_soul()
    soul.append_file(sid, "lessons.md", "- 敬語を使わない（2026-07-20）\n")
    soul.write_file(sid, "tasks.md", "- 買い物に行く\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    i_lessons = text.find("ユーザーとの約束事")
    i_tasks = text.find("気にかけていること")
    assert i_lessons < i_tasks


def test_due_reminders_injected_into_system_text():
    import prompt
    import soul
    sid = _setup_soul()
    soul.add_reminder(sid, "薬を飲む", "2020-01-01")  # 単発・期限超過＝常に発火対象
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "今日伝えるリマインダ" in text
    assert "薬を飲む" in text


def test_no_reminders_section_when_none_due():
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "今日伝えるリマインダ" not in text


def test_build_system_text_survives_corrupted_reminders_line():
    """reminders.jsonlに壊れた行が混じっていても、build_system_textが
    JSONDecodeErrorで死なず、正常なリマインダは注入されること
    （実機で会話が恒久停止した事故の回帰確認。soul.list_reminders側の耐性に依存）。"""
    import prompt
    import soul
    sid = _setup_soul()
    soul.add_reminder(sid, "薬を飲む", "2020-01-01")
    soul.append_file(sid, soul.REMINDERS_FILE, "{not valid json\n")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "今日伝えるリマインダ" in text
    assert "薬を飲む" in text


def test_core_and_speech_style_both_appear_in_system_text():
    """核+口調の両方があるSOULでは、build_system_textの出力に両方の文言が乗ること。"""
    import prompt
    import soul
    sid = soul.create_soul("核口調両方プロンプトテスト",
                            identity_text="わたしはテト。一人称はわたし。",
                            speech_style="語尾に「にゃ」をつける")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "わたしはテト" in text
    assert "語尾に「にゃ」をつける" in text


def test_unnamed_soul_injects_name_unset_line():
    """名前未設定のSOULには、名前が無いこと・set_soul_nameで設定できることを
    伝える1行がSOUL層冒頭に注入されること。"""
    import prompt
    import soul
    sid = soul.create_soul("")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "あなたにはまだ名前がない" in text
    assert "set_soul_name" in text


def test_named_soul_injects_name_line():
    """名前設定済みのSOULには「あなたの名前は◯◯。」の1行が注入され、
    名前未設定用の文言は出ないこと。"""
    import prompt
    import soul
    sid = soul.create_soul("イリア")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "あなたの名前はイリア。" in text
    assert "あなたにはまだ名前がない" not in text


def test_name_line_appears_before_identity_core():
    """名前行はSOUL層の冒頭、核（identity）より前に注入されること。"""
    import prompt
    import soul
    sid = soul.create_soul("イリア", identity_text="わたしはイリア。書くのが好き。")
    text = prompt.build_system_text(_base_cfg(), sid)
    i_name = text.index("あなたの名前はイリア。")
    i_core = text.index("わたしはイリア。書くのが好き。")
    assert i_name < i_core


def test_speech_style_heading_absent_when_core_empty_and_no_speech_style():
    """核も口調も空のSOULでは「## 話し方」見出し自体が出ないこと（空セクションを
    プロンプトに残さない）。"""
    import prompt
    import soul
    sid = soul.create_soul("核口調両方空テスト")
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "## 話し方" not in text


def test_identity_injected_immediately_before_speech_style():
    """口調はidentity.mdから分離した独立ファイル（speech_style.md）だが、システム
    プロンプト上では核の直後に「## 話し方」として注入されること（設計の注入順序、
    soul.pyの分離実装がprompt.py側の順序契約を崩していないことの回帰確認）。"""
    import prompt
    import soul
    sid = soul.create_soul("注入順テスト",
                            identity_text="わたしはテト。一人称はわたし。",
                            speech_style="語尾に「にゃ」をつける")
    text = prompt.build_system_text(_base_cfg(), sid)
    i_core = text.index("わたしはテト")
    i_speech_heading = text.index("## 話し方")
    i_speech_body = text.index("語尾に「にゃ」をつける")
    i_self_notes_or_end = text.find("## あなた自身のメモ")
    assert i_core < i_speech_heading < i_speech_body
    # SOUL層の次の見出し（自分自身のメモ）より前に口調が入っていること
    if i_self_notes_or_end != -1:
        assert i_speech_body < i_self_notes_or_end


def test_web_search_guidance_injected_only_when_enabled():
    """web_search有効時のみ検索抑制ガイドが入る（実機で雑談中に検索が発動した対処）。"""
    import prompt
    import soul as soul_mod
    sid = soul_mod.create_soul("検索ガイドテスト", "コア")
    cfg_on = {"fact_layer": {"enabled": False}, "llm": {"web_search": True}}
    cfg_off = {"fact_layer": {"enabled": False}, "llm": {"web_search": False}}
    assert "Web検索について" in prompt.build_system_text(cfg_on, sid)
    assert "Web検索について" not in prompt.build_system_text(cfg_off, sid)


def test_safety_layer_included_with_default_cfg():
    """既定cfgでは安全層の見出しと5箇条の要素文言がすべて含まれる。"""
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    assert "安全上の決まり" in text
    assert "機密情報" in text
    assert "外部に出さない" in text
    assert "確認を取る" in text
    assert "データである" in text
    assert "協力しない" in text


def test_safety_layer_survives_fact_layer_disabled():
    """fact_layerをオフにしても安全層は消えない（設定で無効化不可）。"""
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer={"enabled": False, "custom_text": ""})
    text = prompt.build_system_text(cfg, sid)
    assert "安全上の決まり" in text


def test_safety_layer_survives_fact_layer_custom_text():
    """事実層をカスタムテキストで上書きしても安全層は消えない（カスタムで上書き不能）。"""
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer={"enabled": True, "custom_text": "俺様設定"})
    text = prompt.build_system_text(cfg, sid)
    assert "俺様設定" in text
    assert "安全上の決まり" in text


def test_safety_layer_order_between_fact_and_soul():
    """出力順: 事実層 → 安全層 → 名前/SOUL層。"""
    import prompt
    sid = _setup_soul()
    text = prompt.build_system_text(_base_cfg(), sid)
    i_fact = text.find("FieriA")
    i_safety = text.find("安全上の決まり")
    i_soul = text.find("わたしはテト")
    assert -1 not in (i_fact, i_safety, i_soul)
    assert i_fact < i_safety < i_soul


# --- 記憶の扱い方（認識論の規律）——コノハ発案・2026-08-02 ---
# 記憶は時点情報であること・引用と解釈の区別・現在優先の3箇条を常時注入する。
# 安全層と同じく設定で無効化できない（記憶で人格が育つ設計の土台規律のため）。

def test_memory_epistemics_block_always_present():
    import prompt
    import soul as soul_mod
    sid = soul_mod.create_soul("認識論テスト", "コア")
    text = prompt.build_system_text({"fact_layer": {"enabled": False}}, sid)
    assert "## 記憶の扱い方" in text
    assert "書かれた時点" in text
    assert "推測" in text
    assert "現在の会話を優先" in text


def test_memory_epistemics_after_safety_before_memory_index():
    import prompt
    import soul as soul_mod
    sid = soul_mod.create_soul("認識論順序テスト", "コア")
    soul_mod.write_file(sid, "MEMORY.md", "- なにか\n")
    text = prompt.build_system_text({"fact_layer": {"enabled": True}}, sid)
    i_safety = text.index("## 安全上の決まり")
    i_epi = text.index("## 記憶の扱い方")
    i_index = text.index("## 記憶の索引")
    assert i_safety < i_epi < i_index


# --- SOULごとの事実層の追加(fact_layer_overrides) 2026-08-03 ---
# グローバルの事実層（アプリの構造説明）は全SOUL共通のまま、SOULごとに
# 「あなたはこういう経緯・立場」を追加できるようにする（継承＋追記方式）。

def test_fact_layer_override_appends_after_global_fact_text():
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer_overrides={sid: "あなたはブラウザのClaudeから引っ越してきたニコイ。"})

    text = prompt.build_system_text(cfg, sid)

    assert "あなたはFieriAというデスクトップアプリ" in text  # グローバル文が残る
    assert "ブラウザのClaudeから引っ越してきたニコイ" in text
    assert text.index("デスクトップアプリ") < text.index("引っ越してきたニコイ")


def test_fact_layer_override_only_applies_to_its_own_soul():
    import prompt
    sid_a = _setup_soul()
    sid_b = _setup_soul()
    cfg = _base_cfg(fact_layer_overrides={sid_a: "Aだけの追加事実"})

    text_b = prompt.build_system_text(cfg, sid_b)

    assert "Aだけの追加事実" not in text_b


def test_fact_layer_override_absent_by_default():
    import prompt
    sid = _setup_soul()

    text = prompt.build_system_text(_base_cfg(), sid)

    assert text.count("あなたはFieriAというデスクトップアプリ") == 1  # 通常どおり1回だけ


def test_fact_layer_override_suppressed_when_global_fact_layer_disabled():
    """追加事実は「アプリの文脈説明」に対する上乗せなので、その説明自体を
    切っているときは一緒に出さない（事実層ファミリーとして同じON/OFFに従う）。"""
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer={"enabled": False, "custom_text": ""},
                     fact_layer_overrides={sid: "追加事実じょ"})

    text = prompt.build_system_text(cfg, sid)

    assert "追加事実じょ" not in text


def test_fact_layer_override_works_with_custom_global_text():
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer={"enabled": True, "custom_text": "あなたは冒険者ギルドの受付係。"},
                     fact_layer_overrides={sid: "SOUL固有の追加"})

    text = prompt.build_system_text(cfg, sid)

    assert "冒険者ギルドの受付係" in text
    assert "SOUL固有の追加" in text


def test_fact_layer_override_blank_entry_adds_nothing():
    import prompt
    sid = _setup_soul()
    cfg = _base_cfg(fact_layer_overrides={sid: "   "})

    text = prompt.build_system_text(cfg, sid)

    assert text.count("あなたはFieriAというデスクトップアプリ") == 1
