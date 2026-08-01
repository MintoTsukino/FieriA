"""tests/test_pdf_extract_text.py — pdf_render.extract_text（PDFテキスト抽出モード・
2026-07-22追加）の検証。

テキスト描画オペレータ入りの最小PDFフィクスチャはtests/test_gui_pdf.pyに追加した
MINI_PDF_TEXT_1PAGE/MINI_PDF_TEXT_2PAGEを再利用する（既存のMINI_PDF_1PAGE/3PAGEは
コンテンツストリームが無くテキスト層が無いため、テキスト層無しPDFのテストにはそちらを使う）。

20000字クリップ・前進保証のテストは、実PDFで巨大コンテンツストリームを手書きするのが
非現実的なため、_assemble_extracted_text（純粋関数・PDFに依存しない中核ロジック）を
直接呼び出して検証する。
"""
import pytest


def _t1page():
    import test_gui_pdf as fixtures
    return fixtures.MINI_PDF_TEXT_1PAGE


def _t2page():
    import test_gui_pdf as fixtures
    return fixtures.MINI_PDF_TEXT_2PAGE


# --- extract_text: 正常系・ページ区切り・start_page ---

def test_extract_text_single_page_returns_text_with_page_marker():
    import pdf_render
    result = pdf_render.extract_text(_t1page())
    assert result["text"] == "[p1]\nHello PDF\n"
    assert result["total_pages"] == 1
    assert result["end_page"] == 1
    assert result["has_more"] is False
    assert result["next_start"] is None
    assert result["no_text_layer"] is False


def test_extract_text_multi_page_concatenates_with_page_markers():
    import pdf_render
    result = pdf_render.extract_text(_t2page())
    assert result["text"] == "[p1]\nPage One Text\n[p2]\nPage Two Text\n"
    assert result["total_pages"] == 2
    assert result["end_page"] == 2
    assert result["has_more"] is False


def test_extract_text_start_page_skips_earlier_pages():
    import pdf_render
    result = pdf_render.extract_text(_t2page(), start_page=2)
    assert result["text"] == "[p2]\nPage Two Text\n"
    assert result["end_page"] == 2
    assert result["has_more"] is False


def test_extract_text_max_pages_limits_range_and_reports_continuation():
    import pdf_render
    result = pdf_render.extract_text(_t2page(), start_page=1, max_pages=1)
    assert result["text"] == ("[p1]\nPage One Text\n"
                               "\n…（全2ページ。続きは start_page=2 で読める）")
    assert result["end_page"] == 1
    assert result["has_more"] is True
    assert result["next_start"] == 2


# --- extract_text: バリデーション ---

def test_extract_text_rejects_zero_start_page():
    import pdf_render
    with pytest.raises(ValueError):
        pdf_render.extract_text(_t1page(), start_page=0)


def test_extract_text_rejects_negative_start_page():
    import pdf_render
    with pytest.raises(ValueError):
        pdf_render.extract_text(_t1page(), start_page=-1)


def test_extract_text_rejects_non_int_start_page():
    import pdf_render
    with pytest.raises(ValueError):
        pdf_render.extract_text(_t1page(), start_page="1")


def test_extract_text_rejects_start_page_beyond_total_pages():
    import pdf_render
    with pytest.raises(ValueError):
        pdf_render.extract_text(_t2page(), start_page=3)  # 全2ページしかない


def test_extract_text_rejects_zero_max_pages():
    import pdf_render
    with pytest.raises(ValueError):
        pdf_render.extract_text(_t1page(), max_pages=0)


def test_extract_text_rejects_non_int_max_pages():
    import pdf_render
    with pytest.raises(ValueError):
        pdf_render.extract_text(_t1page(), max_pages="1")


def test_extract_text_broken_pdf_raises():
    import pdf_render
    with pytest.raises(Exception):
        pdf_render.extract_text(b"this is not a pdf at all, just garbage bytes")


# --- extract_text: テキスト層が無いPDF（既存のMINI_PDF_1PAGEを再利用——
#     コンテンツストリームが無くResourcesも空のためテキスト層が存在しない） ---

