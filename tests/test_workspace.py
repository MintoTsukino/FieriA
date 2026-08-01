"""tests/test_workspace.py — workspace.py（作業フォルダのMarkdown/テキスト読み書き）の単体検証。
FIERIA_HOMEはconftest.pyで一時ディレクトリに隔離済みだが、workspace_dir自体は
FIERIA_HOMEと無関係な任意フォルダなので、各テストはtmp_pathで独立した作業フォルダを使う。
"""
import os


def _paths_only(docs):
    """list_docsの各エントリは2026-07-22追記でサイズ付き（"path (12.3KB)"形式）に
    なったため、pathだけを取り出して既存テストの意図（列挙内容・順序）を検証する。"""
    return [d.split(" (")[0] for d in docs]


def test_list_docs_returns_sorted_md_and_txt_recursively(tmp_path):
    import workspace as ws
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.md").write_text("c", encoding="utf-8")

    docs = ws.list_docs(str(tmp_path))

    assert _paths_only(docs) == ["a.txt", "b.md", "sub/c.md"]
    assert all(" (" in d and d.endswith(")") for d in docs)  # サイズ表記が付いていること


def test_list_docs_skips_hidden_folders(tmp_path):
    import workspace as ws
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config.md").write_text("secret-ish", encoding="utf-8")
    (tmp_path / "note.md").write_text("note", encoding="utf-8")

    docs = ws.list_docs(str(tmp_path))

    assert _paths_only(docs) == ["note.md"]


def test_list_docs_ignores_non_allowed_extensions(tmp_path):
    import workspace as ws
    (tmp_path / "secret.env").write_text("KEY=1", encoding="utf-8")
    (tmp_path / "tool.exe").write_text("MZ", encoding="utf-8")
    (tmp_path / "note.md").write_text("note", encoding="utf-8")

    docs = ws.list_docs(str(tmp_path))

    assert _paths_only(docs) == ["note.md"]


def test_list_docs_entry_size_matches_actual_file_size(tmp_path):
    """サイズ表記が実際のバイト数に対応していることの確認（境界: 1024バイト未満はB表記）。"""
    import workspace as ws
    (tmp_path / "small.md").write_bytes(b"x" * 500)

    docs = ws.list_docs(str(tmp_path))

    assert docs == ["small.md (500B)"]


def test_list_docs_returns_empty_when_dir_missing(tmp_path):
    import workspace as ws
    missing = tmp_path / "not-yet-created"
    assert ws.list_docs(str(missing)) == []


def test_read_doc_returns_content(tmp_path):
    """2026-07-22追記: read_docは常に[L開始-L終了/全N行]ヘッダを先頭に付けるようになった
    （部分読み対応・start_lineの目印として本文自体にも常に付く）。中身の一致は本文部分で見る。"""
    import workspace as ws
    (tmp_path / "article.md").write_text("本文だじょ", encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "article.md")
    assert result == "[L1-L1/全1行]\n本文だじょ"


def test_read_doc_returns_empty_for_missing_file(tmp_path):
    import workspace as ws
    assert ws.read_doc(str(tmp_path), "no-such.md") == ""


def test_write_doc_creates_parent_dirs_and_writes_content(tmp_path):
    import workspace as ws
    ws.write_doc(str(tmp_path), "articles/new.md", "はじめての記事")
    written = tmp_path / "articles" / "new.md"
    assert written.read_text(encoding="utf-8") == "はじめての記事"


def test_write_doc_no_bak_created_for_new_file(tmp_path):
    import workspace as ws
    ws.write_doc(str(tmp_path), "fresh.md", "新規")
    assert not (tmp_path / "fresh.md.bak").exists()


def test_write_doc_backs_up_previous_version_on_overwrite(tmp_path):
    import workspace as ws
    ws.write_doc(str(tmp_path), "article.md", "1版目")
    ws.write_doc(str(tmp_path), "article.md", "2版目")

    assert (tmp_path / "article.md").read_text(encoding="utf-8") == "2版目"
    assert (tmp_path / "article.md.bak").read_text(encoding="utf-8") == "1版目"


