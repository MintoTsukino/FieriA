# -*- mode: python ; coding: utf-8 -*-
# fieria.spec — FieriA の PyInstaller ビルド定義（onedir形式）
#
# 使い方（build_dist.py が生成したクリーン素材フォルダの中で実行する前提）:
#   cd FieriA-build-src
#   python -m PyInstaller fieria.spec
#   → dist/FieriA/ に FieriA.exe + _internal/ ができる
#
# 設計メモ（NikoVoice の nikovoice.spec と同じ判断。理由もほぼ共通）:
# - onedir 一択。onefile は起動のたびに全展開が走って遅く、AV誤検知も増える
# - console=False（黒窓なし）。デバッグ時は False→True にして stdout を見る
# - frozen 時、config.py 等の HERE(__file__基準) は _internal/ を指す。
#   ui/index.html 参照・fieria_home/ 自動生成は _internal/ 内で完結して整合する
# - アイコン未指定（assets/が存在しないため。用意でき次第 icon=… を追加する）
#
# pypdfium2（PDF添付のページ画像化）: 実体のネイティブライブラリ(pdfium.dll)は
# pypdfium2本体ではなく依存パッケージpypdfium2_rawが持つ。pyinstaller-hooks-contrib
# （PyInstallerの依存として自動インストールされる）にstdhooks/hook-pypdfium2_raw.pyが
# 同梱済みで、collect_dynamic_libsによりpdfium.dllは自動同梱されるはず（PyInstaller
# 6.21.0 + pyinstaller-hooks-contrib 2026.6で実機確認済み）だが、暗黙のcontribフックだけに
# 頼るのは将来のフック仕様変更に弱いため、ここでも明示的にcollect_dynamic_libsを呼んで
# binariesへ追加しておく（二重に集めても同じ内容なのでビルド結果は変わらない）。
from PyInstaller.utils.hooks import collect_dynamic_libs

pdfium_binaries = collect_dynamic_libs("pypdfium2_raw")

a = Analysis(
    ["entry.py"],   # gui.py 直でなく entry.py 経由（Mark of the Web 除去を先に走らせる）
    pathex=[],
    binaries=pdfium_binaries,
    datas=[
        ("ui", "ui"),
    ],
    hiddenimports=[
        # pywebview の Windows バックエンド（動的importなので明示する）
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        # pypdfium2はgui.render_pdf内で遅延import。frozen時の動的import検出漏れ対策として明示
        "pypdfium2",
        "pypdfium2_raw",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FieriA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FieriA",
)
