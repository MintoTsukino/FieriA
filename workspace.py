"""workspace.py — ユーザーが指定した「作業フォルダ」内の文書ファイル（ALLOWED_EXTS）の読み書き。
記事執筆・文章作業用。フィエリアが会話中に能動的にツールで読み書きする専用で、
自動注入はしない（memory_tools.pyのFEEDBACK_TOOLS経由でのみ呼ばれる）。

soul.pyの_safe_pathと同方式のトラバーサル拒否に加え、拡張子をALLOWED_EXTSに
限定する（作業フォルダに秘密ファイル・実行ファイルが置かれていても触れない安全弁）。
"""
import os

import config

ALLOWED_EXTS = (".md", ".txt", ".html", ".css", ".js", ".json", ".py")
# list_docsの列挙対象だけ.pdfも含める（read_pdf_bytesで見るため）。
# write_doc/append_doc/read_doc（テキスト読み書き）の対象拡張子はALLOWED_EXTSのまま変えない。
LISTABLE_EXTS = ALLOWED_EXTS + (".pdf",)

MAX_PDF_BYTES = 14 * 1024 * 1024  # 14MB（gui.pdf_native_supported/Gemini直読みのraw上限と揃える）


# ファイル名ベースの機密判定。拡張子ホワイトリストを通る名前（secrets.json等）を
# 名指しで塞ぐ。誤爆（secretary.md等）は安全側に倒す設計判断（2026-08-02）
_SENSITIVE_NAME_PARTS = ("oauth", "secret", "credential", "apikey", "api_key")


def _is_sensitive_name(name):
    low = name.lower()
    return any(p in low for p in _SENSITIVE_NAME_PARTS)


def _resolve_safe_path(workspace_dir, rel_path):
    """コロン拒否・隠しパス拒否・トラバーサル拒否・機密名拒否・fieria_home到達拒否の
    共通検査（拡張子検査はしない）。_safe_path（テキスト系）とread_pdf_bytes（.pdf限定）
    の両方から使う。"""
    base = os.path.abspath(workspace_dir)
    # rel_pathは常に相対パスのはずなので、コロンが含まれる時点で不正とみなす。
    # （NTFS代替データストリーム "hidden.exe:x.md" は拡張子チェックを文字列末尾一致で
    #  すり抜けつつ実体は"hidden.exe"を作ってしまうため／ドライブ絶対パスでの脱出も同時に塞ぐ。
    #  base自体のドライブレターのコロンはrel_path側には現れないので誤検知しない）
    if ":" in rel_path:
        raise ValueError(f"許可されていないパス表記: {rel_path}")
    # 各パス成分が"."始まりなら拒否（.git/note.md・.hidden/x.md・.secret.md等）。
    # list_docsの隠しフォルダスキップと対称に、read/write側でも隠しパスへの素通りを塞ぐ。
    for part in rel_path.replace("\\", "/").split("/"):
        if part.startswith("."):
            raise ValueError(f"隠しフォルダ/ファイルには触れない: {rel_path}")
    full = os.path.abspath(os.path.join(base, rel_path))
    if not full.startswith(base + os.sep) and full != base:
        raise ValueError(f"作業フォルダの外は触れない: {rel_path}")
    # .json解禁に伴う安全弁: OAuth/秘密鍵/認証情報っぽい名前のファイルが作業フォルダに
    # 置かれていても読み書きさせない（.envは拡張子検査で既に弾かれている）。
    if _is_sensitive_name(os.path.basename(full)):
        raise ValueError(f"機密ファイル類には触れない: {rel_path}")
    # 作業フォルダがシンボリックリンク等でfieria_home（会話ログ・記憶・config.json等の
    # 内部データ）に到達していないかを実パスで確認する（2026-08-02追加の物理拒否）。
    real_full = os.path.realpath(full)
    real_home = os.path.realpath(config.HOME)
    if real_full == real_home or real_full.startswith(real_home + os.sep):
        raise ValueError(f"FieriAの内部データ（fieria_home）には触れない: {rel_path}")
    return full


