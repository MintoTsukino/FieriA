"""
xai_oauth.py — xAI (Grok) の OAuth 2.0 PKCE ログインとトークン管理

SuperGrok / X Premium+ の契約者は、APIキーを発行しなくても既存サブスクで
Grok モデルを使える。その認証フローを NikoVoice に組み込むためのモジュール。

仕様の出どころ（2026-07-08 実地確認）:
- OIDCメタデータは https://auth.x.ai/.well-known/openid-configuration を実際に取得して確認
  （authorize/token/device の各エンドポイント、PKCE S256、refresh_token 対応）
- client_id・スコープ・コールバックポートは公開実装 opencode-grok-auth の定数から。
  「Public desktop OAuth client ID used by the Grok CLI flow. This is not a secret.」と明記されている。
  redirect_uri はクライアント登録に紐づくため、同じ 127.0.0.1:56121/callback を使う
  （勝手なポートに変えると invalid redirect_uri で弾かれる）。

フロー:
  start_login() → ブラウザで auth.x.ai が開く → ユーザーがログイン・許可
  → 127.0.0.1:56121 のローカルサーバが認可コードを受け取る
  → トークンエンドポイントで access_token / refresh_token に交換 → xai_oauth.json に保存
以後は get_access_token() が期限切れ2分前に自動で refresh する。
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import config

HERE = os.path.dirname(os.path.abspath(__file__))
# FieriA改変: 置き場を config.HOME 直下へ移設（exeの差し替え・フォルダ削除で
# トークンが消えないように。2026-07-22）。旧位置（コード隣）にあり新位置に
# 無ければ config.migrate_secret_file() が初回起動時にコピー移行する。
if os.environ.get("FIERIA_TESTING"):
    # テスト実行中は実のトークンファイルに一切触れない（env.pyと同じ思想）。
    TOKEN_PATH = os.path.join(config.HOME, "xai_oauth.json")
else:
    TOKEN_PATH = config.migrate_secret_file(HERE, "xai_oauth.json")

ISSUER = "https://auth.x.ai"
AUTHORIZE_URL = f"{ISSUER}/oauth2/authorize"
TOKEN_URL = f"{ISSUER}/oauth2/token"

# Grok CLI と同じ公開デスクトップクライアント（秘密ではない）
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 56121
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"

CALLBACK_TIMEOUT_SEC = 180
REFRESH_SKEW_SEC = 120   # 期限のこの秒数前になったら refresh する

_lock = threading.Lock()


# ================================================================
# トークンの保存・読込
# ================================================================

def load_tokens():
    if not os.path.isfile(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_tokens(tokens):
    # HOME未作成のまま先にOAuthログインした場合でも書けるようにする
    os.makedirs(os.path.dirname(TOKEN_PATH) or ".", exist_ok=True)
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TOKEN_PATH)


def clear_tokens():
    """ログアウト"""
    if os.path.isfile(TOKEN_PATH):
        os.remove(TOKEN_PATH)


def is_logged_in():
    tokens = load_tokens()
    return bool(tokens and tokens.get("refresh_token"))


def _expires_at(token_response):
    return int(time.time()) + int(token_response.get("expires_in", 3600))


# ================================================================
# PKCE ユーティリティ
# ================================================================

def make_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(challenge, state, nonce):
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # auth.x.ai はこの公開クライアントに対して referrer=hermes-agent を要求する
        # （公開実装 opencode-grok-auth のコメントより。無いと「Grok Buildにコードを
        #   コピー」という手動コピー画面に誘導される。2026-07-08 実挙動でも確認）
        "plan": "generic",
        "referrer": "hermes-agent",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


# ================================================================
# トークンエンドポイント
# ================================================================

def _post_token(data):
    body = urllib.parse.urlencode(data).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json",
                 "User-Agent": "NikoVoice"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # xAIが返すエラー本文(invalid_grant等)を診断できるよう、メッセージに含める
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}")


def exchange_code(code, verifier, challenge=""):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }
    if challenge:   # xAIは交換時にもchallengeを受け取る（公開実装に合わせる）
        data["code_challenge"] = challenge
        data["code_challenge_method"] = "S256"
    tokens = _post_token(data)
    tokens["expires_at"] = _expires_at(tokens)
    save_tokens(tokens)
    return tokens


def refresh_tokens():
    """refresh_token で access_token を更新（ローテーションにも対応）。
    失敗（revoke等）したら例外を投げる。呼び出し側で「再ログインして」を案内する。"""
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise RuntimeError("未ログイン（設定画面からGrokにログインして）")
    new = _post_token({
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": CLIENT_ID,
    })
    new["expires_at"] = _expires_at(new)
    if "refresh_token" not in new:      # ローテーションされない実装なら旧を引き継ぐ
        new["refresh_token"] = tokens["refresh_token"]
    save_tokens(new)
    return new


def get_access_token():
    """有効な access_token を返す。期限が近ければ自動 refresh。未ログインなら例外。"""
    with _lock:
        tokens = load_tokens()
        if not tokens:
            raise RuntimeError("未ログイン（設定画面からGrokにログインして）")
        if int(time.time()) >= int(tokens.get("expires_at", 0)) - REFRESH_SKEW_SEC:
            tokens = refresh_tokens()
        return tokens["access_token"]


# ================================================================
# ログインフロー（PKCE + localhostコールバック）
# ================================================================

class LoginFlow:
    """ログインの1セッション。start()でブラウザが開き、裏でコールバックを待つ。
    status() が "pending" → "ok" / "error: ..." に変わる。
    xAIがリダイレクトせず「コードをコピーして」画面を出すケースがあるため、
    submit_code() での手動貼り付け完了にも対応する（URL全体でも素のコードでもよい）。"""

    def __init__(self):
        self._status = "pending"
        self._thread = None
        self._server = None
        self._verifier = None
        self._challenge = None
        self._state = None

    def status(self):
        return self._status

    def submit_code(self, pasted):
        """ブラウザに表示された認可コード（またはコールバックURL丸ごと）を手で貼って完了する"""
        raw = (pasted or "").strip()
        if not raw:
            return "error: コードが空"
        code, state = raw, self._state
        try:
            parsed = urllib.parse.urlparse(raw)
            if parsed.scheme in ("http", "https"):
                q = urllib.parse.parse_qs(parsed.query)
                if "error" in q:
                    return f"error: {q.get('error_description', q['error'])[0]}"
                code = (q.get("code") or [""])[0]
                state = (q.get("state") or [self._state])[0]
        except Exception:
            pass   # 素のコードとして扱う
        if not code:
            return "error: コードが読み取れなかった"
        code = "".join(code.split())   # 折り返しコピーで混入した改行・空白を除去
        if state != self._state:
            return "error: stateが一致しない（最新のログイン試行のコードを貼って）"
        try:
            exchange_code(code, self._verifier, self._challenge)
            self._status = "ok"
        except Exception as e:
            self._status = f"error: トークン交換に失敗（{e}）"
        return self._status

    def start(self):
        verifier, challenge = make_pkce_pair()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        self._verifier, self._challenge, self._state = verifier, challenge, state
        url = build_authorize_url(challenge, state, nonce)

        flow = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != REDIRECT_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                ok = False
                if params.get("state", [""])[0] != state:
                    flow._status = "error: stateが一致しない（別のログイン試行と混線した可能性）"
                elif "error" in params:
                    flow._status = f"error: {params['error'][0]}"
                elif "code" in params:
                    try:
                        exchange_code(params["code"][0], verifier, challenge)
                        flow._status = "ok"
                        ok = True
                    except Exception as e:
                        flow._status = f"error: トークン交換に失敗（{e}）"
                else:
                    flow._status = "error: 認可コードが返ってこなかった"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                msg = ("ログイン完了！このタブは閉じて、NikoVoiceに戻ってね。" if ok
                       else "ログインに失敗した…。NikoVoiceに戻ってやり直してみて。")
                self.wfile.write(f"<html><body style='font-family:sans-serif;text-align:center;padding-top:80px'><h2>{msg}</h2></body></html>".encode("utf-8"))

        try:
            server = http.server.HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler)
        except OSError as e:
            self._status = f"error: ポート{REDIRECT_PORT}が使用中（{e}）"
            return self._status
        server.timeout = 1.0   # 1秒ごとに起きて期限チェック（handle_request 1回分の上限）
        self._server = server
        deadline = time.time() + CALLBACK_TIMEOUT_SEC

        def wait():
            try:
                # /callback を処理するまで受け付け続ける。faviconなどの無関係な
                # リクエストが1発来ただけで店じまいしない（実挙動で踏んだ罠）。
                while self._status == "pending" and time.time() < deadline:
                    server.handle_request()
                # 期限切れでも status は "pending" のまま残す——コールバックが来なくても
                # 手動のコード貼り付し（submit_code）でログインを完了できるため、
                # ここで error にするとUIが「失敗」と誤表示してしまう
            finally:
                server.server_close()

        self._thread = threading.Thread(target=wait, daemon=True)
        self._thread.start()
        webbrowser.open(url)
        return "pending"


_current_flow = None


def start_login():
    """GUIから呼ぶ入口。既存フローがpendingでも新しく始める（古い方は破棄）。"""
    global _current_flow
    _current_flow = LoginFlow()
    return _current_flow.start()


def login_status():
    if _current_flow is None:
        return "idle"
    return _current_flow.status()


def submit_code(pasted):
    """「コードをコピーして」画面が出た場合の手動完了。start_login()の後に呼ぶ。"""
    if _current_flow is None:
        return "error: 先に「Grokにログイン」を押して"
    return _current_flow.submit_code(pasted)
