"""
llm.py — LLMプロバイダ抽象化

config.json の llm ブロックは「名前付きプロバイダ一覧」形式:

  "llm": {
    "provider": "opencode_zen",          ← 使うプロバイダ名
    "providers": {
      "ollama":       {"type": "openai_compat", "base_url": "http://127.0.0.1:11434/v1", "model": "...", "env_key": ""},
      "opencode_go":  {"type": "openai_compat", "base_url": "https://opencode.ai/zen/go/v1", "model": "...", "env_key": "OPENCODE_GO_API_KEY"},
      "opencode_zen": {"type": "openai_compat", "base_url": "https://opencode.ai/zen/v1",    "model": "...", "env_key": "OPENCODE_ZEN_API_KEY"},
      "gemini":       {"type": "gemini", "model": "...", "env_key": "GEMINI_API_KEY"},
      "custom":       {"type": "openai_compat", "base_url": "", "model": "", "env_key": "CUSTOM_API_KEY"}
    },
    "temperature": 0.8, "max_tokens": 400
  }

type は2種類だけ: "openai_compat"（/chat/completions を喋る全サービス）と "gemini"。
APIキーは .env から解決する（env.resolve_api_key）。
どのプロバイダも list_models() でモデル一覧を取れる（GUIの「一覧から選ぶ」用）。
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from env import resolve_api_key

# OpenCode Zen/Go は Cloudflare 配下で、Pythonのデフォルト User-Agent
# （"Python-urllib/x.x"）だとボット判定され 403 (error code: 1010) で
# 弾かれる（2026-07-07に実測確認）。ブラウザのUAを詐称して回避する。
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _post_json(url, payload, headers=None, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url, headers=None, timeout=10):
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_sse(url, payload, headers=None, timeout=120):
    """_post_jsonと同じだがJSONパースせず生のレスポンス本文(SSEテキスト)を返す"""
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}")


# FieriA拡張: ストリーミング
def _post_sse_stream(url, payload, headers=None, timeout=120):
    """_post_sseと同じリクエストを送るが、レスポンス本文を待たずに行単位で
    逐次yieldする(ジェネレータ)。HTTPエラーは1行もyieldする前に検出され、
    そのままRuntimeErrorとして送出される（_post_sseと同じ詳細メッセージ形式）。
    ネットワーク層をここ1関数に分離しているため、テストはこの関数をmonkeypatchして
    行のイテレータ（固定リスト等）に差し替えることで、実urlopenに触れずに
    chat_stream実装（各プロバイダのSSEパース処理）だけを検証できる。"""
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}")
    try:
        for raw_line in resp:
            yield raw_line.decode("utf-8", errors="replace")
    finally:
        resp.close()


class _ChatStreamFallbackMixin:
    """FieriA拡張: ストリーミング。chat_stream未実装のプロバイダ向け既定実装。

    chat()を1回呼び、得られた全文を1個だけyieldする。ネイティブSSE実装を
    持たないプロバイダ（OAuth系のCodexOAuthLLM）やFallbackLLMがこれを継承する
    ことで、呼び出し側（engine.py）は常にllm.chat_streamを呼べる——未対応
    プロバイダでは単に「表示が一括になるだけ」で会話自体は壊れない。"""

    def chat_stream(self, messages, max_tokens=None):
        yield self.chat(messages, max_tokens=max_tokens)


def _to_openai_msg(m):
    """内部メッセージ形式 → OpenAI互換形式。imagesがあればマルチモーダルcontentにする。

    既知の制約: images[].mimeが"application/pdf"の場合（GeminiネイティブPDF添付・
    gui.pdf_native_supported/memory_tools.read_pdf参照）、ここではmimeを見ずに
    そのままimage_urlとして送るため、OpenAI互換プロバイダ（フォールバック含む）が
    実際にPDFを画像として解釈できるかは保証しない。フォールバック先が非Geminiだと
    ネイティブPDF添付を含むターンは失敗しうるが、動的なPDF→画像変換はスコープ外として
    許容する（GeminiLLM.chatはmimeをinline_data.mime_typeへそのまま渡すので問題ない）。
    """
    images = m.get("images")
    if not images:
        return {"role": m["role"], "content": m["content"]}
    content = [{"type": "text", "text": m["content"]}]
    for img in images:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{img['mime']};base64,{img['b64']}"}})
    return {"role": m["role"], "content": content}


class OpenAICompatLLM(_ChatStreamFallbackMixin):
    def __init__(self, entry, api_key, temperature, max_tokens, reasoning_effort=""):
        self.base_url = (entry.get("base_url") or "").rstrip("/")
        self.api_key = api_key
        self.model = entry.get("model", "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        # OpenAI o系の慣習に合わせた文字列("low"/"medium"/"high")をそのまま送る。
        # 対応してないゲートウェイ/モデルは未知フィールドとして無視するはず（保証はできない）。
        self.reasoning_effort = (reasoning_effort or "").strip()

    def _headers(self):
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # FieriA拡張: Web検索。既定では何も足さない。web_search対応のサブクラス
    # （XaiOAuthLLM）だけがこれをオーバーライドしてpayloadへ差分を足す。
    # ここに集約することで、chat()/chat_stream()両方のpayload組み立てを
    # 重複させずに済む。
    def _extra_payload_fields(self):
        return {}

    def health(self):
        if not self.base_url:
            return False, "base_url が未設定"
        if not self.model:
            return False, "モデル名が未設定（設定画面で「一覧から選ぶ」）"
        try:
            names = self.list_models()
            if names and self.model not in names:
                return True, f"接続OK（ただし {self.model} が一覧に無い。例: {', '.join(names[:5])}）"
            return True, f"{self.model} @ {self.base_url}"
        except Exception as e:
            hint = self._friendly_error(e)
            return False, f"{self.base_url} に接続できない{hint}"

    @staticmethod
    def _friendly_error(e):
        """生のPython例外文（WinError等）をそのまま見せない。原因の見当だけ短く添える。"""
        text = str(e)
        if "10061" in text or "Connection refused" in text or "拒否" in text:
            return "（サーバーが起動していない可能性があります）"
        if "getaddrinfo" in text or "Name or service not known" in text:
            return "（URLが間違っている可能性があります）"
        if "timed out" in text or "timeout" in text.lower():
            return "（応答がありません。しばらくしてから再試行してください）"
        return "（設定を確認してください）"

    def list_models(self):
        body = _get_json(f"{self.base_url}/models", headers=self._headers())
        return [m.get("id", "?") for m in body.get("data", [])]

    def chat(self, messages, max_tokens=None):
        payload = {
            "model": self.model,
            "messages": [_to_openai_msg(m) for m in messages],
            "temperature": self.temperature,
            "stream": False,
        }
        # FieriA拡張: max_tokens=0でモデル既定に任せる（NikoVoice版からの分岐点）
        # 明示引数（wrapup等）はインスタンス設定より優先。実効値が0以下/Noneなら
        # "max_tokens"キー自体を送らず、モデル側の既定上限に任せる。
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        if effective_max_tokens and int(effective_max_tokens) > 0:
            payload["max_tokens"] = int(effective_max_tokens)
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        payload.update(self._extra_payload_fields())
        body = _post_json(
            f"{self.base_url}/chat/completions", payload, headers=self._headers(),
        )
        choices = body.get("choices")
        if not choices:
            raise RuntimeError(f"LLM応答が空: {json.dumps(body, ensure_ascii=False)[:200]}")
        return (choices[0]["message"]["content"] or "").strip()

    # FieriA拡張: ストリーミング
    def chat_stream(self, messages, max_tokens=None):
        """stream:trueでSSEを叩き、choices[0].delta.contentの差分を逐次yieldする。
        payload組み立てはchat()と同じロジック（重複を避けるため、変更する際は
        両方に反映すること）。"data: [DONE]"で終端。空行・コメント行(":"始まり)は
        SSEプロトコル上の正常なkeep-aliveとして無視するが、"data:"行のJSONパースに
        失敗した場合は本物の異常とみなし例外を送出する（それまでyield済みの
        差分は呼び出し側にそのまま残る——ジェネレータなので巻き戻らない）。"""
        payload = {
            "model": self.model,
            "messages": [_to_openai_msg(m) for m in messages],
            "temperature": self.temperature,
            "stream": True,
        }
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        if effective_max_tokens and int(effective_max_tokens) > 0:
            payload["max_tokens"] = int(effective_max_tokens)
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        payload.update(self._extra_payload_fields())
        for raw_line in _post_sse_stream(
                f"{self.base_url}/chat/completions", payload, headers=self._headers()):
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data:
                # keep-alive目的の空data行（litellm等のプロキシが送る）。
                # Gemini側と同じガード——無いとjson.loads("")が例外化しターン全体が死ぬ
                continue
            if data == "[DONE]":
                break
            try:
                parsed = json.loads(data)
            except Exception:
                raise RuntimeError(f"ストリームのパースに失敗: {data[:200]}")
            choices = parsed.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                yield text


# Gemini 2.5系のthinkingBudget（内部思考に使うトークン数の上限）目安値。
# 公式の正確な上下限はモデルごとに違うが、範囲外の値は基本クランプされる想定
# （2026-07-07時点、実測はしていない・要検証の値）。
_GEMINI_THINKING_BUDGET = {"low": 0, "medium": 2048, "high": 8192}

REASONING_EFFORTS = ("", "low", "medium", "high")

# FieriA拡張: Web検索（Gemini Google Search grounding）
_GEMINI_MAX_SOURCES = 3


def _extract_grounding_sources(candidate, limit=_GEMINI_MAX_SOURCES):
    """candidates[0]のgroundingMetadata.groundingChunksから出典(title, uri)を
    最大limit件、重複uriを除いて取り出す。metadataが無い/形が崩れている場合は
    静かに空リストを返す（検索メタの欠落・パース失敗で本文まで壊さないため、
    呼び出し側で例外を握る前提の設計だが、ここ自体も型チェックで防御する）。"""
    sources = []
    seen_uris = set()
    chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
    for chunk in chunks:
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if not isinstance(web, dict):
            continue
        uri = web.get("uri")
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        sources.append((web.get("title") or "", uri))
        if len(sources) >= limit:
            break
    return sources


def _extract_search_queries(candidate, limit=3):
    """groundingMetadata.webSearchQueries（Geminiが実際に使った検索語）を最大limit件
    取り出す。「何を調べたか」の透明性のため出典と一緒に表示する（実機フィードバック
    2026-07-23: 出典だけだと何を検索したのか分からない）。欠落・破損は空リスト。"""
    queries = (candidate.get("groundingMetadata") or {}).get("webSearchQueries") or []
    out = []
    for q in queries:
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
        if len(out) >= limit:
            break
    return out


def _format_sources_block(sources, queries=None):
    """出典リストを本文末尾に付ける表示用ブロックに整形する。空なら空文字。

    GeminiのgroundingメタのURIは実URLではなく vertexaisearch...grounding-api-redirect/
    という数百字級のリダイレクトURLで返る（実機確認 2026-07-23）。生で並べると
    吹き出しが崩壊するため、Markdownリンク [タイトル](URI) 形式にして表示は
    タイトル（通常はドメイン名）だけに畳む。UIの自前レンダラは [label](http…) を
    <a>に変換する（ui/index.html renderMarkdownToHtml参照）。"""
    if not sources:
        return ""
    parts = []
    for i, (title, uri) in enumerate(sources, 1):
        label = title or f"出典{i}"
        parts.append(f"[{label}]({uri})")
    block = "\n\n---\n🔎 出典: " + " / ".join(parts)
    if queries:
        block += "\n（検索語: " + " / ".join(f"「{q}」" for q in queries) + "）"
    return block


class GeminiLLM(_ChatStreamFallbackMixin):
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, entry, api_key, temperature, max_tokens, reasoning_effort="", web_search=False):
        self.api_key = api_key
        self.model = entry.get("model", "gemini-2.5-flash")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = (reasoning_effort or "").strip()
        # FieriA拡張: Web検索。Trueだとpayloadにgoogle_searchツールを足し、
        # 応答のgroundingMetadataから出典を本文末尾へ付ける。
        self.web_search = bool(web_search)

    def health(self):
        if not self.api_key:
            return False, "APIキーが未設定（設定画面から登録）"
        try:
            _get_json(f"{self.BASE}/models?key={urllib.parse.quote(self.api_key)}&pageSize=1")
            return True, f"{self.model}"
        except Exception as e:
            return False, f"Gemini API に接続できない: {e}"

    def list_models(self):
        if not self.api_key:
            raise RuntimeError("APIキーが未設定（設定画面から登録してから一覧を取得して）")
        body = _get_json(
            f"{self.BASE}/models?key={urllib.parse.quote(self.api_key)}&pageSize=100"
        )
        names = []
        for m in body.get("models", []):
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            if "generateContent" in m.get("supportedGenerationMethods", []):
                names.append(name)
        return names

    def _build_payload(self, messages, max_tokens):
        """chat()/chat_stream()共通のリクエストペイロード構築（FieriA拡張:
        ストリーミング追加時に両者へ処理を重複させないための抽出。挙動はchat()の
        従来ロジックと完全に同一）。"""
        system_parts = []
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                role = "model" if m["role"] == "assistant" else "user"
                parts = [{"text": m["content"]}]
                for img in m.get("images") or []:
                    parts.append({"inline_data": {
                        "mime_type": img["mime"], "data": img["b64"]}})
                contents.append({"role": role, "parts": parts})
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
            },
        }
        # FieriA拡張: max_tokens=0でモデル既定に任せる（NikoVoice版からの分岐点）
        # 明示引数（wrapup等）はインスタンス設定より優先。実効値が0以下/Noneなら
        # "maxOutputTokens"キー自体を送らず、モデル側の既定上限に任せる。
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        if effective_max_tokens and int(effective_max_tokens) > 0:
            payload["generationConfig"]["maxOutputTokens"] = int(effective_max_tokens)
        if self.reasoning_effort in _GEMINI_THINKING_BUDGET:
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingBudget": _GEMINI_THINKING_BUDGET[self.reasoning_effort]
            }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        # FieriA拡張: Web検索。google_searchツールを足すだけでGeminiが必要に応じて
        # 自律的に検索する（プロンプト方式ツールとは別軸——ネイティブfunction calling
        # 未使用のFieriAでもこのツール自体はGemini側のgroundingフックであり、
        # 既存のプロンプト方式ツール呼び出しとは衝突しない）。
        if self.web_search:
            payload["tools"] = [{"google_search": {}}]
        return payload

    def chat(self, messages, max_tokens=None):
        payload = self._build_payload(messages, max_tokens)
        body = _post_json(
            f"{self.BASE}/models/{self.model}:generateContent?key={urllib.parse.quote(self.api_key)}",
            payload,
        )
        candidates = body.get("candidates")
        if not candidates:
            reason = body.get("promptFeedback", {}).get("blockReason", "不明")
            raise RuntimeError(f"Gemini応答が空（blockReason: {reason}）")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise RuntimeError(f"Gemini応答にテキストなし（finishReason: {candidates[0].get('finishReason')}）")
        # FieriA拡張: Web検索。出典があれば本文末尾に付ける。groundingMetadataの
        # 形が想定と違ってもここで例外を握り、検索メタの不備で本文まで失わせない。
        if self.web_search:
            try:
                sources = _extract_grounding_sources(candidates[0])
                if sources:
                    text += _format_sources_block(
                        sources, _extract_search_queries(candidates[0]))
            except Exception:
                pass
        return text

    # FieriA拡張: ストリーミング
    def chat_stream(self, messages, max_tokens=None):
        """streamGenerateContent?alt=sseでSSEを叩き、candidates[0].content.partsの
        text差分を逐次yieldする。空行・コメント行(":"始まり)はSSEのkeep-aliveとして
        無視するが、"data:"行のJSONパースに失敗した場合は例外を送出する
        （それまでyield済みの差分は呼び出し側にそのまま残る）。"""
        payload = self._build_payload(messages, max_tokens)
        url = (f"{self.BASE}/models/{self.model}:streamGenerateContent"
               f"?alt=sse&key={urllib.parse.quote(self.api_key)}")
        # FieriA拡張: Web検索。ストリーム中の各チャンクにgroundingMetadataが
        # 断片的に載りうるため、出典(uri)を重複なく蓄積し、ストリーム終端で
        # まとめて最後の差分としてyieldする（本文と混ざらないよう分離）。
        collected_sources = []
        seen_uris = set()
        collected_queries = []
        seen_queries = set()
        for raw_line in _post_sse_stream(url, payload):
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data:
                continue
            try:
                parsed = json.loads(data)
            except Exception:
                raise RuntimeError(f"Geminiストリームのパースに失敗: {data[:200]}")
            candidates = parsed.get("candidates")
            if not candidates:
                continue
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if text:
                yield text
            if self.web_search and len(collected_sources) < _GEMINI_MAX_SOURCES:
                try:
                    for title, uri in _extract_grounding_sources(candidate):
                        if uri in seen_uris:
                            continue
                        seen_uris.add(uri)
                        collected_sources.append((title, uri))
                        if len(collected_sources) >= _GEMINI_MAX_SOURCES:
                            break
                except Exception:
                    pass
            if self.web_search and len(collected_queries) < 3:
                try:
                    for q in _extract_search_queries(candidate):
                        if q in seen_queries:
                            continue
                        seen_queries.add(q)
                        collected_queries.append(q)
                        if len(collected_queries) >= 3:
                            break
                except Exception:
                    pass
        if self.web_search and collected_sources:
            yield _format_sources_block(collected_sources, collected_queries)


class XaiOAuthLLM(OpenAICompatLLM):
    """xAI (Grok) をOAuthトークンで使う。APIキーの代わりに xai_oauth モジュールが管理する
    access_token を毎リクエスト時に解決する（期限が近ければ自動refresh）。
    チャット処理・モデル一覧はOpenAI互換のまま全部親クラスを再利用する。"""

    # OAuthトークンで /models が引けない場合に返す既定リスト
    # （公開実装 opencode-grok-auth の DEFAULT_XAI_MODELS、2026-07-08時点）
    FALLBACK_MODELS = ["grok-4.3", "grok-4.20-0309-reasoning",
                       "grok-4.20-0309-non-reasoning", "grok-4.20-multi-agent-0309"]

    def __init__(self, entry, api_key, temperature, max_tokens, reasoning_effort="", web_search=False):
        super().__init__(entry, api_key, temperature, max_tokens, reasoning_effort)
        # FieriA拡張: Web検索（Grok Live Search）。search_parametersがサブスク
        # OAuth経由で実際に効くかは未検証（xai_oauth.get_access_token()が返す
        # トークンの権限範囲次第。通らない場合はAPI側がフィールドを無視するだけの
        # 想定で、リクエスト自体が壊れることは想定していない——が保証はしない）。
        self.web_search = bool(web_search)

    def _headers(self):
        import xai_oauth
        return {"Authorization": f"Bearer {xai_oauth.get_access_token()}"}

    def _extra_payload_fields(self):
        if self.web_search:
            return {"search_parameters": {"mode": "auto"}}
        return {}

    def health(self):
        import xai_oauth
        if not xai_oauth.is_logged_in():
            return False, "Grokに未ログイン（設定画面の「Grokにログイン」から）"
        return super().health()

    def list_models(self):
        try:
            return super().list_models()
        except Exception:
            return list(self.FALLBACK_MODELS)


def _iter_sse_events(raw_text):
    """SSEテキストを(event, data)のリストにパースする(\n\n区切りのブロック単位)"""
    events = []
    for block in raw_text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        if data_lines:
            events.append((event_name, "\n".join(data_lines)))
    return events


def _extract_sse_final_response(raw_text):
    """SSEイベント列から、responseキーを含む最後のイベントを取り出す。
    見つからなければ例外。"""
    latest = None
    for _event_name, data in _iter_sse_events(raw_text):
        try:
            parsed = json.loads(data)
        except Exception:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("response"), dict):
            latest = parsed["response"]
    if latest is None:
        raise RuntimeError("SSEストリームにresponseイベントが見つからなかった")
    return latest


def _responses_output_text(response_obj):
    """Responses APIのresponseオブジェクトからoutput_textを連結して返す"""
    texts = []
    for item in response_obj.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                texts.append(part.get("text", ""))
    return "".join(texts).strip()


def _extract_sse_delta_text(raw_text):
    """response.output_text.deltaイベントのdelta断片を連結して返す。

    stream:true時、本文はこちらのイベント経由で届き、最終response.completed
    イベントのoutputは空になることがある（Codexバックエンドの既知の挙動。
    Hermes Agent実装のcodex_responses_adapter.pyコメントで確認済み:
    "The Codex backend can return empty output when the answer was
    delivered entirely via stream events."）。"""
    chunks = []
    for event_name, data in _iter_sse_events(raw_text):
        try:
            parsed = json.loads(data)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        if (parsed.get("type") or event_name) != "response.output_text.delta":
            continue
        delta = parsed.get("delta")
        if isinstance(delta, str):
            chunks.append(delta)
    return "".join(chunks).strip()


