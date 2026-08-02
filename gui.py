"""gui.py — pywebviewブリッジ。ロジックは持たない（各モジュールへの配線だけ）。
公開属性は最小・内部参照は _ 名（pywebview再帰走査対策、NikoVoice設計判断7番）。
evaluate_jsはページロード完了後のみ（同8番）。"""
import copy
import datetime
import json
import os
import stat
import threading
import time
import zipfile

import webview

import config as config_mod
import importer
import memory_tools
import prompt as prompt_mod
import roles as roles_mod
import soul as soul_mod
import wrapup as wrapup_mod
from engine import Engine
from env import delete_key, load_env, set_key
from llm import REASONING_EFFORTS, build_provider, create_llm
from scheduler import JOBS, Scheduler


# ui/index.html PDF_NATIVE_MAX_BYTESと同じ上限（Geminiネイティブ直読みのraw上限）。
# JS側の検査はクライアント側の親切機能に過ぎず、細工されたリクエストは素通りできて
# しまうため、サーバ側（Bridge.send_message）でも同じ上限を検査する。
PDF_NATIVE_MAX_BYTES = 14 * 1024 * 1024

# ペット（ドット絵マスコット）が取りうる状態名。souls/<id>/pet/配下のskin PNGは
# この名前+".png"のときだけ拾う（それ以外のファイル名は無視）。
PET_STATES = ("idle", "thinking", "writing", "recall", "error", "love")
# skin PNG 1枚あたりの上限。APIキー等の秘匿ファイルではないが、任意の巨大PNGを
# base64化してpywebviewブリッジ越しに渡すとUIスレッドを詰まらせうるための保険。
PET_SKIN_MAX_BYTES = 200 * 1024

# FieriA拡張: ストリーミング。chat_streamの差分をevaluate_jsで1文字ずつ叩くと
# pywebviewのIPCが暴れるため、文字数/経過時間のどちらか条件を満たすまでPython側で
# 軽くバッファしてからまとめて送る（Bridge._push_stream_delta参照）。
STREAM_FLUSH_CHARS = 40
STREAM_FLUSH_SECONDS = 0.08


def _oversized_pdf_error(images):
    """imagesのうちapplication/pdfエントリがGemini直読みの14MB上限を超えていないか
    検査する。b64文字列長×3/4（base64のデコード後サイズの概算式）でraw byte数を
    見積もる（実際にb64decodeしない軽量版。厳密なデコードはこの後どのみち
    memory_tools/engine側で必要になった時点で行われる）。超過エントリがあれば
    エラー文言を返し、無ければNoneを返す（imagesがNone/空でもNone）。
    画像（image/*）は既存のJS側上限（5MB）のみで、サーバ側の対応上限はまだ無い
    ——今回はPDFのみをスコープにする。"""
    for img in images or []:
        if (img or {}).get("mime") != "application/pdf":
            continue
        b64 = img.get("b64") or ""
        approx_bytes = len(b64) * 3 // 4
        if approx_bytes > PDF_NATIVE_MAX_BYTES:
            return "PDFが大きすぎる（Gemini直読みは14MBまで）"
    return None


def _attach_pdf_page_counts(images):
    """UI経由のネイティブPDF添付（ui/index.html addPdfNative、mime:"application/pdf"の
    ままb64）にpagesキーを付与したコピーを返す。engine._estimate_attachment_tokensが
    固定800トークンではなく実ページ数ベースで見積もれるようにするため
    （memory_tools._execute_read_pdfのGemini分岐と同じ理屈だが、UIからの添付は
    Python側で後付けする必要がある）。既にpagesを持つエントリ・pdf以外はそのまま。
    ページ数が取れない（壊れたPDF・不正なb64等）場合はpagesキー無しのまま
    フォールバック概算に任せ、例外は握って会話を止めない。imagesがNone/空ならそのまま返す。
    """
    if not images:
        return images
    out = []
    for img in images:
        if (img or {}).get("mime") == "application/pdf" and "pages" not in img:
            img = dict(img)
            try:
                import base64 as base64_mod

                import pdf_render
                raw = base64_mod.b64decode(img.get("b64", ""))
                img["pages"] = pdf_render.count_pages(raw)
            except Exception:
                pass
        out.append(img)
    return out


def sanitize_llm_cfg(llm_cfg):
    """llm設定をJS側へ渡す前にapi_key直書きを除去したコピーを返す。

    env.resolve_api_key() はproviders内の"api_key"直書きを許す仕様のため、
    configにそれが書かれていた場合でも get_settings() 経由で生キーが
    漏れないようにする防御。元のcfgは変更しない。
    """
    sanitized = copy.deepcopy(llm_cfg)
    providers = sanitized.get("providers", {})
    for entry in providers.values():
        entry.pop("api_key", None)
    return sanitized


def _is_unsafe_zip_member(base_dir, member_name):
    """import_backupのZip Slip防御。zip内エントリ名(member_name)が、展開先base_dirの
    外へ正規化解決されるかを判定する（soul._safe_pathと同じ「base+os.sep」比較方式）。
    細工されたzipは"/"だけでなく"\\"区切り・絶対パス・ドライブレター(C:\\...)を
    使ってくる可能性があるため、比較前に"\\"を"/"へ寄せてから判定する。
    zipエントリ名は常に相対パスのはずなので、コロンが含まれる時点で不正とみなす
    （workspace._safe_pathと同じ理由：NTFS代替データストリーム
    "souls/x.md:ads"はここで拒否しないと拡張子・パス階層チェックを素通りしつつ
    実体は隠しADSストリームとして書き込まれてしまう。ドライブレター形式
    "C:/..."での脱出も同時に塞ぐ）。"""
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if ":" in normalized:
        return True
    base_abs = os.path.abspath(base_dir)
    full_abs = os.path.abspath(os.path.join(base_abs, normalized))
    return not (full_abs == base_abs or full_abs.startswith(base_abs + os.sep))


def _is_symlink_zip_entry(info):
    """zipfile.ZipInfoがUnix symlinkエントリかどうか判定する。symlinkは実体を
    展開先の外に置けてしまう（リンク先はzip内容と無関係な任意パスになり得る）ため、
    import_backupはこれを検出したら即座に復元全体を中止する。"""
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


