"""
importer.py — Markdown取り込み（inbox方式）

GUIで選んだ .md を souls/<id>/inbox/ に搬入し（stage_files）、
「インポート実行」で1ファイルずつ専用LLM呼び出しに読ませ、
AI本人が write_wiki / update_memory_index で自分の記憶に整理する。
原文は無改変のまま souls/<id>/imported/ へ移す。

会話エンジン（engine.py）とは独立した単発呼び出し。
会話側の固定注入トークンには一切影響しない。
"""

import os
import shutil

import memory_tools
import soul as soul_mod

INBOX_DIR = "inbox"
IMPORTED_DIR = "imported"
STAGE_EXTS = (".md", ".markdown", ".txt")


def _inbox_path(soul_id):
    return os.path.join(soul_mod.soul_dir(soul_id), INBOX_DIR)


def _imported_path(soul_id):
    return os.path.join(soul_mod.soul_dir(soul_id), IMPORTED_DIR)


def list_inbox(soul_id):
    """inbox内のファイル名一覧（昇順）。ディレクトリ未作成なら空。"""
    d = _inbox_path(soul_id)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))


def _unique_dest(dirpath, name):
    """dirpath内で衝突しないファイルパスを返す（同名なら -2, -3 …を付ける）。"""
    base, ext = os.path.splitext(name)
    cand, n = name, 1
    while os.path.exists(os.path.join(dirpath, cand)):
        n += 1
        cand = f"{base}-{n}{ext}"
    return os.path.join(dirpath, cand)


def _stage_one(src, inbox, staged, skipped):
    if not src.lower().endswith(STAGE_EXTS):
        skipped.append(src)
        return
    dest = _unique_dest(inbox, os.path.basename(src))
    try:
        shutil.copy2(src, dest)
        staged.append(os.path.basename(dest))
    except OSError:
        skipped.append(src)


def stage_files(soul_id, paths):
    """ファイル/フォルダのパス列をinboxへコピーする。フォルダは再帰で対象拡張子を拾う。"""
    inbox = _inbox_path(soul_id)
    os.makedirs(inbox, exist_ok=True)
    staged, skipped = [], []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirs, files in os.walk(p):
                dirs.sort()  # サブフォルダの走査順を決定的にする
                for f in sorted(files):
                    _stage_one(os.path.join(dirpath, f), inbox, staged, skipped)
        elif os.path.isfile(p):
            _stage_one(p, inbox, staged, skipped)
        else:
            skipped.append(p)
    return {"ok": True, "staged": staged, "skipped": skipped}
