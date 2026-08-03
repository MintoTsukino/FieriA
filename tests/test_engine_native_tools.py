"""Engineのネイティブfunction calling経路。FakeLLMのみ・実API不使用。"""
import engine as engine_mod
import soul as soul_mod


class FakeNativeLLM:
    supports_native_tools = True

    def __init__(self, script):
        """script: chat_tools呼び出しごとの戻り値リスト。"""
        self.script = list(script)
        self.tool_messages_seen = []
        self.tools_seen = None

    def chat(self, messages, max_tokens=None):
        return "フェンス経路の返事"

    def chat_tools(self, messages, tools, max_tokens=None):
        self.tools_seen = tools
        self.tool_messages_seen.append(
            [m for m in messages if m.get("role") == "tool"])
        return self.script.pop(0)


def _cfg(tool_mode=None):
    entry = {"type": "deepseek"}
    if tool_mode:
        entry["tool_mode"] = tool_mode
    return {"fact_layer": {"enabled": False},
            "llm": {"provider": "d", "providers": {"d": entry}}}


def _final(text):
    return {"text": text, "tool_calls": []}


def _calling(name, args, text=""):
    return {"text": text,
            "tool_calls": [{"id": "c1", "name": name, "arguments": args}]}


def test_native_path_executes_tool_and_returns_final_text():
    sid = soul_mod.create_soul("ネイティブ実行テスト", "コア")
    llm = FakeNativeLLM([
        _calling("save_lesson", {"text": "JSONを書くこと"}),
        _final("覚えたじょ"),
    ])
    eng = engine_mod.Engine(_cfg(), llm, sid)

    result = eng.process_turn("これ覚えて")

    assert result["reply"] == "覚えたじょ"
    assert any(op["op"] == "save_lesson" and op["ok"] for op in result["operations"])
    assert "JSONを書くこと" in soul_mod.read_file(sid, "lessons.md")


def test_native_path_feeds_tool_result_back():
    """2回目のchat_tools呼び出しに、1回目の実行結果がrole:toolで渡ること。"""
    sid = soul_mod.create_soul("差し戻しテスト", "コア")
    llm = FakeNativeLLM([
        _calling("search_memory", {"query": "チュロス"}),
        _final("見つけたじょ"),
    ])
    eng = engine_mod.Engine(_cfg(), llm, sid)

    eng.process_turn("チュロス覚えとる？")

    assert llm.tool_messages_seen[0] == []          # 1回目: tool結果なし
    assert len(llm.tool_messages_seen[1]) == 1      # 2回目: 結果が渡っている
    assert llm.tool_messages_seen[1][0]["tool_call_id"] == "c1"


def test_native_exchange_never_persists_into_messages():
    """設計の要: ツール往復はself.messagesに残らない（最終本文だけ残る）。
    圧縮・復元・ログ・Gemini role交互の全経路を無改修で守るため。"""
    sid = soul_mod.create_soul("履歴清潔テスト", "コア")
    llm = FakeNativeLLM([
        _calling("save_lesson", {"text": "x"}),
        _final("done"),
    ])
    eng = engine_mod.Engine(_cfg(), llm, sid)

    eng.process_turn("やって")

    roles = [m["role"] for m in eng.messages]
    assert "tool" not in roles
    assert all("tool_calls" not in m for m in eng.messages)
    assert roles == ["user", "assistant"]


def test_fence_mode_setting_skips_native():
    sid = soul_mod.create_soul("フェンス強制テスト", "コア")
    llm = FakeNativeLLM([])  # chat_toolsが呼ばれたらpop失敗で落ちる
    eng = engine_mod.Engine(_cfg(tool_mode="fence"), llm, sid)

    result = eng.process_turn("やあ")

    assert result["reply"] == "フェンス経路の返事"


def test_native_unsupported_falls_back_to_fence_same_turn():
    """toolsを拒否されたら同じターンをフェンスでやり直し、以後もフェンス固定。"""
    import llm as llm_mod

    class RejectingLLM(FakeNativeLLM):
        def chat_tools(self, messages, tools, max_tokens=None):
            raise llm_mod.NativeToolsUnsupported("tools not supported")

    sid = soul_mod.create_soul("フォールバックテスト", "コア")
    llm = RejectingLLM([])
    eng = engine_mod.Engine(_cfg(), llm, sid)

    result = eng.process_turn("やあ")

    assert result["reply"] == "フェンス経路の返事"
    assert eng._native_broken is True


def test_native_broken_arguments_reported_not_executed():
    sid = soul_mod.create_soul("壊れ引数テスト", "コア")
    llm = FakeNativeLLM([
        _calling("write_wiki", {"__raw": '{"topic": 壊れ'}),
        _final("ごめん、やり直すじょ"),
    ])
    eng = engine_mod.Engine(_cfg(), llm, sid)

    result = eng.process_turn("書いて")

    bad = [op for op in result["operations"] if not op["ok"]]
    assert bad and "壊れ" in bad[0]["detail"]
    assert soul_mod.read_file(sid, "wiki/壊れ.md") == ""  # 実行されていない