class Bridge:
    def __init__(self):
        self._cfg = config_mod.load_config()
        self._env = load_env()
        self._llm = None
        self._engine = None
        self._ensure_engine()
        # FieriA拡張: ストリーミング。pywebviewウィンドウ参照はrun()がcreate_window後に
        # セットする（__init__の時点ではまだ存在しない）。未設定（テスト実行時含む）なら
        # _push_stream_deltaはevaluate_jsを呼ばず何もしない＝on_delta=None相当の従来動作。
        self._window = None
        self._stream_buf = ""
        self._stream_last_flush = 0.0
        # 終了確認ダイアログ（window.events.closing）用: チャット応答中(send_message)の
        # 呼び出し件数カウンタ。0より大きい間は「応答中」とみなす。pywebviewは各API
        # 呼び出しを別スレッドで並行して走らせるため、複数ターンが理論上重なりうる
        # ことを考慮してロックで保護する（単純な+=1/-=1でもGIL上はほぼ安全だが、
        # 「0より大きいか」の判定を含む複合操作なのでロックで確実にする）。
        self._busy_lock = threading.Lock()
        self._busy_turns = 0
        self._scheduler = Scheduler()
        # テスト実行中はstartしない。get_scheduled_jobs等の可視化のため
        # Schedulerインスタンス自体は生成するが、daemonスレッドは起動しない
        # ＝60秒の時限レース（テストが60秒未満で終わることに依存した安全策）
        # をやめ、実LLM到達を構造的に遮断する。
        if not os.environ.get("FIERIA_TESTING"):
            self._scheduler.start(
                lambda: (self._cfg, self._llm, self._cfg.get("active_soul"))
            )
        self._importing = False
        self._import_stop = False

    def _ensure_engine(self):
        if not self._cfg.get("active_soul"):
            souls = soul_mod.list_souls()
            if souls:
                self._cfg["active_soul"] = souls[0]["id"]
                config_mod.save_config(self._cfg)
        if self._cfg.get("active_soul"):
            self._llm = create_llm(self._cfg["llm"], self._env)
            self._engine = Engine(self._cfg, self._llm, self._cfg["active_soul"])
            restore_turns = self._cfg.get("restore_turns", 50)
            if restore_turns:
                self._engine.restore_today(restore_turns)

    def _llm_summary(self):
        """現在のLLMプロバイダ/モデル/推論エフォートのサマリー（画面ヘッダー表示用）。
        providerがproviders configに存在しない等の異常時も例外にせず空文字で埋めて返す。"""
        empty = {"provider": "", "label": "", "model": "", "reasoning_effort": ""}
        llm_cfg = self._cfg.get("llm", {})
        provider = llm_cfg.get("provider", "")
        entry = llm_cfg.get("providers", {}).get(provider)
        if not provider or not entry:
            return empty
        return {
            "provider": provider,
            "label": config_mod.PROVIDER_LABELS.get(provider, provider),
            "model": entry.get("model", ""),
            "reasoning_effort": entry.get("reasoning_effort", ""),
        }

    # --- 起動・会話 ---
    def boot(self):
        active_soul = self._cfg.get("active_soul")
        return {
            "souls": soul_mod.list_souls(),
            "roles": roles_mod.list_roles(),
            "active_soul": active_soul,
            "active_role": self._cfg.get("active_role"),
            "fact_layer": self._cfg.get("fact_layer"),
            "theme": self._cfg.get("theme"),
            "pet_enabled": self._cfg.get("pet_enabled", True),
            "pet_size": self._cfg.get("pet_size", 64),
            "pet_character": self._cfg.get("pet_character", "konoha"),
            "pet_pos": self._cfg.get("pet_pos", {"right": 20, "bottom": 92}),
            "reply_se": self._cfg.get("reply_se", "se-poko.mp3"),
            # 今日の会話ログ（表示専用の復元用）。engine.messagesには入れない＝
            # LLMへ渡す会話コンテキストは増やさない（設計判断: 今日の発言量が多いほど
            # APIコストが増えるトレードオフを許容するかは未確定のため、画面表示のみに留める）。
            "today_log": soul_mod.read_today_log(active_soul) if active_soul else [],
            "llm_summary": self._llm_summary(),
        }

    def send_message(self, text, images=None):
        if self._importing:
            return {"error": "インポート処理中は会話できない（完了かキャンセルを待って）"}
        if not self._engine:
            return {"error": "SOULが未作成。設定からSOULを作ってください"}
        oversized = _oversized_pdf_error(images)
        if oversized:
            return {"error": oversized}
        images = _attach_pdf_page_counts(images)
        # FieriA拡張: ストリーミング。ウィンドウ未設定（テスト実行時）または設定で
        # OFFにされていればon_delta=Noneのまま渡し、engine側は従来どおりchat()の
        # 単発呼び出しに落ちる（後方互換）。
        self._stream_buf = ""
        self._stream_last_flush = time.monotonic()
        on_delta = (self._push_stream_delta
                    if self._window and self._cfg.get("streaming", True) else None)
        # ツール実行状態の通知（記憶書き込み中など）はストリーミング設定と無関係に
        # ウィンドウがあれば送る（表示専用・失敗は握る）。
        on_status = self._push_turn_status if self._window else None
        with self._busy_lock:
            # 冒頭の_importingチェック後にインポートが開始された可能性があるため、
            # ターン開始を確定する直前にロック下で再チェックする（start_import側と対）。
            if self._importing:
                return {"error": "インポート処理中は会話できない（完了かキャンセルを待って）"}
            self._busy_turns += 1
        try:
            result = self._engine.process_turn(text, images=images, on_delta=on_delta,
                                                on_status=on_status)
            # AIがswitch_roleツールで自分から切り替えた場合、応答受信直後にUIの
            # ロールバー表示を更新できるよう、いまのactive_roleを一緒に返す
            # （cfgはengine.cfgと同一参照なので、ツール実行直後の最新値が入る）。
            result["active_role"] = self._cfg.get("active_role")
            return result
        except Exception as e:
            return {"error": f"LLM呼び出し失敗: {e}"}
        finally:
            # バッファに残った端数（40文字/80ms未満で溜まったまま）を確実に吐き出す。
            # 成功・エラー・例外いずれの経路でも、UI側のストリーミングバブルへ
            # 送りそびれた文字が残らないようにするため。
            self._flush_stream_buf()
            # 例外経路でもis_llm_busy()が「応答中のまま」に張り付かないよう、
            # インクリメントと対になるデクリメントは必ずfinallyで行う。
            with self._busy_lock:
                self._busy_turns -= 1

    def is_llm_busy(self):
        """終了確認ダイアログ(on_closing)用: チャット応答中、またはスケジューラの
        定期ジョブ実行中ならTrue。どちらもLLM呼び出しを伴い、途中で打ち切ると
        記憶書き込みが中途半端になりうる処理のため。"""
        with self._busy_lock:
            chat_busy = self._busy_turns > 0
        return chat_busy or self._scheduler.is_running_job()

    def _push_turn_status(self, kind):
        """ツール実行状態（"memory_write"/"tools"）をJS側へ通知する（表示専用）。
        ストリームバッファに未送信の端数があれば先に吐き出して表示順を守る。"""
        try:
            self._flush_stream_buf()
            self._window.evaluate_js(
                "window.onTurnStatus && window.onTurnStatus(" + json.dumps(kind) + ")")
        except Exception:
            pass

    def _push_stream_delta(self, text):
        """FieriA拡張: ストリーミング。chat_streamの差分1個をバッファへ積み、
        文字数(STREAM_FLUSH_CHARS)または経過時間(STREAM_FLUSH_SECONDS)の
        どちらかの条件を満たしたらまとめてJSへ送る（engine.Engine._call_llmから
        差分ごとに呼ばれるコールバック）。"""
        self._stream_buf += text
        now = time.monotonic()
        if (len(self._stream_buf) < STREAM_FLUSH_CHARS
                and (now - self._stream_last_flush) < STREAM_FLUSH_SECONDS):
            return
        self._flush_stream_buf()

    def _flush_stream_buf(self):
        """_stream_bufに溜まった分をwindow.onStreamDelta(text)としてJS側へ送る。
        ウィンドウ未設定ならバッファだけクリアして何もしない。evaluate_js自体の
        失敗（ウィンドウのクローズ処理中等）は表示だけの問題として握りつぶす
        （gui.py冒頭のコメント: evaluate_jsはページロード完了後のみ、の原則どおり
        send_message経由でしか呼ばれないため通常は失敗しない想定だが、保険として握る）。"""
        if not self._stream_buf:
            return
        text = self._stream_buf
        self._stream_buf = ""
        self._stream_last_flush = time.monotonic()
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                "window.onStreamDelta && window.onStreamDelta(" + json.dumps(text) + ")")
        except Exception:
            pass

    def stop_turn(self):
        """考え中の応答を止める。pywebviewは各API呼び出しを別スレッドで走らせるため、
        send_message実行中でもこの呼び出しは並行して届く。engineが無ければ何もしない。"""
        if not self._engine:
            return {"ok": False}
        self._engine.request_stop()
        return {"ok": True}

    def insert_break(self):
        """「区切り」機能: LLMへ渡す直近文脈だけをリセットする（記憶・ログは消えない。
        FieriAは並行スレッドを持たない単線の関係——という設計思想の上で、人間の
        「じゃ、別の話なんだけど」に相当する区切りを入れる）。

        soul.append_breakでログへマーカーを1本追記してからengine.reset_context()で
        messagesを空にする。マーカー書き込みをここ(gui)側の責務にしたのは、
        reset_context自体は「今の会話状態を空にする」ことだけに専念させたい
        （engine.reset_contextのコメント参照）ため。

        送信中（Bridge.send_message実行中）の呼び出しをサーバ側で厳密に排他する
        手段は無い（Engineに「応答生成中」を示す状態フラグが存在せず、
        stop_turnの実装コメントの通りpywebviewは各API呼び出しを別スレッドで
        並行して走らせる）。ここでは追加のロックを設けず、UI側(index.html)で
        送信中は区切りボタンをdisableすることで実用上十分な誤爆防止とする
        （割り切り。理論上は競合しうるが、区切りは「ログに残るだけ」の非破壊操作
        なので最悪でも壊れるのは表示上の一貫性だけで、データが失われることはない）。
        """
        if not self._engine:
            return {"ok": False}
        soul_mod.append_break(self._cfg["active_soul"])
        self._engine.reset_context()
        return {"ok": True}

    # --- PDF添付（ページを画像化してvision添付フローに乗せる） ---
    def pdf_native_supported(self):
        """現在選択中のLLMプロバイダがPDFをネイティブに読める（Gemini）かどうか。
        UI側がこれを見て、PDFをrender_pdf（画像化）に回すか、b64のまま
        application/pdfとして添付リストに乗せるかを分岐する（フォールバック
        プロバイダは考慮しない。フォールバック先が非Geminiだとネイティブ添付を
        含むターンは失敗しうるが、動的変換はスコープ外として許容する）。"""
        llm_cfg = self._cfg.get("llm", {})
        providers = llm_cfg.get("providers", {})
        entry = providers.get(llm_cfg.get("provider", ""), {})
        return entry.get("type") == "gemini"

    def render_pdf(self, b64, max_pages=20):
        """PDFのb64（UI側の既存画像添付と同じFileReader経路で取得したもの）を
        先頭からmax_pages（既定20）ページまで画像化し、既存のimages添付
        （[{"mime","b64"}...]、engine.process_turnがそのまま受け取れる形）に
        変換して返す。テキスト抽出ではなくページ全体を画像にする＝レイアウト・
        図表込みでvision LLMに見せる、という要件のため。実際のレンダリングは
        pdf_render.render_pdf_pages（memory_tools.read_pdfツールとの共通処理）。

        壊れたPDF・暗号化PDF・不正なb64はすべて例外として握り、
        {"ok": False, "error": ...}で返す（UI側に生の例外を漏らさない。
        import_backup等の既存パターンを踏襲）。
        """
        try:
            import base64
            import pdf_render

            raw = base64.b64decode(b64, validate=True)
            result = pdf_render.render_pdf_pages(raw, max_pages=max_pages)
            return {"ok": True, **result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- SOUL ---
    def create_soul(self, name, identity_text, speech_style="", inherit_user_from=None):
        """新規SOULを作る。inherit_user_from（既存soul_id）を指定すると、そのSOULの
        user.md（相手理解）を1回だけコピーして引き継ぐ（soul_mod.create_soul参照）。
        省略時はNoneのまま渡す＝従来どおり空のuser.mdで始まる（後方互換）。
        _soul_existsで先に存在確認する：soul_mod.create_soulもos.path.isdirで
        検証するが、ここで先に弾いておけば無効なidでも新SOULのディレクトリ作成自体を
        試みずにエラーを返せる（ユーザー向けエラーメッセージも付けられる）。"""
        if inherit_user_from and not self._soul_exists(inherit_user_from):
            return {"error": "引き継ぎ元のSOULが見つからない", **self.boot()}
        sid = soul_mod.create_soul(name, identity_text, speech_style,
                                    inherit_user_from=inherit_user_from)
        return self.switch_soul(sid)

    def get_soul_identity(self, soul_id):
        if not self._soul_exists(soul_id):
            return {"core": "", "speech_style": "", "name": "", "error": "SOULが見つからない"}
        parts = soul_mod.read_identity_parts(soul_id)
        # 生の名前（空文字を許す）を足す。list_soulsのname（UNNAMED_LABELフォールバック
        # 済み）を編集フォームの初期値に使うと、未改名のまま保存した時にフォールバック
        # 文言そのものがname.txtへ書き込まれてしまうため、ここは必ずread_name（生値）を使う。
        parts["name"] = soul_mod.read_name(soul_id)
        return parts

    def update_soul_identity(self, soul_id, identity_text, speech_style):
        if not self._soul_exists(soul_id):
            return {"ok": False, "error": "SOULが見つからない"}
        soul_mod.update_identity(soul_id, identity_text, speech_style)
        return {"ok": True}

    def rename_soul(self, soul_id, name):
        """SOULの表示名（name.txt）だけを変更する。フォルダ名（soul_id）は変えない——
        ログ・添付・索引の相対パスはsoul_id基準のディレクトリ構造に書かれているため、
        フォルダ名を変えるとそれらのパスが壊れる。あくまで表示名のみの変更。"""
        if not self._soul_exists(soul_id):
            return {"ok": False, "error": "SOULが見つからない"}
        ok, result = soul_mod.validate_name(name)
        if not ok:
            return {"ok": False, "error": result}
        soul_mod.set_name(soul_id, result)
        return {"ok": True, "name": result}

    def _soul_exists(self, soul_id):
        """soul_idが実在のSOULか検証する。存在しないid（タイポ・削除済み・
        トラバーサル文字列等）を弾き、read_identity_parts/update_identityに
        渡さないための入口ガード。list_souls()はSOULS_DIR直下の実ディレクトリ名
        しか返さないため、".."等を含む文字列はそもそも一致しようがない。"""
        return soul_id in [s["id"] for s in soul_mod.list_souls()]

    def switch_soul(self, soul_id):
        if not self._soul_exists(soul_id):
            return {"error": "SOULが見つからない", **self.boot()}
        self._maybe_wrapup()
        self._cfg["active_soul"] = soul_id
        config_mod.save_config(self._cfg)
        self._ensure_engine()
        return self.boot()

    # --- ロール ---
    def switch_role(self, name):
        self._cfg["active_role"] = name or None
        config_mod.save_config(self._cfg)
        return {"active_role": self._cfg["active_role"]}

    def save_role(self, name, prompt):
        roles_mod.save_role(name, prompt)
        return {"roles": roles_mod.list_roles()}

    def delete_role(self, name):
        roles_mod.delete_role(name)
        return {"roles": roles_mod.list_roles()}

    # --- 設定 ---
    def get_settings(self):
        return {
            "llm": sanitize_llm_cfg(self._cfg["llm"]),
            "fact_layer": self._cfg["fact_layer"],
            "default_fact_text": prompt_mod.DEFAULT_FACT_TEXT,
            "theme": self._cfg.get("theme"),
            "provider_labels": config_mod.PROVIDER_LABELS,
            "wrapup_max_tokens": self._cfg.get("wrapup_max_tokens", 2000),
            "context_limit_tokens": self._cfg.get("context_limit_tokens", 0),
            "workspace_dir": self._cfg.get("workspace_dir", ""),
            "auto_role_switch": self._cfg.get("auto_role_switch", True),
            "auto_recall": self._cfg.get("auto_recall", {"enabled": True, "max_hits": 3}),
            "skill_auto_create": self._cfg.get("skill_auto_create", False),
            "pet_enabled": self._cfg.get("pet_enabled", True),
            "pet_size": self._cfg.get("pet_size", 64),
            "pet_character": self._cfg.get("pet_character", "konoha"),
            "streaming": self._cfg.get("streaming", True),
            "reply_se": self._cfg.get("reply_se", "se-poko.mp3"),
            "ime_auto_ja": self._cfg.get("ime_auto_ja", True),
        }

    def save_settings(self, payload):
        if "fact_layer" in payload:
            self._cfg["fact_layer"] = payload["fact_layer"]
        if "llm" in payload:
            self._cfg["llm"] = payload["llm"]
            # FieriA拡張: Web検索。llm全体をJSから丸ごと差し替える既存パターンに乗るが、
            # 型はJS側の保証に委ねず（auto_role_switch等と同様）ここで確定させる。
            self._cfg["llm"]["web_search"] = bool(self._cfg["llm"].get("web_search", False))
            # 推論エフォート: プロバイダentryごと。GUI/JS由来の値をここで確定させる
            for entry in self._cfg["llm"].get("providers", {}).values():
                if isinstance(entry, dict):
                    val = str(entry.get("reasoning_effort", "") or "").strip().lower()
                    entry["reasoning_effort"] = val if val in REASONING_EFFORTS else ""
        if "theme" in payload:
            # pet_character等と同じホワイトリスト正規化。未知の値（改ざん・旧設定の
            # 残骸等）は既定の"light"へ倒す（config_mod.THEME_IDSが正）。
            val = payload["theme"]
            self._cfg["theme"] = val if val in config_mod.THEME_IDS else "light"
        if "wrapup_max_tokens" in payload:
            self._cfg["wrapup_max_tokens"] = payload["wrapup_max_tokens"]
        if "context_limit_tokens" in payload:
            self._cfg["context_limit_tokens"] = payload["context_limit_tokens"]
        if "workspace_dir" in payload:
            self._cfg["workspace_dir"] = payload["workspace_dir"]
        if "auto_role_switch" in payload:
            self._cfg["auto_role_switch"] = bool(payload["auto_role_switch"])
        if "auto_recall" in payload:
            # auto_role_switchと同様、GUI/JS側から来る値の型をここで確定させる
            # （JS側でboolean/numberとして送られる保証が無いため）。max_hitsは
            # int()変換に失敗したら既定の3にフォールバックする。
            ar = payload["auto_recall"] or {}
            try:
                max_hits = int(ar.get("max_hits", 3))
            except (TypeError, ValueError):
                max_hits = 3
            self._cfg["auto_recall"] = {
                "enabled": bool(ar.get("enabled", True)),
                "max_hits": max_hits,
            }
        if "skill_auto_create" in payload:
            self._cfg["skill_auto_create"] = bool(payload["skill_auto_create"])
        if "pet_enabled" in payload:
            self._cfg["pet_enabled"] = bool(payload["pet_enabled"])
        if "pet_size" in payload:
            # auto_recallのmax_hits同様、JS側からの型は保証されないためint()化する。
            # 失敗時は既定64、範囲外は48〜128へクランプ（設定画面スライダーの上下限と一致）。
            try:
                size = int(payload["pet_size"])
            except (TypeError, ValueError):
                size = 64
            self._cfg["pet_size"] = max(48, min(128, size))
        if "pet_character" in payload:
            # 未知の値は既定コノハに倒す（BUILTIN_PET_SKINSのキーと対応）
            val = payload["pet_character"]
            self._cfg["pet_character"] = val if val in ("konoha", "mokora") else "konoha"
        if "streaming" in payload:
            self._cfg["streaming"] = bool(payload["streaming"])
        if "reply_se" in payload:
            # 不正値は既定("se-poko.mp3")ではなく""へ倒す（鳴らない方に倒す＝安全側）。
            val = payload["reply_se"]
            self._cfg["reply_se"] = val if val in config_mod.REPLY_SE_CHOICES else ""
        if "ime_auto_ja" in payload:
            # auto_role_switch等と同様、JS側からの型を保証せずここでbool確定させる。
            self._cfg["ime_auto_ja"] = bool(payload["ime_auto_ja"])
        config_mod.save_config(self._cfg)
        self._env = load_env()
        self._ensure_engine()
        return self.get_settings()

    def ensure_ime_japanese(self):
        """入力欄フォーカス時にIMEを日本語入力（ひらがな）へ切り替える。
        WebView2の入力ウィンドウは別プロセスのため、ImmSetOpenStatusではなく
        デフォルトIMEウィンドウへのWM_IME_CONTROLで制御する（プロセス跨ぎで有効）。
        設定 ime_auto_ja がFalseなら何もしない。失敗はすべて握る（実害なし優先）。"""
        if not self._cfg.get("ime_auto_ja", True):
            return {"ok": False, "reason": "disabled"}
        try:
            import ctypes
            user32 = ctypes.windll.user32
            imm32 = ctypes.windll.imm32
            WM_IME_CONTROL = 0x0283
            IMC_SETOPENSTATUS = 0x0006
            IMC_SETCONVERSIONMODE = 0x0002
            IME_CMODE_HIRAGANA = 0x0001 | 0x0008  # NATIVE | FULLSHAPE

            fg = user32.GetForegroundWindow()
            if not fg:
                return {"ok": False, "reason": "no-foreground"}
            tid = user32.GetWindowThreadProcessId(fg, None)

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                            ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                            ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                            ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                            ("rcCaret", ctypes.c_long * 4)]

            info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
            if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
                return {"ok": False, "reason": "no-thread-info"}
            target = info.hwndFocus or fg
            ime_wnd = imm32.ImmGetDefaultIMEWnd(target)
            if not ime_wnd:
                return {"ok": False, "reason": "no-ime-wnd"}
            user32.SendMessageW(ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 1)
            user32.SendMessageW(ime_wnd, WM_IME_CONTROL, IMC_SETCONVERSIONMODE, IME_CMODE_HIRAGANA)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def list_models(self, provider_name):
        """指定プロバイダのモデル一覧を取得（設定画面の「一覧から選ぶ」用）。
        会話中のLLMインスタンス(self._llm)とは無関係に、そのプロバイダ単体を
        一時的に組み立てて呼ぶ（未選択中のプロバイダでも一覧が引けるように）。
        例外（キー未設定・接続不可等）は握りつぶし、トレースバックは返さない。"""
        try:
            providers = self._cfg["llm"].get("providers", {})
            if provider_name not in providers:
                return {"ok": False, "error": f"未知のプロバイダ: {provider_name}"}
            entry = providers[provider_name]
            ptype = entry.get("type")
            if ptype == "xai_oauth":
                import xai_oauth
                if not xai_oauth.is_logged_in():
                    return {"ok": False, "error": "Grokに未ログイン（設定画面の「ログイン」から）"}
            elif ptype == "openai_codex_oauth":
                import openai_codex_oauth
                if not openai_codex_oauth.is_logged_in():
                    return {"ok": False, "error": "ChatGPTに未ログイン（設定画面の「ログイン」から）"}
            llm_cfg = self._cfg["llm"]
            temperature = float(llm_cfg.get("temperature", 0.8))
            max_tokens = int(llm_cfg.get("max_tokens", 400))
            provider = build_provider(entry, self._env, temperature, max_tokens)
            models = provider.list_models()
            return {"ok": True, "models": models}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_api_keys(self):
        """プロバイダごとのAPIキー登録状況を返す。キーの値そのものは含めない。"""
        providers = self._cfg["llm"].get("providers", {})
        out = []
        for name, entry in providers.items():
            env_key = (entry.get("env_key") or "").strip()
            if not env_key:
                continue  # OAuth系などenv_keyを持たないプロバイダは対象外
            out.append({
                "provider": name,
                "env_key": env_key,
                "is_set": bool(self._env.get(env_key, "").strip()),
            })
        return out

    def save_api_key(self, env_key, value):
        set_key(env_key, value.strip())
        self._env = load_env()
        self._ensure_engine()
        return {"ok": True}

    def delete_api_key(self, env_key):
        delete_key(env_key)
        self._env = load_env()
        self._ensure_engine()
        return {"ok": True}

    # --- OAuthログイン（Grok/ChatGPT。APIキーの代わりにブラウザでログイン） ---
    def xai_login_start(self):
        try:
            import xai_oauth
            status = xai_oauth.start_login()
            return {"ok": not status.startswith("error"), "status": status,
                    "error": status if status.startswith("error") else ""}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def xai_login_status(self):
        import xai_oauth
        status = xai_oauth.login_status()
        if status == "ok":
            self._ensure_engine()  # ログイン完了 → 即会話できるようLLMを組み立て直す
        return {"status": status, "logged_in": xai_oauth.is_logged_in()}

    def xai_submit_code(self, pasted):
        """ブラウザ側で「コードをコピーして」と出た場合の手動完了。"""
        import xai_oauth
        status = xai_oauth.submit_code(pasted)
        if status == "ok":
            self._ensure_engine()
        return {"ok": status == "ok", "status": status,
                "error": status if status.startswith("error") else ""}

    def xai_logout(self):
        import xai_oauth
        xai_oauth.clear_tokens()
        self._ensure_engine()  # トークン失効後の状態でLLMを組み立て直す
        return {"ok": True}

    def codex_login_start(self):
        try:
            import openai_codex_oauth
            status = openai_codex_oauth.start_login()
            return {"ok": not status.startswith("error"), "status": status,
                    "error": status if status.startswith("error") else ""}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def codex_login_status(self):
        import openai_codex_oauth
        status = openai_codex_oauth.login_status()
        if status == "ok":
            self._ensure_engine()
        return {"status": status, "logged_in": openai_codex_oauth.is_logged_in()}

    def codex_submit_code(self, pasted):
        """ブラウザ側で「コードをコピーして」と出た場合の手動完了。"""
        import openai_codex_oauth
        status = openai_codex_oauth.submit_code(pasted)
        if status == "ok":
            self._ensure_engine()
        return {"ok": status == "ok", "status": status,
                "error": status if status.startswith("error") else ""}

    def codex_logout(self):
        import openai_codex_oauth
        openai_codex_oauth.clear_tokens()
        self._ensure_engine()
        return {"ok": True}

    # --- プロンプト透視 ---
    def get_current_prompt(self):
        """アクティブSOULへ実際に注入されている system プロンプト全文を、LLMを
        呼ばずに合成だけして返す（設定画面「プロンプト透視」用）。SOUL未設定なら
        空文字を返す。開くたびに最新を取るため、呼び出し側はキャッシュしない。"""
        soul_id = self._cfg.get("active_soul")
        if not soul_id:
            return {"text": "", "chars": 0, "approx_tokens": 0}
        tools_spec = memory_tools.build_tools_spec(self._cfg, soul_id)
        text = prompt_mod.build_system_text(self._cfg, soul_id, tools_spec)
        return {"text": text, "chars": len(text), "approx_tokens": int(len(text) * 0.6)}

    # --- ペット（ドット絵マスコット） ---
    def get_pet_skin(self):
        """現在アクティブなSOULの souls/<id>/pet/ 配下にある状態名.png
        （PET_STATES: idle/thinking/writing/recall/error/love）をbase64 data URIへ変換した
        dictで返す。既定はCSSだけで描くペットだが、pet/にPNGが置かれていれば
        そちらを優先表示する（UI側のフォールバック規則: 個別状態が無ければidle.png、
        pet/自体が無ければ既定CSSキャラ）。

        active_soul未設定、またはactive_soulがSOULS_DIR配下の実在ディレクトリ名と
        一致しない（トラバーサル文字列等）場合は空dictを返す——soul_dir()自体は
        任意の文字列からパスを組んでしまう（_safe_pathのようなrel_path側の検証は
        持たない）ため、soul_id自体は他のBridgeメソッド（switch_soul等）と同じ
        _soul_existsによる実在確認をここで先に行う。

        各ファイルはpng拡張子・PET_SKIN_MAX_BYTES(200KB)上限のみ受け付け、
        超過や読み込み失敗はそのファイルだけスキップする（会話・起動を壊さない）。"""
        sid = self._cfg.get("active_soul")
        if not sid or not self._soul_exists(sid):
            return {}
        pet_dir = os.path.join(soul_mod.soul_dir(sid), "pet")
        if not os.path.isdir(pet_dir):
            return {}
        import base64 as base64_mod
        out = {}
        for state in PET_STATES:
            path = os.path.join(pet_dir, f"{state}.png")
            if not os.path.isfile(path):
                continue
            try:
                if os.path.getsize(path) > PET_SKIN_MAX_BYTES:
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                out[state] = "data:image/png;base64," + base64_mod.b64encode(data).decode("ascii")
            except OSError:
                continue
        return out

    def save_pet_pos(self, right, bottom):
        """ペットのドラッグ移動が終わるたび（pointerup毎）に呼ばれる軽量Bridge。
        get_settings/save_settingsが担う設定画面全体の保存フローとは独立に、
        位置(config["pet_pos"])だけを都度即時保存する——サイズ変更のように
        「保存ボタンを押すまでは反映しない」ではなく、ドラッグそのものが確定操作
        なので都度保存が自然（auto_role_switch等のトグル系と同じ「即時反映・即時保存」
        の考え方）。
        JS側からの値の型は保証されないためint()化し、失敗時は0扱い。負値は0へ、
        巨大な値（細工された/バグった座標でペットが画面外はるか彼方の座標を
        覚えてしまうのを防ぐ）は0〜4000へクランプする。戻り値は実際に保存した
        （クランプ後の）値。"""
        def _clamp_offset(value):
            try:
                n = int(value)
            except (TypeError, ValueError):
                n = 0
            return max(0, min(4000, n))
        pos = {"right": _clamp_offset(right), "bottom": _clamp_offset(bottom)}
        self._cfg["pet_pos"] = pos
        config_mod.save_config(self._cfg)
        return pos

    # --- 記憶ビュー ---
    def read_soul_file(self, rel_path):
        if not self._cfg.get("active_soul"):
            return ""
        return soul_mod.read_file(self._cfg["active_soul"], rel_path)

    def get_latest_diary(self):
        """右パネル「日記」用。直近の日次日記を{"date","text"}で返す。1本も無ければ
        date=None, text=""（日記は「今日書かれてるはず」ではなく「セッション終了時に
        書かれる」ため、開いてる間はほぼ常に当日分が無い＝表示するなら最新のもの、
        という2026-07-22の実機フィードバックに基づく）。"""
        sid = self._cfg.get("active_soul")
        if not sid:
            return {"date": None, "text": ""}
        latest = soul_mod.latest_chronicle(sid)
        if latest is None:
            return {"date": None, "text": ""}
        return latest

    def list_soul_files(self):
        sid = self._cfg.get("active_soul")
        if not sid:
            return []
        base = soul_mod.soul_dir(sid)
        out = []
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".md"):
                    rel = os.path.relpath(os.path.join(root, f), base)
                    out.append(rel.replace("\\", "/"))
        return sorted(out)

    def list_log_dates(self):
        """アクティブSOULの過去会話ログ日付一覧（新しい順）。読むだけの閲覧機能用
        （スレッド再開・編集は思想的に不採用。soul.py参照）。active_soul未設定なら空。"""
        sid = self._cfg.get("active_soul")
        if not sid:
            return []
        logs_dir = os.path.join(soul_mod.soul_dir(sid), "logs")
        if not os.path.isdir(logs_dir):
            return []
        dates = [f[:-len(".jsonl")] for f in os.listdir(logs_dir) if f.endswith(".jsonl")]
        return sorted(dates, reverse=True)

    def read_log(self, date):
        """指定日付の会話ログを読み取り専用で返す。dateはファイル名に直接使われるため、
        soul.read_file経由（内部でsoul._safe_pathを通る）でのみ読む。トラバーサル文字列
        （"../.."等）はsoul._safe_pathがValueErrorを投げるので、ここで握って空リストにする
        （list_soul_files/read_soul_fileと違い、dateはUI側でファイル名として直接使われる
        値なので、呼び出し側の存在確認に頼らずここで防御する）。壊れた行はスキップする。"""
        sid = self._cfg.get("active_soul")
        if not sid:
            return []
        try:
            raw = soul_mod.read_file(sid, os.path.join("logs", f"{date}.jsonl"))
        except ValueError:
            return []
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    # --- バックアップ ---
    def backup_souls(self):
        """souls/・config.json・roles.jsonを日付つきzipへまとめる（「1フォルダ=1魂、
        丸ごとコピーで引っ越し」設計思想の実務手段）。.envは絶対に含めない（APIキー）。
        fieria_home/backups/ 自体はバックアップ対象外（souls_rootの外を明示的に
        個別追加しているだけなので、将来この関数を変更してもbackups/を巻き込まない
        よう気をつけること）。例外は握って{"ok": False, "error": ...}で返す
        （UI側に生の例外を漏らさない）。"""
        try:
            backups_dir = os.path.join(config_mod.HOME, "backups")
            os.makedirs(backups_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base_name = f"souls-{ts}"
            zip_path = os.path.join(backups_dir, base_name + ".zip")
            n = 1
            while os.path.exists(zip_path):
                n += 1
                zip_path = os.path.join(backups_dir, f"{base_name}-{n}.zip")

            souls_root = soul_mod.SOULS_DIR
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                if os.path.isdir(souls_root):
                    for dirpath, _dirs, filenames in os.walk(souls_root):
                        for fn in filenames:
                            full = os.path.join(dirpath, fn)
                            rel = os.path.relpath(full, config_mod.HOME)
                            zf.write(full, rel.replace("\\", "/"))
                if os.path.isfile(config_mod.CONFIG_PATH):
                    zf.write(config_mod.CONFIG_PATH, "config.json")
                if os.path.isfile(roles_mod.ROLES_PATH):
                    zf.write(roles_mod.ROLES_PATH, "roles.json")

            size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 1)
            return {"ok": True, "path": os.path.abspath(zip_path), "size_mb": size_mb}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_import_status(self):
        """inboxの状態。UIのカウンタ表示と実行前確認に使う。"""
        soul_id = self._cfg.get("active_soul")
        if not soul_id:
            return {"count": 0, "files": [], "importing": bool(self._importing)}
        files = importer.list_inbox(soul_id)
        return {"count": len(files), "files": files,
                "importing": bool(self._importing)}

    def _stage_import(self, paths):
        soul_id = self._cfg.get("active_soul")
        if not soul_id:
            return {"error": "SOULが選ばれていない"}
        out = importer.stage_files(soul_id, paths)
        out["inbox_count"] = len(importer.list_inbox(soul_id))
        return out

    def start_import(self):
        """インポートのバッチ実行を開始する。実処理はデーモンスレッドで走る。"""
        soul_id = self._cfg.get("active_soul")
        if not soul_id:
            return {"error": "SOULが選ばれていない"}
        files = importer.list_inbox(soul_id)
        if not files:
            return {"error": "inboxが空。先にファイルを追加して"}
        # 判定→フラグセットを_busy_lockの下で一体化する。pywebviewは各API呼び出しを
        # 別スレッドで走らせるため、send_message側の判定・加算と交差しうる。
        # ファイルI/O（list_inbox）はロックの外で済ませてある。is_llm_busy()は
        # 内部で同じロックを取るため、ここでは_busy_turnsを直接見る（再入不可のLock）。
        with self._busy_lock:
            # スケジューラの定期ジョブも同じsoulの記憶へLLM経由で書くため、
            # チャット中と同様にインポート開始を拒否する。is_running_jobは
            # scheduler側の別ロック（_running_lock）なので_busy_lock内から
            # 呼んでもデッドロックしない。
            if self._busy_turns > 0 or self._scheduler.is_running_job():
                return {"error": "会話の処理中はインポートできない"}
            if self._importing:
                return {"error": "すでにインポート処理中"}
            self._importing = True
        self._import_stop = False
        threading.Thread(target=self._run_import_thread,
                         args=(soul_id,), daemon=True).start()
        return {"ok": True, "total": len(files)}

    def cancel_import(self):
        """次のファイル境界で停止する（処理中のLLM呼び出しは中断しない）。"""
        self._import_stop = True
        return {"ok": True}

    def _run_import_thread(self, soul_id):
        try:
            summary = importer.run_import(
                self._cfg, self._llm, soul_id,
                on_progress=self._push_import_progress,
                should_stop=lambda: self._import_stop)
            self._push_import_progress(dict(summary, kind="done"))
        except Exception as e:
            self._push_import_progress({
                "kind": "done", "total": 0, "done": 0, "stopped": False,
                "failed": [{"file": "(内部エラー)", "detail": str(e)}]})
        finally:
            self._importing = False

    def _push_import_progress(self, payload):
        w = self._window
        if not w:
            return
        try:
            w.evaluate_js("window.onImportProgress && window.onImportProgress("
                          + json.dumps(payload, ensure_ascii=False) + ")")
        except Exception:
            pass

    def pick_and_stage_import_files(self):
        """MDファイルを複数選択してinboxへコピー。（ネイティブダイアログ呼び出しのためテスト対象外）"""
        if not webview.windows:
            return {"error": "ウィンドウがない"}
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("Markdownファイル (*.md;*.markdown;*.txt)",))
        if not result:
            return self._stage_import([])
        return self._stage_import(list(result))

    def pick_and_stage_import_folder(self):
        """フォルダを選択して中のMDを再帰的にinboxへコピー。（同上テスト対象外）"""
        if not webview.windows:
            return {"error": "ウィンドウがない"}
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return self._stage_import([])
        return self._stage_import(list(result))

    def pick_backup_file(self):
        """バックアップzipをOSのファイル選択ダイアログで選ばせ、選ばれた絶対パスを
        返す（キャンセル時は空文字）。webview.windows[0]（実ウィンドウ）に依存するため、
        pywebviewウィンドウの無いテスト環境からは呼べない＝このメソッド自体はテスト対象外
        （ユーザー指示どおり。import_backup側の検証・展開ロジックはpath文字列を渡す
        テストで別途カバーする）。"""
        if not webview.windows:
            return ""
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Zipファイル (*.zip)",)
        )
        if not result:
            return ""
        return result[0]

    def import_backup(self, path):
        """backup_souls()が作ったzipからsouls/・config.json・roles.jsonを復元する
        （「魂の引っ越し」の逆方向）。安全策は3段構え：
        (1) 中身がFieriAバックアップの体をなしているか（config.jsonまたはsouls/配下の
            エントリを含むか）を検証。無関係なzipは拒否する。
        (2) 全エントリ（許可リスト外も含む）についてZip Slip・symlinkを検証する。
            1つでも不正なエントリがあれば、その時点で1ファイルも展開せず中止する
            （許可リスト外のエントリは通常展開されないが、"souls/../../evil"のように
            許可リストの接頭辞チェックをすり抜けつつ展開先の外を指すエントリもあり得る
            ため、許可リストでの絞り込みより前に全件を検証する）。
        (3) 展開直前に現在の状態をbackup_souls()で自動バックアップする（復元が
            気に入らなければそのzipへ戻れる安全弁）。自動バックアップ自体が失敗したら
            復元を中止する。
        展開は許可リスト方式（souls/配下・config.json・roles.jsonのみ）。zipに無い
        既存soulは触らない＝マージ（zip側のファイルで上書きするだけで削除はしない）。
        ただしzip側と同じsoul_idの既存SOULは中身を無警告で上書きする（マージの
        仕様上必然。同一マシンでの自己復元では正しい挙動）。soul_idはローカル連番
        のみで生成されるため、別インストール同士だと名前なしSOUL等で偶然一致し
        うる。この関数はどのSOULが上書きされたかを検出し、戻り値の
        overwritten_souls（[{"id","name"}...]、上書き前の名前）としてUIに渡す
        （挙動そのものは変えず、可視化だけする）。
        復元後はconfigを再読込し、_ensure_engine()でエンジンを組み直してからboot()を返す
        （UIが最新状態へ更新される）。例外は握って{"ok": False, "error": ...}で返す。
        auto_backupはtryの外で初期化しておき、(3)の自動バックアップ後・展開完了前に
        例外が起きた場合でも、except節でその復旧先パスをエラーメッセージに含められる
        ようにする（バックアップ自体は取れているのに、案内が無いとユーザーが
        気付けず失われた気になってしまうため）。"""
        auto_backup = None
        try:
            if not path or not zipfile.is_zipfile(path):
                return {"ok": False, "error": "FieriAのバックアップではないようです"}

            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                names = [info.filename for info in infos]
                looks_like_backup = any(
                    name == "config.json" or name.replace("\\", "/").startswith("souls/")
                    for name in names
                )
                if not looks_like_backup:
                    return {"ok": False, "error": "FieriAのバックアップではないようです"}

                for info in infos:
                    if _is_symlink_zip_entry(info):
                        return {"ok": False, "error": f"不正なzipエントリ(symlink)のため復元を中止しました: {info.filename}"}
                    if _is_unsafe_zip_member(config_mod.HOME, info.filename):
                        return {"ok": False, "error": f"不正なzipエントリのため復元を中止しました: {info.filename}"}

                auto_backup = self.backup_souls()
                if not auto_backup.get("ok"):
                    return {"ok": False, "error": f"復元前の自動バックアップに失敗したため中止しました: {auto_backup.get('error')}"}

                # 展開前（＝上書きされる前）の既存SOULを記録しておく。soul_idはローカル
                # 連番のみで生成されるため（soul.create_soul）、別インストール同士だと
                # 同名的な衝突が起こりうる。マージ展開は仕様として維持しつつ、
                # 「どのSOULが無警告で上書きされたか」をUIに見せるための下調べ。
                existing_souls_before = {s["id"]: s["name"] for s in soul_mod.list_souls()}

                restored_ids = set()
                allowed_prefixes = ("souls/",)
                for info in infos:
                    name = info.filename.replace("\\", "/")
                    if name.endswith("/"):
                        continue  # ディレクトリエントリは展開不要
                    if not (name == "config.json" or name == "roles.json"
                            or name.startswith(allowed_prefixes)):
                        continue  # 許可リスト外（.env等）は無視して展開しない
                    dest = os.path.join(config_mod.HOME, *name.split("/"))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(info) as src, open(dest, "wb") as out:
                        out.write(src.read())
                    if name.startswith("souls/"):
                        parts = name.split("/")
                        if len(parts) > 1 and parts[1]:
                            restored_ids.add(parts[1])

            self._cfg = config_mod.load_config()
            self._ensure_engine()
            restored_names = sorted(
                s["name"] for s in soul_mod.list_souls() if s["id"] in restored_ids
            )
            # zipに入っていたsoul_idのうち、展開前から既に存在していたもの＝
            # 中身を無警告で上書きされたSOUL。id・上書き前の名前をUI側の
            # 「既存SOUL◯◯に上書きマージした」表示に使う。
            overwritten_ids = restored_ids & existing_souls_before.keys()
            overwritten_souls = [
                {"id": sid, "name": existing_souls_before[sid]}
                for sid in sorted(overwritten_ids)
            ]
            return {
                "ok": True,
                "restored_souls": restored_names,
                "overwritten_souls": overwritten_souls,
                "auto_backup": auto_backup["path"],
                **self.boot(),
            }
        except Exception as e:
            error = str(e)
            if auto_backup and auto_backup.get("ok"):
                error += (
                    f" / 復元前の状態は {auto_backup['path']} に保存済み。"
                    "fieria_homeに展開し直すことで復旧できます"
                )
            return {"ok": False, "error": error}

    # --- 定期処理（可視化＋ON/OFF） ---
    def get_scheduled_jobs(self):
        info = self._scheduler.last_run_info
        jobs_result = info.get("jobs", {})
        scheduled_cfg = self._cfg.get("scheduled_jobs", {})
        return [{
            "id": job["id"],
            "name": job["name"],
            "description": job["description"],
            "enabled": scheduled_cfg.get(job["id"], True),
            "last_run": info["last_run"],
            "last_result": jobs_result.get(job["id"], []),
        } for job in JOBS]

    def set_scheduled_job(self, job_id, enabled):
        self._cfg.setdefault("scheduled_jobs", {})[job_id] = bool(enabled)
        config_mod.save_config(self._cfg)
        return self.get_scheduled_jobs()

    # --- 終了 ---
    def end_session(self):
        self._maybe_wrapup()
        return {"ok": True}

    def _maybe_wrapup(self):
        if self._engine and self._llm and self._cfg.get("active_soul"):
            wrapup_mod.write_daily_chronicle(self._cfg, self._llm, self._cfg["active_soul"])