def test_write_doc_bak_is_single_generation_overwritten(tmp_path):
    import workspace as ws
    ws.write_doc(str(tmp_path), "article.md", "1版目")
    ws.write_doc(str(tmp_path), "article.md", "2版目")
    ws.write_doc(str(tmp_path), "article.md", "3版目")

    # .bakは直前版のみ（1版目は残らない）
    assert (tmp_path / "article.md.bak").read_text(encoding="utf-8") == "2版目"


def test_append_doc_appends_to_existing_file(tmp_path):
    import workspace as ws
    ws.write_doc(str(tmp_path), "log.md", "1行目\n")
    ws.append_doc(str(tmp_path), "log.md", "2行目\n")
    assert (tmp_path / "log.md").read_text(encoding="utf-8") == "1行目\n2行目\n"


def test_append_doc_creates_file_if_missing(tmp_path):
    import workspace as ws
    ws.append_doc(str(tmp_path), "new.txt", "はじめて")
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "はじめて"


def test_read_doc_rejects_path_traversal(tmp_path):
    import workspace as ws
    outside = tmp_path.parent / "outside.md"
    outside.write_text("秘密", encoding="utf-8")
    try:
        ws.read_doc(str(tmp_path), "../outside.md")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_write_doc_rejects_path_traversal(tmp_path):
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), "../escape.md", "書けたら事故")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path.parent / "escape.md").exists()


def test_read_doc_rejects_disallowed_extension(tmp_path):
    import workspace as ws
    (tmp_path / ".env").write_text("API_KEY=xxx", encoding="utf-8")
    try:
        ws.read_doc(str(tmp_path), ".env")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_write_doc_rejects_disallowed_extension(tmp_path):
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), "tool.exe", "MZ")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path / "tool.exe").exists()


def test_append_doc_rejects_disallowed_extension(tmp_path):
    import workspace as ws
    try:
        ws.append_doc(str(tmp_path), "tool.exe", "{}")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_write_doc_rejects_ads_bypass_via_colon(tmp_path):
    """hidden.exe:x.md はendswith(".md")をすり抜けるが、実体は"hidden.exe"という
    実行ファイルを作ってしまう（NTFS代替データストリーム）。拒否されるべき。"""
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), "hidden.exe:x.md", "実行ファイルになったら事故")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path / "hidden.exe").exists()


def test_write_doc_rejects_short_colon_notation(tmp_path):
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), "a:b.md", "事故")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path / "a").exists()


def test_read_doc_accepts_normal_path_without_colon(tmp_path):
    import workspace as ws
    (tmp_path / "normal.md").write_text("普通の記事", encoding="utf-8")
    assert "普通の記事" in ws.read_doc(str(tmp_path), "normal.md")


def test_write_doc_rejects_hidden_git_folder(tmp_path):
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), ".git/note.md", "listから見えない書き込み")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path / ".git").exists()


def test_write_doc_rejects_hidden_folder_generic(tmp_path):
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), ".hidden/x.md", "事故")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path / ".hidden").exists()


def test_write_doc_rejects_dotfile_directly(tmp_path):
    import workspace as ws
    try:
        ws.write_doc(str(tmp_path), ".secret.md", "事故")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    assert not (tmp_path / ".secret.md").exists()


def test_list_docs_truncates_at_file_count_limit(tmp_path):
    import workspace as ws
    for i in range(ws.MAX_LIST_FILES + 10):
        (tmp_path / f"doc{i:04d}.md").write_text("x", encoding="utf-8")

    docs = ws.list_docs(str(tmp_path))

    assert len(docs) == ws.MAX_LIST_FILES + 1
    assert docs[-1] == ws.TRUNCATION_MARKER