def _safe_path(workspace_dir, rel_path):
    full = _resolve_safe_path(workspace_dir, rel_path)
    if not full.lower().endswith(ALLOWED_EXTS):
        raise ValueError(f"許可されていない拡張子: {rel_path}")
    return full


MAX_LIST_DEPTH = 5
MAX_LIST_FILES = 500
TRUNCATION_MARKER = "...(500件で打ち切り)"


def _format_size(num_bytes):
    """list_docsのdetail表示用に人が読みやすい単位へ丸める（1KB=1024で計算）。"""
    if num_bytes < 1024:
        return f"{num_bytes}B"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.1f}KB"
    return f"{kb / 1024:.1f}MB"


class _FileWalkState:
    """_iter_listable_files呼び出し側がMAX_LIST_FILES到達での打ち切りを検知するための
    共有フラグ入れ物（generatorはreturnで終わるだけで呼び出し側に情報を残せないため）。
    list_docsは従来どおり「yield数がMAX_LIST_FILESに達したか」を自分で数えるので
    state無しでも動く（後方互換）。search_workspaceはヒット走査を打ち切っても
    「ファイル一覧自体が500件で打ち切られたか」を知る必要があるためstateを渡す。"""

    def __init__(self):
        self.truncated = False


def _iter_listable_files(base, state=None):
    """list_docs/search_workspace共通のwalkロジック（深さ制限・隠しフォルダスキップ・
    件数上限MAX_LIST_FILES）。(rel, full)のタプルをyieldする。呼び出し側は
    「yield数がMAX_LIST_FILESに達したか」を見て打ち切りを検知すること
    （list_docsの既存挙動＝到達した時点で打ち切りとみなす、を踏襲）。
    state（_FileWalkStateインスタンス）を渡すと、打ち切り発生時にstate.truncated=Trueが立つ。"""
    count = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel_root = os.path.relpath(root, base)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth >= MAX_LIST_DEPTH:
            dirs[:] = []  # これ以上深くは降りない（現在の階層のファイルは拾う）
        for f in files:
            if f.lower().endswith(LISTABLE_EXTS):
                if _is_sensitive_name(f):
                    continue  # _resolve_safe_pathの機密名拒否と対称（見せない・触らせない）
                full = os.path.join(root, f)
                rel = os.path.relpath(full, base).replace("\\", "/")
                yield rel, full
                count += 1
                if count >= MAX_LIST_FILES:
                    if state is not None:
                        state.truncated = True
                    return


def list_docs(workspace_dir):
    """作業フォルダ配下の許可拡張子ファイルの相対パス一覧（再帰・ソート済み・サイズ付き）。
    隠しフォルダ（.git等）は配下ごとスキップする。作業フォルダが未作成でも
    例外にせず空リストを返す（write_doc/append_docが後から作るため）。

    各エントリは "path (12.3KB)" 形式（LLMがどのファイルが大きいか見て
    read_docの部分読みが要るか判断できるように）。ソート自体はpathのみで行う
    （サイズ表記はソート後に付与するので、既存の並び順は変わらない）。

    C:\\等の巨大フォルダを誤指定された場合の暴走を防ぐため、workspace_dirから
    MAX_LIST_DEPTH階層より深くは降りず、MAX_LIST_FILES件到達で打ち切る
    （打ち切り時はTRUNCATION_MARKERを末尾に付与し、呼び出し側に伝わるようにする）。"""
    base = os.path.abspath(workspace_dir)
    if not os.path.isdir(base):
        return []
    entries = list(_iter_listable_files(base))
    truncated = len(entries) >= MAX_LIST_FILES
    entries.sort(key=lambda e: e[0])
    out = [f"{rel} ({_format_size(os.path.getsize(full))})" for rel, full in entries]
    if truncated:
        out.append(TRUNCATION_MARKER)
    return out


