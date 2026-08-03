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


def find_stale_days(soul_id):
    """日記は既にあるが、その後にログが増えている（ログのmtime > 日記のmtime）
    過去日を古い順のリストで返す。SOUL切替時のwrapupで日記が書かれた後、
    夜まで会話が続いたまま日をまたいだケースを検出する。日記が無い日は
    find_unwritten_days（新規）の担当なのでここでは対象外。今日も対象外
    （今日の分はwrapup.write_daily_chronicleの担当——find_unwritten_daysと対称）。"""
    logs_dir = os.path.join(soul_mod.soul_dir(soul_id), "logs")
    if not os.path.isdir(logs_dir):
        return []
    chronicle_dir = os.path.join(soul_mod.soul_dir(soul_id), "chronicle")
    today = datetime.date.today().isoformat()
    log_dates = sorted(
        fname[: -len(".jsonl")] for fname in os.listdir(logs_dir) if fname.endswith(".jsonl")
    )
    stale = []
    for date_str in log_dates:
        if date_str == today:
            continue
        diary_path = os.path.join(chronicle_dir, f"{date_str}.md")
        if not os.path.isfile(diary_path):
            continue
        try:
            log_mtime = os.path.getmtime(os.path.join(logs_dir, f"{date_str}.jsonl"))
            diary_mtime = os.path.getmtime(diary_path)
        except OSError:
            continue
        if log_mtime > diary_mtime:
            stale.append(date_str)
    return stale


def catch_up(cfg, llm, soul_id, limit=7):
    """未書きの日を古い順に最大limit日ぶん新規で埋め、さらに「日記の後にも
    会話が続いた日」（find_stale_days）へ続きを追記する。ベストエフォート：
    1日の生成が失敗（例外・空応答）しても握りつぶして次の日へ進む。
    書けた（新規・追記とも）日付のリストを返す。追記に成功すると日記の
    mtimeがログより新しくなるため、同じ日が翌tickで再追記されることはない。"""
    written = []
    for date_str in find_unwritten_days(soul_id)[:limit]:
        try:
            ok = wrapup_mod.write_chronicle_for(cfg, llm, soul_id, date_str)
        except Exception:
            ok = False
        if ok:
            written.append(date_str)
    for date_str in find_stale_days(soul_id)[:limit]:
        try:
            ok = wrapup_mod.append_chronicle_for(cfg, llm, soul_id, date_str)
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
# 各ジョブの既定の実行時刻（0-23時）。configの scheduled_job_hours で上書きできる。
# 「この時刻より前には走らない」下限であって、正時ぴったりの引き金ではない
# （その時刻にアプリが消えていても、次に起動したtickでその日ぶんが走る＝自己修復）。
# 全部を0時に集中させると、日をまたいだ瞬間にLLM呼び出しが束で走って会話を
# 待たせるため、日記だけ0時に置き、他は深夜〜早朝へ1時間ずつ散らしてある。
JOBS = [
    {
        "id": "daily_chronicle",
        "hour": 0,
        "name": "日次日記",
        "description": "日付が変わったら前日までの未書き日記を書き、日記の後にも会話が続いた日には続きを追記する（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: catch_up(cfg, llm, soul_id),
    },
    {
        "id": "weekly_digest",
        "hour": 4,
        "name": "週次あらすじ",
        "description": "完了した週の日次日記から週次あらすじをまとめる（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: catch_up_weekly(cfg, llm, soul_id),
    },
    {
        "id": "monthly_digest",
        "hour": 5,
        "name": "月次あらすじ",
        "description": "完了した月の週次あらすじ（無ければ日次日記）から月次あらすじをまとめる（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: catch_up_monthly(cfg, llm, soul_id),
    },
    {
        "id": "index_maintenance",
        "hour": 3,
        "name": "索引の整理",
        "description": "記憶の索引が長くなりすぎたら整理して書き直す（アプリ起動中のみ）",
        "run": lambda cfg, llm, soul_id: run_index_maintenance(cfg, llm, soul_id),
    },
    {
        "id": "self_reflection",
        "hour": 6,
        "name": "週次の内省",
        "description": "週に一度、自分の核と話し方を読み返して育てる（本人がidentityを書き換えます）",
        "run": lambda cfg, llm, soul_id: catch_up_self_reflection(cfg, llm, soul_id),
    },
    {
        "id": "wiki_gardening",
        "hour": 7,
        "name": "wikiの庭仕事",
        "description": "月に一度、長くなったwikiページをLLMが整理する（本人がwikiを書き換えます）",
        "run": lambda cfg, llm, soul_id: catch_up_wiki_gardening(cfg, llm, soul_id),
    },
]


def _job_hour(job, hours_cfg):
    """ジョブの実効実行時刻。configのscheduled_job_hoursに0-23の整数があればそれ、
    無ければ・壊れていればJOBSの既定値。"""
    raw = (hours_cfg or {}).get(job["id"])
    try:
        hour = int(raw)
    except (TypeError, ValueError):
        return job["hour"]
    return hour if 0 <= hour <= 23 else job["hour"]