def test_list_docs_stops_descending_beyond_max_depth(tmp_path):
    import workspace as ws
    deep = tmp_path
    for i in range(ws.MAX_LIST_DEPTH + 3):
        deep = deep / f"lv{i}"
        deep.mkdir()
    (deep / "too-deep.md").write_text("深すぎ", encoding="utf-8")
    (tmp_path / "shallow.md").write_text("浅い", encoding="utf-8")

    docs = ws.list_docs(str(tmp_path))

    assert "shallow.md" in _paths_only(docs)
    assert not any("too-deep.md" in d for d in docs)


# --- read_pdf_bytes / list_docsのPDF列挙（Geminiネイティブ対応・2026-07-22追加）---

def test_list_docs_includes_pdf_files(tmp_path):
    """list_docsの列挙対象はLISTABLE_EXTS（ALLOWED_EXTS+.pdf）。read_doc等の
    テキスト読み書き対象（ALLOWED_EXTS）自体は変わらないことと対にして確認する。"""
    import workspace as ws
    (tmp_path / "note.md").write_text("note", encoding="utf-8")
    (tmp_path / "資料.pdf").write_bytes(b"%PDF-1.4 fake")

    docs = ws.list_docs(str(tmp_path))

    assert _paths_only(docs) == ["note.md", "資料.pdf"]


def test_read_doc_still_rejects_pdf_extension(tmp_path):
    """read_doc/write_doc/append_docの対象拡張子はALLOWED_EXTSのまま変わらない
    （.pdfが列挙されるようになっても、テキストとして読み書きはできない）。"""
    import workspace as ws
    (tmp_path / "資料.pdf").write_bytes(b"%PDF-1.4 fake")
    try:
        ws.read_doc(str(tmp_path), "資料.pdf")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_read_pdf_bytes_returns_content(tmp_path):
    import workspace as ws
    raw = b"%PDF-1.4 fake pdf bytes"
    (tmp_path / "article.pdf").write_bytes(raw)
    assert ws.read_pdf_bytes(str(tmp_path), "article.pdf") == raw


def test_read_pdf_bytes_rejects_non_pdf_extension(tmp_path):
    import workspace as ws
    (tmp_path / "note.md").write_text("note", encoding="utf-8")
    try:
        ws.read_pdf_bytes(str(tmp_path), "note.md")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_read_pdf_bytes_raises_when_missing(tmp_path):
    import workspace as ws
    try:
        ws.read_pdf_bytes(str(tmp_path), "no-such.pdf")
        assert False, "FileNotFoundErrorが飛ぶはず"
    except FileNotFoundError:
        pass


def test_read_pdf_bytes_rejects_path_traversal(tmp_path):
    import workspace as ws
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    try:
        ws.read_pdf_bytes(str(tmp_path), "../outside.pdf")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_read_pdf_bytes_rejects_ads_colon_bypass(tmp_path):
    """workspace._safe_pathと同じADS対策がread_pdf_bytes（_resolve_safe_path経由）にも
    効いていることの確認。"""
    import workspace as ws
    try:
        ws.read_pdf_bytes(str(tmp_path), "hidden.exe:x.pdf")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_read_pdf_bytes_rejects_hidden_folder(tmp_path):
    import workspace as ws
    try:
        ws.read_pdf_bytes(str(tmp_path), ".git/note.pdf")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_read_pdf_bytes_rejects_oversized_file(tmp_path):
    """MAX_PDF_BYTES(14MB)超のファイルは中身を読まず拒否される。
    実際に14MB超のファイルを書くと遅いため、スパースファイル（seek+1バイト書き）で代用する。"""
    import workspace as ws
    big = tmp_path / "huge.pdf"
    with open(big, "wb") as f:
        f.seek(ws.MAX_PDF_BYTES)  # ちょうど MAX_PDF_BYTES + 1 バイトのファイルにする
        f.write(b"\x00")
    try:
        ws.read_pdf_bytes(str(tmp_path), "huge.pdf")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_read_pdf_bytes_accepts_file_at_exact_limit(tmp_path):
    import workspace as ws
    exact = tmp_path / "exact.pdf"
    with open(exact, "wb") as f:
        f.seek(ws.MAX_PDF_BYTES - 1)
        f.write(b"\x00")
    body = ws.read_pdf_bytes(str(tmp_path), "exact.pdf")
    assert len(body) == ws.MAX_PDF_BYTES