def _make_on_closing(bridge, window):
    """window.events.closing用ハンドラを組み立てる。LLM処理中（bridge.is_llm_busy()）
    に閉じようとした場合だけ確認ダイアログを出し、キャンセルならFalseを返して
    クローズを中止する（pywebviewのEvent.set()は、登録済みハンドラのうち1つでも
    Falseを返せばクローズ自体をキャンセルする仕様。window.py Event.set参照）。

    重要: Event.set()の内部実装(execute())は、登録済みの全ハンドラをFalseが
    返るかどうかに関係なく無条件に呼び切る（1つがFalseを返しても他のハンドラの
    実行自体は止まらない）。そのため、終了確定時の後始末（bridge.end_sessionの
    スレッド起動＝日記のwrapup書き込み）をこのハンドラとは別に登録すると、
    確認ダイアログで「終了しない」を選んでクローズをキャンセルしたにもかかわらず
    end_sessionだけは実行されてしまう。これは「終了しない＝何も確定させない」
    という意図に反するため、確認ロジックとend_session起動を1つのハンドラに
    まとめ、キャンセル時はend_sessionを一切呼ばないようにする。

    ダイアログはwindow.create_confirmation_dialog(title, message)を使う
    （pywebview 6.2.1で実装済み・戻り値bool・同期。window.events.closingは
    Event(should_lock=True)で登録され、should_lock=Trueのイベントはハンドラを
    別スレッドに逃さず呼び出し元スレッドで同期実行するため、ここで同期的に
    ダイアログの結果を待って返り値に反映できる。evaluate_js('confirm(...)')系の
    代替は、closing発火時点でページがまだ生きている保証が薄く、
    create_confirmation_dialogの方がOSネイティブダイアログで確実なためこちらを採用）。

    このハンドラ内で起きた例外は握りつぶし、常に「閉じるのを妨げない」側に倒す
    （確認ダイアログの実装不備でアプリ自体が閉じられなくなる事故の方が、
    「本来出るはずの確認が出ない」より実害が大きいため）。ただし例外を握った場合も
    end_sessionの起動は行う（＝通常どおり閉じる経路と同じ後始末をする）。
    """
    def on_closing():
        try:
            if bridge.is_llm_busy():
                proceed = window.create_confirmation_dialog(
                    "終了確認",
                    "フィエリアが応答・記憶の書き込みを処理中です。"
                    "終了すると書き込みが中断される可能性があります。終了しますか？",
                )
                if not proceed:
                    return False  # クローズを中止。end_sessionも呼ばない
        except Exception:
            pass  # ダイアログ自体の異常は「閉じるのを妨げない」側に倒す
        threading.Thread(target=bridge.end_session, daemon=True).start()
        return None
    return on_closing


def run():
    bridge = Bridge()
    window = webview.create_window("FieriA", "ui/index.html",
                                   js_api=bridge, width=1280, height=820)
    # FieriA拡張: ストリーミング。Bridge.__init__の時点ではウィンドウがまだ存在しない
    # ため、生成後にここでセットする（bridge._push_stream_deltaのevaluate_js呼び出しは
    # ページロード完了後・send_message経由でのみ発生するので、この時点でセットして
    # おけば実際に使われるタイミングでは確実に設定済み）。
    bridge._window = window
    # FIERIA_TESTING時はウィンドウを作らずrun()自体がテストで呼ばれない想定だが、
    # 念のためウィンドウ無し・テスト実行中はハンドラ登録自体をスキップする
    # （テストの挙動に影響させないため）。end_sessionの起動もこのハンドラに
    # 統合したため、closingへの登録はこの1本のみになる。
    if window and not os.environ.get("FIERIA_TESTING"):
        window.events.closing += _make_on_closing(bridge, window)
    webview.start()
