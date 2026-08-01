"""soul.py — 魂ディレクトリ（souls/<id>/）の読み書き。
1フォルダ=1魂。丸ごとコピーでバックアップ・引っ越しできることが最重要の不変条件。
ファイルは全部Markdown/JSONL。パスは soul_dir 配下に限定する（トラバーサル拒否）。"""
import base64
import datetime
import difflib
import json
import os
import re
import shutil
import unicodedata

from config import HOME

SOULS_DIR = os.path.join(HOME, "souls")

# mime → 保存拡張子。対応外のmimeはsave_attachmentがValueErrorで拒否する
# （画像添付は「拡張子から見て確実に画像/PDFとわかるもの」だけを許可する設計）。
# application/pdf: GeminiネイティブPDF添付（gui.pdf_native_supported/ui/index.html
# addPdfNative）用。これが無いと、engine.process_turnがPDF添付の度にsave_attachment
# 失敗→「画像保存失敗」opを毎回積んでしまう（チャット自体は継続する＝LLMには届くが、
# 添付の保存・ログ参照だけが失敗する不整合が起きるため、2026-07-22のPDFネイティブ対応で追加）。
_ATTACHMENT_EXT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
}

IDENTITY_PLACEHOLDER = """# identity

（ここは薄い核だけ。人格は記憶が育てる）
"""

MEMORY_INDEX_PLACEHOLDER = """# MEMORY.md — 記憶の索引

（1行1ポインタ。例: `- [トピック名](wiki/トピック名.md) — ひとこと`）
"""

# lessons.md（学習層・手続き記憶）のプレースホルダ。既存SOUL（このファイルが
# 存在しない）でも read_file は"" を返すため、_has_body相当の判定で空扱いになり
# 移行処理は不要（プレースホルダと空文字の両方を「本文なし」として扱う）。
LESSONS_PLACEHOLDER = "# lessons\n"

# tasks.md（気にかけリスト・秘書層）のプレースホルダ。lessons.mdと同じ理屈で、
# 既存SOUL（このファイルが存在しない）でもread_fileは""を返すため
# _has_body相当の判定で空扱いになり、移行処理は不要。
TASKS_PLACEHOLDER = "# tasks\n"

# speech_style.md（口調・独立ファイル）のプレースホルダ。identity.mdと同じく
# 空ならこの文字列そのものになる（read_identity_partsが完全一致で空扱いする）。
SPEECH_STYLE_PLACEHOLDER = "# speech style\n"

# self_notes.md / user.md（全文置換系ファイル）のプレースホルダ。create_soulの初期値
# そのもの。_save_notes_historyが「まだ何も書かれていない」を判定する基準にする
# （identity.md/speech_style.mdの各PLACEHOLDER定数と同じ役割）。
SELF_NOTES_PLACEHOLDER = "# self notes\n"
USER_PLACEHOLDER = "# user\n"

REMINDERS_FILE = "reminders.jsonl"

# 【移行検出専用】旧形式（identity.md内に核と口調が"## 口調"見出しで同居していた）を
# read_identity_partsが検出するための印。現行のcreate_soul/update_identityはもう
# identity.md単体にこの見出しを書かない（口調はspeech_style.mdへ完全に分離した）。
# 旧SOUL（口調分離より前に作られたもの）を開いたときの自動移行にのみ使う。
SPEECH_STYLE_HEADING = "## 口調"
_SPEECH_STYLE_RE = re.compile(r"^##\s*口調\s*$", re.MULTILINE)
CORE_EMPTY_NOTE = "（核は空。人格は記憶が育てる）"

# 名前未設定SOULの表示名フォールバック（list_soulsが使う）。name.txt自体には
# 書き込まない——これはあくまで一覧表示用の代替文字列であり、実際の名前が
# 空であることの記録（read_name/get_soul_identityが返す生の値）とは別物。
UNNAMED_LABEL = "（名前未定）"
NAME_MAX_CHARS = 100


def soul_dir(soul_id):
    return os.path.join(SOULS_DIR, soul_id)


def _safe_path(soul_id, rel_path):
    base = os.path.abspath(soul_dir(soul_id))
    full = os.path.abspath(os.path.join(base, rel_path))
    if not full.startswith(base + os.sep) and full != base:
        raise ValueError(f"魂フォルダの外は触れない: {rel_path}")
    return full


def _core_content(identity_text):
    """核テキストをidentity.mdの中身へ整形する。空ならIDENTITY_PLACEHOLDERそのもの
    （prompt.pyの完全一致除外と整合。空以外の値はプレースホルダ文字列と一致しないので
    必ずプロンプトに乗る）。create_soul/update_identity/_migrate_legacy_identityが
    ここだけを通す（core→ファイル内容への変換規則の重複を作らない）。"""
    core = (identity_text or "").strip()
    return core + "\n" if core else IDENTITY_PLACEHOLDER


def _speech_content(speech_style):
    """口調テキストをspeech_style.mdの中身へ整形する。空ならSPEECH_STYLE_PLACEHOLDER
    そのもの（read_identity_partsの完全一致除外と整合）。_core_contentと対の関数。"""
    speech = (speech_style or "").strip()
    return speech + "\n" if speech else SPEECH_STYLE_PLACEHOLDER


