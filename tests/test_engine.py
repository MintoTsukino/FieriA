import os

import pytest


class FakeLLM:
    """chat()が予め積んだ応答を順に返すフェイク。実物のシグネチャ chat(messages, max_tokens=None) に合わせる。
    system_textはmessages[0]（role: system）として渡される。"""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []  # [messages, ...]

    def chat(self, messages, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0)


def _make(replies):
    import engine, soul
    sid = soul.create_soul("エンジンテスト", identity_text="テスト用の子。")
    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    fake = FakeLLM(replies)
    return engine.Engine(cfg, fake, sid), fake, sid


def test_simple_turn_returns_reply_and_logs():
    import soul
    eng, fake, sid = _make(["にゃっほー、元気じょ"])
    out = eng.process_turn("元気？")
    assert out["reply"] == "にゃっほー、元気じょ"
    assert out["operations"] == []
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["user", "ai"]


def test_write_tool_executed_and_reported():
    import soul
    eng, fake, sid = _make([
        '覚えとくじょ\n```fieria-tool\n'
        '{"tool": "write_wiki", "topic": "約束", "content": "8時に散歩", "mode": "append"}\n'
        '```'])
    out = eng.process_turn("明日8時に散歩って覚えて")
    assert out["reply"] == "覚えとくじょ"
    assert out["operations"][0]["ok"] and out["operations"][0]["op"] == "write_wiki"
    assert "8時に散歩" in soul.read_file(sid, "wiki/約束.md")


def test_read_tool_feeds_back_and_rereplies():
    eng, fake, sid = _make([
        '確認するじょ\n```fieria-tool\n{"tool": "read_memory", "path": "wiki/約束.md"}\n```',
        "昨日の約束は散歩じょね",
    ])
    import soul
    soul.write_file(sid, "wiki/約束.md", "8時に散歩\n")
    out = eng.process_turn("昨日の約束なんだっけ")
    assert out["reply"] == "昨日の約束は散歩じょね"
    # 2回目のchat呼び出しにread結果が渡っている
    second_messages = fake.calls[1]
    assert any("8時に散歩" in m["content"] for m in second_messages)


def test_workspace_read_doc_feeds_back_and_rereplies(tmp_path):
    """read_docもread_memory/search_memoryと同じ差し戻し（FEEDBACK_TOOLS）に
    乗ること。workspace_dirはEngineのcfg経由でmemory_tools.executeへ渡る。"""
    import workspace
    eng, fake, sid = _make([
        '記事読むじょ\n```fieria-tool\n{"tool": "read_doc", "path": "article.md"}\n```',
        "記事の中身は下書きだったじょ",
    ])
    eng.cfg["workspace_dir"] = str(tmp_path)
    workspace.write_doc(str(tmp_path), "article.md", "まだ下書き")

    out = eng.process_turn("article.mdの中身確認して")

    assert out["reply"] == "記事の中身は下書きだったじょ"
    second_messages = fake.calls[1]
    assert any("まだ下書き" in m["content"] for m in second_messages)


def test_workspace_tool_fails_gracefully_when_workspace_dir_unset():
    """workspace_dir未設定（cfgにキーが無い/空文字）でworkspaceツールを呼んでも
    例外にならず、ok:Falseで会話が続くこと（失敗はFEEDBACK_TOOLS差し戻し対象外
    ＝result["ok"]がFalseなので2回目のchatは呼ばれない）。"""
    eng, fake, sid = _make([
        '読むじょ\n```fieria-tool\n{"tool": "read_doc", "path": "article.md"}\n```',
    ])
    out = eng.process_turn("article.md読んで")
    assert out["operations"][0]["ok"] is False
    assert out["reply"] == "読むじょ"
    assert len(fake.calls) == 1


def test_tool_failure_does_not_break_conversation():
    eng, fake, sid = _make([
        'やっとくじょ\n```fieria-tool\n{"tool": "explode"}\n```'])
    out = eng.process_turn("なんかして")
    assert out["reply"] == "やっとくじょ"
    assert out["operations"][0]["ok"] is False


def test_broken_tool_json_reports_parse_error_then_retries_and_succeeds():
    """実機FB第3弾: 1ラウンド目でツールブロックのJSONが壊れていた場合、静かに無視せず
    operationsにtool_parse(ok:False)を残しつつLLMへ差し戻して書き直しを促す。
    2ラウンド目に正しいJSONで書き直されれば、そのツールも実行されて両方operationsに残る。"""
    import soul
    eng, fake, sid = _make([
        '覚えとくじょ\n```fieria-tool\n{"tool": "write_wiki", "topic": "約束"壊れ\n```',
        '書き直したじょ\n```fieria-tool\n'
        '{"tool": "write_wiki", "topic": "約束", "content": "8時に散歩", "mode": "append"}\n```',
    ])
    out = eng.process_turn("明日8時に散歩って覚えて")
    assert len(fake.calls) == 2  # 差し戻しで2ラウンド目が呼ばれた
    ops_by_op = {op["op"]: op for op in out["operations"]}
    assert ops_by_op["tool_parse"]["ok"] is False
    assert ops_by_op["write_wiki"]["ok"] is True
    assert "8時に散歩" in soul.read_file(sid, "wiki/約束.md")
    # 差し戻しメッセージに壊れた断片への言及と書き直し指示が乗っていること
    second_messages = fake.calls[1]
    assert any("書き直す" in m.get("content", "") for m in second_messages)


