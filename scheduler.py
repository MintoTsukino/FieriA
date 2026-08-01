"""scheduler.py — アプリ内スケジューラ（日次日記・週次あらすじ・月次あらすじのキャッチアップ）。

日記の書き損ね（強制終了・シャットダウン・アプリを開きっぱなしで日付を跨いだ等）を
塞ぐための軽いタイマー。アプリ起動中だけ動き、「ログはあるのに日記(chronicle)が無い
過去日」「日次日記はあるのに週次あらすじが無い完了済み週」「週次あらすじ（無ければ
日次日記）はあるのに月次あらすじが無い完了済み月」を検出して自動で書く。
書けなくても（LLM未接続・失敗）例外は外に出さない（ベストエフォート。wrapup.pyと同じ思想）。

定期処理はJOBSのテーブルで管理する。configのscheduled_jobs[job["id"]]がFalseの
ジョブはtickでスキップする（GUIからON/OFFできる：gui.py get_scheduled_jobs/
set_scheduled_job参照）。"""
import datetime
import os
import re
import threading

import soul as soul_mod
import wrapup as wrapup_mod


def _week_str(d):
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def find_unwritten_days(soul_id):
    """logs/に会話ログ(jsonl)があるのにchronicle/に同日のmdが無い過去日を、
    古い順のリストで返す。今日は対象外（今日の分はwrapup.write_daily_chronicleが
    セッション終了時に書く担当なので、ここで先取りして書いてしまわない）。"""
    logs_dir = os.path.join(soul_mod.soul_dir(soul_id), "logs")
    if not os.path.isdir(logs_dir):
        return []
    chronicle_dir = os.path.join(soul_mod.soul_dir(soul_id), "chronicle")
    today = datetime.date.today().isoformat()
    log_dates = sorted(
        fname[: -len(".jsonl")] for fname in os.listdir(logs_dir) if fname.endswith(".jsonl")
    )
    unwritten = []
    for date_str in log_dates:
        if date_str == today:
            continue
        if os.path.isfile(os.path.join(chronicle_dir, f"{date_str}.md")):
            continue
        unwritten.append(date_str)
    return unwritten


def catch_up(cfg, llm, soul_id, limit=7):
    """未書きの日を古い順に最大limit日ぶん埋める。ベストエフォート：1日の生成が
    失敗（例外・空応答）しても握りつぶして次の日へ進む。書けた日付のリストを返す。"""
    written = []
    for date_str in find_unwritten_days(soul_id)[:limit]:
        try:
            ok = wrapup_mod.write_chronicle_for(cfg, llm, soul_id, date_str)
        except Exception:
            ok = False
        if ok:
            written.append(date_str)
    return written


def find_unwritten_weeks(soul_id):
    """chronicle/に日次日記(YYYY-MM-DD.md)が1件以上ある週のうち、今週を除く完了済みで
    chronicle/weekly/<week>.mdがまだ無い週を、古い順のリストで返す（"2026-W29"形式）。
    今週を除外するのは find_unwritten_days が今日を除外するのと同じ理由：
    今週分はまだ書き終わっていない途中の週だから。"""
    chronicle_dir = os.path.join(soul_mod.soul_dir(soul_id), "chronicle")
    if not os.path.isdir(chronicle_dir):
        return []
    weekly_dir = os.path.join(chronicle_dir, "weekly")
    current_week = _week_str(datetime.date.today())
    weeks_with_daily = set()
    for fname in os.listdir(chronicle_dir):
        if not fname.endswith(".md"):
            continue
        try:
            day = datetime.date.fromisoformat(fname[: -len(".md")])
        except ValueError:
            continue
        weeks_with_daily.add(_week_str(day))
    unwritten = []
    for week in sorted(weeks_with_daily):
        if week == current_week:
            continue
        if os.path.isfile(os.path.join(weekly_dir, f"{week}.md")):
            continue
        unwritten.append(week)
    return unwritten


def catch_up_weekly(cfg, llm, soul_id, limit=4):
    """未書きの週次あらすじを古い順に最大limit週ぶん埋める。catch_upと同じく
    ベストエフォート：1週の生成が失敗（例外・空応答）しても握りつぶして次の週へ進む。"""
    written = []
    for week_str in find_unwritten_weeks(soul_id)[:limit]:
        try:
            ok = wrapup_mod.write_weekly_digest(cfg, llm, soul_id, week_str)
        except Exception:
            ok = False
        if ok:
            written.append(week_str)
    return written


