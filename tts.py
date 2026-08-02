"""tts.py — VOICEVOX互換HTTPエンジン（第一想定はAivisSpeech）での読み上げ。

audio_query→synthesisの2段呼び（VOICEVOX/AivisSpeech共通API）でWAVバイトを合成し、
winsoundで非同期再生する。llm.pyの_post_json/_get_jsonと同じ「urllib直呼びは薄い
内部関数に集約してmonkeypatchしやすくする」流儀に倣うが、llm.pyへの依存は作らない
（User-Agent偽装も不要——ローカルエンジン相手のため）。

失敗（エンジン未起動・タイムアウト・変な応答）は speak() が全部握って静かに諦める
——読み上げの失敗で会話体験を絶対に壊さないため（設定画面のテスト再生=tts_testだけは
呼び出し側でエラーを表示してよい）。

winsoundはWindows専用のためモジュール先頭でtry import。無い環境（非Windows）では
Noneのままとなり、play_wav_async/stopは静かに無効化される。
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

try:
    import winsound
except ImportError:  # pragma: no cover - Windows専用。非Windows環境でのみ通る経路
    winsound = None


_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_SYMBOL_RE = re.compile(r"[#*`]")
_URL_RE = re.compile(r"https?://\S+")
_TRAILING_RULE_RE = re.compile(r"-{3,}\s*$")


def prepare_text(text, limit=600):
    """読み上げ前処理。Global Constraints記載の順で除去する:
    1) コードブロック除去→「（コード略）」
    2) Markdown記号（#, *, `, リンクの[]()等）除去（リンクは表示名だけ残す）
    3) 🔎出典ブロック除去（llm._format_sources_blockが付ける「\\n\\n---\\n🔎 出典: ...」形式）
    4) URL除去
    5) 先頭limit字でクリップ
    全部除去されて空になったら""を返す。"""
    if not text:
        return ""
    out = _CODE_BLOCK_RE.sub("（コード略）", text)
    out = _MD_LINK_RE.sub(r"\1", out)
    out = _MD_SYMBOL_RE.sub("", out)
    idx = out.find("🔎")
    if idx != -1:
        out = out[:idx]
        out = _TRAILING_RULE_RE.sub("", out)
    out = _URL_RE.sub("", out)
    out = out.strip()
    return out[:limit]


def _http_post(url, data=None, headers=None, timeout=15):
    """POST。data=Noneならボディ無し送信（audio_query用）。生バイトを返す。
    テストはこの関数をmonkeypatchして実urlopenに触れずに検証する。"""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_get(url, headers=None, timeout=15):
    """GET。生バイトを返す。_http_postと同じくmonkeypatch対象。"""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def synthesize(engine_url, text, speaker, speed=1.0, timeout=15):
    """audio_query→synthesisの2段呼びでWAVバイトを合成する。
    失敗（HTTPエラー・接続不可・JSON崩れ）は例外をそのまま送出する（呼び元のspeak()が握る）。"""
    base = engine_url.rstrip("/")
    query_url = base + "/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": speaker})
    raw_query = _http_post(query_url, data=None, timeout=timeout)
    query = json.loads(raw_query.decode("utf-8"))
    query["speedScale"] = speed

    synth_url = base + "/synthesis?" + urllib.parse.urlencode({"speaker": speaker})
    body = json.dumps(query).encode("utf-8")
    return _http_post(synth_url, data=body, headers={"Content-Type": "application/json"}, timeout=timeout)


def play_wav_async(wav_bytes):
    """winsoundで非同期再生（成功True）。winsound無し環境はFalse。
    新規再生は前の再生をSND_ASYNCの挙動で自動的に置き換える（スレッド管理不要）。"""
    if winsound is None:
        return False
    winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC)
    return True


def stop():
    """再生停止。winsound無し環境では何もしない。"""
    if winsound is None:
        return
    winsound.PlaySound(None, winsound.SND_PURGE)


def speak(cfg_tts, text):
    """設定→前処理→合成→再生を束ねる高レベル関数。例外はすべて内部で握ってFalseを返す
    ——読み上げの失敗で会話体験を絶対に壊さないため（Global Constraints）。"""
    try:
        if not cfg_tts.get("enabled"):
            return False
        prepared = prepare_text(text)
        if not prepared:
            return False
        wav = synthesize(
            cfg_tts.get("engine_url", ""),
            prepared,
            cfg_tts.get("speaker", 0),
            speed=cfg_tts.get("speed", 1.0),
        )
        return play_wav_async(wav)
    except Exception:
        return False


def list_speakers(engine_url, timeout=5):
    """GET /speakers を [{"name": 話者名, "styles": [{"name","id"}]}] 形式で返す。
    失敗は例外送出（呼び元=gui.tts_list_speakersが握って平易なエラーに変換する）。"""
    base = engine_url.rstrip("/")
    raw = _http_get(base + "/speakers", timeout=timeout)
    data = json.loads(raw.decode("utf-8"))
    result = []
    for speaker in data:
        styles = [
            {"name": style.get("name", ""), "id": style.get("id")}
            for style in speaker.get("styles", [])
        ]
        result.append({"name": speaker.get("name", ""), "styles": styles})
    return result