def create_soul(name, identity_text="", speech_style="", inherit_user_from=None):
    """SOUL用ディレクトリを新規作成し、soul_idを返す。
    slugは名前から生成するが、名前が空文字（＝名前なし開始）でスラッグが
    "soul"にフォールバックする場合に限り、代わりに時刻ベース
    （soul-<YYYYmmdd-HHMMSS>）をスラッグにする。soul_idはローカル連番でしか
    衝突回避しない（下のwhileループ）ため、名前なしのまま固定の"soul"スラッグ
    だと、バックアップ経由で別インストールのSOULを持ち込んだ時に同じID
    （"soul", "soul-2", ...）へ偶然一致し、import_backupのマージ展開が
    無警告で中身を上書きしてしまう（実測済みの欠陥）。名前ありのスラッグは
    元々名前由来で衝突しにくいため、この変更では触らない（既存SOULのIDに
    影響を与えない）。

    inherit_user_from: 指定すると、既存SOUL（soul_id）のuser.md（相手理解）を
    新SOULのuser.mdへ1回だけコピーする。SOULは完全分離設計（1フォルダ=1魂）
    のため、常時共有はしない——「作成時に1回だけ引き継ぐ選択肢」に留める。
    存在しないsoul_id（タイポ・削除済み・トラバーサル文字列）を渡された場合は
    ValueErrorで拒否する（soul_dirのwhileループと同じos.path.isdirチェックを
    既存の検証パターンとして流用）。引き継ぎ元のuser.mdが空/プレースホルダ
    （＝まだ何も書かれていない）なら、コピーせず従来どおりの空user.mdにする
    （空を「引き継いだ」と偽って冒頭注記だけ付くのは不自然なため）。"""
    if inherit_user_from is not None and not os.path.isdir(soul_dir(inherit_user_from)):
        raise ValueError(f"引き継ぎ元のSOULが存在しない: {inherit_user_from}")
    os.makedirs(SOULS_DIR, exist_ok=True)
    slug = re.sub(r"[^\w\-]", "-", name.strip())[:20]
    if not slug:
        slug = "soul-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    soul_id, n = slug, 1
    while os.path.isdir(soul_dir(soul_id)):
        n += 1
        soul_id = f"{slug}-{n}"
    d = soul_dir(soul_id)
    for sub in ("chronicle", "wiki", "sacred", "logs", "readings"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    _write(os.path.join(d, "identity.md"), _core_content(identity_text))
    _write(os.path.join(d, "speech_style.md"), _speech_content(speech_style))
    _write(os.path.join(d, "self_notes.md"), SELF_NOTES_PLACEHOLDER)
    _write(os.path.join(d, "user.md"), _inherited_user_content(inherit_user_from))
    _write(os.path.join(d, "MEMORY.md"), MEMORY_INDEX_PLACEHOLDER)
    _write(os.path.join(d, "lessons.md"), LESSONS_PLACEHOLDER)
    _write(os.path.join(d, "tasks.md"), TASKS_PLACEHOLDER)
    _write(os.path.join(d, "name.txt"), name.strip())
    return soul_id


def _inherited_user_content(inherit_user_from):
    """create_soul用: 引き継ぎ元が無い/空/プレースホルダならUSER_PLACEHOLDERそのもの
    （従来どおり）。中身があれば冒頭に引き継ぎ元を明記した1行を足して返す。"""
    if inherit_user_from is None:
        return USER_PLACEHOLDER
    source_raw = read_file(inherit_user_from, "user.md").strip()
    if source_raw in ("", USER_PLACEHOLDER.strip()):
        return USER_PLACEHOLDER
    source_name = read_name(inherit_user_from) or UNNAMED_LABEL
    note = (f"（この相手理解は SOUL『{source_name}』から引き継いだ。"
            "以後は自分の目で更新していくこと）")
    return note + "\n\n" + source_raw + "\n"


def list_souls():
    """SOUL一覧を返す。表示名(name)は name.txt が空/欠落なら UNNAMED_LABEL
    （soul_id剥き出しはやめる。表示専用のフォールバックで、name.txt自体は書き換えない）。
    生の名前（空文字を含む）が必要な呼び出し元は read_name を直接使うこと
    （gui.get_soul_identityが編集フォーム用にそちらを使う）。"""
    if not os.path.isdir(SOULS_DIR):
        return []
    out = []
    for soul_id in sorted(os.listdir(SOULS_DIR)):
        d = soul_dir(soul_id)
        if not os.path.isdir(d):
            continue
        out.append({"id": soul_id, "name": read_name(soul_id) or UNNAMED_LABEL})
    return out


def read_name(soul_id):
    """name.txtの生の値（トリム済み）を返す。未設定/ファイル欠落なら空文字。
    list_souls（表示フォールバック適用前）・prompt.build_system_text（名前注入）・
    gui.get_soul_identity（改名フォームの初期値）が共通で使う唯一の読み取り経路。"""
    return read_file(soul_id, "name.txt").strip()


def validate_name(name):
    """名前として妥当かを検証する。改行を含む場合・NAME_MAX_CHARS超・
    制御文字（Unicodeカテゴリ Cc。改行\\nもCcの一種だが明示チェックは残す）を
    含む場合・トリム後がUNNAMED_LABEL（一覧表示用の予約フォールバック文言）と
    完全一致する場合は拒否する。絵文字・サロゲートペア・通常のユニコード文字は
    許可する。空文字（名前を白紙に戻す操作）は正当な入力として許可する。
    戻り値: (True, トリム済み文字列) または (False, エラー文言)。
    gui.Bridge.rename_soul と memory_tools.execute の set_soul_name が共通で使う
    唯一の検証ロジック（改名経路が2つあっても判定基準が食い違わないようにする）。"""
    raw = name or ""
    if "\n" in raw or "\r" in raw:
        return False, "名前に改行は使えません"
    if any(unicodedata.category(ch) == "Cc" for ch in raw):
        return False, "名前に制御文字は使えません"
    cleaned = raw.strip()
    if len(cleaned) > NAME_MAX_CHARS:
        return False, f"名前は{NAME_MAX_CHARS}字までです"
    if cleaned == UNNAMED_LABEL:
        return False, "その名前は表示用の予約語のため使えません"
    return True, cleaned


def set_name(soul_id, name):
    """name.txtへ書き込む唯一の経路。呼び出し前に validate_name を通した文字列を
    渡すこと（この関数自体は検証しない、write_file相当の薄いラッパー）。"""
    write_file(soul_id, "name.txt", name)


def read_file(soul_id, rel_path):
    full = _safe_path(soul_id, rel_path)
    if not os.path.isfile(full):
        return ""
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def write_file(soul_id, rel_path, content):
    full = _safe_path(soul_id, rel_path)
    _write(full, content)


# archive_file/unarchive_fileの対象範囲。「忘れる」＝連想（auto-recall）から外れる
# ことであり、記録から消えることではない、という思想の実装（recall.pyがarchive/を
# 除外するのが「忘れる」の実体）。対象はwiki/・readings/配下の.mdだけに絞る：
# sacred（永久保存の趣旨と矛盾する）・lessons/tasks/self_notes/user/identity等の
# コアファイル・logs/chronicle（会話の生ログ・日記そのもの）は「忘れる」対象外とし、
# ここで拒否する（判断主体はAI本人だが、核と生ログは動かせない設計にする）。
_ARCHIVABLE_PREFIXES = ("wiki/", "readings/")


def _validate_archivable(rel_path):
    """archive_fileが受け付けてよいrel_pathかを検証する。wiki/・readings/配下の
    .mdファイルだけを許可し、それ以外はValueErrorで拒否する。"""
    if not rel_path.endswith(".md"):
        raise ValueError(f"archiveできるのは.mdファイルのみ: {rel_path}")
    if not rel_path.startswith(_ARCHIVABLE_PREFIXES):
        raise ValueError(f"archiveできるのはwiki/・readings/配下のみ: {rel_path}")


def _collision_free_dest(soul_id, rel_path):
    """rel_pathの行き先に既に同名ファイルがあれば、拡張子の直前へ -2, -3... と
    連番を挿んで衝突を避ける（上書きしない——退避先行の家訓。save_attachmentの
    連番探索と同じ考え方）。"""
    base, ext = os.path.splitext(rel_path)
    candidate = rel_path
    n = 1
    while os.path.isfile(_safe_path(soul_id, candidate)):
        n += 1
        candidate = f"{base}-{n}{ext}"
    return candidate


def archive_file(soul_id, rel_path):
    """wiki/・readings/配下の.mdファイル1本を、元の相対構造を保ったまま
    archive/配下へ移動する（例: "wiki/x.md" → "archive/wiki/x.md"）。
    shutil.moveによる移動であって削除ではない（完全可逆・「歴史を消さない」の掟に
    抵触しない）。移動先に同名が既にあれば_collision_free_destで連番回避する
    （上書きしない）。戻り値は実際に書き込んだ先の相対パス。
    対象外（sacred/lessons/tasks/self_notes/user/identity/logs/chronicle等、
    または存在しないファイル）はValueErrorで拒否し、一切変更しない。"""
    rel_path = (rel_path or "").replace("\\", "/")
    _validate_archivable(rel_path)
    src = _safe_path(soul_id, rel_path)
    if not os.path.isfile(src):
        raise ValueError(f"存在しないファイル: {rel_path}")
    dest_rel = _collision_free_dest(soul_id, f"archive/{rel_path}")
    dest = _safe_path(soul_id, dest_rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    return dest_rel


def unarchive_file(soul_id, rel_path):
    """archive_fileの逆操作。"archive/wiki/x.md" → "wiki/x.md" のように、archive/の
    接頭辞を外した元の場所へ戻す。戻し先に同名が既にあれば_collision_free_destで
    連番回避する（上書きしない）。戻り値は実際に書き込んだ先の相対パス。
    rel_pathが"archive/"で始まらない、または存在しないファイルの指定はValueErrorで
    拒否し、一切変更しない。"""
    rel_path = (rel_path or "").replace("\\", "/")
    if not rel_path.startswith("archive/"):
        raise ValueError(f"unarchiveできるのはarchive/配下のみ: {rel_path}")
    src = _safe_path(soul_id, rel_path)
    if not os.path.isfile(src):
        raise ValueError(f"存在しないファイル: {rel_path}")
    original_rel = rel_path[len("archive/"):]
    dest_rel = _collision_free_dest(soul_id, original_rel)
    dest = _safe_path(soul_id, dest_rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    return dest_rel


def _migrate_legacy_identity(soul_id):
    """旧形式（identity.md内に核と口調が"## 口調"見出しで同居）を検出したら、
    口調をspeech_style.mdへ分離し、identity.mdを核だけに書き戻す（1回きりの移行）。
    読み直しても見出しはもう見つからないため、2回目以降は何もしない
    （見出しが無ければ即returnするため冪等）。

    書き込み順は必ず speech_style.md 保存 → identity.md 書き戻し（この逆はやらない）。
    理由: identity.mdを先に上書きしてしまうと、その直後（speech_style.md保存前）に
    クラッシュした場合、旧identity.mdに同居していた口調が消え、identity.mdはもう
    見出しを含まないため次回の read_identity_parts は「移行不要」と判断して再試行
    できず、口調が恒久的に失われる。speech_style.mdを先に保存する順なら、途中で
    クラッシュしても identity.md は旧形式のまま無傷＝見出しがまだ残っているので
    次回の呼び出しで再び移行が走る（安全側に倒れる）。"""
    raw = read_file(soul_id, "identity.md")
    m = _SPEECH_STYLE_RE.search(raw)
    if not m:
        return
    legacy_core = raw[:m.start()].strip()
    if legacy_core == CORE_EMPTY_NOTE:
        legacy_core = ""
    legacy_speech = raw[m.end():].strip()
    write_file(soul_id, "speech_style.md", _speech_content(legacy_speech))
    write_file(soul_id, "identity.md", _core_content(legacy_core))


def read_identity_parts(soul_id):
    """identity.md（核）とspeech_style.md（口調）を別々に読んで返す。SOUL編集GUI用。
    プレースホルダ由来の文言はユーザーが書いた内容ではないので、編集フォームには
    空文字として渡す。旧形式（核と口調がidentity.md内に同居）のSOULを開いた場合は
    ここで自動移行してから読む（_migrate_legacy_identity参照）。"""
    _migrate_legacy_identity(soul_id)
    core_raw = read_file(soul_id, "identity.md").strip()
    core = "" if core_raw in ("", IDENTITY_PLACEHOLDER.strip()) else core_raw
    speech_raw = read_file(soul_id, "speech_style.md").strip()
    speech = "" if speech_raw in ("", SPEECH_STYLE_PLACEHOLDER.strip()) else speech_raw
    return {"core": core, "speech_style": speech}


def update_identity(soul_id, identity_text, speech_style):
    write_file(soul_id, "identity.md", _core_content(identity_text))
    write_file(soul_id, "speech_style.md", _speech_content(speech_style))


_REVISION_TARGETS = {
    "identity": ("identity.md", _core_content),
    "speech_style": ("speech_style.md", _speech_content),
}
REVISION_MIN_CHARS = 10
REVISION_MAX_CHARS = 3000
REVISION_MIN_SIMILARITY = 0.5


def revise_identity_file(soul_id, which, new_text):
    """本人が自分のidentity.md（核）またはspeech_style.md（口調）を自分で改訂するための
    唯一の書き込み経路。安全弁3枚つき（呼び出し順もこの通り）:
    - ガード3（下限・上限）: 空/10字未満、3000字超は拒否
    - ガード2（変化量制限・旧文の保持率ベース）: 旧文（プレースホルダ/空を除く）のうち
      新文にそのまま残っている量（difflib.SequenceMatcher.get_matching_blocks()の
      合計サイズ）が旧文の長さの REVISION_MIN_SIMILARITY 未満なら拒否する
      （＝旧文の半分以上を消す改訂は認めない。追記は量に関わらず自由）。
      SequenceMatcher.ratio()（2*M/(len(old)+len(new))）は使わない：純粋な追記でも
      new側が長くなるほど分母が膨らんでratioが下がり、旧文を一切消していない追記すら
      誤って拒否してしまう（実測: 旧10字+加筆36字→ratio=0.357で拒否）。保持率は
      分母をlen(old)固定にすることでこの追記パラドックスを避ける。上限3000字ガードが
      総量の歯止めとして別途機能する。
      旧文がプレースホルダ/空＝まだ何も自己定義していない状態なら初回の自由な自己定義として
      このガードをスキップする
    - ガード1（履歴保全）: 実際に書き込む直前、変更前の生ファイル内容を
      identity_history/YYYY-MM-DD-HHMMSS-<which>.md へ保存してから上書きする
      （同一秒内の複数回改訂はsave_attachmentと同じ流儀で連番サフィックスを足し、
      既存の履歴を上書きしない）
    どのガードで拒否してもファイルは一切変更しない。"""
    if which not in _REVISION_TARGETS:
        return {"ok": False, "detail": f"未知の改訂対象: {which}"}
    text = (new_text or "").strip()
    if len(text) < REVISION_MIN_CHARS:
        return {"ok": False, "detail": f"短すぎる（{REVISION_MIN_CHARS}字未満は拒否）"}
    if len(text) > REVISION_MAX_CHARS:
        return {"ok": False, "detail": f"長すぎる（{REVISION_MAX_CHARS}字超は拒否。核は薄く保つべき）"}

    rel_path, content_fn = _REVISION_TARGETS[which]
    old_raw = read_file(soul_id, rel_path)
    old_stripped = old_raw.strip()
    placeholder = content_fn("").strip()
    old_effective = "" if old_stripped in ("", placeholder) else old_stripped
    if old_effective:
        matcher = difflib.SequenceMatcher(None, old_effective, text)
        retained = sum(block.size for block in matcher.get_matching_blocks())
        retention = retained / len(old_effective)
        if retention < REVISION_MIN_SIMILARITY:
            return {"ok": False,
                    "detail": "旧文の半分以上を消す改訂はできません（追記は自由）"}

    now = datetime.datetime.now()
    base_name = now.strftime("%Y-%m-%d-%H%M%S") + f"-{which}"
    hist_rel = f"identity_history/{base_name}.md"
    n = 1
    while os.path.isfile(_safe_path(soul_id, hist_rel)):
        n += 1
        hist_rel = f"identity_history/{base_name}-{n}.md"
    write_file(soul_id, hist_rel, old_raw)
    write_file(soul_id, rel_path, content_fn(text))
    return {"ok": True, "detail": f"{rel_path} を改訂した（旧版は{hist_rel}へ保存）"}


# self_notes.md/user.md（全文置換系ファイル）とwiki/配下のwikiページ（wrapup.py
# run_wiki_gardeningが使う）の、上書き前の世代付き退避で共通する仕組み。
# identity_history/と違い世代上限をつける（下記NOTES_HISTORY_MAX_GENERATIONS）：
# identity_history/には上限が無い（現物確認済み。revise_identity_fileは書き込み
# 頻度が低い＝本人の明示的な自己改訂のみ）のに対し、こちらはLLMが高頻度で
# 全文書き直しうるファイル群なので、無制限だと際限なく増える。
NOTES_HISTORY_MAX_GENERATIONS = 50

# rel_path → (履歴ファイル名の接頭辞, プレースホルダ) のマップ。
# update_self_notes/update_user_notesのみが _save_notes_history を通して使う。
_NOTES_HISTORY_TARGETS = {
    "self_notes.md": ("self_notes", SELF_NOTES_PLACEHOLDER),
    "user.md": ("user", USER_PLACEHOLDER),
}

# 履歴ファイル名 "<接頭辞>-YYYYmmdd-HHMMSS[-N].md" を解析して並び順キーを取り出す。
# mtime（ファイルシステムの更新日時）ではなくファイル名から並び順を決める：
# 短時間に連続書き込みが起きるとmtimeの解像度によっては同着になり得て、
# タイブレークがos.listdir()の返し順（生成順の保証がない）に落ちてしまうため。
_NOTES_HIST_NAME_RE = re.compile(r"^(?P<prefix>.+)-(?P<ts>\d{8}-\d{6})(?:-(?P<n>\d+))?\.md$")


def _save_history_generic(soul_id, rel_path, hist_dir_name, prefix, placeholder,
                           max_generations):
    """rel_pathの現在の内容を、上書きされる前に<hist_dir_name>/<接頭辞>-<YYYYmmdd-HHMMSS>.md
    へ退避する。現内容が空/プレースホルダ（＝まだ何も書かれていない）なら退避しない
    （ゴミ防止。revise_identity_fileの同種判定と同じ考え方）。退避後、同ファイルの履歴が
    max_generationsを超えていたら、生成順（_NOTES_HIST_NAME_RE参照）で最も古いものから削除する。
    _save_notes_history（self_notes.md/user.md）とsave_wiki_history（wiki/配下）が
    共有する実装（退避先ディレクトリ・接頭辞・世代上限だけが呼び出し側で違う）。

    同一秒内の衝突サフィックスNは、「まだ存在しないファイル名」ではなく
    「同じ接頭辞・同じ秒で現在存在する最大のN」+1として決める。前者
    （os.path.isfileでの空き番号探索）だと、直後のプルーニングで小さいNの
    ファイルが消えた瞬間にその番号が「空き」に見えてしまい、以降の保存が
    その番号を再利用して別内容を上書き＝実質的に履歴を消してしまう
    （実測して発見したバグ）。後者（既存最大値+1）なら、一度使われた番号は
    そのファイルが消えても二度と再利用されないため、この問題が起きない。"""
    old_raw = read_file(soul_id, rel_path)
    if old_raw.strip() in ("", (placeholder or "").strip()):
        return
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    hist_dir = os.path.join(soul_dir(soul_id), hist_dir_name)
    max_n = 0
    if os.path.isdir(hist_dir):
        for f in os.listdir(hist_dir):
            m = _NOTES_HIST_NAME_RE.match(f)
            if m and m.group("prefix") == prefix and m.group("ts") == ts:
                max_n = max(max_n, int(m.group("n")) if m.group("n") else 1)
    n = max_n + 1
    base_name = f"{prefix}-{ts}"
    hist_rel = (f"{hist_dir_name}/{base_name}.md" if n == 1
                else f"{hist_dir_name}/{base_name}-{n}.md")
    write_file(soul_id, hist_rel, old_raw)
    _prune_history_generic(soul_id, hist_dir_name, prefix, max_generations)


def _prune_history_generic(soul_id, hist_dir_name, prefix, max_generations):
    hist_dir = os.path.join(soul_dir(soul_id), hist_dir_name)
    if not os.path.isdir(hist_dir):
        return
    entries = []
    for f in os.listdir(hist_dir):
        m = _NOTES_HIST_NAME_RE.match(f)
        if not m or m.group("prefix") != prefix:
            continue
        n = int(m.group("n")) if m.group("n") else 1
        entries.append((m.group("ts"), n, f))
    entries.sort()
    excess = len(entries) - max_generations
    if excess <= 0:
        return  # entries[:excess]は負のexcessだと末尾からのスライスになり誤って
                 # 削除してしまう（実測して発見したバグ）。ここで明示的に打ち切る。
    for _, _, f in entries[:excess]:
        os.remove(os.path.join(hist_dir, f))


def _save_notes_history(soul_id, rel_path):
    """rel_path（self_notes.md/user.md）版の_save_history_generic呼び出し
    （退避先はnotes_history/、世代上限はNOTES_HISTORY_MAX_GENERATIONS）。"""
    prefix, placeholder = _NOTES_HISTORY_TARGETS[rel_path]
    _save_history_generic(soul_id, rel_path, "notes_history", prefix, placeholder,
                           NOTES_HISTORY_MAX_GENERATIONS)


def save_wiki_history(soul_id, rel_path):
    """wiki/配下のページ（rel_path、例: "wiki/topicA.md"）の現在の内容を、上書きされる
    前にwiki_history/<ファイル名（拡張子抜き）>-<YYYYmmdd-HHMMSS>.mdへ退避する。
    wrapup.run_wiki_gardening専用（LLMによる整理で上書きする直前に呼ぶ）。
    _save_notes_historyと同じ仕組みを共有する（退避先ディレクトリと接頭辞だけが違う。
    プレースホルダ概念がwikiページには無いので空文字を渡す＝空でなければ常に退避）。
    世代上限はnotes_history/と同じNOTES_HISTORY_MAX_GENERATIONS(50世代)。"""
    prefix = os.path.splitext(os.path.basename(rel_path))[0]
    _save_history_generic(soul_id, rel_path, "wiki_history", prefix, "",
                           NOTES_HISTORY_MAX_GENERATIONS)


def update_self_notes(soul_id, content):
    """self_notes.md（自己認識メモ）を全文置換する唯一の経路
    （memory_tools.executeのupdate_self_notesツールが使う）。書き換え前に旧内容を
    notes_history/へ退避してから上書きする（「1ヶ月前の自分はどう見ていたか」が
    全文書き直しのたびに消えていた問題への対処。2026-07-22追加）。"""
    _save_notes_history(soul_id, "self_notes.md")
    write_file(soul_id, "self_notes.md", content)


# skills/（手続き記憶・スキル）関連。1スキル=1ファイル、souls/<id>/skills/<サニタイズ名>.md。
# ファイル形式: 1行目 `# <スキル名>`、2行目 `> <1行説明>`、以降が手順本文。
# ロール(roles.json、globalな「いま何者モードか」)とは別概念——スキルは「このタスクの
# やり方を知ってる」という手順書であり、SOULごとに獲得する記憶（souls/配下）である点が
# 決定的に違う（roles.jsonはSOUL非依存のグローバル設定）。
SKILLS_DIR_NAME = "skills"
SKILLS_HISTORY_DIR_NAME = "skills_history"
SKILLS_HISTORY_MAX_GENERATIONS = NOTES_HISTORY_MAX_GENERATIONS

# スキル名の上限（レビュー指摘・2026-07-22追加）。_safe_nameの40字打ち切りは
# 元々「危険文字の置換」目的で、名前が長すぎる場合は黙って切り詰めていた。
# しかしスキル名は自由記述でLLMが付けるため、41字以上で先頭39字までが一致する
# 別名同士（例:「AAAA...(41字)」と「AAAA...(41字だが末尾だけ違う)」）が
# 切り詰め後に同一ファイルへ収束し、片方が無警告で消える事故になり得る。
# 対処として、_safe_name自体の挙動（他用途——現状はスキル以外に呼び出し元は
# 無いが、将来の共通化に備えて据え置く）は変えず、スキル名だけ_skill_path側で
# 上限超をValueErrorとして拒否する（切り詰めではなく拒否にすることで、
# 衝突が起きる余地そのものを無くす）。
SKILL_NAME_MAX_CHARS = 40


def _safe_name(name):
    """ファイル名として安全な形に変換する。memory_tools._safe_nameと同じサニタイズ
    規則（Windows禁止文字の置換・40字打ち切り・空なら"無題"）をここに複製する
    （soul.pyはmemory_tools.pyに依存しない一方向の依存構造を保つため、共通化せず
    複製する。ルール自体は完全一致させること）。"""
    return re.sub(r'[\\/:*?"<>|]', "-", (name or "").strip())[:40] or "無題"


def _skill_path(name):
    """スキル名からファイルパスを作る。write_skill/read_skill/skill_exists/
    skill_header_nameの唯一の経路（この関数を通さずskills/配下のパスを
    組み立てるコードは無い）。SKILL_NAME_MAX_CHARS超はValueErrorで拒否する
    （上のSKILL_NAME_MAX_CHARSのコメント参照）。"""
    stripped = (name or "").strip()
    if len(stripped) > SKILL_NAME_MAX_CHARS:
        raise ValueError(f"スキル名は{SKILL_NAME_MAX_CHARS}字以内にしてください")
    return f"{SKILLS_DIR_NAME}/{_safe_name(name)}.md"


def list_skills(soul_id):
    """souls/<id>/skills/配下の各.mdファイルの先頭2行（1行目 `# 名前`、2行目 `> 説明`）を
    パースして [{"name","description"}, ...] をファイル名の辞書順で返す。skills/自体が
    無ければ空リスト。壊れた形式（1行目が"# "始まりでない等）は、説明を空にしつつ
    ファイル名（拡張子抜き）を名前のフォールバックにする——1本の壊れたファイルで
    索引全体が読めなくなることはない。"""
    d = os.path.join(soul_dir(soul_id), SKILLS_DIR_NAME)
    if not os.path.isdir(d):
        return []
    out = []
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".md"):
            continue
        fallback = fname[: -len(".md")]
        raw = read_file(soul_id, f"{SKILLS_DIR_NAME}/{fname}")
        name, description = _parse_skill_header(raw, fallback)
        out.append({"name": name, "description": description})
    return out


def _parse_skill_header(raw, fallback):
    lines = raw.splitlines()
    name = fallback
    if lines and lines[0].strip().startswith("# "):
        parsed = lines[0].strip()[2:].strip()
        if parsed:
            name = parsed
    description = ""
    if len(lines) > 1 and lines[1].strip().startswith(">"):
        description = lines[1].strip()[1:].strip()
    return name, description


def read_skill(soul_id, name):
    """スキル1本の全文（ヘッダ2行込み）を読む。存在しなければ空文字
    （read_fileと同じ挙動。memory_tools.execute の use_skill が存在チェックに使う）。"""
    return read_file(soul_id, _skill_path(name))


def skill_exists(soul_id, name):
    """指定名のスキルが（サニタイズ後の実ファイルパス基準で）既に存在するか。
    memory_tools.execute の create_skill（同名拒否）・update_skill（不存在拒否）が使う。
    見た目の名前ではなく実ファイルの有無で判定するのがポイント——"a/b"と"a:b"は
    _safe_nameのサニタイズで共に"a-b.md"へ収束するため、この基準を使えば
    「別名のつもりが同じファイルを指していた」という衝突も自然に検知できる。"""
    return bool(read_skill(soul_id, name).strip())


def skill_header_name(soul_id, name):
    """指定名に対応するスキルファイルの1行目ヘッダ（"# 名前"）に書かれている
    名前を返す。ファイルが存在しない場合／1行目が壊れていて_parse_skill_headerが
    フォールバック名を使った場合はNoneを返す（両者を区別する必要が無い呼び出し元
    ＝update_skillのヘッダ照合にとっては「照合しようがない」という同じ扱いになるため）。
    _parse_skill_header にユニークなsentinelをfallbackとして渡し、戻り値が
    sentinelのままかどうかでフォールバックが使われたかを判定する（パース処理自体を
    複製しないための手法）。"""
    raw = read_skill(soul_id, name)
    if not raw.strip():
        return None
    sentinel = object()
    parsed_name, _ = _parse_skill_header(raw, sentinel)
    return None if parsed_name is sentinel else parsed_name


def write_skill(soul_id, name, description, content):
    """スキル1本（souls/<id>/skills/<サニタイズ名>.md）を書く。create_skill/update_skillの
    唯一の書き込み経路（両ツールとも実体はこの1関数——「新規か上書きか」で処理を
    分ける必要が無い。全文置換であり、上書き時は旧版をskills_history/へ退避してから
    書く（_save_history_generic再利用、50世代=SKILLS_HISTORY_MAX_GENERATIONS。
    まだ存在しない/空なら退避しない、という_save_history_generic既存の判定をそのまま使う）。"""
    path = _skill_path(name)
    prefix = os.path.splitext(os.path.basename(path))[0]
    _save_history_generic(soul_id, path, SKILLS_HISTORY_DIR_NAME, prefix, "",
                           SKILLS_HISTORY_MAX_GENERATIONS)
    body = (content or "").rstrip()
    text = f"# {(name or '').strip()}\n> {(description or '').strip()}\n\n{body}\n"
    write_file(soul_id, path, text)


def update_user_notes(soul_id, content):
    """user.md（相手理解メモ）を全文置換する唯一の経路
    （memory_tools.executeのupdate_user_notesツールが使う）。update_self_notesと
    対の関数で方針は同じ（notes_history退避→上書き）。"""
    _save_notes_history(soul_id, "user.md")
    write_file(soul_id, "user.md", content)


def append_file(soul_id, rel_path, content):
    full = _safe_path(soul_id, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(content)


def save_attachment(soul_id, mime, b64_data):
    """画像添付1枚をattachments/YYYY-MM-DD/HHMMSS-n.<ext>へデコード保存し、
    soul_dir相対のパス（"attachments/2026-07-22/143022-1.png"等）を返す。

    mimeが未対応（_ATTACHMENT_EXT非掲載）ならValueErrorで拒否する（拡張子から
    確実に画像とわかるものだけ許可する設計。engine.pyはb64をログに書かず、この
    相対パスだけをログへ残すため、保存に失敗するデータをそもそも受け取らない）。
    同一秒内の連続添付はn（1始まり）を衝突が無くなるまで繰り上げてファイル名を決める。
    """
    ext = _ATTACHMENT_EXT.get((mime or "").strip().lower())
    if ext is None:
        raise ValueError(f"未対応の画像形式: {mime}")
    now = datetime.datetime.now()
    day = now.date().isoformat()
    time_part = now.strftime("%H%M%S")
    n = 1
    while True:
        rel_path = os.path.join("attachments", day, f"{time_part}-{n}.{ext}")
        full = _safe_path(soul_id, rel_path)
        if not os.path.exists(full):
            break
        n += 1
    data = base64.b64decode(b64_data)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    return rel_path.replace("\\", "/")


def append_log(soul_id, who, text):
    day = datetime.date.today().isoformat()
    entry = {"who": who, "text": text,
             "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    append_file(soul_id, os.path.join("logs", f"{day}.jsonl"),
                json.dumps(entry, ensure_ascii=False) + "\n")


def append_break(soul_id):
    """「区切り」機能: 今日のログへ who="break", text="" の行を1本追記する。

    append_logと同じjsonl追記フォーマットに乗せるだけの薄いラッパー
    （既存の_parse_jsonl_lines/read_today_logはdictの行なら何でも読める汎用パーサ
    なので、"break"という新しいwho値が来ても壊れない）。区切りは会話の削除では
    なくマーカーの挿入なので、他のログ行同様に消えない記録として残る。"""
    append_log(soul_id, "break", "")


def read_today_log(soul_id):
    return read_log_for(soul_id, datetime.date.today().isoformat())


def read_log_for(soul_id, date_str):
    raw = read_file(soul_id, os.path.join("logs", f"{date_str}.jsonl"))
    return _parse_jsonl_lines(raw)


def recent_chronicle(soul_id, n=2):
    d = os.path.join(soul_dir(soul_id), "chronicle")
    if not os.path.isdir(d):
        return ""
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    parts = []
    for fname in files[-n:]:
        parts.append(read_file(soul_id, os.path.join("chronicle", fname)))
    return "\n\n".join(p for p in parts if p)


def latest_chronicle(soul_id):
    """chronicle/直下（weekly/monthly除く）の最新の日次日記1本を
    {"date": "YYYY-MM-DD", "text": str} で返す。1本も無ければNone。
    GUIの「今日の日記」パネルが、セッション終了時にしか日記が書かれない
    仕様のせいで開いている間ずっと「まだ書かれてない」表示になる問題への対処
    （2026-07-22 実機フィードバック）。ファイル名は"YYYY-MM-DD.md"形式が前提で、
    辞書順ソート=日付順ソートが成立する（月日とも常に2桁ゼロ埋めのため）。"""
    d = os.path.join(soul_dir(soul_id), "chronicle")
    if not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    if not files:
        return None
    fname = files[-1]
    text = read_file(soul_id, os.path.join("chronicle", fname))
    return {"date": fname[:-len(".md")], "text": text}


def recent_weekly_digest(soul_id):
    """chronicle/weekly/の最新1本（ファイル名の辞書順=週の新しい順）を返す。
    無ければ空文字（prompt.pyはこれを見て見出しごと省略する）。
    注意: 辞書順ソートは「YYYY-W日日」のゼロ埋め（W09等）が前提。手動でゼロ埋め
    なしのファイル（2026-W9.md）を置くとW10より新しい扱いになり誤選択する。"""
    d = os.path.join(soul_dir(soul_id), "chronicle", "weekly")
    if not os.path.isdir(d):
        return ""
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    if not files:
        return ""
    return read_file(soul_id, os.path.join("chronicle", "weekly", files[-1]))


def recent_monthly_digest(soul_id):
    """chronicle/monthly/の最新1本（ファイル名の辞書順=月の新しい順）を返す。
    無ければ空文字（prompt.pyはこれを見て見出しごと省略する）。
    月は"YYYY-MM"形式（例: "2026-07"）で月の部分が常に2桁ゼロ埋めのため、
    recent_weekly_digestが注意している「ゼロ埋め崩れで辞書順が狂う」問題は
    月では起こらない（月は1〜12で常に2桁の"07"等になる。週番号のような
    1〜2桁ゆれが無い）。"""
    d = os.path.join(soul_dir(soul_id), "chronicle", "monthly")
    if not os.path.isdir(d):
        return ""
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    if not files:
        return ""
    return read_file(soul_id, os.path.join("chronicle", "monthly", files[-1]))


def add_reminder(soul_id, text, due, annual=False):
    """reminders.jsonlへ1件追記する。due は"YYYY-MM-DD"必須（不正ならValueError、
    呼び出し元のmemory_tools.executeが外側のtry/exceptでok:False化する）。
    idは既存の最大id+1（欠番があっても単調増加を維持する。削除機能は無いので
    衝突の心配はない）。"""
    try:
        datetime.date.fromisoformat(due)
    except (ValueError, TypeError):
        raise ValueError(f"不正な日付形式（YYYY-MM-DDで指定）: {due}")
    existing = list_reminders(soul_id, include_done=True)
    next_id = max((r.get("id", 0) for r in existing), default=0) + 1
    entry = {"id": next_id, "text": (text or "").strip(), "due": due,
              "annual": bool(annual), "done": False, "last_fired": None}
    append_file(soul_id, REMINDERS_FILE, json.dumps(entry, ensure_ascii=False) + "\n")
    return next_id


def list_reminders(soul_id, include_done=False):
    """reminders.jsonlの全件を読む。include_done=False（既定）ならdone:Trueの
    単発リマインダを除く（annualは done が立たない設計なので常に含まれる）。
    壊れた行（不正なJSON・dict以外のJSON）はsearch.pyのログパーサと同じ方針で
    黙ってスキップする（1行の破損で毎ターンbuild_system_text/process_turnが
    JSONDecodeErrorで死んで会話が恒久停止する事故を避けるため）。"""
    raw = read_file(soul_id, REMINDERS_FILE)
    items = _parse_jsonl_lines(raw)
    if include_done:
        return items
    return [r for r in items if not r.get("done")]


def _parse_jsonl_lines(raw):
    """jsonl本文を行単位でjson.loadsし、dictの行だけをリストで返す。
    壊れた行（不正なJSON）・dictでない正当なJSON（配列・数値等）の行は
    黙ってスキップする（read_log_for/list_reminders共通の耐性ロジック。
    search.py _log_docsと同じ「例外は握って空扱い」方針）。"""
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        items.append(entry)
    return items


def mark_reminder_fired(soul_id, reminder_id):
    """発火記録: 単発(annual:False)はdone:Trueにする。annualはlast_firedに
    発火した年を記録する（doneは立てない＝翌年また対象になる）。
    reminders.jsonl全体（done済み含む）を読み直して書き戻す（履歴を消さない）。"""
    items = list_reminders(soul_id, include_done=True)
    today = datetime.date.today()
    found = False
    for r in items:
        if r.get("id") == reminder_id:
            if r.get("annual"):
                r["last_fired"] = today.year
            else:
                r["done"] = True
            found = True
            break
    if found:
        content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items)
        write_file(soul_id, REMINDERS_FILE, content)
    return found


def due_reminders(soul_id, today=None):
    """今日伝えるべきリマインダの判定を一本化する関数（prompt.py注入・engine.py発火記録が
    両方ここを呼ぶ。二重実装を作らないための唯一の判定ロジック）。
    - 単発（annual:False）: due <= today でまだdoneでない（期限超過分も拾う）
    - annual: due の月日 == today の月日（当日のみ）かつ今年まだ発火していない
      （last_fired が今年の年と一致していない）
    today は date / "YYYY-MM-DD"文字列 / None（省略時は実際の今日）を受け付ける
    （テストから任意の日付で判定を検証できるようにするため）。"""
    if today is None:
        today = datetime.date.today()
    elif isinstance(today, str):
        today = datetime.date.fromisoformat(today)
    due = []
    for r in list_reminders(soul_id, include_done=False):
        try:
            due_date = datetime.date.fromisoformat(r.get("due", ""))
        except (ValueError, TypeError):
            continue
        if r.get("annual"):
            if (due_date.month, due_date.day) == (today.month, today.day) \
                    and r.get("last_fired") != today.year:
                due.append(r)
        else:
            if due_date <= today:
                due.append(r)
    return due


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
