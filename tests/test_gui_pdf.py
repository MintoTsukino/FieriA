"""tests/test_gui_pdf.py — gui.Bridge.render_pdf（PDF添付をページ画像化してvision
添付フローに乗せる機能）の検証。

テスト用PDFはreportlab等の追加ライブラリに依存せず、手書きの最小PDFバイト列
（%PDF-1.4ヘッダ + Catalog/Pages/Pageオブジェクト + xref + trailerを素朴に
文字列結合しただけの、仕様上ぎりぎり正当な最小構成）をそのままバイト列
リテラルとして埋め込む。pypdfium2で実際に開けることは作成時に手動確認済み。
"""
import base64


# 1ページ、200x200ptの最小PDF。
MINI_PDF_1PAGE = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>\nendobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000117 00000 n \n"
    b"trailer\n<< /Size 4 /Root 1 0 R >>\n"
    b"startxref\n205\n%%EOF"
)

# 3ページ版（truncated判定の検証用）。
MINI_PDF_3PAGE = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>\nendobj\n"
    b"4 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>\nendobj\n"
    b"5 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>\nendobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000127 00000 n \n"
    b"0000000215 00000 n \n"
    b"0000000303 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
    b"startxref\n391\n%%EOF"
)


# --- テキスト描画オペレータ入りの最小PDF（extract_textテスト用・2026-07-22追加）---
# MINI_PDF_1PAGE/3PAGEは/Resourcesが空でコンテンツストリームも無い（テキスト層無し）ため、
# pdf_render.extract_textの検証にはテキストを描画する版が別途必要。
# Helveticaは埋め込み不要の標準14フォントなのでフォントファイル無しで足りる。
# バイト列はbuild_pdf()相当のスクリプトで生成し、pypdfium2で実際に開けて
# 期待テキストが取れることを作成時に手動確認済み（xrefオフセットも実測値）。

# 1ページ、"Hello PDF"というテキストを描画する最小PDF。
MINI_PDF_TEXT_1PAGE = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"5 0 obj\n<< /Length 40 >>\nstream\n"
    b"BT /F1 18 Tf 20 250 Td (Hello PDF) Tj ET"
    b"\nendstream\nendobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000241 00000 n \n"
    b"0000000311 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
    b"startxref\n401\n%%EOF"
)

# 2ページ版（ページ区切りマーカー・start_pageの検証用）。
# p1: "Page One Text" / p2: "Page Two Text"
MINI_PDF_TEXT_2PAGE = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
    b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
    b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>\nendobj\n"
    b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"6 0 obj\n<< /Length 44 >>\nstream\n"
    b"BT /F1 18 Tf 20 250 Td (Page One Text) Tj ET"
    b"\nendstream\nendobj\n"
    b"7 0 obj\n<< /Length 44 >>\nstream\n"
    b"BT /F1 18 Tf 20 250 Td (Page Two Text) Tj ET"
    b"\nendstream\nendobj\n"
    b"xref\n0 8\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000121 00000 n \n"
    b"0000000247 00000 n \n"
    b"0000000373 00000 n \n"
    b"0000000443 00000 n \n"
    b"0000000537 00000 n \n"
    b"trailer\n<< /Size 8 /Root 1 0 R >>\n"
    b"startxref\n631\n%%EOF"
)


def _b64(raw_bytes):
    return base64.b64encode(raw_bytes).decode("ascii")


def test_render_pdf_success_returns_one_page_image():
    import gui
    bridge = gui.Bridge()

    result = bridge.render_pdf(_b64(MINI_PDF_1PAGE))

    assert result["ok"] is True
    assert result["total_pages"] == 1
    assert result["truncated"] is False
    assert len(result["pages"]) == 1
    page = result["pages"][0]
    assert page["mime"] == "image/jpeg"
    assert isinstance(page["b64"], str) and len(page["b64"]) > 0
    # b64がJPEGとしてデコード可能（先頭バイトがJPEG SOIマーカー）であることを確認
    decoded = base64.b64decode(page["b64"])
    assert decoded[:2] == b"\xff\xd8"


def test_render_pdf_truncates_when_exceeding_max_pages():
    import gui
    bridge = gui.Bridge()

    result = bridge.render_pdf(_b64(MINI_PDF_3PAGE), max_pages=2)

    assert result["ok"] is True
    assert result["total_pages"] == 3
    assert result["truncated"] is True
    assert len(result["pages"]) == 2  # 超過分(3ページ目)は変換されない


def test_render_pdf_default_max_pages_is_not_truncated_for_small_pdf():
    import gui
    bridge = gui.Bridge()

    result = bridge.render_pdf(_b64(MINI_PDF_3PAGE))

    assert result["ok"] is True
    assert result["total_pages"] == 3
    assert result["truncated"] is False
    assert len(result["pages"]) == 3


def test_render_pdf_broken_pdf_returns_ok_false():
    import gui
    bridge = gui.Bridge()

    result = bridge.render_pdf(_b64(b"this is not a pdf at all, just garbage bytes"))

    assert result["ok"] is False
    assert "error" in result and result["error"]