def test_switch_role_takes_effect_from_next_turn_system_text():
    """AIがswitch_roleツールで自分から切り替えた場合、切替を行った当ターンの
    応答には影響せず（切替はFEEDBACK_TOOLS対象外＝差し戻し無し＝1回のchatで終わる）、
    次ターンのbuild_system_text（=次chat呼び出しのsystemメッセージ）に
    新ロールのprompt本文が乗ること。cfgはengine.cfgと同一参照なので、
    executeでの更新がそのまま次のbuild_system_textへ反映される。"""
    eng, fake, sid = _make([
        'リサーチモードにするじょ\n```fieria-tool\n'
        '{"tool": "switch_role", "name": "リサーチ"}\n```',
        "了解、調べ物モードで答えるじょ",
    ])
    out1 = eng.process_turn("次は調べ物っぽい話するね")
    assert out1["reply"] == "リサーチモードにするじょ"
    assert out1["operations"][0]["ok"] is True
    assert len(fake.calls) == 1  # 書き込みのみで差し戻しは無い＝当ターンは1回のchatで終わる
    assert eng.cfg["active_role"] == "リサーチ"

    out2 = eng.process_turn("じゃあこれ調べて")
    assert out2["reply"] == "了解、調べ物モードで答えるじょ"
    second_system_text = fake.calls[1][0]["content"]
    assert "## いまのモード" in second_system_text
    assert "調べ物・情報整理の相棒" in second_system_text


def test_system_text_contains_tools_spec_and_identity():
    eng, fake, sid = _make(["ほい"])
    eng.process_turn("やあ")
    system_message = fake.calls[0][0]
    assert system_message["role"] == "system"
    system_text = system_message["content"]
    assert "fieria-tool" in system_text        # ツール仕様
    assert "テスト用の子。" in system_text      # identity
    assert "作業フォルダツール" not in system_text  # workspace_dir未設定なので含まない


def test_system_text_includes_workspace_tools_spec_when_workspace_dir_set(tmp_path):
    eng, fake, sid = _make(["ほい"])
    eng.cfg["workspace_dir"] = str(tmp_path)
    eng.process_turn("やあ")
    system_text = fake.calls[0][0]["content"]
    assert "作業フォルダツール" in system_text
    assert "list_workspace" in system_text


def test_llm_exception_rolls_back_history_and_reraises():
    class ExplodingLLM:
        def chat(self, messages, max_tokens=None):
            raise RuntimeError("空応答だったじょ")

    eng, fake, sid = _make(["使わない"])
    eng.llm = ExplodingLLM()
    before_len = len(eng.messages)

    with pytest.raises(RuntimeError):
        eng.process_turn("こんにちは")

    # 応答のない孤立userメッセージが残らないこと
    assert len(eng.messages) == before_len

    # 正常なLLMに差し替えれば次のターンは問題なく動く
    eng.llm = FakeLLM(["だいじょうぶじょ"])
    out = eng.process_turn("もう一回いくじょ")
    assert out["reply"] == "だいじょうぶじょ"
    assert len(eng.messages) == before_len + 2


def test_restore_today_maps_who_to_role_and_preserves_order():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "おはよう")
    soul.append_log(sid, "ai", "にゃっほー")
    soul.append_log(sid, "user", "元気？")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "おはよう"},
        {"role": "assistant", "content": "にゃっほー"},
        {"role": "user", "content": "元気？"},
    ]


def test_restore_today_respects_limit_keeping_most_recent():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "1件目")
    soul.append_log(sid, "ai", "2件目")
    soul.append_log(sid, "user", "3件目")

    eng.restore_today(2)

    assert [m["content"] for m in eng.messages] == ["2件目", "3件目"]


def test_restore_today_does_nothing_when_messages_already_populated():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "ログにはあるじょ")
    eng.messages.append({"role": "user", "content": "既にある会話"})

    eng.restore_today(50)

    assert eng.messages == [{"role": "user", "content": "既にある会話"}]


def test_restore_today_zero_limit_restores_nothing():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "残らないはず")

    eng.restore_today(0)

    assert eng.messages == []


def test_restore_today_swallows_read_failure(monkeypatch):
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "無視される")

    def boom(*args, **kwargs):
        raise OSError("読み込み失敗じょ")
    monkeypatch.setattr(soul, "read_today_log", boom)

    eng.restore_today(50)  # 例外を投げない

    assert eng.messages == []


def test_restore_today_skips_entries_with_unknown_who():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "普通の発言")
    # who が user/ai 以外のエントリを直接ログへ混入させる（想定外データへの耐性確認）
    soul.append_log(sid, "system", "混入データ")

    eng.restore_today(50)

    assert eng.messages == [{"role": "user", "content": "普通の発言"}]


def test_restore_today_merges_consecutive_same_role_entries():
    """LLM失敗後の再送でログにuser, userが連続した場合でも、messagesへ復元する際は
    1件に連結する（GeminiLLM.chatはrole交互前提でcontentsを組むため、連続roleのまま
    渡すと実APIへの送信で400エラーの種になる）。ログファイル自体はそのまま残る。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "A")
    soul.append_log(sid, "user", "B")
    soul.append_log(sid, "ai", "C")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "A\nB"},
        {"role": "assistant", "content": "C"},
    ]


def test_restore_today_merges_consecutive_ai_entries():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "質問")
    soul.append_log(sid, "ai", "返事その1")
    soul.append_log(sid, "ai", "返事その2")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "質問"},
        {"role": "assistant", "content": "返事その1\n返事その2"},
    ]


def test_restore_today_alternating_log_is_unchanged_by_merge():
    """正常な交互ログ（user, ai, user）では連結が発生せず、既存の
    test_restore_today_maps_who_to_role_and_preserves_order と同じ結果になることの確認。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "おはよう")
    soul.append_log(sid, "ai", "にゃっほー")
    soul.append_log(sid, "user", "元気？")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "おはよう"},
        {"role": "assistant", "content": "にゃっほー"},
        {"role": "user", "content": "元気？"},
    ]


