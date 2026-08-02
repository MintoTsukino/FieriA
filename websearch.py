"""websearch.py — DuckDuckGo(ddgs)によるキー不要のWeb検索ラッパー。
memory_toolsのweb_searchツールから呼ばれる想定（Task 2）。ddgsは関数内遅延import
（未インストール環境でもモジュールimport自体は壊れない）。
実行失敗は {"ok": False, "detail": ...} で返し、例外で会話を壊さない(設計書§8-3と同じ思想)。"""

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