def find_unwritten_months(soul_id):
    """chronicle/に日次日記(YYYY-MM-DD.md)が1件以上ある月のうち、今月を除く完了済みで
    chronicle/monthly/<month>.mdがまだ無い月を、古い順のリストで返す("2026-07"形式)。
    今月を除外するのは find_unwritten_weeks が今週を除外するのと同じ理由：
    今月分はまだ書き終わっていない途中の月だから。「材料あり」の判定を日次日記の存在で
    行うのは write_monthly_digest 自身が週次優先・無ければ日次直読みの2段構えなので、
    どちらの経路でも書ける月をここで漏らさず拾うため。"""
    chronicle_dir = os.path.join(soul_mod.soul_dir(soul_id), "chronicle")
    if not os.path.isdir(chronicle_dir):
        return []
    monthly_dir = os.path.join(chronicle_dir, "monthly")
    current_month = datetime.date.today().isoformat()[:7]
    months_with_daily = set()
    for fname in os.listdir(chronicle_dir):
        if not fname.endswith(".md"):
            continue
        try:
            day = datetime.date.fromisoformat(fname[: -len(".md")])
        except ValueError:
            continue
        months_with_daily.add(day.isoformat()[:7])
    unwritten = []
    for month in sorted(months_with_daily):
        if month == current_month:
            continue
        if os.path.isfile(os.path.join(monthly_dir, f"{month}.md")):
            continue
        unwritten.append(month)
    return unwritten


def catch_up_monthly(cfg, llm, soul_id, limit=3):
    """未書きの月次あらすじを古い順に最大limit月ぶん埋める。catch_up/catch_up_weeklyと
    同じくベストエフォート：1ヶ月の生成が失敗（例外・空応答）しても握りつぶして次の月へ進む。"""
    written = []
    for month_str in find_unwritten_months(soul_id)[:limit]:
        try:
            ok = wrapup_mod.write_monthly_digest(cfg, llm, soul_id, month_str)
        except Exception:
            ok = False
        if ok:
            written.append(month_str)
    return written


_REFLECTION_WEEK_RE = re.compile(r"week=(\S+)")


def _last_reflected_week(soul_id):
    """identity_history/reflections.log から、これまでに内省が実施された（＝ログに
    記録が付いた）最新の週を返す（無ければNone）。week_str（"YYYY-Www"形式・週番号は
    常に2桁ゼロ埋め）はゼロ埋めが揃っているので文字列としてのmax()が時系列の最新と一致する
    （_week_strが同じ書式で生成している前提。write_weekly_digest周りと同じ前提）。"""
    log = soul_mod.read_file(soul_id, "identity_history/reflections.log")
    weeks = [m.group(1) for m in _REFLECTION_WEEK_RE.finditer(log)]
    return max(weeks) if weeks else None


def find_unreflected_weeks(soul_id):
    """日次日記が1件以上ある週のうち、今週を除く完了済みで、まだreflections.logに
    記録が無い週を、古い順のリストで返す。find_unwritten_weeksと同じ「材料の有無」判定
    （chronicle/の日次日記ファイル）を使い、「書いたか」の判定だけreflections.logの
    最終実施週に差し替えたもの。"""
    chronicle_dir = os.path.join(soul_mod.soul_dir(soul_id), "chronicle")
    if not os.path.isdir(chronicle_dir):
        return []
    current_week = _week_str(datetime.date.today())
    last_done = _last_reflected_week(soul_id)
    weeks_with_daily = set()
    for fname in os.listdir(chronicle_dir):
        if not fname.endswith(".md"):
            continue
        try:
            day = datetime.date.fromisoformat(fname[: -len(".md")])
        except ValueError:
            continue
        weeks_with_daily.add(_week_str(day))
    unreflected = []
    for week in sorted(weeks_with_daily):
        if week == current_week:
            continue
        if last_done is not None and week <= last_done:
            continue
        unreflected.append(week)
    return unreflected