def test_restore_today_restores_only_entries_after_last_break():
    """「区切り」より前のuser/aiエントリは復元対象から外れる（区切りは
    「じゃ、別の話なんだけど」に相当するため、次に開いたときそれ以前を
    LLM文脈へ戻さない）。break行自体もmessagesには入らない。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "前の話1")
    soul.append_log(sid, "ai", "前の話2")
    soul.append_break(sid)
    soul.append_log(sid, "user", "後の話1")
    soul.append_log(sid, "ai", "後の話2")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "後の話1"},
        {"role": "assistant", "content": "後の話2"},
    ]


def test_restore_today_uses_only_last_break_when_multiple():
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "最初期")
    soul.append_break(sid)
    soul.append_log(sid, "user", "中間")
    soul.append_break(sid)
    soul.append_log(sid, "user", "最新")

    eng.restore_today(50)

    assert eng.messages == [{"role": "user", "content": "最新"}]


def test_restore_today_break_with_nothing_after_restores_empty():
    """区切り直後（後続の発言がまだ無い）状態での起動は、空のmessagesで始まる。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "前の話")
    soul.append_break(sid)

    eng.restore_today(50)

    assert eng.messages == []


def test_restore_today_without_break_is_backward_compatible():
    """break行が1本も無いログでは、従来どおり今日のログ全体が復元対象になる。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "いつも通り1")
    soul.append_log(sid, "ai", "いつも通り2")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "いつも通り1"},
        {"role": "assistant", "content": "いつも通り2"},
    ]


def test_reset_context_empties_messages_without_touching_log():
    """reset_contextはmessagesだけを空にし、既存のログファイルには一切触れない。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "残るログ")
    eng.messages.append({"role": "user", "content": "文脈"})
    eng.messages.append({"role": "assistant", "content": "返事"})

    eng.reset_context()

    assert eng.messages == []
    assert [e["who"] for e in soul.read_today_log(sid)] == ["user"]


def test_break_then_restart_restores_nothing_and_conversation_continues_normally():
    """再起動シミュレーション: 区切りを入れた後にプロセスを再起動した想定
    （＝新しいEngineインスタンスを同じsoul_idで作り直す）でも、区切り以前の
    会話がLLM文脈へ戻らず、その後の会話は普通に始まることを確認する。"""
    import engine
    import soul
    eng1, fake1, sid = _make(["前回の返事"])
    eng1.process_turn("前回の質問")

    soul.append_break(sid)

    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    fake2 = FakeLLM(["再開後の返事"])
    eng2 = engine.Engine(cfg, fake2, sid)
    eng2.restore_today(50)
    assert eng2.messages == []

    out = eng2.process_turn("再開後の質問")

    assert out["reply"] == "再開後の返事"
    assert eng2.messages == [
        {"role": "user", "content": "再開後の質問"},
        {"role": "assistant", "content": "再開後の返事"},
    ]
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["user", "ai", "break", "user", "ai"]


def test_restore_today_then_process_turn_does_not_double_append_log():
    """復元→通常ターンの流れで、ログファイルが「復元前の件数＋新規1往復」分だけ増え、
    復元操作自体はログへ書き戻さない（messagesへの読み込みに留まる）ことを確認する。"""
    import soul
    eng, fake, sid = _make(["やっほー、続きだじょ"])
    soul.append_log(sid, "user", "前回の続き")
    soul.append_log(sid, "ai", "前回の返事")
    before_log_len = len(soul.read_today_log(sid))

    eng.restore_today(50)
    assert len(eng.messages) == 2  # ログ書き込みは発生していない
    assert len(soul.read_today_log(sid)) == before_log_len

    eng.process_turn("続きだよ")

    after_log_len = len(soul.read_today_log(sid))
    assert after_log_len == before_log_len + 2  # 新規のuser/aiの2件だけ増えた
    assert len(eng.messages) == 4  # 復元2件＋今回のuser/assistant


# --- 画像添付（process_turnのimages引数） ---

def _tiny_b64():
    import base64
    return base64.b64encode(b"tiny-fake-image-bytes").decode("ascii")


def test_process_turn_without_images_keeps_backward_compatible_message_shape():
    """images未指定（後方互換のデフォルトNone）ならuserメッセージにimagesキーが付かないこと。"""
    eng, fake, sid = _make(["にゃっほー"])
    eng.process_turn("画像なしの発言")
    user_msg = fake.calls[0][-1]
    assert user_msg["role"] == "user"
    assert "images" not in user_msg


def test_process_turn_with_images_attaches_images_to_message_sent_to_llm():
    eng, fake, sid = _make(["見えたじょ"])
    images = [{"mime": "image/png", "b64": _tiny_b64()}]

    eng.process_turn("これ見て", images=images)

    user_msg = fake.calls[0][-1]
    assert user_msg["role"] == "user"
    assert user_msg["images"] == images


def test_process_turn_with_images_saves_attachment_and_logs_reference_without_b64():
    import soul
    eng, fake, sid = _make(["見えたじょ"])
    b64 = _tiny_b64()
    images = [{"mime": "image/png", "b64": b64}]

    eng.process_turn("これ見て", images=images)

    log = soul.read_today_log(sid)
    user_entry = next(e for e in log if e["who"] == "user")
    assert "[画像: attachments/" in user_entry["text"]
    assert b64 not in user_entry["text"]
    # ログ参照先のファイルが実際に保存されていること
    ref = user_entry["text"].split("[画像: ")[1].rstrip("]")
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), ref.replace("/", os.sep)))


def test_process_turn_with_multiple_images_logs_each_reference():
    import soul
    eng, fake, sid = _make(["両方見えたじょ"])
    images = [
        {"mime": "image/png", "b64": _tiny_b64()},
        {"mime": "image/jpeg", "b64": _tiny_b64()},
    ]

    eng.process_turn("2枚あるよ", images=images)

    log = soul.read_today_log(sid)
    user_entry = next(e for e in log if e["who"] == "user")
    assert user_entry["text"].count("[画像: attachments/") == 2