def test_render_pdf_invalid_base64_returns_ok_false():
    import gui
    bridge = gui.Bridge()

    result = bridge.render_pdf("!!!not-valid-base64!!!")

    assert result["ok"] is False
    assert "error" in result and result["error"]


# --- pdf_render.count_pages（レンダリングせずページ数だけ数える・2026-07-22追加）---

def test_count_pages_returns_page_count_without_rendering():
    import pdf_render
    assert pdf_render.count_pages(MINI_PDF_1PAGE) == 1
    assert pdf_render.count_pages(MINI_PDF_3PAGE) == 3


def test_count_pages_raises_on_broken_pdf():
    import pdf_render
    import pytest
    with pytest.raises(Exception):
        pdf_render.count_pages(b"this is not a pdf at all, just garbage bytes")


# --- gui._oversized_pdf_error / _attach_pdf_page_counts（send_messageのサーバ側
# サイズ検査・ページ数付与。2026-07-22追加）---
# 純粋関数（Bridge()を経由しない）として直接検証する。理由: このファイル冒頭コメントの
# 通り、実LLM APIを呼びうるsend_message自体はテストから呼ばない方針のため。

def test_oversized_pdf_error_none_when_no_images():
    import gui
    assert gui._oversized_pdf_error(None) is None
    assert gui._oversized_pdf_error([]) is None


def test_oversized_pdf_error_none_for_pdf_under_limit():
    import gui
    small_b64 = _b64(MINI_PDF_1PAGE)
    images = [{"mime": "application/pdf", "b64": small_b64}]
    assert gui._oversized_pdf_error(images) is None


def test_oversized_pdf_error_ignores_non_pdf_mimes():
    import gui
    huge_b64 = "a" * (gui.PDF_NATIVE_MAX_BYTES * 2)  # 画像扱いなら無視されるべき
    images = [{"mime": "image/png", "b64": huge_b64}]
    assert gui._oversized_pdf_error(images) is None


def test_oversized_pdf_error_rejects_pdf_over_14mb():
    import gui
    # b64長×3/4 > 14MBになるよう、余裕を持って b64長 を決める
    oversized_b64 = "a" * (gui.PDF_NATIVE_MAX_BYTES * 2)
    images = [{"mime": "application/pdf", "b64": oversized_b64}]
    err = gui._oversized_pdf_error(images)
    assert err == "PDFが大きすぎる（Gemini直読みは14MBまで）"


def test_attach_pdf_page_counts_adds_pages_for_native_pdf_attachment():
    import gui
    images = [{"mime": "application/pdf", "b64": _b64(MINI_PDF_1PAGE)}]

    out = gui._attach_pdf_page_counts(images)

    assert out[0]["pages"] == 1
    # 元のリスト/dictは変更されない（呼び出し元のimagesを書き換えない）
    assert "pages" not in images[0]


def test_attach_pdf_page_counts_leaves_non_pdf_images_untouched():
    import gui
    images = [{"mime": "image/png", "b64": "abc"}]

    out = gui._attach_pdf_page_counts(images)

    assert out == images
    assert "pages" not in out[0]


def test_attach_pdf_page_counts_swallows_broken_pdf_without_pages_key():
    import gui
    images = [{"mime": "application/pdf", "b64": _b64(b"not a real pdf")}]

    out = gui._attach_pdf_page_counts(images)

    assert "pages" not in out[0]  # 数えられなくても例外にはしない（フォールバック概算に任せる）


def test_attach_pdf_page_counts_none_images_returns_none():
    import gui
    assert gui._attach_pdf_page_counts(None) is None
    assert gui._attach_pdf_page_counts([]) == []


# --- pdf_native_supported（GeminiネイティブPDF添付の判定・2026-07-22追加）---

def test_pdf_native_supported_true_for_gemini_provider():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["llm"] = {
        "provider": "gemini",
        "providers": {"gemini": {"type": "gemini"}},
    }
    assert bridge.pdf_native_supported() is True


def test_pdf_native_supported_false_for_openai_compat_provider():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["llm"] = {
        "provider": "custom",
        "providers": {"custom": {"type": "openai_compat"}},
    }
    assert bridge.pdf_native_supported() is False


def test_pdf_native_supported_false_when_provider_unknown():
    import gui
    bridge = gui.Bridge()
    bridge._cfg["llm"] = {"provider": "does-not-exist", "providers": {}}
    assert bridge.pdf_native_supported() is False


def test_pet_states_include_love():
    """なでなで(love)状態がSOULスキン差し替え対象に含まれること。"""
    import gui
    assert "love" in gui.PET_STATES


def test_save_settings_pet_character_validates_unknown_value(bridge_factory=None):
    """未知のキャラ名はコノハに倒す（BUILTIN_PET_SKINSのキーと対応する検証）。"""
    import config as config_mod
    import gui
    bridge = gui.Bridge()
    bridge._cfg = config_mod.load_config()
    data = bridge.save_settings({"pet_character": "mokora"})
    assert data["pet_character"] == "mokora"
    data = bridge.save_settings({"pet_character": "evil-skin"})
    assert data["pet_character"] == "konoha"
