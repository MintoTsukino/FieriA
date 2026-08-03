"""嘘発見器（実機FB 2026-07-23: 「書いたよ」と言いながらツールを呼ばない）の検証。
フェイクLLMのみ・実LLM不使用。"""
import engine as engine_mod
import soul as soul_mod


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.system_texts = []

    def chat(self, messages):
        self.system_texts.append(messages[0]["content"])
        return self.replies.pop(0)


def _engine(sid, replies):
    llm = FakeLLM(replies)
    return engine_mod.Engine({"fact_layer": {"enabled": False}}, llm, sid), llm


def test_claim_without_tool_triggers_next_turn_callout():
    sid = soul_mod.create_soul("嘘発見テスト", "コア")
    eng, llm = _engine(sid, ["わかった、wikiに書いておいたよ！", "ふつうの返事"])
    eng.process_turn("これ覚えて")
    assert eng._unbacked_write_claim is True
    eng.process_turn("ありがと")
    assert "実際にはツールを呼んでいない" in llm.system_texts[-1]


def test_claim_with_actual_tool_does_not_trigger():
    sid = soul_mod.create_soul("嘘発見テスト2", "コア")
    reply = 'wikiに書いておいたよ！\n```fieria-tool\n{"tool": "save_lesson", "text": "規則"}\n```'
    eng, llm = _engine(sid, [reply, "ふつうの返事"])
    eng.process_turn("これ覚えて")
    assert eng._unbacked_write_claim is False
    eng.process_turn("ありがと")
    assert "実際にはツールを呼んでいない" not in llm.system_texts[-1]


def test_everyday_phrasing_does_not_trigger():
    """「昨日書いた小説」のような日常語は拾わない（対象語+保存動詞の近接要求）。"""
    sid = soul_mod.create_soul("嘘発見テスト3", "コア")
    eng, llm = _engine(sid, ["昨日書いた小説の続き、読みたいにゃ"])
    eng.process_turn("小説の話しよ")
    assert eng._unbacked_write_claim is False


def test_callout_clears_after_one_turn_if_resolved():
    """指摘後のターンで実際に書けばフラグは下りて、以後注入されない。"""
    sid = soul_mod.create_soul("嘘発見テスト4", "コア")
    replies = [
        "記憶に残しておくね！",
        '今度こそ\n```fieria-tool\n{"tool": "save_lesson", "text": "規則"}\n```',
        "ふつうの返事",
    ]
    eng, llm = _engine(sid, replies)
    eng.process_turn("これ覚えて")
    eng.process_turn("ほんとに書いた？")
    assert eng._unbacked_write_claim is False
    eng.process_turn("ありがと")
    assert "実際にはツールを呼んでいない" not in llm.system_texts[-1]


def test_tool_execution_claim_is_detected():
    """「ツールを実行したよ！」型の自称も検知する（実機FB第2弾: 対象語・動詞の網を拡大）。"""
    import engine as engine_mod
    assert engine_mod._CLAIM_PATTERN.search("ツールを実行したよ！")
    assert engine_mod._CLAIM_PATTERN.search("記憶ツールをしっかり呼び出したよ")
    assert engine_mod._CLAIM_PATTERN.search("メモに残しておいたからね")
    # 日常語は引き続き拾わない
    assert not engine_mod._CLAIM_PATTERN.search("昨日書いた小説の続きを実行に移す")
    assert not engine_mod._CLAIM_PATTERN.search("カラオケでツールボックスの歌を歌った")


# --- ト書き検知（実機FB 2026-08-04: GPT系がツールを「演じる」） ---
# 実機でGPT系モデルが「【ツールを使って記憶に残す】」のような隅付き括弧の
# 地の文だけを書き、JSONを一切出さないままツールを使ったつもりになる事象。
# DeepSeekの裸JSON（de64338）と違い本文にJSONが存在しないため、パーサ側では
# 救えない——検知して次ターンで書き直させる（嘘発見器と同じ型）。

def test_narrated_tool_use_triggers_next_turn_callout():
    sid = soul_mod.create_soul("ト書き検知テスト", "コア")
    # 実機ログ（souls/ニコイ/logs/2026-08-04.jsonl）の実際の応答を模した形
    eng, llm = _engine(sid, [
        "……大事なことだから覚えておくじぇ。\n\n【ツールを使って記憶に残す】\n\n……うん、書けたじぇ。",
        "ふつうの返事",
    ])
    eng.process_turn("これ覚えて")
    assert eng._narrated_tool_use is True
    eng.process_turn("ありがと")
    # マーカーは注入文にしか現れない文字列を使う（「地の文」はツール索引の
    # 事前警告にも含まれるため、それで判定すると常に真になってしまう）
    assert "前の応答で「【ツールを使う】」" in llm.system_texts[-1]


def test_narrated_variants_from_real_log_are_detected():
    """実機で観測された4パターン全部を拾えること。"""
    import engine as engine_mod
    for phrase in ("【ツールでタスク一覧を確認する】", "【タスクを書き込む】",
                    "【大事な瞬間を保存する】", "【ツールを使って記憶を検索してみる】"):
        assert engine_mod._NARRATED_TOOL_PATTERN.search(phrase), phrase


def test_epistemics_brackets_do_not_trigger():
    """記憶の認識論の規範（【推測】【撤回済み 日付】）は正当な用法なので拾わない。"""
    import engine as engine_mod
    assert not engine_mod._NARRATED_TOOL_PATTERN.search("【推測】たぶんそうだと思う")
    assert not engine_mod._NARRATED_TOOL_PATTERN.search("【撤回済み 2026-07-01】古い方針")


def test_narration_with_actual_tool_call_does_not_trigger():
    """本物の呼び出しが同じターンにあれば演技扱いしない（保守側）。"""
    sid = soul_mod.create_soul("ト書き混在テスト", "コア")
    reply = ('【ツールを使って記憶に残す】\n'
             '```fieria-tool\n{"tool": "save_lesson", "text": "規則"}\n```')
    eng, llm = _engine(sid, [reply])
    eng.process_turn("これ覚えて")
    assert eng._narrated_tool_use is False


def test_narration_callout_clears_after_real_call():
    sid = soul_mod.create_soul("ト書き解消テスト", "コア")
    replies = [
        "【タスクを書き込む】\nこれで登録したにゃ！",
        '今度こそ\n```fieria-tool\n{"tool": "save_lesson", "text": "規則"}\n```',
        "ふつうの返事",
    ]
    eng, llm = _engine(sid, replies)
    eng.process_turn("タスク登録して")
    assert eng._narrated_tool_use is True
    eng.process_turn("どこに作った？")
    assert eng._narrated_tool_use is False
    eng.process_turn("ありがと")
    assert "前の応答で「【ツールを使う】」" not in llm.system_texts[-1]