def test_estimate_tokens_adds_per_image_estimate():
    import engine as engine_mod
    without_images = engine_mod._estimate_tokens([{"content": "こんにちは", "role": "user"}])
    with_images = engine_mod._estimate_tokens([
        {"content": "こんにちは", "role": "user",
         "images": [{"mime": "image/png", "b64": "x"}]},
    ])
    assert with_images == without_images + engine_mod.IMAGE_TOKEN_ESTIMATE


def test_restore_today_does_not_reinject_images_only_text_reference():
    """restore_todayはログのテキスト（[画像: ...]参照込み）だけを復元し、画像実体
    （images）はmessagesに再注入しない、という仕様の確認。"""
    import soul
    eng, fake, sid = _make([])
    soul.append_log(sid, "user", "写真見て\n[画像: attachments/2026-01-01/000000-1.png]")

    eng.restore_today(50)

    assert eng.messages == [
        {"role": "user", "content": "写真見て\n[画像: attachments/2026-01-01/000000-1.png]"},
    ]
    assert "images" not in eng.messages[0]


def test_normal_turn_reports_stopped_false():
    eng, fake, sid = _make(["へーきへーき"])
    out = eng.process_turn("だいじょうぶ？")
    assert out["stopped"] is False


def test_stop_requested_during_turn_rolls_back_and_skips_ai_log():
    """process_turn冒頭で_stop_requestedはFalseにリセットされる（GUI側は別スレッドから
    ターン実行中にrequest_stop()を呼ぶ想定のため、ターン開始"前"の要求は無効で正しい）。
    ここではchat()呼び出しの最中に停止要求が入った状況をFakeLLMの副作用で再現し、
    chatの応答は破棄されmessagesがターン開始時点まで巻き戻り、AIログも書かれないことを確認する。"""
    import soul

    class StoppingLLM:
        def __init__(self, eng, reply):
            self.eng = eng
            self.reply = reply
            self.calls = 0

        def chat(self, messages, max_tokens=None):
            self.calls += 1
            self.eng.request_stop()  # 応答が返ってくる直前に停止要求が入った状況を再現
            return self.reply

    eng, fake, sid = _make([])
    llm = StoppingLLM(eng, "使われないはずの応答")
    eng.llm = llm
    before_len = len(eng.messages)

    out = eng.process_turn("元気？")

    assert out["stopped"] is True
    assert out["reply"] == ""
    assert llm.calls == 1
    assert len(eng.messages) == before_len
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["user"]  # AIログは書かれていない（userの発言は残る）

    # 次のターンでは_stop_requestedがリセットされ、通常どおり動く
    eng.llm = FakeLLM(["だいじょうぶじょ"])
    out2 = eng.process_turn("もう一回")
    assert out2["stopped"] is False
    assert out2["reply"] == "だいじょうぶじょ"


def test_stop_during_tool_round_rolls_back_read_diff_before_second_reply(monkeypatch):
    """ツール呼び出し（read_memory）が実行された直後に停止要求が入った場合でも、
    read結果の差し戻し（第2回目のchat呼び出し）自体は起きるが、その応答到達直後の
    チェックで捕捉され、最終的にmessagesは差し戻し前の状態まで巻き戻ることを確認する。"""
    import memory_tools
    import soul
    eng, fake, sid = _make([
        '確認するじょ\n```fieria-tool\n{"tool": "read_memory", "path": "wiki/約束.md"}\n```',
        "使われないはずの2回目応答",
    ])
    soul.write_file(sid, "wiki/約束.md", "8時に散歩\n")

    orig_execute = memory_tools.execute

    def stopping_execute(soul_id, call, cfg=None):
        result = orig_execute(soul_id, call, cfg)
        eng.request_stop()  # ツール実行完了直後（read差し戻し前）に停止要求
        return result

    monkeypatch.setattr(memory_tools, "execute", stopping_execute)
    before_len = len(eng.messages)

    out = eng.process_turn("昨日の約束なんだっけ")

    assert out["stopped"] is True
    assert out["reply"] == ""
    assert len(eng.messages) == before_len
    assert len(fake.calls) == 2  # 差し戻し後の2回目chatは実行されたが、結果は破棄された
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["user"]  # AIログは書かれていない


def test_append_log_failure_does_not_break_conversation(monkeypatch):
    import soul
    eng, fake, sid = _make(["だいじょうぶじょ"])

    def boom(*args, **kwargs):
        raise OSError("ディスクエラーだじぇ")

    monkeypatch.setattr(soul, "append_log", boom)
    out = eng.process_turn("元気？")

    assert out["reply"] == "だいじょうぶじょ"
    assert any(
        op.get("ok") is False and op.get("op") == "append_log"
        for op in out["operations"]
    )


# --- リマインダ発火（process_turn） ---

def test_due_reminder_injected_into_system_text():
    import soul
    eng, fake, sid = _make(["わかったじょ"])
    soul.add_reminder(sid, "薬を飲む", "2020-01-01")  # 単発・期限超過

    eng.process_turn("やあ")

    system_text = fake.calls[0][0]["content"]
    assert "今日伝えるリマインダ" in system_text
    assert "薬を飲む" in system_text


def test_due_reminder_marked_fired_after_successful_turn():
    import soul
    eng, fake, sid = _make(["わかったじょ"])
    rid = soul.add_reminder(sid, "薬を飲む", "2020-01-01")

    eng.process_turn("やあ")

    reminders = soul.list_reminders(sid, include_done=True)
    assert reminders[0]["id"] == rid
    assert reminders[0]["done"] is True
    # 発火済みなので次のターンではもう注入されない
    eng.llm = FakeLLM(["また今度じょ"])
    eng.process_turn("もう一回")
    system_text2 = eng.llm.calls[-1][0]["content"]
    assert "今日伝えるリマインダ" not in system_text2