def catch_up_self_reflection(cfg, llm, soul_id, limit=4):
    """未実施の週次内省を古い順に最大limit週ぶん行う。他のcatch_up_*と同じく
    ベストエフォート：1週の実施が失敗（例外）しても握りつぶして次の週へ進む。
    実施を試みた週（結果の真偽によらず）のリストを返す（wrapup.run_self_reflection側が
    ログを書くかどうかを判断するので、ここでは「何週を対象に走らせたか」だけを返す）。"""
    attempted = []
    for week_str in find_unreflected_weeks(soul_id)[:limit]:
        try:
            wrapup_mod.run_self_reflection(cfg, llm, soul_id, week_str)
        except Exception:
            pass
        attempted.append(week_str)
    return attempted


def _last_completed_month_str(today):
    """todayが属する月の1つ前の月を"YYYY-MM"形式で返す（find_unwritten_months等が
    今月を除外するのと同じ理由：wiki庭仕事も「進行中の月」ではなく完了した月を
    対象にする）。"""
    first_of_this_month = today.replace(day=1)
    last_month_day = first_of_this_month - datetime.timedelta(days=1)
    return last_month_day.isoformat()[:7]


def find_ungardened_months(soul_id):
    """wiki庭仕事（wrapup.run_wiki_gardening）の対象月を返す。他のfind_unwritten_*と
    違い、「素材（日次日記等）が存在する月」を洗い出すのではない：wikiページは
    月に紐付く時系列素材ではなく「今そこにあるもの」を整理する対象なので、
    バックログを遡って何ヶ月分も再実行する意味が薄い（遡っても同じページを
    同じ内容で整理し直すだけ）。そのため直近の完了済み月（先月）だけを候補にし、
    gardening/<month>.doneが既にあれば候補ゼロ、無ければ[先月]の1件だけを返す
    （0件か1件のリスト）。"""
    today = datetime.date.today()
    month = _last_completed_month_str(today)
    marker = os.path.join(soul_mod.soul_dir(soul_id), "gardening", f"{month}.done")
    if os.path.isfile(marker):
        return []
    return [month]


def catch_up_wiki_gardening(cfg, llm, soul_id):
    """未実施のwiki庭仕事（find_ungardened_monthsが返す最大1件の月）を実行する。
    他のcatch_up_*と違い「書けた月」ではなく「実施を試みた月」のリストを返す
    （catch_up_self_reflectionと同じ考え方：wrapup.run_wiki_gardening自身がマーカーを
    書くかどうかを判断するので、ここでは「何月を対象に走らせたか」だけを返す）。
    1件の失敗（例外）も握りつぶす（ベストエフォート）。"""
    attempted = []
    for month_str in find_ungardened_months(soul_id):
        try:
            wrapup_mod.run_wiki_gardening(cfg, llm, soul_id, month_str)
        except Exception:
            pass
        attempted.append(month_str)
    return attempted


def run_index_maintenance(cfg, llm, soul_id):
    """他のジョブ（find_unwritten_*→catch_up_*）とは発火条件の形が違う：「未書きの日」
    ではなく「今のMEMORY.mdが閾値超過しているか」を直接見る。tick()のJOBSループは
    1日1回しか回らない（_last_tick_dateの日付ゲート）ので、これがそのまま
    「同日に何度も書き直さない」の保証にもなる。wrapup.rewrite_memory_index自身も
    同じ閾値判定をするが、ここでも判定してBoolをlast_run_infoに残す（二重チェック）。"""
    limit = cfg.get("memory_index_limit_chars", 4000)
    if not limit:
        return False
    current = soul_mod.read_file(soul_id, "MEMORY.md")
    if len(current) <= limit:
        return False
    try:
        return wrapup_mod.rewrite_memory_index(cfg, llm, soul_id)
    except Exception:
        return False


