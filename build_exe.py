"""
build_exe.py — テスター配布用のexeパッケージを一発で作る

やること（全自動）:
  1. build_dist.py でクリーン素材を作る（個人データ・開発ファイル除去済み）
  2. その素材を PyInstaller (fieria.spec) で onedir ビルド
  3. README.md を同梱
  4. exe を実際に起動して生存確認（スモークテスト）→ 生成された fieria_home/ は
     削除してまっさらな状態に戻す
  5. 機密走査: 完成品フォルダに .env / *oauth* / config.json / .git / fieria_home /
     souls / *.jsonl に該当するものが無いか実際に検索する。1件でもヒットしたら
     ビルド失敗扱いにする（絶対条件）

使い方:
  py -3.10 build_exe.py
  → D:/作業/FieriA-build/dist/FieriA/ が完成品。このフォルダをZIPで配る。

前提: py -3.10 -m pip install pyinstaller 済みであること。
"""

import fnmatch
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(os.path.dirname(HERE), "FieriA-build")   # ビルド作業場
FINAL = os.path.join(WORK, "dist", "FieriA")                  # 完成品

# 機密走査パターン。ファイル名(小文字化)に対するglobマッチと、ディレクトリ名の完全一致
# *.jsonlは本アプリが会話ログ・日記をこの拡張子で書く（gui.py/soul.py等）ため、
# 開発機のfieria_home実データが誤って同梱されていないかの検出に使う（防御の多重化。
# fieria_home/soulsディレクトリ名の完全一致チェックと二重）。
SENSITIVE_FILE_GLOBS = [".env", "config.json", "*oauth*", "*.jsonl"]
SENSITIVE_DIR_NAMES = {".git", "fieria_home", "souls"}

# サードパーティ依存が同梱する、ユーザーデータではない静的パッケージデータへの
# 例外（誤検知除外）。ddgs→fake_useragentが実際のブラウザシェア統計jsonlを同梱する
# （公開統計データ・個人情報なし。2026-08-02に内容を実際に確認済み）。
# パスはPyInstaller onedir配置基準（FINALルートからの相対）で厳密一致のみ許可する
# （glob等の緩いマッチはしない。新規に増やす際は必ず内容を確認してから追加すること）。
SAFE_THIRDPARTY_RELPATHS = {
    os.path.join("_internal", "fake_useragent", "data", "browsers.jsonl"),
}


def run(cmd, cwd=None):
    print(f"[RUN] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd)
    if proc.returncode != 0:
        print(f"[ERROR] 失敗した (exit {proc.returncode})")
        sys.exit(1)


def scan_confidential(root):
    """完成品フォルダを再帰的に走査し、混入していてはいけないパターンを探す。
    ヒットしたパス一覧を返す（空リスト=クリーン）。"""
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            if d in SENSITIVE_DIR_NAMES:
                hits.append(os.path.join(dirpath, d) + os.sep)
        for f in filenames:
            fl = f.lower()
            if any(fnmatch.fnmatch(fl, pat.lower()) for pat in SENSITIVE_FILE_GLOBS):
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, root)
                if rel in SAFE_THIRDPARTY_RELPATHS:
                    continue
                hits.append(full)
    return hits


def main():
    t0 = time.time()

    # 1. クリーン素材
    if os.path.exists(WORK):
        # 【重要】既存exe内にユーザーデータ（魂＝会話・記憶）が溜まっている場合、
        # 作業場ごと消すと復元不能で失われる。必ず退避してから削除する。
        # 由来: 2026-07-22、実際にリビルドで当日分のexe側の記憶を消失させた事故。
        old_home = os.path.join(WORK, "dist", "FieriA", "_internal", "fieria_home")
        if os.path.isdir(old_home):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            rescue = os.path.join(os.path.dirname(HERE), "FieriA-rescued-homes", stamp)
            shutil.copytree(old_home, rescue)
            print(f"[RESCUE] 旧exe内のfieria_homeを退避した: {rescue}")
        print(f"[CLEAN] 既存の作業場を削除: {WORK}")
        # read-onlyファイルが混ざっていても消せるようにする
        shutil.rmtree(WORK, onerror=lambda fn, path, exc: (
            os.chmod(path, stat.S_IWRITE), fn(path)))
    run([sys.executable, os.path.join(HERE, "build_dist.py"), WORK])
    shutil.copy2(os.path.join(HERE, "fieria.spec"), WORK)

    # 2. PyInstaller
    run([sys.executable, "-m", "PyInstaller", "fieria.spec", "--noconfirm"], cwd=WORK)
    if not os.path.isfile(os.path.join(FINAL, "FieriA.exe")):
        print("[ERROR] FieriA.exe が生成されていない")
        sys.exit(1)

    # 3. README同梱
    shutil.copy2(os.path.join(HERE, "README.md"), os.path.join(FINAL, "README.md"))

    # 4. スモークテスト: 起動→10秒生存→終了
    # frozen実行時のHOMEは%LOCALAPPDATA%\FieriA\fieria_homeになった（config.py参照）ため、
    # 何もしなければこのビルドマシンの実データ置き場を起動のたびに汚してしまう。
    # FIERIA_HOME環境変数（config.pyの最優先項目）で一時ディレクトリに差し替え、
    # 実マシンの%LOCALAPPDATA%\FieriAには一切触れさせない。
    print("[TEST] exeを起動して生存確認…")
    exe = os.path.join(FINAL, "FieriA.exe")
    smoke_home = tempfile.mkdtemp(prefix="fieria-smoke-")
    smoke_env = dict(os.environ, FIERIA_HOME=smoke_home)
    proc = subprocess.Popen([exe], env=smoke_env)
    time.sleep(10)
    alive = proc.poll() is None
    if alive:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not alive:
        print("[ERROR] exeが起動後すぐ落ちた。デバッグはspecの console=True で")
        sys.exit(1)
    print("[TEST] 10秒生存OK")

    shutil.rmtree(smoke_home, onerror=lambda fn, path, exc: (
        os.chmod(path, stat.S_IWRITE), fn(path)))
    print("[TEST] スモークテスト用の一時fieria_home（実マシンの%LOCALAPPDATA%は不使用）を削除した")

    # 旧世代exe（このパッチ以前のビルド）が_internal直下に生成する可能性に備えた掃除。
    # 現行のfrozen実行はもう_internal配下にfieria_homeを作らないので、通常はヒットしない。
    generated_home = os.path.join(FINAL, "_internal", "fieria_home")
    if os.path.isdir(generated_home):
        shutil.rmtree(generated_home, onerror=lambda fn, path, exc: (
            os.chmod(path, stat.S_IWRITE), fn(path)))
        print("[TEST] _internal配下に生成されたfieria_homeを削除（出荷状態に戻した）")

    # 5. 機密走査（絶対条件）
    print("[SCAN] 機密ファイル走査…")
    hits = scan_confidential(FINAL)
    if hits:
        print("[ERROR] 機密走査で検出:")
        for h in hits:
            print(f"  - {h}")
        print("[ERROR] ビルド失敗扱い。配布してはいけない")
        sys.exit(1)
    print("[SCAN] クリーン（該当パターンなし）")

    mins = (time.time() - t0) / 60
    print(f"\n[DONE] 完成じょ ({mins:.1f}分): {FINAL}")
    print("       このフォルダをZIPにしてテスターへ。")


if __name__ == "__main__":
    main()