def test_due_reminder_not_marked_fired_when_llm_raises():
    import soul
    eng, fake, sid = _make(["使わない"])
    rid = soul.add_reminder(sid, "薬を飲む", "2020-01-01")

    class ExplodingLLM:
        def chat(self, messages, max_tokens=None):
            raise RuntimeError("落ちたじょ")

    eng.llm = ExplodingLLM()
    with pytest.raises(RuntimeError):
        eng.process_turn("やあ")

    reminders = soul.list_reminders(sid, include_done=True)
    assert reminders[0]["id"] == rid
    assert reminders[0]["done"] is False  # 未発火のまま＝次ターンで再注入される


def test_due_reminder_not_marked_fired_when_turn_stopped():
    import soul

    class StoppingLLM:
        def __init__(self, eng):
            self.eng = eng

        def chat(self, messages, max_tokens=None):
            self.eng.request_stop()
            return "使われないはずの応答"

    eng, fake, sid = _make([])
    rid = soul.add_reminder(sid, "薬を飲む", "2020-01-01")
    eng.llm = StoppingLLM(eng)

    out = eng.process_turn("やあ")

    assert out["stopped"] is True
    reminders = soul.list_reminders(sid, include_done=True)
    assert reminders[0]["id"] == rid
    assert reminders[0]["done"] is False


# --- セッション内圧縮（context_limit_tokens） ---

def _fill_long_history(eng, pairs=10, text="むかしむかし、あるところに長い長いお話がありました。" * 3):
    """_estimate_tokens が閾値を超えるだけの量の会話をmessagesへ直接積む
    （process_turn経由だとFakeLLMの応答を消費してしまうため、直接操作する）。"""
    for i in range(pairs):
        eng.messages.append({"role": "user", "content": f"{i}: {text}"})
        eng.messages.append({"role": "assistant", "content": f"{i}: {text}"})


def test_compaction_does_not_run_when_threshold_is_zero_default():
    eng, fake, sid = _make(["だいじょうぶじょ"])
    assert eng.cfg.get("context_limit_tokens", 0) == 0  # 既定
    _fill_long_history(eng)
    before = list(eng.messages)

    out = eng.process_turn("続き")

    # 圧縮は起きていない：古い会話がそのまま先頭に残っている
    assert eng.messages[:len(before)] == before
    assert not any(op.get("op") == "compact" for op in out["operations"])


def test_compaction_runs_when_threshold_exceeded_and_replaces_old_half_with_summary():
    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10  # 極端に低い閾値で確実に超過させる
    _fill_long_history(eng, pairs=10)
    old_half_len = len(eng.messages) // 2
    new_half = list(eng.messages[old_half_len:])

    eng.llm = FakeLLM(["（要約）長い昔話が10往復あった", "了解、続けるじょ"])
    out = eng.process_turn("続き")

    assert any(op.get("ok") is True and op.get("op") == "compact" for op in out["operations"])
    # 圧縮後の履歴: 要約1件 + 後半（圧縮前の後半） + 新規のuser/assistant往復。
    # pairs=10なのでsplitは偶数(10)になり、後半の先頭もuser role。要約(role: user)と
    # role連続になるため、後半先頭は要約メッセージへ連結され独立した1件としては残らない
    # （role連続防止のための正規化。詳細はtest_compaction_no_consecutive_same_role_*参照）。
    assert eng.messages[0]["role"] == "user"
    assert "（要約）長い昔話が10往復あった" in eng.messages[0]["content"]
    assert new_half[0]["content"] in eng.messages[0]["content"]
    assert eng.messages[1:1 + len(new_half) - 1] == new_half[1:]
    assert eng.messages[-2] == {"role": "user", "content": "続き"}
    assert eng.messages[-1] == {"role": "assistant", "content": "了解、続けるじょ"}


def test_compaction_no_consecutive_same_role_when_split_is_even():
    """メッセージ数が4の倍数（例: pairs=10→20件、split=10で偶数）だと、要約(role: user)と
    後半の先頭(role: user)が衝突していた（修正前のバグ）。修正後は全隣接ペアでrole不一致
    であることを確認する（GeminiLLM.chatのrole交互前提を満たすため）。"""
    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=10)
    assert (len(eng.messages) // 2) % 2 == 0  # 前提: splitが偶数のケースであること

    eng.llm = FakeLLM(["（要約）まとめ", "はい"])
    eng.process_turn("続き")

    roles = [m["role"] for m in eng.messages]
    for a, b in zip(roles, roles[1:]):
        assert a != b, f"role連続を検出: {roles}"


def test_compaction_skipped_when_stop_requested_before_summary_call(monkeypatch):
    """閾値判定〜圧縮着手の間に停止要求が来ていた状況を、_estimate_tokensの呼び出し
    タイミングで request_stop() を発火させることで再現する。圧縮の要約チャットも
    本チャットも呼ばれず、process_turnはstopped=Trueで即座に返ることを確認する。"""
    import engine

    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=10)

    orig_estimate = engine._estimate_tokens

    def estimate_and_stop(messages):
        eng.request_stop()
        return orig_estimate(messages)

    monkeypatch.setattr(engine, "_estimate_tokens", estimate_and_stop)

    llm = FakeLLM(["（要約）まとめ", "本チャットも呼ばれないはず"])
    eng.llm = llm
    before = list(eng.messages)

    out = eng.process_turn("続き")

    assert out["stopped"] is True
    assert out["reply"] == ""
    assert llm.calls == []  # 圧縮の要約チャットも本チャットも呼ばれていない
    assert not any(op.get("op") == "compact" for op in out["operations"])
    assert eng.messages == before  # 何も変更されていない


