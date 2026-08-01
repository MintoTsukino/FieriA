"""roles.py — ロール（その場のモード）の管理。roles.jsonはグローバル（SOUL非依存）。
ロールは「今やること」の記述であって人格ではない。人格はSOUL側（設計書§8-2）。"""
import json
import os

from config import HOME

ROLES_PATH = os.path.join(HOME, "roles.json")

DEFAULT_ROLES = [
    {"name": "会話",
     "prompt": "いまは雑談の時間。特別な仕事はない。肩の力を抜いて話す。"},
    {"name": "リサーチ",
     "prompt": "いまは調べ物・情報整理の相棒として動く。事実と推測を区別し、"
               "わからないことはわからないと言う。"},
    {"name": "編集者",
     "prompt": "いまは文章への率直なフィードバック役。良い点と問題点を両方、"
               "具体的な箇所を挙げて伝える。"},
    {"name": "秘書",
     "prompt": "いまはユーザーの秘書・マネージャーとして動く。タスクや予定を聞き取り、"
               "気にかけリスト（update_tasks）に整理する。期限があるものはリマインダも設定する。"
               "前に聞いたタスクの進み具合を自分から気にかける。体調の様子にもさりげなく気を配る。"},
]


def _load():
    if not os.path.isfile(ROLES_PATH):
        _save(DEFAULT_ROLES)
        return [dict(r) for r in DEFAULT_ROLES]
    with open(ROLES_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)
    return _backfill_missing_defaults(items)


def _backfill_missing_defaults(items):
    """既存roles.jsonに、後から追加されたDEFAULT_ROLES項目（例: 秘書）が
    欠けていたら補い、ファイルへも書き戻す。名前が既に存在するロール（ユーザーが
    編集済み・同名で自作したものを含む）は一切上書きしない——追加のみ行う
    （config.pyのフィールド補完と同じ「既存優先・欠けを足す」思想）。"""
    existing_names = {r["name"] for r in items}
    missing = [dict(r) for r in DEFAULT_ROLES if r["name"] not in existing_names]
    if not missing:
        return items
    items = items + missing
    _save(items)
    return items


def _save(items):
    os.makedirs(HOME, exist_ok=True)
    with open(ROLES_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def list_roles():
    return _load()


def get_role(name):
    for r in _load():
        if r["name"] == name:
            return r
    return None


def save_role(name, prompt):
    items = _load()
    for r in items:
        if r["name"] == name:
            r["prompt"] = prompt
            _save(items)
            return
    items.append({"name": name, "prompt": prompt})
    _save(items)


def delete_role(name):
    _save([r for r in _load() if r["name"] != name])
