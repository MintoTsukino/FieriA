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