# --- read_docの部分読み・サイズガード（10万字級原稿対応・2026-07-22追加）---

def test_read_doc_start_line_reads_from_middle(tmp_path):
    import workspace as ws
    (tmp_path / "article.md").write_text("1行目\n2行目\n3行目\n", encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "article.md", start_line=2)
    assert result == "[L2-L3/全3行]\n2行目\n3行目\n"


def test_read_doc_start_line_and_max_lines_reads_a_window(tmp_path):
    import workspace as ws
    (tmp_path / "article.md").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "article.md", start_line=2, max_lines=2)
    assert result.startswith("[L2-L3/全5行]\n2\n3\n")
    assert "続きは start_line=4" in result


def test_read_doc_no_continuation_hint_when_reading_to_end(tmp_path):
    import workspace as ws
    (tmp_path / "article.md").write_text("1\n2\n3\n", encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "article.md", start_line=1, max_lines=100)
    assert "続きは" not in result


def test_read_doc_start_line_out_of_range_returns_message_not_exception(tmp_path):
    import workspace as ws
    (tmp_path / "article.md").write_text("1行目\n2行目\n", encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "article.md", start_line=99)
    assert "範囲外" in result
    assert "全2行" in result


def test_read_doc_clips_to_20000_chars_and_offers_continuation(tmp_path):
    import workspace as ws
    # 1行500字 × 50行 = 25000字（20000字クリップの閾値を超える）
    lines = [("あ" * 500) for _ in range(50)]
    (tmp_path / "long.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = ws.read_doc(str(tmp_path), "long.md")

    header, _, body = result.partition("\n")
    assert header.startswith("[L1-L")
    assert "全50行" in header
    assert "続きは start_line=" in result
    # ヘッダ行と末尾の案内文を除いた本文自体が20000字ちょうどにクリップされていること
    guidance_idx = result.rindex("\n…（")
    body_only = result[len(header) + 1:guidance_idx]
    assert len(body_only) == ws.MAX_READ_CHARS


def test_read_doc_backward_compatible_defaults_to_full_file_up_to_cap(tmp_path):
    """引数なし呼び出し（start_line/max_lines省略）は従来どおり先頭から全文
    （ただし20000字上限）を返す。"""
    import workspace as ws
    (tmp_path / "short.md").write_text("短い本文", encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "short.md")
    assert result == "[L1-L1/全1行]\n短い本文"


def test_read_doc_still_returns_empty_for_missing_or_empty_file(tmp_path):
    import workspace as ws
    assert ws.read_doc(str(tmp_path), "no-such.md") == ""
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    assert ws.read_doc(str(tmp_path), "empty.md") == ""


# --- search_workspace（横断検索・2026-07-22追加）---

def test_search_workspace_finds_hits_with_line_numbers(tmp_path):
    import workspace as ws
    (tmp_path / "a.md").write_text("1行目\n探し物あった\n3行目\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("関係ない内容\n", encoding="utf-8")

    hits = ws.search_workspace(str(tmp_path), "探し物")

    assert hits == ["a.md:2: 探し物あった"]


def test_search_workspace_is_case_insensitive(tmp_path):
    import workspace as ws
    (tmp_path / "a.md").write_text("Hello World\n", encoding="utf-8")
    hits = ws.search_workspace(str(tmp_path), "hello")
    assert hits == ["a.md:1: Hello World"]


def test_search_workspace_clips_long_lines_to_200_chars(tmp_path):
    import workspace as ws
    long_line = "x" * 300 + "ヒット" + "y" * 300
    (tmp_path / "a.md").write_text(long_line + "\n", encoding="utf-8")
    hits = ws.search_workspace(str(tmp_path), "ヒット")
    assert len(hits) == 1
    snippet = hits[0].split(": ", 1)[1]
    assert len(snippet) <= 201  # 200字+省略記号1字


def test_search_workspace_truncates_at_50_hits(tmp_path):
    import workspace as ws
    for i in range(60):
        (tmp_path / f"f{i:02d}.md").write_text("的中ワード\n", encoding="utf-8")

    hits = ws.search_workspace(str(tmp_path), "的中")

    assert len(hits) == ws.MAX_SEARCH_HITS + 1
    assert hits[-1] == ws.SEARCH_HITS_TRUNCATION_MARKER


def test_search_workspace_skips_files_over_2mb(tmp_path):
    import workspace as ws
    huge = tmp_path / "huge.md"
    with open(huge, "wb") as f:
        f.seek(ws.MAX_SEARCH_FILE_BYTES)
        f.write(b"a")
    (tmp_path / "small.md").write_text("見つかるはず\n", encoding="utf-8")

    hits = ws.search_workspace(str(tmp_path), "見つかる")

    assert any("small.md" in h for h in hits)
    assert not any(h.startswith("huge.md:") for h in hits)
    assert any("2MB超のためスキップ" in h and "huge.md" in h for h in hits)


def test_search_workspace_skips_undecodable_binary_like_files(tmp_path):
    import workspace as ws
    bad = tmp_path / "bad.md"
    with open(bad, "wb") as f:
        f.write(b"\xff\xfe\x00broken")
    (tmp_path / "good.md").write_text("見える文字列\n", encoding="utf-8")

    hits = ws.search_workspace(str(tmp_path), "見える")

    assert any("good.md" in h for h in hits)
    assert not any(h.startswith("bad.md:") for h in hits)


def test_search_workspace_excludes_pdf_files(tmp_path):
    import workspace as ws
    (tmp_path / "note.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "note.md").write_text("PDFという単語を含む\n", encoding="utf-8")

    hits = ws.search_workspace(str(tmp_path), "PDF")

    assert not any(h.startswith("note.pdf:") for h in hits)


def test_search_workspace_rejects_empty_query(tmp_path):
    import workspace as ws
    try:
        ws.search_workspace(str(tmp_path), "")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


def test_search_workspace_returns_empty_when_dir_missing(tmp_path):
    import workspace as ws
    missing = tmp_path / "not-yet-created"
    assert ws.search_workspace(str(missing), "何か") == []


# --- read_docの無限ループ修正（改行なし長文・2026-07-22追加）---

def test_read_doc_progresses_past_unbreakable_long_line_without_infinite_loop(tmp_path):
    """25000字の改行なし1行（20000字クリップ内に改行が1個も無い）でも、
    次に案内されたstart_lineへ進めば必ず前進し、無限ループにならないこと。"""
    import workspace as ws
    long_line = "あ" * 25000
    (tmp_path / "long.md").write_text(long_line + "\n2行目\n", encoding="utf-8")

    result1 = ws.read_doc(str(tmp_path), "long.md")
    assert result1.startswith("[L1-L1/全2行]")
    assert "続きは start_line=2" in result1
    assert "この行の残りは読めない" in result1

    # 案内どおり呼び続けると全行読み終わり、同一結果の繰り返しにはならないこと
    result2 = ws.read_doc(str(tmp_path), "long.md", start_line=2)
    assert "2行目" in result2
    assert "続きは" not in result2


def test_read_doc_header_end_never_before_start_on_mid_line_clip(tmp_path):
    """ヘッダの[Lx-Ly/全N行]はy>=xを必ず満たすこと（無改行長文クリップ時の回帰確認）。"""
    import workspace as ws
    (tmp_path / "long.md").write_text("い" * 30000, encoding="utf-8")
    result = ws.read_doc(str(tmp_path), "long.md")
    header = result.split("\n", 1)[0]
    assert header == "[L1-L1/全1行]"


# --- read_docの引数バリデーション（2026-07-22追加）---

def test_read_doc_rejects_zero_or_negative_max_lines(tmp_path):
    import workspace as ws
    (tmp_path / "a.md").write_text("1行目\n", encoding="utf-8")
    for bad in (0, -1):
        try:
            ws.read_doc(str(tmp_path), "a.md", max_lines=bad)
            assert False, "ValueErrorが飛ぶはず"
        except ValueError:
            pass


def test_read_doc_rejects_zero_or_negative_start_line(tmp_path):
    import workspace as ws
    (tmp_path / "a.md").write_text("1行目\n", encoding="utf-8")
    for bad in (0, -1):
        try:
            ws.read_doc(str(tmp_path), "a.md", start_line=bad)
            assert False, "ValueErrorが飛ぶはず"
        except ValueError:
            pass


def test_read_doc_rejects_non_int_start_line_and_max_lines(tmp_path):
    import workspace as ws
    (tmp_path / "a.md").write_text("1行目\n", encoding="utf-8")
    try:
        ws.read_doc(str(tmp_path), "a.md", start_line="1")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass
    try:
        ws.read_doc(str(tmp_path), "a.md", max_lines="10")
        assert False, "ValueErrorが飛ぶはず"
    except ValueError:
        pass


# --- search_workspaceのファイル一覧打ち切り通知（2026-07-22追加）---

def test_search_workspace_notes_file_list_truncation_when_over_500_files(tmp_path):
    """MAX_LIST_FILES(500)超のファイル構成では、ヒットが無くても
    「先頭500件のみ検索した」旨の注記が付くこと（打ち切りの無警告化の修正確認）。
    レア語は501件目以降にのみ置くので、決定的なパスソート走査であれば0ヒットになる。"""
    import workspace as ws
    for i in range(600):
        content = "普通の内容\n" if i < 550 else "レア語ヒット\n"
        (tmp_path / f"f{i:04d}.md").write_text(content, encoding="utf-8")

    hits = ws.search_workspace(str(tmp_path), "レア語")

    assert any(ws.SEARCH_FILE_LIST_TRUNCATION_NOTE in h for h in hits)
    assert not any("f05" in h or "f0550" in h for h in hits)  # 500件目以降は見ていない


def test_search_workspace_no_truncation_note_when_under_limit(tmp_path):
    import workspace as ws
    (tmp_path / "a.md").write_text("普通の内容\n", encoding="utf-8")
    hits = ws.search_workspace(str(tmp_path), "普通")
    assert not any(ws.SEARCH_FILE_LIST_TRUNCATION_NOTE in h for h in hits)


def test_search_workspace_scans_in_deterministic_sorted_order(tmp_path):
    """50件打ち切りの優先順がパスソート順（決定的）になっていること。"""
    import workspace as ws
    for name in ("z.md", "a.md", "m.md"):
        (tmp_path / name).write_text("共通ワード\n", encoding="utf-8")
    hits = ws.search_workspace(str(tmp_path), "共通")
    files_in_order = [h.split(":")[0] for h in hits]
    assert files_in_order == ["a.md", "m.md", "z.md"]


# --- .json対応（2026-07-22追加）: 読み書き解禁＋OAuthトークン類の安全弁 ---


def test_json_files_can_be_read_written_and_listed(tmp_path):
    """.jsonがread_doc/write_doc/append_doc/list_docsの対象になること。"""
    import workspace as ws
    ws.write_doc(str(tmp_path), "data.json", '{"a": 1}')
    assert '{"a": 1}' in ws.read_doc(str(tmp_path), "data.json")
    ws.append_doc(str(tmp_path), "data.json", "\n")
    docs = ws.list_docs(str(tmp_path))
    assert any(d.startswith("data.json") for d in docs)


def test_oauth_named_files_are_rejected_and_hidden(tmp_path):
    """OAuthトークン類（*oauth*.json等）は読み書き拒否・一覧非表示・検索対象外。
    .json解禁でトークンファイルへ手が届かないための安全弁。"""
    import pytest
    import workspace as ws
    secret = tmp_path / "xai_oauth.json"
    secret.write_text('{"access_token": "dummy-not-real"}', encoding="utf-8")
    with pytest.raises(ValueError):
        ws.read_doc(str(tmp_path), "xai_oauth.json")
    with pytest.raises(ValueError):
        ws.write_doc(str(tmp_path), "xai_oauth.json", "x")
    assert not any("oauth" in d.lower() for d in ws.list_docs(str(tmp_path)))
    assert not any("oauth" in h.lower() for h in ws.search_workspace(str(tmp_path), "dummy"))


def test_py_files_can_be_read_written_and_listed(tmp_path):
    """.pyがread_doc/write_doc/list_docsの対象になること（読み書きのみ、実行機能は無い）。"""
    import workspace as ws
    ws.write_doc(str(tmp_path), "script.py", "print('hi')\n")
    assert "print('hi')" in ws.read_doc(str(tmp_path), "script.py")
    assert any(d.startswith("script.py") for d in ws.list_docs(str(tmp_path)))


def test_sensitive_named_files_are_rejected_and_hidden(tmp_path):
    """機密名（oauth/secret/credential/apikey/api_key）を含むファイル名は読み書き拒否・
    一覧非表示・検索対象外（2026-08-02: oauthのみだった判定を拡張）。"""
    import pytest
    import workspace as ws
    names = ["secrets.json", "my-credentials.json", "apikey.txt", "api_key.md"]
    for name in names:
        (tmp_path / name).write_text("dummy-not-real", encoding="utf-8")

    for name in names:
        with pytest.raises(ValueError, match="機密ファイル類には触れない"):
            ws.read_doc(str(tmp_path), name)
        with pytest.raises(ValueError, match="機密ファイル類には触れない"):
            ws.write_doc(str(tmp_path), name, "x")

    docs = ws.list_docs(str(tmp_path))
    for name in names:
        assert not any(d.startswith(name) for d in docs)
    hits = ws.search_workspace(str(tmp_path), "dummy")
    for name in names:
        assert not any(h.startswith(name) for h in hits)


def test_sensitive_name_substring_false_positive_is_accepted_by_design(tmp_path):
    """secretary.md のようなサブストリング誤爆も安全側に倒して拒否する
    （意図的な仕様固定。誤爆を許容してでも機密ファイルの取りこぼしを避ける）。"""
    import pytest
    import workspace as ws
    (tmp_path / "secretary.md").write_text("議事録", encoding="utf-8")

    with pytest.raises(ValueError, match="機密ファイル類には触れない"):
        ws.read_doc(str(tmp_path), "secretary.md")
    assert not any(d.startswith("secretary.md") for d in ws.list_docs(str(tmp_path)))


def test_workspace_dir_inside_fieria_home_rejects_config_json_read():
    """workspace_dirをFIERIA_HOME配下に向けても、config.json（FieriAの内部データ）の
    読みは拒否される（2026-08-02追加のfieria_home到達物理拒否）。テスト隔離済みの
    FIERIA_HOME（conftest.py）を直接workspace_dirとして使う。"""
    import pytest
    import config
    import workspace as ws
    config.save_config(config.load_config())  # config.jsonが確実に存在する状態にする

    with pytest.raises(ValueError, match="FieriAの内部データ"):
        ws.read_doc(config.HOME, "config.json")


def test_oauth_named_files_are_rejected_and_hidden_still_passes(tmp_path):
    """既存のoauth拒否テストが機密名判定の統合後も引き続きパスすることの確認
    （test_oauth_named_files_are_rejected_and_hiddenと同内容の再確認）。"""
    import pytest
    import workspace as ws
    secret = tmp_path / "xai_oauth.json"
    secret.write_text('{"access_token": "dummy-not-real"}', encoding="utf-8")
    with pytest.raises(ValueError):
        ws.read_doc(str(tmp_path), "xai_oauth.json")
    with pytest.raises(ValueError):
        ws.write_doc(str(tmp_path), "xai_oauth.json", "x")
    assert not any("oauth" in d.lower() for d in ws.list_docs(str(tmp_path)))
    assert not any("oauth" in h.lower() for h in ws.search_workspace(str(tmp_path), "dummy"))
