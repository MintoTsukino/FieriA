"""作業文書変更・Web検索の確認設定と、設定によらず守る境界の検査。"""
import copy
import difflib
import os
import threading
import uuid

import workspace

MAX_DOCUMENT_BYTES = 500_000
MAX_PREVIEW_CHARS = 100_000


def _snapshot(path):
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None, b""
    if not os.path.isfile(path) or info.st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("確認できる文書の上限（500KB）を超えているか、通常ファイルではありません")
    with open(path, "rb") as f:
        body = f.read(MAX_DOCUMENT_BYTES + 1)
    if len(body) > MAX_DOCUMENT_BYTES:
        raise ValueError("確認できる文書の上限（500KB）を超えています")
    return (info.st_dev, info.st_ino, info.st_mtime_ns, body), body


def is_automatic(cfg, kind):
    policy = (cfg or {}).get("tool_approvals", {})
    return isinstance(policy, dict) and policy.get(kind) == "auto"


def _check_cancelled(is_cancelled):
    if is_cancelled is not None and is_cancelled():
        raise PermissionError("停止が要求されたため実行しませんでした")


def _require_approval(approve, request, cfg=None):
    if is_automatic(cfg, request["kind"]):
        return
    # コールバックなし・例外・True以外はすべて実行不可。
    if approve is None or approve(copy.deepcopy(request)) is not True:
        raise PermissionError("ユーザーの承認がないため実行しませんでした")


def write_document(workspace_dir, rel_path, content, mode, approve, cfg=None, is_cancelled=None):
    if not isinstance(content, str):
        raise ValueError("contentは文字列で指定してください")
    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("変更が大きすぎます。小さい単位に分割してください")
    full = workspace._safe_path(workspace_dir, rel_path)
    before_state, raw = _snapshot(full)
    before = raw.decode("utf-8")
    after = before + content if mode == "append" else content
    if len(before.splitlines()) + len(after.splitlines()) > 10_000:
        raise ValueError("変更の行数が多すぎます。小さい単位に分割してください")
    diff = difflib.unified_diff(
        before.replace("\r\n", "\n").splitlines(keepends=True),
        after.replace("\r\n", "\n").splitlines(keepends=True),
        fromfile="変更前", tofile="変更後", n=3)
    preview = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                      for line in diff)
    if not preview:
        return "変更内容が同じため書き込みませんでした"
    if len(preview) > MAX_PREVIEW_CHARS:
        raise ValueError("変更内容をすべて表示できません。小さい単位に分割してください")
    _check_cancelled(is_cancelled)
    _require_approval(approve, {
        "kind": "workspace_write", "title": "作業文書の変更を確認",
        "description": f"対象: {full}\n" + ("末尾に追記します" if mode == "append" else "この内容に置き換えます"),
        "preview": preview,
    }, cfg)
    _check_cancelled(is_cancelled)
    # 承認待ちの間の書き換え・差し替え・リンク化を再検査する。
    current = workspace._safe_path(workspace_dir, rel_path)
    if current != full or _snapshot(current)[0] != before_state:
        raise ValueError("確認中に文書が変更されたため中止しました。改めて変更内容を確認してください")
    if mode == "append":
        workspace.append_doc(workspace_dir, rel_path, content)
    else:
        workspace.write_doc(workspace_dir, rel_path, content)
    return f"{rel_path} に" + ("追記" if mode == "append" else "書き込み")


def approve_search(cfg, query, approve, destination="外部の検索サービス", note=None, is_cancelled=None):
    if not ((cfg or {}).get("llm") or {}).get("web_search", False):
        raise PermissionError("Web検索は設定でOFFのため実行できません")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("検索語が空です")
    query = query.strip()
    if len(query) > 2000:
        raise ValueError("検索語が長すぎます")
    _check_cancelled(is_cancelled)
    _require_approval(approve, {
        "kind": "web_search", "title": "Web検索の送信内容を確認",
        "description": f"検索先: {destination}\n" +
                       (note or "表示された検索語を外部の検索サービスへ送信します。") +
                       "\n個人情報や文書の内容が含まれていないか確認してください。",
        "preview": query,
    }, cfg)
    _check_cancelled(is_cancelled)
    if not ((cfg or {}).get("llm") or {}).get("web_search", False):
        raise PermissionError("確認中にWeb検索がOFFになったため中止しました")
    return query


class ApprovalBroker:
    """ウィンドウとワーカースレッド間の単発承認。応答なし・終了時は拒否。"""
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = {}

    def request(self, payload, show, hide, timeout=180):
        request_id = uuid.uuid4().hex
        pending = {"event": threading.Event(), "approved": False, "resolved": False}
        with self._lock:
            if self._pending:
                return False
            self._pending[request_id] = pending
        try:
            show({**payload, "id": request_id})
            if not pending["event"].wait(timeout):
                return False
            return pending["approved"]
        except Exception:
            return False
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
            try:
                hide(request_id)
            except Exception:
                pass

    def resolve(self, request_id, approved):
        with self._lock:
            pending = self._pending.get(request_id)
            if not pending or pending["resolved"]:
                return False
            pending["resolved"] = True
            pending["approved"] = approved is True
            pending["event"].set()
            return True

    def cancel_all(self):
        with self._lock:
            for pending in self._pending.values():
                pending["approved"] = False
                pending["resolved"] = True
                pending["event"].set()