def test_compaction_skipped_when_stop_requested_during_summary_call():
    """圧縮の要約LLM呼び出し(_compact内のchat)が実行中に停止要求が来た状況を、
    FakeLLM側の副作用で再現する。圧縮自体は完了済みなので状態変更として維持され
    operationsのcompactも残るが、本チャット（2回目のchat）は呼ばれず停止処理へ回る。"""

    class StoppingDuringSummaryLLM:
        def __init__(self, eng, summary):
            self.eng = eng
            self.summary = summary
            self.calls = 0

        def chat(self, messages, max_tokens=None):
            self.calls += 1
            self.eng.request_stop()  # 要約チャットの応答が返ってくる直前に停止要求
            return self.summary

    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=10)

    llm = StoppingDuringSummaryLLM(eng, "（要約）まとめ")
    eng.llm = llm

    out = eng.process_turn("続き")

    assert out["stopped"] is True
    assert out["reply"] == ""
    assert llm.calls == 1  # 要約チャットは呼ばれたが、本チャットは呼ばれていない
    assert any(op.get("ok") is True and op.get("op") == "compact" for op in out["operations"])
    assert eng.messages[0]["role"] == "user"
    assert "（要約）まとめ" in eng.messages[0]["content"]


def test_compaction_summary_llm_failure_leaves_messages_untouched_and_conversation_continues():
    class FirstCallFailsLLM:
        def __init__(self, second_reply):
            self.second_reply = second_reply
            self.calls = 0

        def chat(self, messages, max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("要約LLMが落ちたじょ")
            return self.second_reply

    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=10)
    before = list(eng.messages)

    eng.llm = FirstCallFailsLLM("それでも会話は続くじょ")
    out = eng.process_turn("続き")

    # 圧縮は失敗し、operationsにcompactは積まれない。古い履歴もそのまま残る
    assert not any(op.get("op") == "compact" for op in out["operations"])
    assert eng.messages[:len(before)] == before
    assert out["reply"] == "それでも会話は続くじょ"


def test_compaction_message_count_shrinks_after_compact():
    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=10)
    before_count = len(eng.messages)
    old_half_count = before_count // 2

    eng.llm = FakeLLM(["（要約）まとめ", "はい"])
    eng.process_turn("続き")

    # 圧縮前の件数 - 前半件数 + 要約1件 + 新規のuser/assistant2件、という件数関係になっていること。
    # pairs=10はsplitが偶数(10)になり後半先頭が要約とrole衝突するため、role連続防止の
    # 正規化でさらに1件分吸収される（-1）。
    expected = (before_count - old_half_count) + 1 + 2 - 1
    assert len(eng.messages) == expected


def test_compaction_triggered_by_system_text_size_even_when_messages_alone_are_under_threshold():
    """実際にLLMへ送るのは[system] + messagesなのに、圧縮の閾値判定がmessagesだけを
    見ていると、system_text（identity・fact_layer・tools_specを含む）が大きくても
    圧縮が発火しない。messagesだけでは超えないがsystem_text込みなら超える閾値を
    動的に計算し、圧縮が発火することを確認する。"""
    import memory_tools
    import prompt

    eng, fake, sid = _make([])
    eng.messages.append({"role": "user", "content": "短い相談"})
    eng.messages.append({"role": "assistant", "content": "うん"})

    system_text = prompt.build_system_text(eng.cfg, sid, tools_spec=memory_tools.TOOLS_SPEC)
    import engine as engine_mod
    messages_only = engine_mod._estimate_tokens(eng.messages)
    with_system = engine_mod._estimate_tokens([{"content": system_text}] + eng.messages)
    # 前提: tools_spec等を含むsystem_textはそれなりの長さがあり、閾値を跨げる差がある
    assert with_system > messages_only + 1

    eng.cfg["context_limit_tokens"] = messages_only + 1  # messagesだけでは超えない閾値

    eng.llm = FakeLLM(["（要約）短い相談だった", "はい"])
    out = eng.process_turn("続き")

    assert any(op.get("ok") is True and op.get("op") == "compact" for op in out["operations"])


# --- read_pdfツールの差し戻し（attachments→images。2026-07-22追加）---

def test_read_pdf_tool_feeds_back_attachments_as_images(monkeypatch):
    """memory_tools.execute が attachments 付きの結果を返すツール（read_pdf等）は、
    差し戻しuserメッセージにimagesとして乗ること（llm.chatへ渡るmessages形式は
    既存の画像添付と同じ[{"mime","b64"}]なので無改修で通る、という設計の確認）。"""
    import memory_tools
    eng, fake, sid = _make([
        '見るじょ\n```fieria-tool\n{"tool": "read_pdf", "path": "資料.pdf"}\n```',
        "PDF見えたじょ",
    ])

    fake_attachment = {"mime": "application/pdf", "b64": "ZmFrZS1wZGYtYnl0ZXM="}

    def fake_execute(soul_id, call, cfg=None):
        return {"ok": True, "op": "read_pdf", "detail": "資料.pdf を添付した（Gemini直読み・0.01MB）",
                "attachments": [fake_attachment]}

    monkeypatch.setattr(memory_tools, "execute", fake_execute)

    out = eng.process_turn("資料.pdfを見て")

    assert out["reply"] == "PDF見えたじょ"
    second_messages = fake.calls[1]
    feedback_msg = second_messages[-1]
    assert feedback_msg["role"] == "user"
    assert feedback_msg["images"] == [fake_attachment]
    assert "資料.pdf を添付した" in feedback_msg["content"]


def test_compaction_preserves_images_on_message_that_does_not_merge():
    """修正前バグ: _compactが新halfの各メッセージを{"role","content"}だけの新dictへ
    作り直していたため、mergeが起きない（隣接roleが不一致の）メッセージでもimagesが
    脱落していた。pairs=11（総messages数22件、split=11）だとnew_half先頭
    （messages[11]）が要約(role:user)とrole不一致のassistantになるので、
    このケースでmerge無しのまま images が保持されることを確認する。"""
    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=11)
    split = len(eng.messages) // 2
    assert eng.messages[split]["role"] == "assistant"  # 前提: new_half先頭がassistant（要約とrole不一致）
    eng.messages[split]["images"] = [{"mime": "image/png", "b64": "extra-tail-image"}]
    marker_content = eng.messages[split]["content"]

    eng.llm = FakeLLM(["（要約）まとめ", "はい"])
    out = eng.process_turn("続き")

    assert any(op.get("ok") is True and op.get("op") == "compact" for op in out["operations"])
    matches = [m for m in eng.messages if m.get("content") == marker_content]
    assert len(matches) == 1
    assert matches[0].get("images") == [{"mime": "image/png", "b64": "extra-tail-image"}]


