"""engine.py — 会話エンジン。1ターン = プロンプト合成 → LLM → ツール実行（→必要なら再応答）。
llmオブジェクトは chat(messages, max_tokens=None) -> str を持つこと（Task 5で確認したNikoVoice
llm.pyの実物、またはテストのフェイク）。systemメッセージはself.messagesには混ぜず、
{"role": "system", "content": system_text} を毎ターン合成してmessages先頭に付けて渡す。

FieriA拡張: ネイティブfunction calling。llmオブジェクトがsupports_native_tools=True
（クラス属性）を持ち、cfg側のtool_modeがfence指定でなければ、_process_turn_nativeが
APIネイティブのtoolsパラメータでツールを呼ぶ経路を使う（_use_native_tools参照）。
ツール往復（assistant tool_calls・role:toolメッセージ）はローカル変数exchangeにだけ
積み、self.messagesには最終応答の本文だけを残す——圧縮・復元・ログ・Gemini role交互の
既存経路を無改修で守るための設計上の要（tests/test_engine_native_tools.py参照）。"""
import json
import re

import llm as llm_mod
import memory_tools
import prompt as prompt_mod
import recall as recall_mod
import soul as soul_mod

MAX_TOOL_ROUNDS = 2

# 記憶書き込みツール未使用のままこのターン数が過ぎたら、書き促しの一行を
# システムプロンプトへ注入する（memory_tools.MEMORY_WRITE_TOOLS参照）。
MEMORY_WRITE_NUDGE_TURNS = 10

# 「書いた/保存した」と自称する表現の検知（嘘発見器用・実機FB 2026-07-23）。
# wiki・記憶・メモ等の対象語と保存系動詞の近接を要求し、「昨日書いた小説」の
# ような日常語は拾いにくくする。誤検知しても注入文は穏当なので実害は小さい。
_CLAIM_PATTERN = re.compile(
    r"(wiki|ウィキ|記憶|メモ|ノート|日記|リマインダ|スキル|資料|ツール)[^。\n]{0,25}"
    r"(書き込ん|書いてお|保存し|記録し|残してお|覚えてお|追記し|更新し"
    r"|実行した|実行できた|呼び出した|呼出できた|使った|使えた)")

# ト書き検知（実機FB 2026-08-04）: GPT系モデルが「【ツールを使って記憶に残す】」の
# ような隅付き括弧の地の文（演技）だけを書き、JSONを一切出さないままツールを
# 使ったつもりになる。API側のfunction calling作法に深く訓練されたモデルは、
# 本文内JSON方式を長い応答の中で守り続けられず、ト書きへ退行する。
# 括弧の中にツール系の語（ツール/保存/書き込/検索/記録/読み/追記/呼び出）が
# あるものだけを拾う——記憶の認識論の正当な用法（【推測】【撤回済み 日付】）は
# これらの語を含まないため誤検知しない。
_NARRATED_TOOL_PATTERN = re.compile(
    r"【[^】\n]*(?:ツール|保存|書き込|検索|記録|読み|追記|呼び出)[^】\n]*】")

# 圧縮対象（前半）を要約させるためのシステムプロンプト。日記(wrapup.py)と違い一人称の
# 読み物ではなく、後でLLMが会話を続けるための実務メモとして箇条書きでよい。
COMPACT_PROMPT = """以下は長くなった会話の前半部分。後で会話を続けるために必要な情報を
落とさず要約する: 事実・固有名詞・決定事項・約束・相手の状況・話題の流れ。
一人称視点の丁寧な要約でなく、簡潔な箇条書きでよい。

## 会話（前半）
{log}"""


# 画像1枚あたりの概算トークン加算値。実トークナイザは使わないため厳密な値ではないが、
# 文字数だけの概算だと画像添付分のコストが完全に抜け落ち、圧縮判定が実態より
# 楽観的（圧縮が遅れる）になる。過小評価を避けるため大きめの固定値を足す。
IMAGE_TOKEN_ESTIMATE = 800

# GeminiネイティブPDF添付（mime: application/pdf）は画像1枚(800)と違い、実際には
# 数十ページ分になりうる（read_pdf/gui.send_messageがpagesキーを付与する）。
# Gemini公称の「1ページ≈258トークン」を単価として使う。
PDF_TOKENS_PER_PAGE = 258

# pagesが無いPDF添付（旧データ・ページ数取得に失敗したケース）向けのフォールバック概算。
# b64文字列2000文字あたり概ね1トークン相当という粗い経験則（実トークナイザ不使用のため
# 厳密な根拠は無いが、JPEG/PDFのbase64は日本語テキストよりずっと低密度なので専用の
# 単価を置く）。IMAGE_TOKEN_ESTIMATE(800)を下回らないようにクリップし、画像1枚分の
# 既存見積りより過小評価にはしない。
PDF_B64_CHARS_PER_TOKEN = 2000