def test_native_fallback_mid_turn_reports_prior_tool_execution():
    """ラウンド1でツール実行成功→ラウンド2でNativeToolsUnsupported→フェンスへ
    切り替わるケース。フェンス側のモデルはネイティブの往復（exchange、ローカル
    変数）を一切見ないので、既に実行済みの分を申し送らないと同じ書き込みを
    繰り返しうる（実機レビュー指摘 2026-08-04）。"""
    import llm as llm_mod

    class PartialFailLLM(FakeNativeLLM):
        def __init__(self, script):
            super().__init__(script)
            self.call_count = 0
            self.fence_system_text = None

        def chat_tools(self, messages, tools, max_tokens=None):
            self.call_count += 1
            if self.call_count == 1:
                return self.script.pop(0)
            raise llm_mod.NativeToolsUnsupported("boom")

        def chat(self, messages, max_tokens=None):
            self.fence_system_text = messages[0]["content"]
            return "フェンス経由で完了"

    sid = soul_mod.create_soul("フォールバック途中失敗テスト", "コア")
    llm = PartialFailLLM([
        _calling("save_lesson", {"text": "二重書き込み防止テスト"}),
    ])
    eng = engine_mod.Engine(_cfg(), llm, sid)

    result = eng.process_turn("これ覚えて")

    assert result["reply"] == "フェンス経由で完了"
    # (a) lessons.mdへの書き込みは1回だけ
    body = soul_mod.read_file(sid, "lessons.md")
    assert body.count("二重書き込み防止テスト") == 1
    # (b) フェンス側system_textに実行済み注記がある
    assert "既に実行済み" in llm.fence_system_text
    assert "save_lesson" in llm.fence_system_text
    # (c) operationsにラウンド1のopが1回だけ入っている
    save_ops = [op for op in result["operations"] if op["op"] == "save_lesson"]
    assert len(save_ops) == 1
    assert save_ops[0]["ok"] is True


def test_native_round_limit_stops_loop():
    import memory_tools
    sid = soul_mod.create_soul("ラウンド上限テスト", "コア")
    endless = [_calling("list_reminders", {})] * (engine_mod.MAX_TOOL_ROUNDS + 5)
    llm = FakeNativeLLM(endless)
    eng = engine_mod.Engine(_cfg(), llm, sid)

    result = eng.process_turn("無限にやって")  # 例外なく返ること

    assert result["reply"]


def test_native_system_prompt_has_note_but_no_fence_spec():
    captured = {}

    class SpyLLM(FakeNativeLLM):
        def chat_tools(self, messages, tools, max_tokens=None):
            captured["system"] = messages[0]["content"]
            return _final("ok")

    sid = soul_mod.create_soul("ネイティブプロンプトテスト", "コア")
    eng = engine_mod.Engine(_cfg(), SpyLLM([]), sid)

    eng.process_turn("やあ")

    assert "『』" in captured["system"]           # 書く規範は残る
    assert "fieria-tool" not in captured["system"]  # フェンス作法は教えない


def test_native_streaming_deltas_reach_on_delta():
    class StreamNativeLLM(FakeNativeLLM):
        def chat_tools_stream(self, messages, tools, max_tokens=None, on_delta=None,
                               should_stop=None):
            result = self.chat_tools(messages, tools, max_tokens)
            if on_delta and result["text"]:
                on_delta(result["text"])
            return result

    sid = soul_mod.create_soul("ネイティブストリームテスト", "コア")
    llm = StreamNativeLLM([_final("流れる返事じょ")])
    eng = engine_mod.Engine(_cfg(), llm, sid)
    deltas = []

    result = eng.process_turn("やあ", on_delta=deltas.append)

    assert result["reply"] == "流れる返事じょ"
    assert deltas == ["流れる返事じょ"]


def test_native_streaming_stop_mid_stream_joins_existing_stop_flow():
    """ストリーム途中（1チャンク目の受信直後）にrequest_stop()相当が起きたとき、
    既存の停止フロー（stopped結果・履歴巻き戻し・AIログ未記録）に合流すること。
    should_stopコールバック経由で打ち切られたLLM側も、そこまでの部分結果を返して
    よい——engine側が_stop_requestedを見て最終的に破棄する（tests/test_engine_
    streaming.pyのchat_stream版と同じ設計）。"""
    class StopMidStreamLLM(FakeNativeLLM):
        def chat_tools_stream(self, messages, tools, max_tokens=None, on_delta=None,
                               should_stop=None):
            emitted = []
            for chunk in ["前半", "この続きは使われないはず"]:
                if should_stop and should_stop():
                    break
                emitted.append(chunk)
                if on_delta:
                    on_delta(chunk)
            return {"text": "".join(emitted), "tool_calls": []}

    sid = soul_mod.create_soul("ネイティブストリーム停止テスト", "コア")
    eng = engine_mod.Engine(_cfg(), StopMidStreamLLM([]), sid)
    before_len = len(eng.messages)
    received = []

    def stop_after_first_chunk(delta):
        received.append(delta)
        eng.request_stop()

    result = eng.process_turn("やあ", on_delta=stop_after_first_chunk)

    assert result["stopped"] is True
    assert result["reply"] == ""
    assert received == ["前半"]  # 2チャンク目は打ち切られて届いていない
    assert len(eng.messages) == before_len