class CodexOAuthLLM(_ChatStreamFallbackMixin):
    """ChatGPT Plus/ProのサブスクをOAuth経由で使うプロバイダ。OpenAI公式のResponses API
    （常にSSEで返る）を使う。OpenAICompatLLMは継承しない——リクエスト/レスポンス形式が
    /chat/completionsと違うため。

    FieriA拡張: ストリーミング。chat_stream()は独自実装せず_ChatStreamFallbackMixinの
    既定（chat()を1回呼んで全文を1個だけyield）に任せる——Responses APIのSSEは
    response.output_text.delta等、/chat/completionsとは異なる専用パースが必要で、
    real-time表示のための実装コストに見合わないため（OAuth系は「未対応」枠として許容）。"""

    CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
    FALLBACK_MODELS = ["gpt-5.5", "gpt-5.4-mini", "gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"]

    def __init__(self, entry, temperature, max_tokens, reasoning_effort=""):
        self.model = entry.get("model", "gpt-5.5")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = (reasoning_effort or "").strip()

    def _headers(self):
        import openai_codex_oauth
        return {
            "Authorization": f"Bearer {openai_codex_oauth.get_access_token()}",
            "chatgpt-account-id": openai_codex_oauth.get_account_id(),
            "OpenAI-Beta": "responses=experimental",
            "Content-Type": "application/json",
        }

    def health(self):
        import openai_codex_oauth
        if not openai_codex_oauth.is_logged_in():
            return False, "ChatGPTに未ログイン（設定画面の「ChatGPTにログイン」から）"
        try:
            names = self.list_models()
            if names and self.model not in names:
                return True, f"接続OK（ただし {self.model} が一覧に無い。例: {', '.join(names[:5])}）"
            return True, self.model
        except Exception as e:
            return False, f"Codexに接続できない: {e}"

    def list_models(self):
        import openai_codex_oauth
        try:
            access_token = openai_codex_oauth.get_access_token()
            body = _get_json(
                f"{self.CODEX_BASE_URL}/models?client_version=1.0.0",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            entries = body.get("models", []) if isinstance(body, dict) else []
            visible = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                slug = item.get("slug")
                if not isinstance(slug, str) or not slug.strip():
                    continue
                visibility = str(item.get("visibility") or "").strip().lower()
                if visibility in ("hide", "hidden"):
                    continue
                priority = item.get("priority")
                rank = int(priority) if isinstance(priority, (int, float)) else 10_000
                visible.append((rank, slug.strip()))
            visible.sort(key=lambda x: (x[0], x[1]))
            names = [slug for _, slug in visible]
            return names if names else list(self.FALLBACK_MODELS)
        except Exception:
            return list(self.FALLBACK_MODELS)

    def chat(self, messages, max_tokens=None):
        system_text = next((m["content"] for m in messages if m["role"] == "system"), "")
        payload = {
            "model": self.model,
            "instructions": system_text,
            "store": False,
            "stream": True,
            "input": [
                {"role": m["role"], "content": m["content"]}
                for m in messages if m["role"] != "system"
            ],
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        raw = _post_sse(f"{self.CODEX_BASE_URL}/responses", payload, headers=self._headers())

        # stream:true時は本文がresponse.output_text.deltaで届く（最終イベントの
        # outputが空になりうるため、まずこちらを優先する）
        text = _extract_sse_delta_text(raw)
        if text:
            return text

        # deltaが無かった場合の保険として最終response.completedを見る
        response_obj = _extract_sse_final_response(raw)
        status = str(response_obj.get("status") or "").lower()
        if status in ("failed", "cancelled"):
            error = response_obj.get("error")
            detail = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(
                f"Codex応答が失敗（status={status}）: "
                f"{detail or json.dumps(error, ensure_ascii=False)[:200]}"
            )
        text = _responses_output_text(response_obj)
        if not text:
            raise RuntimeError(f"Codex応答が空: {json.dumps(response_obj, ensure_ascii=False)[:200]}")
        return text


def _is_free_openrouter_model(m):
    """OpenRouterのモデルオブジェクトが無料(prompt/completionの単価が両方0)かどうか"""
    pricing = m.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


class OpenRouterLLM(OpenAICompatLLM):
    """OpenRouter: 多数のモデルを1つのAPIキーで横断できるゲートウェイ。
    free_onlyがTrueならlist_models()を無料モデル（pricing.prompt/completionが
    両方0）だけに絞る。chat()/health()はOpenAICompatLLMのまま（OpenAI互換）。"""

    def __init__(self, entry, api_key, temperature, max_tokens, reasoning_effort=""):
        super().__init__(entry, api_key, temperature, max_tokens, reasoning_effort)
        self.free_only = bool(entry.get("free_only", False))

    def list_models(self):
        body = _get_json(f"{self.base_url}/models", headers=self._headers())
        models = body.get("data", [])
        if self.free_only:
            models = [m for m in models if _is_free_openrouter_model(m)]
        return [m.get("id", "?") for m in models]


def build_provider(entry, env, temperature, max_tokens, reasoning_effort="", web_search=False):
    """プロバイダ設定エントリ1つからインスタンスを作る（GUIの一覧取得でも使う）。

    FieriA拡張: Web検索。web_searchはgemini/xai_oauthタイプのコンストラクタにだけ
    渡す——対応外プロバイダ（openai_compat等）は元々このキーワード自体を受け付けない
    ため、ここで渡し先を分岐させて「渡さない」ことで無視を実現する。"""
    api_key = resolve_api_key(entry, env)
    ptype = entry.get("type", "openai_compat")
    if ptype == "openai_compat":
        return OpenAICompatLLM(entry, api_key, temperature, max_tokens, reasoning_effort)
    if ptype == "gemini":
        return GeminiLLM(entry, api_key, temperature, max_tokens, reasoning_effort, web_search=web_search)
    if ptype == "xai_oauth":
        return XaiOAuthLLM(entry, "", temperature, max_tokens, reasoning_effort, web_search=web_search)
    if ptype == "openai_codex_oauth":
        return CodexOAuthLLM(entry, temperature, max_tokens, reasoning_effort)
    if ptype == "openrouter":
        return OpenRouterLLM(entry, api_key, temperature, max_tokens, reasoning_effort)
    raise ValueError(f"未対応のLLMタイプ: {ptype}")


class FallbackLLM(_ChatStreamFallbackMixin):
    """プライマリのchat()が例外を投げたら、フォールバック側に自動で切り替える薄いラッパー。
    health()/list_models()はプライマリのものをそのまま使う（UI表示はプライマリ基準）。
    レート制限(429)等はhealth()の事前チェックでは拾えないことがあるため、
    TTSのフォールバックとは違い「実際に呼んで失敗したら切り替える」方式にしている。

    FieriA拡張: ストリーミング。chat_stream()は独自実装せず_ChatStreamFallbackMixinの
    既定に任せる——ストリーム途中でプライマリが失敗した場合に「既にUIへ流れた文字」を
    差し替えるのは表示上不自然なため、フォールバック判定はchat()と同じ「1回丸ごと
    試して失敗したら切り替える」方式のまま、ストリーミングでは表示が一括になる
    （self.chat()自体がプライマリ/フォールバックの切替を内包している）。"""

    def __init__(self, primary, fallback, on_log=None):
        self.primary = primary
        self.fallback = fallback
        self._on_log = on_log or (lambda t: None)

    def health(self):
        return self.primary.health()

    def list_models(self):
        return self.primary.list_models()

    def chat(self, messages, max_tokens=None):
        try:
            return self.primary.chat(messages, max_tokens=max_tokens)
        except Exception as e:
            self._on_log(f"[LLM] メインが失敗（{e}）。フォールバックに切り替えるじょ")
            return self.fallback.chat(messages, max_tokens=max_tokens)


def create_llm(llm_cfg, env, on_log=None):
    name = llm_cfg.get("provider", "ollama")
    providers = llm_cfg.get("providers", {})
    if name not in providers:
        raise ValueError(f"llm.provider '{name}' が providers に無い")
    temperature = float(llm_cfg.get("temperature", 0.8))
    max_tokens = int(llm_cfg.get("max_tokens", 400))

    def _effort_for(entry):
        # プロバイダentry個別の指定を優先し、無ければllm直下（旧・手書き互換）に落ちる
        return ((entry.get("reasoning_effort") or llm_cfg.get("reasoning_effort", "") or "")).strip()

    # FieriA拡張: Web検索。llm直下のグローバルトグル（プロバイダ個別のproviders[name]
    # ではない）なので、プライマリ・フォールバック両方の構築に同じ値を渡す。
    web_search = bool(llm_cfg.get("web_search", False))
    primary = build_provider(providers[name], env, temperature, max_tokens,
                             _effort_for(providers[name]), web_search)

    fb_name = (llm_cfg.get("fallback_provider") or "").strip()
    if fb_name and fb_name != name and fb_name in providers:
        fb_entry = dict(providers[fb_name])
        fb_model = (llm_cfg.get("fallback_model") or "").strip()
        if fb_model:
            fb_entry["model"] = fb_model  # フォールバック専用のモデル指定（プロバイダ本来のmodelとは独立）
        fallback = build_provider(fb_entry, env, temperature, max_tokens,
                                  _effort_for(fb_entry), web_search)
        return FallbackLLM(primary, fallback, on_log=on_log)
    return primary
