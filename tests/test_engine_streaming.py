"""engine.py — process_turn(on_delta=...)（FieriA拡張・ストリーミング）の検証。

FakeStreamingLLMはchat()とchat_stream()の両方を持つ最小フェイク。chat_stream()は
repliesの文字列を1文字ずつchunkとしてyieldし、on_deltaが差分ごとに呼ばれることを
検証できるようにする。実LLM呼び出しは一切発生しない。
"""
import pytest


class FakeStreamingLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.chat_calls = 0
        self.stream_calls = 0

    def chat(self, messages, max_tokens=None):
        self.chat_calls += 1
        return self.replies.pop(0)

    def chat_stream(self, messages, max_tokens=None):
        self.stream_calls += 1
        text = self.replies.pop(0)
        for ch in text:
            yield ch


class ExplodingMidStreamLLM:
    """1文字目までは正常にyieldし、その後の反復でストリーム内エラーを送出するフェイク。
    _call_llmが例外を握りつぶさずそのまま伝播させることの確認用。"""

    def chat_stream(self, messages, max_tokens=None):
        yield "部分"
        raise RuntimeError("ストリーム内で落ちた")


def _make(replies):
    import engine
    import soul
    sid = soul.create_soul("ストリーミングテスト", identity_text="テスト用の子。")
    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    fake = FakeStreamingLLM(replies)
    return engine.Engine(cfg, fake, sid), fake, sid


def test_on_delta_called_for_each_chunk_and_final_reply_matches_concatenation():
    eng, fake, sid = _make(["にゃっほー、元気じょ"])
    deltas = []

    out = eng.process_turn("元気？", on_delta=deltas.append)

    assert "".join(deltas) == "にゃっほー、元気じょ"
    assert out["reply"] == "にゃっほー、元気じょ"
    assert fake.stream_calls == 1
    assert fake.chat_calls == 0


def test_on_delta_none_uses_plain_chat_not_chat_stream():
    """on_delta未指定（デフォルトNone）なら、llmがchat_streamを持っていても
    従来どおりchat()の単発呼び出しに落ちること（後方互換の要）。"""
    eng, fake, sid = _make(["だいじょうぶじょ"])

    out = eng.process_turn("元気？")

    assert out["reply"] == "だいじょうぶじょ"
    assert fake.chat_calls == 1
    assert fake.stream_calls == 0


def test_llm_without_chat_stream_attribute_ignores_on_delta_and_uses_chat():
    """llmがchat_streamを持たない（既存のFakeLLM等）場合、on_deltaを指定しても
    hasattrチェックで従来のchat()単発呼び出しに落ちること（未対応プロバイダでも
    壊れない後方互換の確認）。"""
    import engine
    import soul

    class PlainFakeLLM:
        def chat(self, messages, max_tokens=None):
            return "普通に返すじょ"

    sid = soul.create_soul("ストリーミングテスト2", identity_text="テスト用の子。")
    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    eng = engine.Engine(cfg, PlainFakeLLM(), sid)
    deltas = []

    out = eng.process_turn("元気？", on_delta=deltas.append)

    assert out["reply"] == "普通に返すじょ"
    assert deltas == []  # chat_stream自体が呼ばれていないのでon_deltaは一度も発火しない


def test_on_delta_exception_is_swallowed_and_conversation_continues():
    """表示用コールバック(on_delta)が例外を投げても、ターン自体は正常に完了すること。"""
    eng, fake, sid = _make(["へーきへーき"])

    def boom(_delta):
        raise RuntimeError("表示側の描画エラー")

    out = eng.process_turn("だいじょうぶ？", on_delta=boom)

    assert out["reply"] == "へーきへーき"
    assert out["stopped"] is False


def test_stop_requested_during_stream_rolls_back_history_and_skips_ai_log():
    """ストリーミング中（最初のchunk受信直後）に停止要求が入った状況を、on_delta内で
    eng.request_stop()を呼ぶことで再現する。既存のchat()レベル停止テストと同じく、
    messagesがターン開始時点まで巻き戻り、AIログも書かれないことを確認する。"""
    import soul

    eng, fake, sid = _make(["この続きは使われないはず"])
    before_len = len(eng.messages)
    received = []

    def stop_after_first_chunk(delta):
        received.append(delta)
        eng.request_stop()

    out = eng.process_turn("元気？", on_delta=stop_after_first_chunk)

    assert out["stopped"] is True
    assert out["reply"] == ""
    assert len(received) == 1  # 1文字目を受け取った直後に打ち切られている
    assert len(eng.messages) == before_len
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["user"]  # AIログは書かれていない


def test_chat_stream_exception_rolls_back_history_and_reraises():
    """chat_stream内で例外が起きた場合、蓄積済みの部分テキストがあってもターン全体を
    失敗として扱い（既存chat()失敗時と同じロールバック）、例外がそのまま呼び出し元へ
    伝播すること。"""
    import engine
    import soul

    sid = soul.create_soul("ストリーミングテスト3", identity_text="テスト用の子。")
    cfg = {"active_role": None, "fact_layer": {"enabled": True, "custom_text": ""}}
    eng = engine.Engine(cfg, ExplodingMidStreamLLM(), sid)
    before_len = len(eng.messages)
    deltas = []

    with pytest.raises(RuntimeError):
        eng.process_turn("こんにちは", on_delta=deltas.append)

    assert deltas == ["部分"]  # 例外前にyieldされた分はon_deltaへ届いている
    assert len(eng.messages) == before_len  # それでも履歴は巻き戻る
    log = soul.read_today_log(sid)
    assert [e["who"] for e in log] == ["user"]  # AIログは書かれていない（userの発言は残る）
