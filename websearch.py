"""websearch.py — アプリ内蔵検索と、検索専用Gemini呼び出しの選択。
memory_toolsのweb_searchツールから呼ばれる想定（Task 2）。ddgsは関数内遅延import
（未インストール環境でもモジュールimport自体は壊れない）。
実行失敗は {"ok": False, "detail": ...} で返し、例外で会話を壊さない(設計書§8-3と同じ思想)。"""

import copy
from dataclasses import dataclass, field

SEARCH_BACKENDS = ("auto", "builtin", "gemini")


@dataclass(frozen=True)
class SearchPlan:
    label: str
    note: str
    run: object = field(repr=False)


def make_search_plan(llm_cfg, env):
    """承認前に検索先・モデルを確定する。会話・記憶の引数は持たない。"""
    llm_cfg = copy.deepcopy(llm_cfg or {})
    backend = llm_cfg.get("web_search_backend", "auto")
    if backend not in SEARCH_BACKENDS:
        raise ValueError("検索方法の設定が不正です。設定画面で選び直してください")
    providers = llm_cfg.get("providers", {})
    current = providers.get(llm_cfg.get("provider", ""), {})
    if backend == "builtin" or (backend == "auto" and current.get("type") != "gemini"):
        return SearchPlan("アプリ内蔵検索", "表示された検索語を外部の検索サービスへ送信します。",
                          lambda query: search_text(query))
    entry = current if backend == "auto" else providers.get("gemini", {})
    if entry.get("type") != "gemini":
        raise ValueError("Gemini検索には、プロバイダのGemini設定が必要です")
    from llm import GeminiLLM, resolve_api_key
    key = resolve_api_key(entry, env)
    if not key:
        raise ValueError("Gemini検索のAPIキーが未設定です。設定画面で登録してください")
    client = GeminiLLM(entry, key, float(llm_cfg.get("temperature", 0.8)), 2000,
                       entry.get("reasoning_effort", llm_cfg.get("reasoning_effort", "")),
                       web_search=True)
    def search(query):
        try:
            return client.search_web(query)
        except Exception:
            return {"ok": False, "results": [], "detail":
                    "Gemini検索に失敗しました。APIキー・モデルの検索対応・利用上限を確認してください"}
    return SearchPlan(
        f"Gemini検索（{client.model}）",
        "表示された依頼文だけをGeminiに送り、Google検索を行います。会話や記憶は送りません。"
        "Geminiが実際の検索語を組み立てます。API利用料がかかる場合があります。",
        search,
    )


_MAX_CHARS = 3000
_CLIP_SUFFIX = "（以下省略）"


def search_text(query, max_results=5):
    """DuckDuckGoでテキスト検索する。
    戻り値: {"ok": bool, "results": [{"title","href","body"}], "detail": str}
    query空/空白のみ・ddgs未導入・その他すべての例外はok=Falseで返す。"""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "results": [], "detail": "検索語が空です"}
    try:
        from ddgs import DDGS
    except ImportError:
        return {"ok": False, "results": [], "detail": "検索ライブラリ未導入"}
    try:
        results = DDGS().text(query, max_results=max_results, region="jp-jp") or []
    except Exception as e:
        return {"ok": False, "results": [], "detail": f"検索に失敗しました: {e}"}
    if not results:
        return {"ok": True, "results": [], "detail": "0件"}
    return {"ok": True, "results": results, "detail": f"{len(results)}件"}


def format_results(results):
    """検索結果を整形する。1件=「タイトル\\nURL\\n抜粋」の3行、件同士は空行区切り。
    全体で3000字を超えたらクリップし、末尾に「（以下省略）」を付ける。"""
    blocks = [
        "{}\n{}\n{}".format(r.get("title", ""), r.get("href", ""), r.get("body", ""))
        for r in results
    ]
    text = "\n\n".join(blocks)
    if len(text) > _MAX_CHARS:
        cut = _MAX_CHARS - len(_CLIP_SUFFIX)
        text = text[:cut] + _CLIP_SUFFIX
    return text