def test_extract_text_no_text_layer_returns_image_mode_guidance():
    import pdf_render
    import test_gui_pdf as fixtures
    result = pdf_render.extract_text(fixtures.MINI_PDF_1PAGE)
    assert result["no_text_layer"] is True
    assert 'mode:"image"' in result["text"]
    assert result["has_more"] is False


# --- _assemble_extracted_text: 中核ロジックを直接検証（20000字クリップ・
#     前進保証はPDF実体を経由せず合成データで検証する） ---

def test_assemble_no_pages_returns_no_text_layer():
    import pdf_render
    result = pdf_render._assemble_extracted_text([], total_pages=0)
    assert result["no_text_layer"] is True
    assert result["has_more"] is False
    assert result["end_page"] is None


def test_assemble_all_blank_pages_returns_no_text_layer():
    import pdf_render
    result = pdf_render._assemble_extracted_text(
        [(1, ""), (2, "   \n")], total_pages=2)
    assert result["no_text_layer"] is True
    assert 'mode:"image"' in result["text"]


def test_assemble_stops_at_page_boundary_when_next_page_would_overflow():
    """累積が予算を超える場合、超えるページ自体は未消費のまま境界で止める
    （clipped_mid_page=Falseで、そのページは次回のstart_pageとして案内される）。"""
    import pdf_render
    max_chars = 20
    page_texts = [(1, "a" * 10), (2, "b" * 10), (3, "c" * 10)]
    result = pdf_render._assemble_extracted_text(page_texts, total_pages=3, max_chars=max_chars)
    # p1のセグメント "[p1]\n" + "a"*10 + "\n" = 4+10+1=15字 <= 20 -> 消費
    # p2を足すと15+15=30>20 -> p2は未消費のまま打ち切り
    assert result["end_page"] == 1
    assert result["clipped_mid_page"] is False
    assert result["has_more"] is True
    assert result["next_start"] == 2
    assert "[p2]" not in result["text"]
    assert "続きは start_page=2" in result["text"]


def test_assemble_forces_progress_when_single_page_exceeds_budget():
    """要求範囲の最初のページ単体がmax_charsを超える場合、そのページを強制的に
    消費済み扱いにして打ち切り、必ず次のstart_pageへ前進させる（無限ループ防止）。"""
    import pdf_render
    max_chars = 10
    page_texts = [(1, "x" * 100), (2, "second page text")]
    result = pdf_render._assemble_extracted_text(page_texts, total_pages=2, max_chars=max_chars)
    assert result["clipped_mid_page"] is True
    assert result["end_page"] == 1
    assert result["has_more"] is True
    assert result["next_start"] == 2
    assert len(result["text"].split("\n…")[0]) == max_chars  # クリップ後の本文はmax_chars丁度
    assert "[p2]" not in result["text"]
    assert "続きは start_page=2" in result["text"]


def test_assemble_progress_guarantee_across_repeated_calls():
    """同じ巨大ページに対してnext_startで案内された値を使い続けても、
    毎回start_pageが前進し無限ループにならないことをシミュレーションで確認する。"""
    import pdf_render
    max_chars = 10
    all_pages = [(1, "x" * 100), (2, "y" * 100), (3, "short")]
    seen_starts = []
    start = 1
    while True:
        remaining = [(n, t) for n, t in all_pages if n >= start]
        result = pdf_render._assemble_extracted_text(remaining, total_pages=3, max_chars=max_chars)
        seen_starts.append(start)
        if not result["has_more"]:
            break
        assert result["next_start"] > start  # 必ず前進する
        start = result["next_start"]
        assert len(seen_starts) < 10  # 無限ループ検知用の安全弁
    assert seen_starts == [1, 2, 3]


def test_assemble_no_continuation_note_when_all_pages_fit():
    import pdf_render
    result = pdf_render._assemble_extracted_text(
        [(1, "short")], total_pages=1, max_chars=20000)
    assert "続きは" not in result["text"]
    assert result["has_more"] is False
