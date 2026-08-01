"""
env.py — .env ファイルの読み書き

APIキーは config.json ではなく .env（KEY=VALUE形式、git管理外）に置く。
GUIの設定画面から set_key() で書き込む。読み込みは load_env() で dict として返す
（os.environ は汚さない）。

FieriA改変: 置き場を config.HOME 直下へ移設（exeの差し替え・フォルダ削除で
APIキーが消えないように。2026-07-22）。旧位置（コード隣）にあり新位置に無ければ
config.migrate_secret_file() が初回起動時にコピー移行する。
"""

import os

import config

HERE = os.path.dirname(os.path.abspath(__file__))
# 注意: ENV_PATHはimport時に1回だけ束縛される。以後config.HOMEが変わっても
# （importlib.reload(config)等）この値は追随しない。関数のpath=デフォルト引数も
# この時点の値で固定される。HOMEを動的に変えるテストはenv自体もreloadすること。
if os.environ.get("FIERIA_TESTING"):
    # テスト実行中は実の.envに一切触れない（存在確認すらしない）。conftest.pyが
    # FIERIA_TESTINGを設定するため、テストからの実.env到達を構造的に遮断する
    # （conftest.pyの「.envはFIERIA_HOME隔離の対象外」コメントと同じ思想の第二弾）。
    ENV_PATH = os.path.join(config.HOME, ".env")
else:
    ENV_PATH = config.migrate_secret_file(HERE, ".env")


def load_env(path=ENV_PATH):
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            env[key.strip()] = value
    return env


def set_key(env_key, value, path=ENV_PATH):
    """既存キーは行を置換、なければ追記。他の行は保持する。"""
    lines = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() == env_key:
            lines[i] = f"{env_key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{env_key}={value}")
    # 新規インストール直後などHOME未作成のままここへ来ても書けるようにする
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def delete_key(env_key, path=ENV_PATH):
    """既存キーの行を削除する。無ければ何もしない。他の行は保持する。"""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            kept.append(line)
            continue
        if stripped.partition("=")[0].strip() == env_key:
            continue
        kept.append(line)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + ("\n" if kept else ""))
    os.replace(tmp, path)


def resolve_api_key(provider_entry, env):
    """プロバイダ設定からAPIキーを解決。直書き(api_key) > .env(env_key) の順。"""
    direct = (provider_entry.get("api_key") or "").strip()
    if direct:
        return direct
    env_key = (provider_entry.get("env_key") or "").strip()
    if env_key:
        return env.get(env_key, "")
    return ""