# 定期処理のジョブテーブル。GUIのON/OFF・可視化はこの並び順・内容をそのまま使う
# （gui.py get_scheduled_jobs参照）。tickはこの順（daily→weekly→monthly）で実行する。
# 週次の素材は日次日記、月次の素材は週次（無ければ日次）なので、同じtick内で
# 上流のジョブを先に走らせておく必要がある。
JOBS = [
    {
        "id": "daily_chronicle",
        "name": "日次日記",
        "description": "日付が変わったら前日までの未書き日記を書く（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: catch_up(cfg, llm, soul_id),
    },
    {
        "id": "weekly_digest",
        "name": "週次あらすじ",
        "description": "完了した週の日次日記から週次あらすじをまとめる（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: catch_up_weekly(cfg, llm, soul_id),
    },
    {
        "id": "monthly_digest",
        "name": "月次あらすじ",
        "description": "完了した月の週次あらすじ（無ければ日次日記）から月次あらすじをまとめる（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: catch_up_monthly(cfg, llm, soul_id),
    },
    {
        "id": "index_maintenance",
        "name": "索引の整理",
        "description": "記憶の索引が長くなりすぎたら整理して書き直す（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: run_index_maintenance(cfg, llm, soul_id),
    },
    {
        "id": "self_reflection",
        "name": "週次の内省",
        "description": "週に一度、自分の核と話し方を読み返して育てる（本人がidentityを書き換えます）",
        "run": lambda cfg, llm, soul_id: catch_up_self_reflection(cfg, llm, soul_id),
    },
    {
        "id": "wiki_gardening",
        "name": "wikiの庭仕事",
        "description": "月に一度、長くなったwikiページをLLMが整理する（本人がwikiを書き換えます）",
        "run": lambda cfg, llm, soul_id: catch_up_wiki_gardening(cfg, llm, soul_id),
    },
]


class Scheduler:
    """アプリ起動中だけ動くdaemonスレッド。get_context()でBridgeの現在状態
    （cfg, llm, active_soul）を毎tick取得するので、SOUL切替やLLM再生成に
    自然に追従する（Scheduler自身は状態のスナップショットを持たない）。"""

    def __init__(self):
        self._get_context = None
        self._stop_event = threading.Event()
        self._thread = None
        self._last_tick_date = None
        self.last_run_info = {"last_run": None, "written": []}
        # 終了確認ダイアログ(gui.py on_closing)用: JOBSループ実行中フラグ。
        # tick()自体はdaemonスレッド1本からしか呼ばれないため書き込み側の競合は
        # 無いが、is_running_job()はメインスレッド（closingハンドラ）から読まれる
        # ため、読み書きともロックで保護する。
        self._running_lock = threading.Lock()
        self._running_job = False

    def start(self, get_context):
        """get_context: () -> (cfg, llm, soul_id or None)"""
        self._get_context = get_context
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        # 60秒sleep→tick、を停止されるまで繰り返す。stop_event.waitが60秒待ってから
        # Falseで抜けた時だけtickする（setされたら即ループを抜ける）。
        while not self._stop_event.wait(60):
            self.tick()

    def tick(self, today=None):
        """前回tickと日付が変わった時（または初回）だけJOBSを呼ぶ。
        毎分ファイルスキャンしないための間引き。todayを渡せる形にして、
        実際の日付に依存せずテストから発火判定を検証できるようにしてある。
        configのscheduled_jobs[job["id"]]がFalseのジョブはスキップする
        （キーが無ければ既定でON。DEFAULT_CONFIGは全ジョブTrueで補完されるので
        実運用では欠けない想定だが、テストの素のcfgでも安全に動くようにしてある）。"""
        today = today or datetime.date.today().isoformat()
        if self._last_tick_date == today:
            return
        self._last_tick_date = today
        if self._get_context is None:
            return
        cfg, llm, soul_id = self._get_context()
        if not soul_id or not llm:
            return
        scheduled = cfg.get("scheduled_jobs", {})
        results = {}
        with self._running_lock:
            self._running_job = True
        try:
            for job in JOBS:
                if not scheduled.get(job["id"], True):
                    continue
                results[job["id"]] = job["run"](cfg, llm, soul_id)
        finally:
            # ジョブ内で例外が漏れた場合（現状のJOBS.runはいずれもベストエフォートで
            # 内部の例外を握るが、想定外の例外が漏れる可能性への保険）でも、
            # is_running_job()が「実行中のまま」に張り付かないようにする。
            with self._running_lock:
                self._running_job = False
        self.last_run_info = {
            "last_run": datetime.datetime.now().isoformat(timespec="seconds"),
            "written": results.get("daily_chronicle", []),
            "jobs": results,
        }

    def is_running_job(self):
        with self._running_lock:
            return self._running_job

    def stop(self):
        self._stop_event.set()
