"""embed.py — Ollama /api/embed クライアントとコサイン類似（純Python）。

tts.pyの「urllib直呼びは薄い内部関数(_http_post)に集約してmonkeypatchしやすくする」
流儀に倣うが、tts.pyへの依存は作らない（User-Agent偽装も不要——ローカルエンジン
相手のため。docs/plans/2026-08-03-semantic-recall.md Global Constraints参照）。

失敗（エンジン未起動・モデル未pull・タイムアウト・レスポンス形式崩れ）はすべて
例外をそのまま送出する。会話を壊さないためのフォールバック（字面検索のみへ静かに
切り替える）はこのモジュールの責務ではなく、呼び元（recall.py等）が担う。

Ollamaの/api/embedのレスポンス形式は2026-08-03に実機で確認済み:
{"model": ..., "embeddings": [[...], [...]]}（順序はinputと対応）。
"""
import json
import urllib.request

EMBED_QUERY_PREFIX = "Instruct: Given a query, retrieve relevant memory passages\nQuery: "


def _http_post(url, data=None, headers=None, timeout=30):
    """POST。生バイトを返す。テストはこの関数をmonkeypatchして実urlopenに触れずに検証する。"""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def embed_texts(engine_url, model, texts, timeout=30):
    """POST {engine_url}/api/embed（body: {"model": model, "input": texts}）。
    渡されたtextsをそのまま1回のリクエストで送る（バッチ分割はembed_texts_batchedの責務）。
    レスポンスの"embeddings"キー（list[list[float]]）を返す。
    失敗（HTTPエラー・接続不可・JSON崩れ・キー欠落）は例外をそのまま送出する。"""
    base = engine_url.rstrip("/")
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    raw = _http_post(
        base + "/api/embed",
        data=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    data = json.loads(raw.decode("utf-8"))
    return data["embeddings"]


def embed_texts_batched(engine_url, model, texts, batch=32):
    """textsをbatch件ずつに分割してembed_texts()を複数回呼び、結果を連結して返す。
    呼び元（search.py）がここを通して大量断片を渡す想定。空入力は0回呼び出しで[]を返す。"""
    if not texts:
        return []
    result = []
    for i in range(0, len(texts), batch):
        result.extend(embed_texts(engine_url, model, texts[i:i + batch]))
    return result


def cosine(a, b):
    """コサイン類似度（純Python）。ゼロベクトル・長さ不一致は0.0を返す。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_query(engine_url, model, text):
    """クエリ用: EMBED_QUERY_PREFIXを付与して1件embedし、ベクトル1本を返す
    （設計判断3: Qwen3-Embeddingはクエリ側にのみinstructionを付けると精度が上がる）。
    失敗は例外をそのまま送出する（embed_texts同様）。"""
    return embed_texts(engine_url, model, [EMBED_QUERY_PREFIX + text])[0]