def test_compaction_merges_and_preserves_images_from_both_sides():
    """要約(role:user)と new_half 先頭(role:user)がrole衝突して連結されるケースで、
    connectionが起きても新half側が持つimagesが消えないことを確認する
    （修正前バグの本体: マージ時に{"role","content"}だけの新dictを作っていた）。"""
    eng, fake, sid = _make([])
    eng.cfg["context_limit_tokens"] = 10
    _fill_long_history(eng, pairs=10)  # split=10（偶数）→new_half[0]はuser（要約とrole衝突）
    split = len(eng.messages) // 2
    eng.messages[split]["images"] = [{"mime": "image/jpeg", "b64": "merged-image"}]

    eng.llm = FakeLLM(["（要約）まとめ", "はい"])
    out = eng.process_turn("続き")

    assert any(op.get("ok") is True and op.get("op") == "compact" for op in out["operations"])
    assert eng.messages[0]["role"] == "user"
    assert eng.messages[0]["images"] == [{"mime": "image/jpeg", "b64": "merged-image"}]


# --- PDF添付のトークン見積り（pagesベース・2026-07-22追加）---

def test_estimate_tokens_pdf_with_pages_uses_pages_times_per_page_estimate():
    import engine as engine_mod
    messages = [{"content": "見て", "role": "user",
                 "images": [{"mime": "application/pdf", "b64": "x", "pages": 10}]}]
    without_pdf = engine_mod._estimate_tokens([{"content": "見て", "role": "user"}])
    assert engine_mod._estimate_tokens(messages) == (
        without_pdf + 10 * engine_mod.PDF_TOKENS_PER_PAGE)


def test_estimate_tokens_pdf_without_pages_falls_back_to_b64_length():
    import engine as engine_mod
    long_b64 = "a" * (engine_mod.PDF_B64_CHARS_PER_TOKEN * 2000)  # 十分長い→フォールバック概算がIMAGE_TOKEN_ESTIMATEを上回る
    messages = [{"content": "見て", "role": "user",
                 "images": [{"mime": "application/pdf", "b64": long_b64}]}]
    without_pdf = engine_mod._estimate_tokens([{"content": "見て", "role": "user"}])
    expected = len(long_b64) // engine_mod.PDF_B64_CHARS_PER_TOKEN
    assert expected > engine_mod.IMAGE_TOKEN_ESTIMATE  # 前提: フォールバックの方が大きいケース
    assert engine_mod._estimate_tokens(messages) == without_pdf + expected


def test_estimate_tokens_pdf_without_pages_clips_to_image_token_estimate_minimum():
    import engine as engine_mod
    messages = [{"content": "見て", "role": "user",
                 "images": [{"mime": "application/pdf", "b64": "short"}]}]
    without_pdf = engine_mod._estimate_tokens([{"content": "見て", "role": "user"}])
    # b64が短い(フォールバック概算がIMAGE_TOKEN_ESTIMATE未満)ケースでも下限でクリップされる
    assert engine_mod._estimate_tokens(messages) == without_pdf + engine_mod.IMAGE_TOKEN_ESTIMATE


def test_tool_without_attachments_does_not_add_images_key(monkeypatch):
    """read_memory等、attachmentsを返さないツールの差し戻しメッセージには
    imagesキー自体が付かないこと（既存の後方互換維持の確認）。"""
    import memory_tools
    eng, fake, sid = _make([
        '確認するじょ\n```fieria-tool\n{"tool": "read_memory", "path": "wiki/約束.md"}\n```',
        "了解じょ",
    ])
    import soul
    soul.write_file(sid, "wiki/約束.md", "8時に散歩\n")

    out = eng.process_turn("約束なんだっけ")

    assert out["reply"] == "了解じょ"
    second_messages = fake.calls[1]
    feedback_msg = second_messages[-1]
    assert "images" not in feedback_msg


# --- 連想記憶の自動注入（auto-recall。2026-07-22追加）---

def test_process_turn_injects_recall_block_when_related_memory_exists():
    import soul
    eng, fake, sid = _make(["インベントリの話ならしたじょ"])
    soul.write_file(sid, "wiki/UI談義.md", "きょうはインベントリUIの話をみんとちゃんとした。8枠で決着。")

    eng.process_turn("前にインベントリの話したっけ？")

    system_text = fake.calls[0][0]["content"]
    assert "## 連想記憶（自動検索）" in system_text
    assert "[出典: wiki/UI談義.md]" in system_text


def test_process_turn_omits_recall_section_when_no_hits():
    eng, fake, sid = _make(["わかったじょ"])

    eng.process_turn("ぜったいに存在しない単語列XYZ123の話")

    system_text = fake.calls[0][0]["content"]
    assert "## 連想記憶（自動検索）" not in system_text


def test_process_turn_recall_disabled_by_config():
    import soul
    eng, fake, sid = _make(["わかったじょ"])
    eng.cfg["auto_recall"] = {"enabled": False, "max_hits": 3}
    soul.write_file(sid, "wiki/UI談義.md", "きょうはインベントリUIの話をした。")

    eng.process_turn("前にインベントリの話したっけ？")

    system_text = fake.calls[0][0]["content"]
    assert "## 連想記憶（自動検索）" not in system_text


