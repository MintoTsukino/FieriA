"""
entry.py — exe版のエントリポイント（Mark of the Web 除去 → GUI起動）

ネット経由で配布したZIPをエクスプローラーで解凍すると、中の全ファイルに
「インターネット由来」マーク（Zone.Identifier 代替データストリーム）が付く。
.NET Framework はマーク付きDLLのロードを拒否するため、pythonnet が
Python.Runtime.dll を解決できず起動に失敗する
（NikoVoiceのテスター環境で実際に発生: "Failed to resolve Python.Runtime.Loader.Initialize"）。

対策: webview（→clr→.NET）を import する前に、_internal 配下の
dll/exe/pyd から Zone.Identifier を削除する。ADSの削除は通常のファイル操作で
可能（管理者権限不要）。マークが無いファイルは即スキップされるので毎回走っても
数十ミリ秒で終わる。
"""

import os
import sys


def _unblock_bundled_files():
    if not getattr(sys, "frozen", False):
        return  # 開発環境（python gui.py）では不要
    base = os.path.join(os.path.dirname(sys.executable), "_internal")
    if not os.path.isdir(base):
        return
    for root, _dirs, files in os.walk(base):
        for name in files:
            if not name.lower().endswith((".dll", ".exe", ".pyd")):
                continue
            try:
                os.remove(os.path.join(root, name) + ":Zone.Identifier")
            except OSError:
                pass  # マークが無い（大半のケース）


_unblock_bundled_files()

import gui  # noqa: E402  （unblock後にimportする必要がある）

if __name__ == "__main__":
    gui.run()