MAX_READ_CHARS = 20000


def read_doc(workspace_dir, rel_path, start_line=1, max_lines=None):
    """作業フォルダ内のテキストファイルを読む。start_line（1始まり）とmax_lines
    （省略時は末尾まで）で部分読みできる。10万字級の原稿でも一括で差し戻さないよう、
    返却は常に文字数上限MAX_READ_CHARSでクリップし、先頭に行番号ヘッダ
    "[L{start}-L{end}/全N行]" を付ける。クリップ／範囲指定により未読部分が残る場合は
    末尾に次に読むべきstart_lineの案内を付ける。

    1行がMAX_READ_CHARSを超えて改行に達しないまま切れた場合（consumed_lines==0）も、
    その行は消費済み扱いにしてnext_start=start_line+1へ必ず前進させる。でないと同じ
    start_lineへの呼び出しが繰り返され無限ループになる（その行の残りは読めなくなるが、
    案内文でそれを正直に伝える）。

    後方互換: start_line/max_lines省略時は従来どおり先頭から（MAX_READ_CHARSまで）読む。
    ファイルが存在しない／空なら従来どおり空文字列を返す（ヘッダも付けない）。"""
    if not isinstance(start_line, int):
        raise ValueError(f"start_lineは整数で指定してください: {start_line!r}")
    if start_line < 1:
        raise ValueError(f"start_lineは1以上を指定してください: {start_line}")
    if max_lines is not None:
        if not isinstance(max_lines, int):
            raise ValueError(f"max_linesは整数で指定してください: {max_lines!r}")
        if max_lines <= 0:
            raise ValueError(f"max_linesは1以上を指定してください: {max_lines}")

    full = _safe_path(workspace_dir, rel_path)
    if not os.path.isfile(full):
        return ""
    with open(full, "r", encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    if total == 0:
        return ""
    start_idx = start_line - 1
    if start_idx >= total:
        return (f"[L{start_line}-/全{total}行] "
                f"指定したstart_lineは範囲外（このファイルは全{total}行）")
    end_idx = total if max_lines is None else min(start_idx + max_lines, total)
    body = "".join(lines[start_idx:end_idx])

    clipped_mid_line = False
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS]
        consumed_lines = body.count("\n")
        if consumed_lines > 0:
            end_line = start_line + consumed_lines - 1
            next_start = start_line + consumed_lines
        else:
            # 1行がMAX_READ_CHARS超で改行に到達できなかった: この行を消費済み扱いにして
            # 必ず次へ前進させる（そうしないとnext_start==start_lineで無限ループになる）。
            clipped_mid_line = True
            end_line = start_line
            next_start = start_line + 1
        has_more = True
    else:
        end_line = end_idx
        next_start = end_idx + 1
        has_more = end_idx < total

    header = f"[L{start_line}-L{end_line}/全{total}行]"
    result = f"{header}\n{body}"
    if has_more:
        if clipped_mid_line:
            result += (f"\n…（L{start_line}は{MAX_READ_CHARS}字を超えていたため途中で打ち切った。"
                       f"この行の残りは読めない。続きは start_line={next_start} で読める）")
        else:
            result += f"\n…（全{total}行。続きは start_line={next_start} で読める）"
    return result


MAX_SEARCH_HITS = 50
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024  # 2MB超のファイルは走査スキップ
SEARCH_HITS_TRUNCATION_MARKER = "...(50件で打ち切り)"
SEARCH_FILE_LIST_TRUNCATION_NOTE = "（注: ファイル数が多く先頭500件のみ検索した）"