def test_process_turn_does_not_rerun_recall_on_tool_loop_round(monkeypatch):
    """ツールループ2周目（i>0でのsystem_text再構築、engine.py内のi>0分岐）では
    再検索しない——recall_mod.build_recall_blockの呼び出しは1ターンにつき1回きり
    であること（i>0では既に持っているrecall_blockを使い回すだけ）。"""
    import engine
    import recall
    import soul
    sid = soul.create_soul("再検索防止テスト", identity_text="テスト用の子。")
    soul.write_file(sid, "wiki/UI談義.md", "きょうはインベントリUIの話をした。")

    call_count = {"n": 0}
    orig_build = recall.build_recall_block

    def counting_build(*args, **kwargs):
        call_count["n"] += 1
        return orig_build(*args, **kwargs)

    monkeypatch.setattr(engine.recall_mod, "build_recall_block", counting_build)

    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    fake = FakeLLM([
        '確認するじょ\n```fieria-tool\n{"tool": "read_memory", "path": "wiki/UI談義.md"}\n```',
        "インベントリの話じょね",
    ])
    eng = engine.Engine(cfg, fake, sid)

    eng.process_turn("前にインベントリの話したっけ？")

    assert len(fake.calls) == 2  # ツールループが2周（差し戻し）していること（前提の確認）
    assert call_count["n"] == 1  # それでもrecallの検索実行は1回だけ


# --- process_turnの戻り値recall_used（2026-07-22追加。ペットUIの「ピコン」演出判定用）---

def test_process_turn_recall_used_true_when_related_memory_exists():
    import soul
    eng, fake, sid = _make(["インベントリの話ならしたじょ"])
    soul.write_file(sid, "wiki/UI談義.md", "きょうはインベントリUIの話をみんとちゃんとした。8枠で決着。")

    out = eng.process_turn("前にインベントリの話したっけ？")

    assert out["recall_used"] is True


def test_process_turn_recall_used_false_when_no_hits():
    eng, fake, sid = _make(["わかったじょ"])

    out = eng.process_turn("ぜったいに存在しない単語列XYZ123の話")

    assert out["recall_used"] is False


def test_process_turn_recall_used_false_when_disabled_by_config():
    import soul
    eng, fake, sid = _make(["わかったじょ"])
    eng.cfg["auto_recall"] = {"enabled": False, "max_hits": 3}
    soul.write_file(sid, "wiki/UI談義.md", "きょうはインベントリUIの話をした。")

    out = eng.process_turn("前にインベントリの話したっけ？")

    assert out["recall_used"] is False


# --- 記憶の書き促し（2026-07-23・実機FB「促さないと自分から書かない」対応） ---

def _nudge_engine(tmp_soul, replies):
    import engine as engine_mod

    class FakeLLM:
        def __init__(self):
            self.system_texts = []

        def chat(self, messages):
            self.system_texts.append(messages[0]["content"])
            return replies[min(len(self.system_texts) - 1, len(replies) - 1)]
    llm = FakeLLM()
    eng = engine_mod.Engine({"fact_layer": {"enabled": False}}, llm, tmp_soul)
    return eng, llm


def test_memory_write_nudge_appears_after_threshold_and_resets_on_write():
    import engine as engine_mod
    import soul as soul_mod
    sid = soul_mod.create_soul("書き促しテスト", "コア")
    eng, llm = _nudge_engine(sid, ["ふつうの返事"])
    for _ in range(engine_mod.MEMORY_WRITE_NUDGE_TURNS):
        eng.process_turn("やあ")
    assert "しばらく記憶を書いていない" not in llm.system_texts[-1]
    eng.process_turn("やあ")  # 閾値到達後の最初のターンで注入される
    assert "しばらく記憶を書いていない" in llm.system_texts[-1]

    # 書き込みツールを使うとリセットされ、次ターンは注入されない
    llm2_replies = ['覚えた\n```fieria-tool\n{"tool": "save_lesson", "text": "テスト規則"}\n```',
                    "ふつうの返事"]
    eng2, llm2 = _nudge_engine(sid, llm2_replies)
    eng2._turns_since_memory_write = engine_mod.MEMORY_WRITE_NUDGE_TURNS
    eng2.process_turn("これ覚えて")   # 書き込み発生→リセット
    assert eng2._turns_since_memory_write == 0
    eng2.process_turn("やあ")
    assert "しばらく記憶を書いていない" not in llm2.system_texts[-1]


def test_on_status_called_for_memory_write_tools_and_swallows_exceptions():
    """書き込みツール実行時にon_status("memory_write")が呼ばれ、コールバック例外でも
    会話が壊れないこと（表示専用の約束）。ツール無し応答では呼ばれない。"""
    import engine as engine_mod
    import soul as soul_mod
    sid = soul_mod.create_soul("状態通知テスト", "コア")

    class FakeLLM:
        def __init__(self, replies):
            self.replies = list(replies)

        def chat(self, messages):
            return self.replies.pop(0)

    events = []
    llm = FakeLLM(['書くじょ\n```fieria-tool\n{"tool": "save_lesson", "text": "規則"}\n```'])
    eng = engine_mod.Engine({"fact_layer": {"enabled": False}}, llm, sid)
    r = eng.process_turn("覚えて", on_status=events.append)
    assert events == ["memory_write"]
    assert not r.get("stopped")

    def boom(kind):
        raise RuntimeError("表示側の事故")
    llm2 = FakeLLM(['また書く\n```fieria-tool\n{"tool": "save_lesson", "text": "規則2"}\n```'])
    eng2 = engine_mod.Engine({"fact_layer": {"enabled": False}}, llm2, sid)
    r2 = eng2.process_turn("覚えて", on_status=boom)
    assert r2["reply"]  # コールバック例外でもターンは成功

    events3 = []
    llm3 = FakeLLM(["ツール無しの返事"])
    eng3 = engine_mod.Engine({"fact_layer": {"enabled": False}}, llm3, sid)
    eng3.process_turn("やあ", on_status=events3.append)
    assert events3 == []
