"""build_dist.py — 配布用クリーンパッケージを作る

開発フォルダ（このファイルがある場所）から、個人データ・開発専用ファイルを除外した
配布用コピーを別フォルダに作る。

除外対象のうち .env / fieria_home/ は「無ければアプリが自動でデフォルト扱いする」
設計になっているので、手でリセット処理を書く必要はない。含めなければ、初回起動時に
fieria_home/ が自動生成されてクリーンな状態になる（config.py の HOME 定義を参照）。

使い方:
  python build_dist.py [出力先フォルダ]
  省略時は ../FieriA-build-src （このフォルダの1つ上）に作る。
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# トップレベルで丸ごと除外するもの（開発専用ファイル・個人データの温床）
EXCLUDE_TOP = {
    ".env", "fieria_home", ".git", ".gitignore", ".superpowers", "tests", "docs",
    "__pycache__", "build_dist.py", "build_exe.py", "fieria.spec",
    ".pytest_cache", "build", "dist",
    # NikoVoiceと同じく、ログイン時に生成されうる生トークンファイル。現時点では
    # 存在しないが、将来生成された場合に混入させないため防御的に除外する
    "xai_oauth.json", "openai_codex_oauth.json",
    # config.py の HOME は既定で fieria_home/ を指すため、リポジトリ直下の
    # souls/ はアプリが実際に使う場所ではない未使用の空フォルダ（2026-07-22時点で
    # git管理外・空）。同梱してもアプリからは参照されず、機密走査で紛らわしいので除外
    "souls",
}


def _copy_tree(src, dst, skipped):
    os.makedirs(dst, exist_ok=True)
    for entry in sorted(os.listdir(src)):
        if entry in EXCLUDE_TOP:
            if entry != "__pycache__":
                skipped.append(entry)
            continue
        if entry.endswith(".pyc"):
            skipped.append(entry)
            continue
        s = os.path.join(src, entry)
        d = os.path.join(dst, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(s, d)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(HERE), "FieriA-build-src")
    out = os.path.abspath(out)
    if os.path.exists(out):
        print(f"[ERROR] 出力先が既に存在する: {out}")
        print("        中身を確認してから手動で消すか、別の出力先を指定してにゃ")
        sys.exit(1)

    print(f"[BUILD] {HERE}")
    print(f"     -> {out}")
    skipped = []
    _copy_tree(HERE, out, skipped)

    print("\n[除外したもの]")
    for s in skipped:
        print(f"  - {s}")

    print(f"\n[DONE] 配布用フォルダを作ったじょ: {out}")
    print("       .env と fieria_home/ は含まれてない")
    print("       → 初回起動時に fieria_home/ が自動生成されてクリーンな状態になる")


if __name__ == "__main__":
    main()