def _estimate_attachment_tokens(img):
    """images1件あたりの概算トークン数。application/pdfはpagesキー（read_pdf/
    gui.send_messageが付与）があればページ数×PDF_TOKENS_PER_PAGEで見積もる。
    pagesが無ければb64長からのフォールバック概算（下限IMAGE_TOKEN_ESTIMATE）。
    画像（png/jpeg等）はこれまでどおり固定のIMAGE_TOKEN_ESTIMATE。"""
    if (img.get("mime") or "") != "application/pdf":
        return IMAGE_TOKEN_ESTIMATE
    pages = img.get("pages")
    if isinstance(pages, int) and pages > 0:
        return pages * PDF_TOKENS_PER_PAGE
    b64_len = len(img.get("b64") or "")
    return max(IMAGE_TOKEN_ESTIMATE, b64_len // PDF_B64_CHARS_PER_TOKEN)


class _TurnStopped(Exception):
    """内部シグナル専用（engine.py外には出さない）。ネイティブ/フェンス両ループの
    途中で_stop_requestedを検知したことをprocess_turnの共通except節へ伝える。
    従来のフェンス経路は各チェックポイントで直接returnしていたが、ループ本体を
    _process_turn_native/_process_turn_fenceへ分離した後もprocess_turn側で
    _stopped_result()を1箇所にまとめるための橋渡し。"""


def _status_kind_for_names(names):
    """ツール実行中にon_statusへ渡す種別。優先順位: web_search > 記憶書き込み > 汎用
    （表示の優先順位はprocess_turn従来コメントのとおり——検索は数秒かかるうえ
    「何をしているか」が最も伝わる情報のため最優先で見せる）。フェンス経路・
    ネイティブ経路の両方から使う共通ロジック（callの形が違う＝tool名か"name"キーかが
    異なるだけなので、呼び出し側で名前のリストに正規化してから渡す）。"""
    if "web_search" in names:
        return "web_search"
    if any(n in memory_tools.MEMORY_WRITE_TOOLS for n in names):
        return "memory_write"
    return "tools"


def _estimate_tokens(messages):
    """会話履歴の概算トークン数。

    正確なトークナイザは使わず、日本語主体の会話であることを踏まえた粗い概算。
    日本語はおおむね1文字≒1〜2トークンになりがちだが、英数字・記号混じりの実際の
    会話文を均すと「文字数×0.6」程度が閾値判定用途としては実用的なバランスになる
    （閾値超過の有無を大雑把に判定できれば足り、正確なトークン数は不要なため）。
    画像・PDFつきメッセージは_estimate_attachment_tokensの見積り分を加算する
    （完全に無視すると圧縮判定が過小評価になるため）。
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    total_attachment_tokens = sum(
        _estimate_attachment_tokens(img)
        for m in messages for img in (m.get("images") or []))
    return int(total_chars * 0.6) + total_attachment_tokens


class Engine:
    def __init__(self, cfg, llm, soul_id):
        self.cfg = cfg
        self.llm = llm
        self.soul_id = soul_id
        self.messages = []  # [{"role": "user"|"assistant", "content": str}]
        self._stop_requested = False
        # 記憶の書き促し: 記憶書き込みツールを使わないままターンが続いたら、
        # システム側から一行注入して「残すべきことはないか」を突つく
        # （実機フィードバック 2026-07-23: 促さないと自分から書かない問題への対処。
        # 書く内容・書くかどうかは本人判断のまま——注入は気づきの機会だけを与える）。
        self._turns_since_memory_write = 0
        # 前ターンが「書いたと言いつつ実績ゼロ」だったか（嘘発見器・次ターンで指摘）
        self._unbacked_write_claim = False
        # 前ターンが「【ツールを使う】のようなト書きだけで実呼び出しゼロ」だったか
        # （ト書き検知・次ターンで指摘。_NARRATED_TOOL_PATTERN参照）
        self._narrated_tool_use = False
        # FieriA拡張: ネイティブfunction calling。実行時にAPI側からtoolsパラメータを
        # 拒否された（llm.NativeToolsUnsupported）ら立てる。一度立ったらこの
        # Engineインスタンスは以後ずっとフェンス経路固定（_use_native_tools参照）——
        # 同じエンドポイントに毎ターン拒否されるツール定義を送り直しても無駄なため。
        self._native_broken = False

    def request_stop(self):
        """考え中の応答を止めたい、というUI側からの要求を受け付ける。

        urllibのブロッキング呼び出し（llm.chat）は実行中に強制中断できないため、
        NikoVoiceと同じ「破棄方式」を取る：チェックポイントで停止要求を見つけたら、
        既に得られている応答を捨ててmessagesをターン開始時点まで巻き戻す。
        呼び出し自体を打ち切るわけではない。"""
        self._stop_requested = True

    def restore_today(self, limit):
        """起動時に今日のログをLLMの実会話コンテキスト（self.messages）へ復元する。

        self.messagesが既に非空なら何もしない（二重復元防止）。ログ読込に失敗しても
        例外を投げず空のまま返す（会話を壊さない原則）。あくまでmessagesへの読み込み
        であり、ログファイルへの書き込みは発生しないため、この後process_turnが
        append_logで追記しても二重化はしない。

        「区切り」機能: ログ中に who="break" の行があれば、その最後の1本より後ろの
        エントリだけを復元対象にする（break行自体より前は「もう別の話」として
        切り捨てる——区切りの目的が新セッション起動後も効くようにするため）。
        break行が無ければ従来どおり今日のログ全体が対象。
        """
        if self.messages:
            return
        if not limit:
            return
        try:
            entries = soul_mod.read_today_log(self.soul_id)
        except Exception:
            return
        last_break = -1
        for i, entry in enumerate(entries):
            if entry.get("who") == "break":
                last_break = i
        entries = entries[last_break + 1:]
        role_map = {"user": "user", "ai": "assistant"}
        restored = []
        for entry in entries:
            role = role_map.get(entry.get("who"))
            if role is None:
                continue
            text = entry.get("text", "")
            # process_turnはLLM呼び出し失敗時にmessagesだけロールバックしログは残すため、
            # ログ上に同一role（user, user等）が連続することがある。GeminiLLM.chatは
            # role交互を前提にcontentsを組むため、正規化せず読み込むと実APIへ
            # role連続のcontentsが送られてしまう。ここでmessages側だけ連結して吸収する
            # （ログファイル自体は事実の記録として無加工のまま残す）。
            if restored and restored[-1]["role"] == role:
                restored[-1]["content"] += "\n" + text
            else:
                restored.append({"role": role, "content": text})
        self.messages.extend(restored[-limit:] if limit > 0 else [])

    def reset_context(self):
        """「区切り」機能: LLMへ渡す直近文脈（self.messages）だけを空にする。

        記憶ファイル・ログには一切触れない（区切りは削除ではなくコンテキストの
        リセットのため）。区切りマーカー自体のログ書き込み(soul.append_break)は
        ここでは行わず呼び出し側(gui.Bridge.insert_break)の責務とする——
        reset_contextは「今の会話状態を空にする」だけに専念させ、「区切りを記録として
        残す」というログ側の副作用と責務を分けた方が凝集度が高いと判断した
        （boot()がsoul_mod読み出しをengineを介さず直接行っているのと同じ切り分け方）。
        """
        self.messages = []

    def compact_now(self):
        """手動圧縮（GUIの圧縮ボタン）。自動圧縮(_compact)と同じ処理を、文脈上限の
        閾値と無関係に実行する。停止直後は_stop_requestedがTrueのまま残っている
        （process_turnの冒頭でしかリセットされない）ため、手動実行では明示的に
        下ろしてから呼ぶ——ユーザーがボタンを押した意思の方が新しい。"""
        self._stop_requested = False
        return self._compact()

    def _compact(self):
        """会話履歴（self.messages）の古い前半をLLMによる要約1件に畳んで後半と連結する。

        メッセージ数ベースで前半/後半に単純二分する（境界がassistantの途中になっても
        「前半の一部」として要約に含まれるだけなので問題ない）。要約LLM呼び出しが
        失敗、または空応答なら圧縮せず現状のself.messagesをそのまま維持する
        （会話を壊さない原則。呼び出し元でoperationsに積む・積まないの判断に使う）。

        要約メッセージ（role: user）を後半の先頭と連結する際、後半の先頭がuserだと
        role連続（user, user）になりGeminiLLM.chatのrole交互前提を壊す
        （restore_todayの実機事故コメント参照）。restore_todayと同じ「同roleなら
        内容を\\n連結で1件に統合」する正規化を境界に適用して吸収する。
        """
        if self._stop_requested:
            return None
        if len(self.messages) < 2:
            return None
        split = len(self.messages) // 2
        old_half = self.messages[:split]
        new_half = self.messages[split:]
        log_text = "\n".join(f"{m['role']}: {m['content']}" for m in old_half)
        system_text = COMPACT_PROMPT.format(log=log_text)
        try:
            summary = self.llm.chat(
                [{"role": "system", "content": system_text},
                 {"role": "user", "content": "要約してください。"}],
                max_tokens=1000)
        except Exception:
            return None
        if not (summary or "").strip():
            return None
        summary_msg = {
            "role": "user",
            "content": "（これまでの会話の要約——システムによる圧縮）\n" + summary.strip(),
        }
        merged = [summary_msg]
        for m in new_half:
            if merged[-1]["role"] == m["role"]:
                # 同role連結時、両側のimagesを連結して保持する（片方だけが持つ場合も
                # 保持する）。従来はcontent/roleだけの新dictを作っていたためimagesが
                # 脱落していた（修正前バグ。要約直後の new_half 先頭が summary_msg と
                # 同role(user)のとき必ず発生する）。
                combined_images = (merged[-1].get("images") or []) + (m.get("images") or [])
                new_entry = {"role": merged[-1]["role"],
                             "content": merged[-1]["content"] + "\n" + m["content"]}
                if combined_images:
                    new_entry["images"] = combined_images
                merged[-1] = new_entry
            else:
                # 連結が起きない場合も、mを{"role","content"}だけの新dictへ作り直すと
                # mが持つimagesがそのまま脱落する（修正前バグ。連結の有無に関わらず
                # 全メッセージがこの再構築を通るため）。mのimagesを引き継ぐ。
                new_entry = {"role": m["role"], "content": m["content"]}
                if m.get("images"):
                    new_entry["images"] = m["images"]
                merged.append(new_entry)
        self.messages = merged
        return {"ok": True, "op": "compact",
                "detail": f"会話が長くなったので古い部分を要約に畳んだ（{len(old_half)}件→1件）"}

    def _call_llm(self, system_text, on_delta):
        """FieriA拡張: ストリーミング。system_text + self.messagesをLLMへ渡し、
        全文を返す（戻り値の形は従来のself.llm.chat(...)と同じ）。

        on_deltaがNone、またはllmがchat_stream未実装（hasattrで判定。テストの
        FakeLLM等はchat()しか持たないため、この分岐で従来どおりself.llm.chat()の
        単発呼び出しに落ちる＝完全後方互換）なら、従来どおりself.llm.chat()を
        1回呼ぶだけ。on_delta指定時はself.llm.chat_stream()で差分を受け取るたび
        on_deltaへ渡し（on_delta自体の例外は表示用コールバックの失敗として握り
        つぶし、会話は止めない）、_stop_requestedが立った時点でストリームの
        以降のチャンクを読まずループを打ち切る（呼び出し元のprocess_turnが
        その後_stop_requestedを見て停止処理へ回る。既存のstop機構と同じ「破棄方式」）。
        chat_stream自体が例外を送出した場合はここで握らずそのまま呼び出し元へ
        伝播させる——蓄積済みの部分テキストがあってもターン全体を失敗として扱う
        既存chat()失敗時の処理（履歴ロールバック）に委ねるため。"""
        messages = [{"role": "system", "content": system_text}] + self.messages
        if on_delta is None or not hasattr(self.llm, "chat_stream"):
            return self.llm.chat(messages)
        chunks = []
        for delta in self.llm.chat_stream(messages):
            chunks.append(delta)
            try:
                on_delta(delta)
            except Exception:
                pass
            if self._stop_requested:
                break
        return "".join(chunks)

    def _use_native_tools(self):
        """ネイティブfunction callingを使うかの解決。
        fence指定→使わない / native・auto→LLMクラスが対応していれば使う。
        _native_broken（実行時にtoolsを拒否された）が立っていたら常に使わない。"""
        if self._native_broken:
            return False
        entry = (self.cfg.get("llm") or {}).get("providers", {}).get(
            (self.cfg.get("llm") or {}).get("provider", ""), {})
        mode = (entry.get("tool_mode") or "auto").strip().lower()
        if mode == "fence":
            return False
        return bool(getattr(self.llm, "supports_native_tools", False))

    def _prompt_nudges(self):
        """記憶の書き促し・嘘発見器・ト書き検知の注入文（system_textの追加分）。
        ネイティブ/フェンス両経路で共通——「書く内容の規範」であってツールの
        呼び方の作法ではないため、経路によらず同じ文言を使う（ブリーフの指示どおり
        「そのまま生かす」。_unbacked_write_claim側の文言がfieria-tool記法に触れる点は
        ネイティブ経路では字面が合わないが、ブリーフが要求する「従来どおり」を優先した
        既知の制約——実害は「たまに的外れな言い回しが混ざる」程度に留まる）。"""
        text = ""
        if self._turns_since_memory_write >= MEMORY_WRITE_NUDGE_TURNS:
            text += ("\n\n（システム: しばらく記憶を書いていない。この会話の中に"
                     "残すべき事実・設定・気づきがあれば、応答の中で記憶ツールに書くこと。"
                     "無ければ書かなくてよい）")
        if self._unbacked_write_claim:
            text += ("\n\n（システム: 前の応答で記憶に書いたと言ったが、実際には"
                     "ツールを呼んでいないため何も保存されていない。本当に残すなら、"
                     "今度こそ応答の中にfieria-toolブロックを書くこと）")
        if self._narrated_tool_use:
            text += ("\n\n（システム: 前の応答で「【ツールを使う】」のような"
                     "地の文を書いたが、それではツールは動かない。演技の描写ではなく、"
                     "応答の中に実際のfieria-toolブロックのJSONを書いたときだけ実行される）")
        return text

    def _build_fence_system_text(self, recall_block):
        """フェンス経路用system_textの合成（tools_spec込み）。ネイティブからの
        フォールバック時、および従来のフェンス経路そのものの両方から呼ぶ
        （重複合成を避けるための共通化）。"""
        tools_spec = memory_tools.build_tools_spec(self.cfg, self.soul_id)
        system_text = prompt_mod.build_system_text(
            self.cfg, self.soul_id, tools_spec=tools_spec)
        if recall_block:
            system_text += "\n\n" + recall_block
        return system_text

    def _already_executed_note(self, ops):
        """ネイティブ→フェンスへのフォールバック時、直前のネイティブラウンドで
        既に成功実行済みのツールをフェンス側へ申し送るシステム注記（実機レビュー
        指摘 2026-08-04）。フェンス側のモデルはネイティブの往復（exchange、ローカル
        変数）を一切見ないため、知らせないと同じ書き込みを繰り返しうる
        （例: save_lesson済みなのに気づかず再度save_lesson）。成功したもの
        （op["ok"]がTrue）だけを対象にする——失敗したものは実行されていないため
        「繰り返すな」の対象外（むしろ書き直してほしいケース）。opsが空（何も
        実行されないままtoolsを拒否された）なら空文字列を返す。"""
        done = [op for op in ops if op.get("ok")]
        if not done:
            return ""
        lines = "\n".join(f"- {op.get('op')}: {op.get('detail')}" for op in done)
        return ("\n\n（システム: 直前に以下のツールは既に実行済み。"
                f"同じ書き込みを繰り返さないこと:\n{lines}）")

    def _process_turn_native(self, system_text, operations, on_delta, on_status):
        """ネイティブfunction callingのツールループ。system_textはラウンド間で
        再合成しない（ブリーフの仕様: フェンス経路と違いtools_specを持たないため
        毎ラウンド作り直す理由がない）。ツール往復はexchange（ローカル変数）にだけ
        積み、self.messagesには一切触れない——最終本文だけを呼び出し元(process_turn)
        が従来どおりmessagesへ追加する。

        戻り値: (reply, any_tool_call)。停止要求を検知したら_TurnStoppedを送出する。
        llm.NativeToolsUnsupportedはここで握らずそのまま呼び出し元へ伝播させる
        （フェンスへのフォールバックはprocess_turn側の責務）。

        FieriA拡張: ストリーミング。llmがchat_tools_streamを実装していて
        （hasattrで判定——テストのFakeNativeLLM等は非対応のまま）on_deltaが
        指定されているときだけstream版を使う。無ければ従来どおりchat_toolsの
        一括呼び出し（既存のFakeNativeLLM系テストが変更なしで通り続ける）。"""
        native_tools = memory_tools.build_native_tools(self.cfg, self.soul_id)
        use_stream = on_delta is not None and hasattr(self.llm, "chat_tools_stream")
        exchange = []
        reply = ""
        any_tool_call = False
        for _ in range(MAX_TOOL_ROUNDS + 1):
            messages = [{"role": "system", "content": system_text}] + self.messages + exchange
            if use_stream:
                result = self.llm.chat_tools_stream(
                    messages, native_tools, max_tokens=None, on_delta=on_delta)
            else:
                result = self.llm.chat_tools(messages, native_tools, max_tokens=None)
            if self._stop_requested:
                raise _TurnStopped()
            calls = result.get("tool_calls") or []
            text = result.get("text") or ""
            if not calls:
                reply = text
                break
            any_tool_call = True
            if on_status:
                try:
                    on_status(_status_kind_for_names([c.get("name") for c in calls]))
                except Exception:
                    pass
            round_ops = []
            for c in calls:
                name = c.get("name", "")
                args = c.get("arguments") or {}
                if "__raw" in args:
                    op = {"ok": False, "op": name, "detail": "引数のJSONが壊れていた"}
                else:
                    op = memory_tools.execute(
                        self.soul_id, {"tool": name, **args}, self.cfg)
                operations.append(op)
                round_ops.append(op)
            exchange.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": c.get("id") or "", "type": "function",
                     "function": {"name": c.get("name", ""),
                                  "arguments": json.dumps(
                                      c.get("arguments") or {}, ensure_ascii=False)}}
                    for c in calls
                ],
            })
            for c, op in zip(calls, round_ops):
                exchange.append({"role": "tool", "tool_call_id": c.get("id") or "",
                                  "content": f"{'ok' if op['ok'] else 'error'}: {op['detail']}"})
            reply = text
            if self._stop_requested:
                raise _TurnStopped()
        else:
            # ラウンド上限到達（forがbreakせず尽きた）: 最後のtextを応答にする。
            # 空なら「ツール実行だけで終わった」ことが分かる文言で埋める。
            reply = reply or "（ツール実行だけで応答が尽きた）"
        return reply, any_tool_call

    def _process_turn_fence(self, system_text, recall_block, operations, on_delta, on_status):
        """従来のフェンス（本文内```fieria-tool```JSON）方式のツールループ。
        process_turnから抽出しただけで挙動は無改修——ネイティブ経路と共通化した
        呼び出し元の下で使い回すための切り出し（重複を作らないため）。
        戻り値: (reply, any_tool_call)。停止要求検知時は_TurnStoppedを送出する
        （旧実装のreturn self._stopped_result(...)を、呼び出し元1箇所に畳むため）。"""
        reply = ""
        any_tool_call = False
        for i in range(MAX_TOOL_ROUNDS + 1):
            if i > 0:
                system_text = self._build_fence_system_text(recall_block)
            raw = self._call_llm(system_text, on_delta)
            if self._stop_requested:
                raise _TurnStopped()
            reply, calls = memory_tools.extract_tool_calls(raw)
            any_tool_call = any_tool_call or bool(calls)

            read_results = []
            feedback_images = []
            if calls and on_status:
                names = [c.get("tool") for c in calls if isinstance(c, dict)]
                try:
                    on_status(_status_kind_for_names(names))
                except Exception:
                    pass
            for call in calls:
                if call.get("tool") == "__parse_error__":
                    snippet = call.get("snippet", "")
                    operations.append({
                        "ok": False, "op": "tool_parse",
                        "detail": f"ツールブロックのJSONが壊れていて実行できなかった: {snippet[:60]}…"})
                    read_results.append(
                        "（システム: fieria-toolブロックのJSONが壊れていて実行できなかった。"
                        f"同じ内容を正しいJSON1個のブロックで書き直すこと。壊れていた断片: {snippet}）")
                    continue
                result = memory_tools.execute(self.soul_id, call, self.cfg)
                operations.append(result)
                if call.get("tool") in memory_tools.FEEDBACK_TOOLS and result["ok"]:
                    label = call.get("path") or call.get("query") or call.get("tool")
                    read_results.append(f"[{label}]\n{result['detail']}")
                    feedback_images.extend(result.get("attachments") or [])

            if not read_results:
                break
            self.messages.append({"role": "assistant", "content": raw})
            feedback_msg = {
                "role": "user",
                "content": "（システム: 記憶ツールの結果）\n" + "\n\n".join(read_results)}
            if feedback_images:
                feedback_msg["images"] = feedback_images
            self.messages.append(feedback_msg)
        if self._stop_requested:
            raise _TurnStopped()
        return reply, any_tool_call

    def process_turn(self, user_text, images=None, on_delta=None, on_status=None):
        """user_text: ユーザー発言。images: [{"mime", "b64"}, ...]（省略/None可、後方互換）。
        添付があれば各画像をsoul.save_attachmentでファイル保存し、ログにはb64を含めず
        相対パス参照だけを残す。LLMへ渡すself.messages側のuserメッセージには実画像
        （images）を付けたままにする（記憶ファイルには実体を持たず、参照だけ残す設計）。

        on_delta: FieriA拡張・ストリーミング用コールバック（省略時None＝従来動作）。
        指定するとLLM応答の文字が生成され次第 on_delta(text_delta) が呼ばれる
        （_call_llm参照）。

        FieriA拡張: ネイティブfunction calling。_use_native_toolsがTrueなら
        _process_turn_nativeへ委譲する（system_textはtools_spec無し＋
        NATIVE_SPEC_NOTE）。ネイティブ経路がllm.NativeToolsUnsupportedを送出したら
        self._native_broken=Trueを立て、userメッセージは再追加・再ログせずに
        （既に下でこの関数内で1回だけappend済み）フェンス経路で同じターンを
        やり直す——「ユーザー発言・履歴を失わない、二重append_logもしない」という
        ブリーフの要求を、userメッセージ追加をパス分岐より前に1回だけ行う構造で満たす。"""
        self._stop_requested = False
        operations = []

        # 今回の注入対象リマインダをターン開始時点で確定させる（soul.due_remindersは
        # prompt.build_system_textも内部で同じ関数を呼んでいるので、ここで別途
        # 計算しても二重実装にはならない）。発火記録（mark_reminder_fired）は
        # ターンが最後まで成功したときだけ行う——途中で例外/停止になったら未発火の
        # ままにして、次ターンで再度注入されるようにする（安全側）。
        due_reminders_at_start = soul_mod.due_reminders(self.soul_id)

        use_native = self._use_native_tools()

        # 連想記憶（auto-recall）: user_textから既存のFTS検索(search.py)で関連記憶を
        # 裏引きしてsystem_text末尾に注入する。ここで1回だけ検索し、フェンス経路の
        # ツールループ再構築（_process_turn_fence内、i>0）では同じ recall_block を
        # 使い回す——無駄な再検索とターン内での注入内容の揺れを防ぐため。失敗・0件時は
        # recall_blockが空文字列になるだけで会話は壊れない（recall.build_recall_block参照）。
        recall_block = recall_mod.build_recall_block(self.cfg, self.soul_id, user_text)

        # system_textはmessagesと一緒に毎回LLMへ送られる実体なので、圧縮の閾値判定にも
        # 含める（messagesだけの概算だと閾値超過を見逃す）。ここで組んだものは後段の
        # ツールループ1周目にそのまま使い回す（毎ターン同じ引数で合成するので無駄がない。
        # フェンス経路の2周目以降は既存どおりループ内で再合成する——_build_fence_system_text
        # を使い回す。ネイティブ経路はラウンド間で再合成しない——tools_specを持たない
        # ため作り直す理由がない）。
        if use_native:
            system_text = prompt_mod.build_system_text(self.cfg, self.soul_id, tools_spec="")
            system_text += "\n\n" + memory_tools.NATIVE_SPEC_NOTE
            if recall_block:
                system_text += "\n\n" + recall_block
        else:
            system_text = self._build_fence_system_text(recall_block)
        # 記憶の書き促し・嘘発見器・ト書き検知（実機FB 2026-07-23/2026-08-04）。
        # ネイティブ/フェンス両経路で共通の注入文（_prompt_nudges参照）。
        system_text += self._prompt_nudges()

        # 圧縮判定はuserメッセージ追加前・start_len記録前に完結させる。圧縮は正当な
        # 状態変更であり、この後の例外/停止時ロールバック（start_len基準）の対象外とする。
        context_limit = self.cfg.get("context_limit_tokens", 0)
        if context_limit and _estimate_tokens(
                [{"content": system_text}] + self.messages) > context_limit:
            # 閾値判定〜圧縮着手の間に停止要求が来ていたら圧縮せず即座に停止フローへ。
            if self._stop_requested:
                return self._stopped_result(len(self.messages), operations,
                                             recall_used=bool(recall_block))
            compact_result = self._compact()
            if compact_result:
                operations.append(compact_result)
            # 要約LLM呼び出し中（ブロッキングI/O）に停止要求が来ていた場合、圧縮自体は
            # （成功していれば）正当な状態変更として維持しoperationsのcompactも残すが、
            # 本チャットは呼ばずにそのまま停止処理へ回す。
            if self._stop_requested:
                return self._stopped_result(len(self.messages), operations,
                                             recall_used=bool(recall_block))

        start_len = len(self.messages)
        log_text = user_text
        if images:
            for img in images:
                try:
                    rel_path = soul_mod.save_attachment(
                        self.soul_id, img.get("mime", ""), img.get("b64", ""))
                    log_text += f"\n[画像: {rel_path}]"
                except Exception as e:
                    operations.append({"ok": False, "op": "save_attachment",
                                        "detail": f"画像保存失敗: {e}"})
        try:
            soul_mod.append_log(self.soul_id, "user", log_text)
        except Exception as e:
            operations.append({"ok": False, "op": "append_log",
                                "detail": f"ログ保存失敗: {e}"})
        user_msg = {"role": "user", "content": user_text}
        if images:
            user_msg["images"] = images
        self.messages.append(user_msg)

        reply = ""
        # このターン中に（壊れたJSON含め）ツール呼び出しの試みが1回でもあったか。
        # ト書き検知の条件に使う——本物の呼び出しや壊れJSON（＝書こうとはした）が
        # あるターンは演技扱いしない（保守側。壊れJSONは__parse_error__の差し戻しが担当）。
        any_tool_call = False
        try:
            if use_native:
                # フォールバック時、ネイティブ側で既に実行「済み」のツールを
                # フェンス側へ申し送るため、ネイティブ実行前の時点のoperations件数を
                # 覚えておく（差分＝ネイティブが積んだ分。実機レビュー指摘2026-08-04:
                # ラウンド1で成功後ラウンド2がtools拒否されると、フェンスは何も知らずに
                # 同じ書き込みを繰り返しうる）。
                pre_native_ops_len = len(operations)
                try:
                    reply, any_tool_call = self._process_turn_native(
                        system_text, operations, on_delta, on_status)
                except llm_mod.NativeToolsUnsupported:
                    # エンドポイントがtoolsパラメータを拒否した。ネイティブ経路の
                    # ツール往復はexchange（ローカル変数）にしか積んでいないので
                    # self.messagesはuserメッセージ追加時点のまま——巻き戻し不要で
                    # そのままフェンス経路へやり直せる。以後このEngineは常時フェンス。
                    # ここで使うsystem_textはネイティブ用（tools_spec無し）だったため、
                    # フェンス用（tools_spec込み）に組み直す必要がある。operations自体は
                    # ネイティブ実行分をそのまま残す（実際に発生した副作用は表示に残す
                    # のが正——ブリーフの既定挙動）。
                    self._native_broken = True
                    already_done = operations[pre_native_ops_len:]
                    system_text = self._build_fence_system_text(recall_block)
                    system_text += self._prompt_nudges()
                    system_text += self._already_executed_note(already_done)
                    reply, any_tool_call = self._process_turn_fence(
                        system_text, recall_block, operations, on_delta, on_status)
            else:
                reply, any_tool_call = self._process_turn_fence(
                    system_text, recall_block, operations, on_delta, on_status)
        except _TurnStopped:
            return self._stopped_result(start_len, operations,
                                         recall_used=bool(recall_block))
        except Exception:
            # 応答のない孤立userメッセージ等で履歴を壊さないよう、開始時点まで巻き戻してから再送出する。
            # エラー表示は呼び出し元（将来のgui.py）の責務。
            del self.messages[start_len:]
            raise

        self.messages.append({"role": "assistant", "content": reply})
        try:
            soul_mod.append_log(self.soul_id, "ai", reply)
        except Exception as e:
            operations.append({"ok": False, "op": "append_log",
                                "detail": f"ログ保存失敗: {e}"})
        wrote_memory = any(
            op.get("ok") and op.get("op") in memory_tools.MEMORY_WRITE_TOOLS
            for op in operations)
        self._turns_since_memory_write = 0 if wrote_memory else (
            self._turns_since_memory_write + 1)
        # 嘘発見器（実機FB 2026-07-23: 「書いたよ」と言いながらツールを呼ばない）:
        # 応答本文が書き込みを自称しているのに実績ゼロのターンを検知し、次ターンの
        # システムプロンプトで指摘する（_CLAIM_PATTERNは控えめに——誤検知しても
        # 注入文は穏当なので実害は小さいが、日常語の「昨日書いた」等は拾わない形にする）。
        self._unbacked_write_claim = bool(
            not wrote_memory and _CLAIM_PATTERN.search(reply))
        # ト書き検知（実機FB 2026-08-04）: ターン全体でツール呼び出しの試みがゼロ
        # なのに「【ツールを使う】」型の地の文がある＝演じただけ。次ターンで指摘する。
        self._narrated_tool_use = bool(
            not any_tool_call and _NARRATED_TOOL_PATTERN.search(reply))
        for r in due_reminders_at_start:
            try:
                soul_mod.mark_reminder_fired(self.soul_id, r["id"])
            except Exception:
                pass
        return {"reply": reply, "operations": operations, "stopped": False,
                "recall_used": bool(recall_block), "history_len": len(self.messages)}

    def _stopped_result(self, start_len, operations, recall_used=False):
        """停止要求を検知した際の共通の巻き戻し処理。userの発言自体はappend_log済み・
        messagesにも追加済みのままログ上には残す（発言した事実は残る）が、
        AIの応答は破棄しAIログも書かない（NikoVoice実績の破棄方式）。
        recall_usedは呼び出し元がそのターンのrecall_blockの有無を渡す
        （停止してもrecallブロック自体は組み立て済みのため、ペットUIのrecall演出
        判定に使える値をそのまま伝える）。"""
        del self.messages[start_len:]
        return {"reply": "", "operations": operations, "stopped": True,
                "recall_used": recall_used, "history_len": len(self.messages)}