def search_workspace(workspace_dir, query):
    """作業フォルダ配下のテキストファイル（.pdfは除外・list_docsと同じ深さ/件数制限/
    隠しフォルダスキップを流用）を素朴に部分一致（大文字小文字無視）で走査し、
    "path:行番号: 行内容（200字クリップ）" の形式でヒットを返す。

    ヒット50件で打ち切り（打ち切り時はSEARCH_HITS_TRUNCATION_MARKERを末尾に付与）。
    1ファイル2MB超は走査せずスキップし、スキップした旨を末尾に付与する。
    バイナリ誤爆防止のためUTF-8デコードに失敗したファイルは無言でスキップする。
    毎回外部フォルダを素朴に全走査する（外部からファイルが増減する前提のため、
    インデックスは作らない）。

    ファイル一覧自体がMAX_LIST_FILES（500件）で打ち切られた場合（=対象フォルダに
    ファイルが多すぎて全部は見ていない場合）は、ヒット件数に関わらず
    SEARCH_FILE_LIST_TRUNCATION_NOTEを末尾に付ける（ヒット0件の場合も含む。
    「見つからなかった」のか「見ていないだけ」なのかをLLMに正直に伝えるため）。
    走査順はlist_docsと同様パスでソートしてから行う（50件打ち切りの優先順が
    OSのディレクトリ走査順に依存しないようにするため）。"""
    query = (query or "").strip()
    if not query:
        raise ValueError("検索語が空")
    base = os.path.abspath(workspace_dir)
    if not os.path.isdir(base):
        return []
    q_lower = query.lower()
    hits = []
    skipped_large = []
    truncated = False
    state = _FileWalkState()
    entries = sorted(_iter_listable_files(base, state), key=lambda e: e[0])
    for rel, full in entries:
        if rel.lower().endswith(".pdf"):
            continue
        try:
            if os.path.getsize(full) > MAX_SEARCH_FILE_BYTES:
                skipped_large.append(rel)
                continue
        except OSError:
            continue
        try:
            with open(full, "r", encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if q_lower in line.lower():
                snippet = line.strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"
                hits.append(f"{rel}:{lineno}: {snippet}")
                if len(hits) >= MAX_SEARCH_HITS:
                    truncated = True
                    break
        if truncated:
            break
    if truncated:
        hits.append(SEARCH_HITS_TRUNCATION_MARKER)
    if skipped_large:
        hits.append(f"（2MB超のためスキップ: {', '.join(skipped_large)}）")
    if state.truncated:
        hits.append(SEARCH_FILE_LIST_TRUNCATION_NOTE)
    return hits


def write_doc(workspace_dir, rel_path, content):
    """全文置換で書き込む。親フォルダは自動作成。既存ファイルを上書きする場合、
    直前版を<name>.bak（1世代・上書き）へ退避してから書き込む。"""
    full = _safe_path(workspace_dir, rel_path)
    if os.path.isfile(full):
        with open(full, "r", encoding="utf-8") as f:
            previous = f.read()
        with open(full + ".bak", "w", encoding="utf-8") as f:
            f.write(previous)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def append_doc(workspace_dir, rel_path, content):
    full = _safe_path(workspace_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(content)


def read_pdf_bytes(workspace_dir, rel_path):
    """作業フォルダ内のPDFファイルをバイト列で読む（read_doc等のテキスト系とは別系統。
    PDFはテキストとして読めないため、.pdf拡張子限定でバイト列を返す）。
    read_doc/write_doc/append_docの対象拡張子（ALLOWED_EXTS）はこの関数の追加により
    変わらない——_safe_pathではなく_resolve_safe_path＋独自の.pdf拡張子検査を使う。"""
    full = _resolve_safe_path(workspace_dir, rel_path)
    if not full.lower().endswith(".pdf"):
        raise ValueError(f"許可されていない拡張子: {rel_path}")
    if not os.path.isfile(full):
        raise FileNotFoundError(f"ファイルが存在しない: {rel_path}")
    size = os.path.getsize(full)
    if size > MAX_PDF_BYTES:
        raise ValueError(f"PDFが大きすぎる（14MB超）: {rel_path}")
    with open(full, "rb") as f:
        return f.read()