class Scheduler:
    """アプリ起動中だけ動くdaemonスレッド。get_context()でBridgeの現在状態
    （cfg, llm, active_soul）を毎tick取得するので、SOUL切替やLLM再生成に
    自然に追従する（Scheduler自身は状態のスナップショットを持たない）。"""

    def __init__(self):
        self._get_context = None
        self._is_busy = None
        self._on_job_change = None
        self._stop_event = threading.Event()
        self._thread = None
        # (job_id, soul_id) -> 最後に実行した日付("YYYY-MM-DD")。
        # 「SOULごとに」1日1回のゲート。job_idだけを鍵にすると、コノハの日記を
        # 書いた後にクロエへ切り替えてもクロエの日記が翌日まで書かれず、
        # 毎日そのジョブの時刻に同じSOULが選ばれていれば他のSOULの日記は
        # 永久に書かれない（SOULごとに記憶が育つという前提そのものが壊れる）。
        self._last_run_dates = {}
        # 今日ぶんの job_id -> 実行結果。日付が変わったらまるごと捨てる
        # （last_run_info["jobs"]の材料。1tick1ジョブになったため、
        # 1回のtickの結果だけでは「今日何が走ったか」を表せないので累積する）。
        self._day_results = {}
        self._results_date = None
        self.last_run_info = {"last_run": None, "written": []}
        self._running_job_name = ""
        # 終了確認ダイアログ(gui.py on_closing)用: JOBSループ実行中フラグ。
        # tick()自体はdaemonスレッド1本からしか呼ばれないため書き込み側の競合は
        # 無いが、is_running_job()はメインスレッド（closingハンドラ）から読まれる
        # ため、読み書きともロックで保護する。
        self._running_lock = threading.Lock()
        self._running_job = False

    def start(self, get_context, is_busy=None, on_job_change=None):
        """get_context: () -> (cfg, llm, soul_id or None)
        is_busy: () -> bool。Trueの間はジョブを開始しない（会話・インポート優先）。
        on_job_change: (job_name) -> None。開始時は表示名、終了時は""で呼ばれる
        （画面のバナー表示用。失敗しても握りつぶす＝画面都合でジョブを壊さない）。"""
        self._get_context = get_context
        self._is_busy = is_busy
        self._on_job_change = on_job_change
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        # 60秒sleep→tick、を停止されるまで繰り返す。stop_event.waitが60秒待ってから
        # Falseで抜けた時だけtickする（setされたら即ループを抜ける）。
        while not self._stop_event.wait(60):
            self.tick()

    def tick(self, now=None):
        """走らせるべきジョブを1つだけ実行し、そのjob_idを返す（無ければNone）。

        発火条件は3つ全部を満たすこと: (1) configで有効 (2) その日の実行時刻を
        過ぎている (3) 今日まだ走っていない。時刻は「これより前には走らない」下限で
        あって正時ぴったりの引き金ではないので、その時刻にアプリが消えていても
        次に起動したtickでその日ぶんが走る（状態差分ベースの自己修復を維持する）。

        1回のtickで1ジョブに絞るのは、溜まったジョブが一斉に走ってLLM呼び出しが
        束になり、会話を長時間ブロックする（gui.send_messageがジョブ実行中は
        送信を弾く）のを避けるため。_loopが60秒ごとに回すので、溜まっていても
        1分に1つずつ消化される。

        is_busy()がTrueの間は何も始めない（会話・インポート優先）。ジョブは
        状態差分で自己修復するので、次のtickへ回しても失われない。
        なおis_busy()はguiの_busy_lockを取りうるが、ここでは_running_lockを
        取る前に呼び終えるためロック順の逆転（デッドロック）は起きない。"""
        now = now or datetime.datetime.now()
        today = now.date().isoformat()
        if self._get_context is None:
            return None
        if self._is_busy is not None and self._is_busy():
            return None
        cfg, llm, soul_id = self._get_context()
        if not soul_id or not llm:
            return None
        scheduled = cfg.get("scheduled_jobs", {})
        hours = cfg.get("scheduled_job_hours", {})
        job = self._next_due_job(scheduled, hours, today, now.hour, soul_id)
        if job is None:
            return None
        self._last_run_dates[(job["id"], soul_id)] = today
        with self._running_lock:
            self._running_job = True
            self._running_job_name = job["name"]
        self._notify_job_change(job["name"])
        try:
            result = job["run"](cfg, llm, soul_id)
        finally:
            # ジョブ内で例外が漏れた場合（現状のJOBS.runはいずれもベストエフォートで
            # 内部の例外を握るが、想定外の例外が漏れる可能性への保険）でも、
            # is_running_job()が「実行中のまま」に張り付かないようにする。
            with self._running_lock:
                self._running_job = False
                self._running_job_name = ""
            self._notify_job_change("")
        if self._results_date != today:
            self._results_date = today
            self._day_results = {}
        self._day_results[job["id"]] = result
        self.last_run_info = {
            "last_run": now.isoformat(timespec="seconds"),
            "written": self._day_results.get("daily_chronicle", []),
            "jobs": dict(self._day_results),
        }
        return job["id"]

    def _next_due_job(self, scheduled, hours, today, hour, soul_id):
        """JOBSの並び順で、今この瞬間にこのSOULへ走らせるべき最初のジョブを返す
        （無ければNone）。並び順はそのまま優先順位でもある（日次→週次→月次。
        週次の素材は日次日記なので同じ日のうちに上流を先に済ませる必要がある）。"""
        for job in JOBS:
            if not scheduled.get(job["id"], True):
                continue
            if self._last_run_dates.get((job["id"], soul_id)) == today:
                continue
            if hour < _job_hour(job, hours):
                continue
            return job
        return None

    def _notify_job_change(self, job_name):
        """画面バナーへの通知。表示都合の失敗（ウィンドウ破棄直後・JS例外）で
        ジョブ本体やフラグ復帰を巻き添えにしないよう、例外は必ず握りつぶす。"""
        if self._on_job_change is None:
            return
        try:
            self._on_job_change(job_name)
        except Exception:
            pass

    def running_job_name(self):
        """実行中ジョブの表示名（idle時は""）。gui.send_messageが「いま○○を書いとる」と
        伝えるために使う。"""
        with self._running_lock:
            return self._running_job_name

    def is_running_job(self):
        with self._running_lock:
            return self._running_job

    def stop(self):
        self._stop_event.set()
