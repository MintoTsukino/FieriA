"""tests/test_websearch.py — websearch.pyの検証。
実ネットワークには絶対に触れない: DDGSクラスをmonkeypatchでモックする
（test_llm_web_search.pyと同じ「外部I/Oをmonkeypatchで差し替える」流儀）。
"""
import sys

import pytest

import websearch


class _FakeDDGS:
    """DDGS(...).text(...) の形だけ真似るスタブ。"""

    def __init__(self, results=None, exc=None):
        self._results = results if results is not None else []
        self._exc = exc

    def text(self, query, max_results=5, region="jp-jp"):
        if self._exc is not None:
            raise self._exc
        return self._results


def _patch_ddgs(monkeypatch, fake_ddgs):
    """websearch.search_textは関数内で `from ddgs import DDGS` する設計なので、
    sys.modulesに偽の ddgs モジュールを差し込んで遅延importを差し替える。"""
    import types
    fake_module = types.ModuleType("ddgs")
    fake_module.DDGS = lambda: fake_ddgs
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)


# --- search_text ---

def test_search_text_normal_three_results(monkeypatch):
    results = [
        {"title": "記事A", "href": "https://a.example/1", "body": "抜粋A"},
        {"title": "記事B", "href": "https://b.example/2", "body": "抜粋B"},
        {"title": "記事C", "href": "https://c.example/3", "body": "抜粋C"},
    ]
    _patch_ddgs(monkeypatch, _FakeDDGS(results=results))
    result = websearch.search_text("みんと 小説家")
    assert result["ok"] is True
    assert result["results"] == results


def test_search_text_zero_results(monkeypatch):
    _patch_ddgs(monkeypatch, _FakeDDGS(results=[]))
    result = websearch.search_text("該当なしクエリ")
    assert result["ok"] is True
    assert result["results"] == []
    assert "0件" in result["detail"]


def test_search_text_ddgs_exception_returns_ok_false(monkeypatch):
    _patch_ddgs(monkeypatch, _FakeDDGS(exc=RuntimeError("network down")))
    result = websearch.search_text("なにか")
    assert result["ok"] is False
    assert result["results"] == []


def test_search_text_import_error_returns_ok_false(monkeypatch):
    # sys.modules[name] = None は `import ddgs` / `from ddgs import DDGS` を
    # ImportErrorにする標準トリック（未インストール環境の再現）。
    monkeypatch.setitem(sys.modules, "ddgs", None)
    result = websearch.search_text("なにか")
    assert result["ok"] is False
    assert "未導入" in result["detail"]


def test_search_text_empty_query_returns_ok_false(monkeypatch):
    _patch_ddgs(monkeypatch, _FakeDDGS(results=[{"title": "x", "href": "y", "body": "z"}]))
    result = websearch.search_text("")
    assert result["ok"] is False


def test_search_text_whitespace_only_query_returns_ok_false(monkeypatch):
    _patch_ddgs(monkeypatch, _FakeDDGS(results=[{"title": "x", "href": "y", "body": "z"}]))
    result = websearch.search_text("   　  ")
    assert result["ok"] is False


def test_search_text_respects_max_results_arg(monkeypatch):
    captured = {}
    fake = _FakeDDGS(results=[])

    def fake_text(query, max_results=5, region="jp-jp"):
        captured["query"] = query
        captured["max_results"] = max_results
        captured["region"] = region
        return []

    fake.text = fake_text
    _patch_ddgs(monkeypatch, fake)
    websearch.search_text("クエリ", max_results=3)
    assert captured["max_results"] == 3
    assert captured["region"] == "jp-jp"
    assert captured["query"] == "クエリ"


# --- format_results ---

def test_format_results_three_line_blocks():
    results = [
        {"title": "記事A", "href": "https://a.example/1", "body": "抜粋A"},
        {"title": "記事B", "href": "https://b.example/2", "body": "抜粋B"},
    ]
    out = websearch.format_results(results)
    blocks = out.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0] == "記事A\nhttps://a.example/1\n抜粋A"
    assert blocks[1] == "記事B\nhttps://b.example/2\n抜粋B"


def test_format_results_empty_list():
    assert websearch.format_results([]) == ""


def test_format_results_clips_at_3000_chars_with_suffix():
    results = [
        {"title": f"記事{i}", "href": f"https://x.example/{i}", "body": "あ" * 200}
        for i in range(30)
    ]
    out = websearch.format_results(results)
    assert len(out) <= 3000
    assert out.endswith("（以下省略）")
