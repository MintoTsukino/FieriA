"""pdf_render.py — PDFページを画像化する共通処理（pypdfium2）。

gui.Bridge.render_pdf（UI添付フロー・非ネイティブプロバイダ時）と
memory_tools.execute の read_pdf ツール（非Geminiプロバイダ時のフォールバック）
の両方から使う共通ロジックを1箇所に集約する。

pypdfium2はBSD/Apache系ライセンス（配布可）。PyMuPDF/fitzはAGPLで配布アプリに
ライセンス汚染するため使用禁止（README要件）。importは関数内遅延にし、
pypdfium2未導入環境でもアプリ自体は起動できるようにする（gui.render_pdfの
既存方針を踏襲）。
"""
import base64
import io

DEFAULT_MAX_PAGES = 20

MAX_EXTRACT_CHARS = 20000  # workspace.MAX_READ_CHARSと同値（read_docと同じ体感の部分読み単位に揃える）


def count_pages(raw_bytes):
    """PDFのページ数だけを数える（レンダリングはしない・軽量版）。
    memory_tools._execute_read_pdf のGeminiネイティブ分岐、gui.send_message の
    UI添付分岐の両方が、トークン見積り(engine._estimate_attachment_tokens)用に
    ページ数だけ欲しい場合に使う。壊れたPDF・暗号化PDF・不正なバイト列は例外を
    そのまま投げる（呼び出し側がtry/exceptで握り、取得できなければpages無しの
    フォールバック概算に任せる設計）。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(raw_bytes)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pdf_pages(raw_bytes, max_pages=DEFAULT_MAX_PAGES):
    """PDFバイト列の先頭からmax_pagesページまでをJPEG画像化する。

    戻り値: {"pages": [{"mime": "image/jpeg", "b64": ...}, ...],
             "total_pages": int, "truncated": bool}
    壊れたPDF・暗号化PDF・不正なバイト列は例外をそのまま投げる
    （握りつぶすかどうかは呼び出し側の責務。gui.render_pdf/memory_tools.execute
    はそれぞれ既存の例外ハンドリング方針でここを包む）。
    """
    import pypdfium2 as pdfium

    if not isinstance(max_pages, int) or max_pages <= 0:
        max_pages = DEFAULT_MAX_PAGES

    pdf = pdfium.PdfDocument(raw_bytes)
    try:
        total_pages = len(pdf)
        render_count = min(total_pages, max_pages)
        pages = []
        for i in range(render_count):
            page = pdf[i]
            try:
                bitmap = page.render(scale=2.0)
                try:
                    pil_img = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=85)
                page_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                pages.append({"mime": "image/jpeg", "b64": page_b64})
            finally:
                page.close()
    finally:
        pdf.close()

    return {
        "pages": pages,
        "total_pages": total_pages,
        "truncated": total_pages > max_pages,
    }


def _assemble_extracted_text(page_texts, total_pages, max_chars=MAX_EXTRACT_CHARS):
    """extract_textの中核ロジック（純粋関数・PDFに依存しない）。
    page_texts: [(page_num, text), ...]（page_numは1始まり・すでに要求範囲へ絞り込み済み）を
    受け取り、"[p{n}]\\n{text}\\n"のページ区切りマーカーを挟んで連結し、max_charsで
    クリップする。read_doc（workspace.py）と同じ思想:
    - ページ境界を優先してクリップする（次に読むページ全体が予算に収まらないなら、
      そのページは未消費のまま次回のstart_pageに回す）
    - ただし要求範囲の最初のページ単体がmax_charsを超える場合のみ、そのページを
      max_chars丸めで強制的に消費済み扱いにし、次のstart_pageへ必ず前進させる
      （でないと同じstart_pageへの呼び出しが繰り返され無限ループになる）
    テキスト層が無い（全ページ空）場合は、image modeへ誘導する案内文を返す。

    戻り値: {"text": str, "total_pages": int, "end_page": int|None,
             "next_start": int|None, "has_more": bool,
             "no_text_layer": bool, "clipped_mid_page": bool}
    page_textsが空（要求範囲に該当ページが無い）の場合は呼び出し側のバリデーション漏れ
    なので、その場合もこの関数はクラッシュせずno_text_layer相当の空扱いで返す
    （実際には extract_text 側で事前にstart_page妥当性を検査するため通常到達しない）。"""
    if not page_texts:
        return {"text": "", "total_pages": total_pages, "end_page": None,
                "next_start": None, "has_more": False,
                "no_text_layer": True, "clipped_mid_page": False}

    if all(not text.strip() for _, text in page_texts):
        # 判定は「今回読んだ範囲」に限られる（文書全体は見ていない）ため、
        # 断定せず範囲を明示する。窓の外にテキスト層がある可能性を残した表現にする。
        first = page_texts[0][0]
        last = page_texts[-1][0]
        rng = f"p{first}" if first == last else f"p{first}-p{last}"
        return {"text": (f"{rng}にはテキストが無い（スキャン画像の可能性）。"
                          f"全{total_pages}ページ。他のページをmode:\"text\"で試すか、"
                          f'mode:"image"（既定）で画像として読める'),
                "total_pages": total_pages, "end_page": None,
                "next_start": None, "has_more": False,
                "no_text_layer": True, "clipped_mid_page": False}

    parts = []
    current_len = 0
    last_consumed = None
    clipped_mid_page = False
    for page_num, text in page_texts:
        segment = f"[p{page_num}]\n{text}\n"
        if current_len == 0 and len(segment) > max_chars:
            # このページ単体で予算超過: 前進保証のため強制的に消費済み扱いにして打ち切る。
            parts.append(segment[:max_chars])
            current_len = max_chars
            last_consumed = page_num
            clipped_mid_page = True
            break
        if current_len + len(segment) > max_chars:
            # このページを足すと超過する: ページ境界を優先し、このページは未消費のまま打ち切る。
            break
        parts.append(segment)
        current_len += len(segment)
        last_consumed = page_num

    text_out = "".join(parts)
    has_more = last_consumed is not None and last_consumed < total_pages
    next_start = (last_consumed + 1) if has_more else None
    if has_more:
        if clipped_mid_page:
            text_out += (f"\n…（p{last_consumed}は{max_chars}字を超えていたため途中で打ち切った。"
                         f"このページの残りは読めない。続きは start_page={next_start} で読める）")
        else:
            text_out += f"\n…（全{total_pages}ページ。続きは start_page={next_start} で読める）"

    return {"text": text_out, "total_pages": total_pages, "end_page": last_consumed,
            "next_start": next_start, "has_more": has_more,
            "no_text_layer": False, "clipped_mid_page": clipped_mid_page}


def extract_text(raw_bytes, start_page=1, max_pages=None):
    """PDFバイト列からテキストレイヤーを抽出する（画像化しない・pypdfium2標準機能のみ）。
    start_page（1始まり）とmax_pages（省略時は末尾まで）で部分読みできる。
    返却は常にMAX_EXTRACT_CHARSでクリップされ、詳しい挙動は_assemble_extracted_textを見よ。

    start_page/max_pagesのバリデーション: 非int・0以下はValueError。start_pageが
    総ページ数を超える場合もValueError（read_docのように範囲外を空文字で返さず、
    ここでは呼び出し側が範囲を誤ったことを明確にエラーとして伝える——PDFの総ページ数は
    read_docの総行数と違い、事前にlist_workspaceのファイルサイズだけでは分からず
    誤指定されやすいため）。
    壊れたPDF・暗号化PDF・不正なバイト列はpypdfium2の例外をそのまま投げる
    （render_pdf_pages/count_pagesと同じ方針）。"""
    import pypdfium2 as pdfium

    if not isinstance(start_page, int) or isinstance(start_page, bool) or start_page < 1:
        raise ValueError(f"start_pageは1以上の整数で指定してください: {start_page!r}")
    if max_pages is not None:
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages <= 0:
            raise ValueError(f"max_pagesは1以上の整数で指定してください: {max_pages!r}")

    pdf = pdfium.PdfDocument(raw_bytes)
    try:
        total_pages = len(pdf)
        if start_page > total_pages:
            raise ValueError(f"start_pageが範囲外（このPDFは全{total_pages}ページ）: {start_page}")
        end_bound = total_pages if max_pages is None else min(total_pages, start_page - 1 + max_pages)

        page_texts = []
        for page_num in range(start_page, end_bound + 1):
            page = pdf[page_num - 1]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
            page_texts.append((page_num, text))
    finally:
        pdf.close()

    return _assemble_extracted_text(page_texts, total_pages)
