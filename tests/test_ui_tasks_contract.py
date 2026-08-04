"""tests/test_ui_tasks_contract.py — ui/index.html のタスクビューがgui.Bridgeの
実際のAPIと食い違っていないかを検査する契約テスト。

ui/index.htmlはpywebviewのGUIウィンドウ内でしか実行されず、このリポジトリの
テストはブラウザを起動できない環境で動くため、DOM/JSの実際の挙動そのものは
検証できない。その代わりにテキストベースで以下を確認する:
  1. タスクビューが要求するid群がHTML内に存在するか（ブリーフ通りの骨格になっているか）
  2. ナビにタスクタブへの切替ボタンがあるか
  3. index.htmlが呼んでいるpywebview.api.task_*/get_tasksの名前が、
     実際にgui.Bridgeのメソッドとして存在するか（タイポ・削除漏れの検出）
"""
import re


def _read_index_html():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


REQUIRED_TASK_IDS = [
    "tasks-view",
    "tasks-now-list",
    "tasks-future-list",
    "tasks-done-list",
    "task-add-text",
    "task-add-due",
    "task-add-cat",
    "task-add-section",
    "task-add-btn",
    "tasks-status",
]


def test_required_task_view_ids_exist():
    html = _read_index_html()
    for id_ in REQUIRED_TASK_IDS:
        assert 'id="' + id_ + '"' in html, "id=%s がui/index.htmlに見つからない" % id_


def test_nav_has_tasks_button():
    html = _read_index_html()
    assert re.search(r'<button[^>]*data-view="tasks"', html), \
        'ナビにdata-view="tasks"のボタンが無い'


def test_pywebview_task_api_calls_match_bridge_methods():
    html = _read_index_html()
    called = set(re.findall(r"pywebview\.api\.(task_\w+|get_tasks)\(", html))
    # 呼び出しが1件も見つからない場合は正規表現自体がズレている可能性が高く、
    # 「何も検査していないのに緑」という事故を防ぐため空集合を明示的に弾く。
    assert called, "index.html内でtask_*/get_tasksの呼び出しが見つからない"

    import gui
    bridge = gui.Bridge()
    missing = [name for name in sorted(called) if not hasattr(bridge, name)]
    assert not missing, "gui.Bridgeに存在しないAPIをindex.htmlが呼んでいる: %s" % missing
