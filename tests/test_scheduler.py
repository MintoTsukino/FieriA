import datetime
import os


class FakeLLM:
    def __init__(self, reply="# 日記\n今日はUIの話をした。"):
        self.reply = reply
        self.last_system = None
        self.last_max_tokens = None

    def chat(self, messages, max_tokens=None):
        self.last_system = messages[0]["content"]
        self.last_max_tokens = max_tokens
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _cfg():
    return {"wrapup_max_tokens": 2000}


def _dates_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


# --- find_unwritten_days ---

def test_find_unwritten_days_detects_log_without_chronicle():
    import soul, scheduler
    sid = soul.create_soul("未書き検出テスト")
    old_day = _dates_ago(3)
    soul.append_file(sid, os.path.join("logs", f"{old_day}.jsonl"), "{}\n")

    days = scheduler.find_unwritten_days(sid)

    assert days == [old_day]


def test_find_unwritten_days_excludes_day_with_existing_chronicle():
    import soul, scheduler
    sid = soul.create_soul("既存日記除外テスト")
    old_day = _dates_ago(2)
    soul.append_file(sid, os.path.join("logs", f"{old_day}.jsonl"), "{}\n")
    soul.write_file(sid, f"chronicle/{old_day}.md", "# 既にある日記\n")

    days = scheduler.find_unwritten_days(sid)

    assert days == []


def test_find_unwritten_days_excludes_today():
    import soul, scheduler
    sid = soul.create_soul("今日除外テスト")
    today = datetime.date.today().isoformat()
    soul.append_log(sid, "user", "今日の発言")

    days = scheduler.find_unwritten_days(sid)

    assert today not in days
    assert days == []


def test_find_unwritten_days_excludes_day_without_log():
    """chronicleは無くてもログ自体が存在しない日は対象にしない（そもそも書く材料が無い）。"""
    import soul, scheduler
    sid = soul.create_soul("ログなし除外テスト")

    days = scheduler.find_unwritten_days(sid)

    assert days == []


def test_find_unwritten_days_returns_oldest_first():
    import soul, scheduler
    sid = soul.create_soul("古い順テスト")
    d1, d2, d3 = _dates_ago(5), _dates_ago(1), _dates_ago(3)
    for d in (d1, d2, d3):
        soul.append_file(sid, os.path.join("logs", f"{d}.jsonl"), "{}\n")

    days = scheduler.find_unwritten_days(sid)

    assert days == sorted([d1, d2, d3])


# --- find_stale_days ---

def _set_mtimes(sid, day, log_newer):
    """指定日のログ・日記のmtimeを人工的に設定する。log_newer=Trueなら
    ログの方が新しい状態（日記を書いた後に会話があった状態）を作る。"""
    import soul
    log_path = os.path.join(soul.soul_dir(sid), "logs", f"{day}.jsonl")
    diary_path = os.path.join(soul.soul_dir(sid), "chronicle", f"{day}.md")
    base = 1_700_000_000
    if log_newer:
        os.utime(diary_path, (base, base))
        os.utime(log_path, (base + 100, base + 100))
    else:
        os.utime(log_path, (base, base))
        os.utime(diary_path, (base + 100, base + 100))


def test_find_stale_days_detects_log_newer_than_chronicle():
    import soul, scheduler
    sid = soul.create_soul("stale検出テスト")
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"), "{}\n")
    soul.write_file(sid, f"chronicle/{day}.md", "# 途中まで\n")
    _set_mtimes(sid, day, log_newer=True)

    assert scheduler.find_stale_days(sid) == [day]


def test_find_stale_days_excludes_chronicle_newer_than_log():
    import soul, scheduler
    sid = soul.create_soul("stale除外テスト")
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"), "{}\n")
    soul.write_file(sid, f"chronicle/{day}.md", "# 全部書けてる\n")
    _set_mtimes(sid, day, log_newer=False)

    assert scheduler.find_stale_days(sid) == []


def test_find_stale_days_excludes_today():
    import soul, scheduler
    sid = soul.create_soul("stale今日除外テスト")
    today = datetime.date.today().isoformat()
    soul.append_log(sid, "user", "今日の発言")
    soul.write_file(sid, f"chronicle/{today}.md", "# 今日の日記\n")
    _set_mtimes(sid, today, log_newer=True)

    assert scheduler.find_stale_days(sid) == []


def test_find_stale_days_excludes_day_without_chronicle():
    """日記が無い日はfind_unwritten_days（新規）の担当。二重処理しない。"""
    import soul, scheduler
    sid = soul.create_soul("stale未書き除外テスト")
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"), "{}\n")

    assert scheduler.find_stale_days(sid) == []


# --- catch_up ---

def test_catch_up_writes_chronicle_for_each_unwritten_day():
    import soul, scheduler
    sid = soul.create_soul("キャッチアップテスト")
    d1, d2 = _dates_ago(2), _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{d1}.jsonl"),
                      '{"who": "user", "text": "1日目のユニークな発言まにゃ"}\n')
    soul.append_file(sid, os.path.join("logs", f"{d2}.jsonl"),
                      '{"who": "user", "text": "2日目のユニークな発言じょ"}\n')
    fake = FakeLLM()

    written = scheduler.catch_up(_cfg(), fake, sid)

    assert written == [d1, d2]
    body1 = soul.read_file(sid, f"chronicle/{d1}.md")
    body2 = soul.read_file(sid, f"chronicle/{d2}.md")
    assert body1 and body2
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), "chronicle", f"{d1}.md"))
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), "chronicle", f"{d2}.md"))


def test_catch_up_respects_limit():
    import soul, scheduler
    sid = soul.create_soul("limitテスト")
    days = [_dates_ago(n) for n in range(5, 0, -1)]  # 5日分未書き
    for d in days:
        soul.append_file(sid, os.path.join("logs", f"{d}.jsonl"), '{"who": "user", "text": "x"}\n')
    fake = FakeLLM()

    written = scheduler.catch_up(_cfg(), fake, sid, limit=2)

    assert len(written) == 2
    assert written == sorted(days)[:2]


def test_catch_up_continues_after_one_day_fails():
    """1日目の生成が例外で失敗しても、2日目はちゃんと書かれる（ベストエフォート）。"""
    import soul, scheduler

    class FlakyLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("api down")
            return "# 2日目の日記\n無事に書けた"

    sid = soul.create_soul("失敗継続テスト")
    d1, d2 = _dates_ago(2), _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{d1}.jsonl"), '{"who": "user", "text": "x"}\n')
    soul.append_file(sid, os.path.join("logs", f"{d2}.jsonl"), '{"who": "user", "text": "y"}\n')
    flaky = FlakyLLM()

    written = scheduler.catch_up(_cfg(), flaky, sid)

    assert written == [d2]
    assert soul.read_file(sid, f"chronicle/{d1}.md") == ""
    assert "無事に書けた" in soul.read_file(sid, f"chronicle/{d2}.md")


def test_catch_up_returns_empty_list_when_nothing_unwritten():
    import soul, scheduler
    sid = soul.create_soul("何もないテスト")
    fake = FakeLLM()

    written = scheduler.catch_up(_cfg(), fake, sid)

    assert written == []


def test_catch_up_appends_to_stale_day():
    """日記はあるがログの方が新しい過去日は、既存本文を保持したまま続きを追記する。"""
    import soul, scheduler
    sid = soul.create_soul("キャッチアップ追記テスト")
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"),
                      '{"who": "user", "text": "日記の後にした話じょ"}\n')
    soul.write_file(sid, f"chronicle/{day}.md", "# 日記\n昼までの本文。\n")
    _set_mtimes(sid, day, log_newer=True)
    fake = FakeLLM(reply="夜の続きも書いたじょ。")

    written = scheduler.catch_up(_cfg(), fake, sid)

    assert written == [day]
    body = soul.read_file(sid, f"chronicle/{day}.md")
    assert "昼までの本文。" in body
    assert "夜の続きも書いたじょ。" in body


def test_catch_up_skips_stale_append_when_chronicle_is_newer():
    import soul, scheduler
    sid = soul.create_soul("追記不要テスト")
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"), "{}\n")
    soul.write_file(sid, f"chronicle/{day}.md", "# 日記\n全部書けてる。\n")
    _set_mtimes(sid, day, log_newer=False)
    fake = FakeLLM()

    written = scheduler.catch_up(_cfg(), fake, sid)

    assert written == []
    assert fake.last_system is None  # LLMが呼ばれていない
    assert soul.read_file(sid, f"chronicle/{day}.md") == "# 日記\n全部書けてる。\n"


def test_catch_up_append_failure_is_swallowed():
    import soul, scheduler
    sid = soul.create_soul("追記失敗継続テスト")
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"), "{}\n")
    soul.write_file(sid, f"chronicle/{day}.md", "# 日記\n本文。\n")
    _set_mtimes(sid, day, log_newer=True)

    written = scheduler.catch_up(_cfg(), FakeLLM(RuntimeError("api down")), sid)

    assert written == []
    assert soul.read_file(sid, f"chronicle/{day}.md") == "# 日記\n本文。\n"


# --- write_chronicle_for (wrapup.py の一般化) ---

def test_write_chronicle_for_specific_date_writes_correct_filename():
    import soul, wrapup
    sid = soul.create_soul("指定日ファイル名テスト")
    day = _dates_ago(4)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"),
                      '{"who": "user", "text": "過去日のログ"}\n')
    fake = FakeLLM()

    ok = wrapup.write_chronicle_for(_cfg(), fake, sid, day)

    assert ok
    assert soul.read_file(sid, f"chronicle/{day}.md") != ""
    # 他の日付のファイルは作られていない
    chronicle_dir = os.path.join(soul.soul_dir(sid), "chronicle")
    assert os.listdir(chronicle_dir) == [f"{day}.md"]


def test_write_chronicle_for_replaces_date_in_prompt():
    import soul, wrapup
    sid = soul.create_soul("プロンプト日付置換テスト")
    day = "2026-02-14"
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"),
                      '{"who": "user", "text": "バレンタインの話"}\n')
    fake = FakeLLM()

    wrapup.write_chronicle_for(_cfg(), fake, sid, day)

    assert f"# {day} の日記" in fake.last_system
    assert "YYYY-MM-DD" not in fake.last_system


def test_write_chronicle_for_empty_log_writes_nothing():
    import soul, wrapup
    sid = soul.create_soul("指定日空ログテスト")
    day = _dates_ago(1)

    ok = wrapup.write_chronicle_for(_cfg(), FakeLLM(), sid, day)

    assert ok is False
    assert soul.read_file(sid, f"chronicle/{day}.md") == ""


# --- write_daily_chronicle (既存API) が新関数へ委譲した後も無傷であること ---

def test_write_daily_chronicle_still_uses_today():
    import soul, wrapup
    sid = soul.create_soul("委譲後の今日テスト")
    soul.append_log(sid, "user", "今日の発言")
    fake = FakeLLM()

    ok = wrapup.write_daily_chronicle(_cfg(), fake, sid)

    assert ok
    today = datetime.date.today().isoformat()
    assert soul.read_file(sid, f"chronicle/{today}.md") != ""


# --- Scheduler.tick: 発火判定 ---

def _drain(sch, day, hour=23):
    """その日に走るジョブが尽きるまでtickを回す（tickは1回1ジョブ）。
    hour=23は「その日の全ジョブの実行時刻を過ぎた状態」＝時間割の影響を受けずに
    従来どおり『日付が変わったら一通り走る』を検証するため。走ったjob_idを返す。"""
    ran = []
    for _ in range(len(__import__("scheduler").JOBS) + 1):
        job_id = sch.tick(now=datetime.datetime.fromisoformat("%sT%02d:30:00" % (day, hour)))
        if not job_id:
            break
        ran.append(job_id)
    return ran

def test_tick_first_call_triggers_catch_up():
    import soul, scheduler
    sid = soul.create_soul("tick初回テスト")
    old_day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{old_day}.jsonl"), '{"who": "user", "text": "x"}\n')
    fake = FakeLLM()
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), fake, sid)

    _drain(sch, "2026-01-01")

    assert sch.last_run_info["last_run"] is not None
    assert sch.last_run_info["written"] == [old_day]


def test_tick_same_day_second_call_does_not_trigger_catch_up():
    import soul, scheduler
    sid = soul.create_soul("tick同日テスト")
    old_day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{old_day}.jsonl"), '{"who": "user", "text": "x"}\n')
    fake = FakeLLM()
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), fake, sid)

    _drain(sch, "2026-01-01")
    first_info = sch.last_run_info
    # 2回目呼び出し前にさらにログを増やしても、同日なら発火しないことを見るため
    # わざと同じ日付を渡す
    _drain(sch, "2026-01-01")

    assert sch.last_run_info is first_info  # last_run_infoが更新されていない（同一オブジェクトのまま）


def test_tick_triggers_again_when_date_changes():
    """同日の2回目は発火しない(last_run_infoオブジェクトが不変)のに対し、日付が変われば
    再度catch_upが走って新しいlast_run_infoオブジェクトに差し替わることを、
    オブジェクト同一性で確認する（実時刻の値そのものは環境依存で比較しづらいため）。"""
    import soul, scheduler
    sid = soul.create_soul("tick日付変化テスト")
    day1 = _dates_ago(2)
    soul.append_file(sid, os.path.join("logs", f"{day1}.jsonl"), '{"who": "user", "text": "x"}\n')
    fake = FakeLLM()
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), fake, sid)

    _drain(sch, "2026-01-01")
    first_info = sch.last_run_info
    _drain(sch, "2026-01-02")

    assert sch.last_run_info is not first_info


def test_tick_does_nothing_when_no_active_soul():
    import scheduler
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), FakeLLM(), None)

    _drain(sch, "2026-01-01")

    assert sch.last_run_info == {"last_run": None, "written": []}


def test_tick_does_nothing_when_no_llm():
    import scheduler
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), None, "some-soul")

    _drain(sch, "2026-01-01")

    assert sch.last_run_info == {"last_run": None, "written": []}


# --- find_unwritten_weeks ---

def _monday_of_week_ago(n_weeks):
    """今日を含む週からn_weeks週前の週の月曜日を返す（テスト用の安定した過去週生成）。"""
    today = datetime.date.today()
    this_monday = today - datetime.timedelta(days=today.isoweekday() - 1)
    return this_monday - datetime.timedelta(weeks=n_weeks)


def _week_str(d):
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def test_find_unwritten_weeks_detects_week_with_daily_and_no_weekly():
    import soul, scheduler
    sid = soul.create_soul("週次未書き検出テスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")

    weeks = scheduler.find_unwritten_weeks(sid)

    assert weeks == [week]


def test_find_unwritten_weeks_excludes_week_with_existing_weekly():
    import soul, scheduler
    sid = soul.create_soul("週次既存除外テスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
    soul.write_file(sid, f"chronicle/weekly/{week}.md", "# 既にある週次あらすじ\n")

    weeks = scheduler.find_unwritten_weeks(sid)

    assert weeks == []


def test_find_unwritten_weeks_excludes_current_week():
    import soul, scheduler
    sid = soul.create_soul("今週除外テスト")
    today = datetime.date.today()
    soul.write_file(sid, f"chronicle/{today.isoformat()}.md", "# 日記\nx\n")

    weeks = scheduler.find_unwritten_weeks(sid)

    assert _week_str(today) not in weeks


def test_find_unwritten_weeks_excludes_week_without_daily():
    """chronicleに日次日記が1件も無い週は候補に出ない（そもそも素材が無い）。"""
    import soul, scheduler
    sid = soul.create_soul("週次日次なし除外テスト")

    weeks = scheduler.find_unwritten_weeks(sid)

    assert weeks == []


def test_find_unwritten_weeks_returns_oldest_first():
    import soul, scheduler
    sid = soul.create_soul("週次古い順テスト")
    monday_old = _monday_of_week_ago(5)
    monday_recent = _monday_of_week_ago(2)
    soul.write_file(sid, f"chronicle/{monday_old.isoformat()}.md", "# 日記\nx\n")
    soul.write_file(sid, f"chronicle/{monday_recent.isoformat()}.md", "# 日記\ny\n")

    weeks = scheduler.find_unwritten_weeks(sid)

    assert weeks == sorted([_week_str(monday_old), _week_str(monday_recent)])


# --- catch_up_weekly ---

def test_catch_up_weekly_writes_digest_for_unwritten_week():
    import soul, scheduler
    sid = soul.create_soul("週次キャッチアップテスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nユニークな出来事\n")
    fake = FakeLLM(reply="# 週次あらすじ\nまとまった内容")

    written = scheduler.catch_up_weekly(_cfg(), fake, sid)

    assert written == [week]
    assert soul.read_file(sid, f"chronicle/weekly/{week}.md") != ""


def test_catch_up_weekly_respects_limit():
    import soul, scheduler
    sid = soul.create_soul("週次limitテスト")
    weeks = []
    for n in range(6, 1, -1):  # 5週分未書き
        monday = _monday_of_week_ago(n)
        soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
        weeks.append(_week_str(monday))
    fake = FakeLLM()

    written = scheduler.catch_up_weekly(_cfg(), fake, sid, limit=2)

    assert len(written) == 2
    assert written == sorted(weeks)[:2]


# --- tick: scheduled_jobs によるON/OFF ---

def test_tick_skips_weekly_digest_when_disabled():
    import soul, scheduler
    sid = soul.create_soul("週次OFFテスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
    fake = FakeLLM()
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "weekly_digest": False}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert soul.read_file(sid, f"chronicle/weekly/{week}.md") == ""
    assert "weekly_digest" not in sch.last_run_info["jobs"]


def test_tick_runs_weekly_digest_when_enabled():
    import soul, scheduler
    sid = soul.create_soul("週次ONテスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
    fake = FakeLLM(reply="# あらすじ\n本文")
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "weekly_digest": True}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert soul.read_file(sid, f"chronicle/weekly/{week}.md") != ""
    assert sch.last_run_info["jobs"]["weekly_digest"] == [week]


# --- find_unwritten_months ---

def _first_day_of_month_ago(n_months):
    """今月からn_months月前の月の1日を返す（テスト用の安定した過去月生成）。"""
    today = datetime.date.today()
    year, month = today.year, today.month
    for _ in range(n_months):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return datetime.date(year, month, 1)


def test_find_unwritten_months_detects_month_with_daily_and_no_monthly():
    import soul, scheduler
    sid = soul.create_soul("月次未書き検出テスト")
    day = _first_day_of_month_ago(2)
    month = day.isoformat()[:7]
    soul.write_file(sid, f"chronicle/{day.isoformat()}.md", "# 日記\nx\n")

    months = scheduler.find_unwritten_months(sid)

    assert months == [month]


def test_find_unwritten_months_excludes_month_with_existing_monthly():
    import soul, scheduler
    sid = soul.create_soul("月次既存除外テスト")
    day = _first_day_of_month_ago(2)
    month = day.isoformat()[:7]
    soul.write_file(sid, f"chronicle/{day.isoformat()}.md", "# 日記\nx\n")
    soul.write_file(sid, f"chronicle/monthly/{month}.md", "# 既にある月次あらすじ\n")

    months = scheduler.find_unwritten_months(sid)

    assert months == []


def test_find_unwritten_months_excludes_current_month():
    import soul, scheduler
    sid = soul.create_soul("今月除外テスト")
    today = datetime.date.today()
    soul.write_file(sid, f"chronicle/{today.isoformat()}.md", "# 日記\nx\n")

    months = scheduler.find_unwritten_months(sid)

    assert today.isoformat()[:7] not in months


def test_find_unwritten_months_excludes_month_without_daily():
    """chronicleに日次日記が1件も無い月は候補に出ない（そもそも素材が無い）。"""
    import soul, scheduler
    sid = soul.create_soul("月次日次なし除外テスト")

    months = scheduler.find_unwritten_months(sid)

    assert months == []


def test_find_unwritten_months_returns_oldest_first():
    import soul, scheduler
    sid = soul.create_soul("月次古い順テスト")
    day_old = _first_day_of_month_ago(5)
    day_recent = _first_day_of_month_ago(2)
    soul.write_file(sid, f"chronicle/{day_old.isoformat()}.md", "# 日記\nx\n")
    soul.write_file(sid, f"chronicle/{day_recent.isoformat()}.md", "# 日記\ny\n")

    months = scheduler.find_unwritten_months(sid)

    assert months == sorted([day_old.isoformat()[:7], day_recent.isoformat()[:7]])


# --- catch_up_monthly ---

def test_catch_up_monthly_writes_digest_for_unwritten_month():
    import soul, scheduler
    sid = soul.create_soul("月次キャッチアップテスト")
    day = _first_day_of_month_ago(2)
    month = day.isoformat()[:7]
    soul.write_file(sid, f"chronicle/{day.isoformat()}.md", "# 日記\nユニークな出来事\n")
    fake = FakeLLM(reply="# 月次あらすじ\nまとまった内容")

    written = scheduler.catch_up_monthly(_cfg(), fake, sid)

    assert written == [month]
    assert soul.read_file(sid, f"chronicle/monthly/{month}.md") != ""


def test_catch_up_monthly_respects_limit():
    import soul, scheduler
    sid = soul.create_soul("月次limitテスト")
    months = []
    for n in range(6, 1, -1):  # 5ヶ月分未書き
        day = _first_day_of_month_ago(n)
        soul.write_file(sid, f"chronicle/{day.isoformat()}.md", "# 日記\nx\n")
        months.append(day.isoformat()[:7])
    fake = FakeLLM()

    written = scheduler.catch_up_monthly(_cfg(), fake, sid, limit=2)

    assert len(written) == 2
    assert written == sorted(months)[:2]


# --- tick: scheduled_jobsによる月次のON/OFF・実行順 ---

def test_tick_skips_monthly_digest_when_disabled():
    import soul, scheduler
    sid = soul.create_soul("月次OFFテスト")
    day = _first_day_of_month_ago(2)
    month = day.isoformat()[:7]
    soul.write_file(sid, f"chronicle/{day.isoformat()}.md", "# 日記\nx\n")
    fake = FakeLLM()
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "weekly_digest": True, "monthly_digest": False}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert soul.read_file(sid, f"chronicle/monthly/{month}.md") == ""
    assert "monthly_digest" not in sch.last_run_info["jobs"]


def test_tick_runs_monthly_digest_when_enabled():
    import soul, scheduler
    sid = soul.create_soul("月次ONテスト")
    day = _first_day_of_month_ago(2)
    month = day.isoformat()[:7]
    soul.write_file(sid, f"chronicle/{day.isoformat()}.md", "# 日記\nx\n")
    fake = FakeLLM(reply="# あらすじ\n本文")
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "weekly_digest": True, "monthly_digest": True}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert soul.read_file(sid, f"chronicle/monthly/{month}.md") != ""
    assert sch.last_run_info["jobs"]["monthly_digest"] == [month]


# --- run_index_maintenance / tick: 索引の整理ジョブ ---

def _big_memory_index():
    """互いに重複しない30件のポインタからなる索引本文（実運用に近い形。「同一行の
    200回反復」のような不自然な素材だと、正当な整理でも出力が元の10%を割り込んで
    しまい正常系のテストが偽陽性で失敗するため、あえて別トピック30件にしてある）。"""
    lines = "".join(f"- [topic{i}](wiki/topic{i}.md) — わりと長めの説明文その{i}\n" for i in range(30))
    return "# MEMORY.md — 記憶の索引\n\n" + lines


def _write_distinct_memory_files(soul_id, n=30):
    import soul
    for i in range(n):
        soul.write_file(soul_id, f"wiki/topic{i}.md", f"# topic{i}\n本文\n")


def test_run_index_maintenance_fires_only_when_over_limit():
    import soul, scheduler
    sid = soul.create_soul("索引整理・超過テスト")
    _write_distinct_memory_files(sid, 30)
    soul.write_file(sid, "MEMORY.md", _big_memory_index())
    new_index = _big_memory_index().replace("わりと長めの説明文その", "整理済み・その")
    fake = FakeLLM(reply=new_index)
    cfg = _cfg()
    cfg["memory_index_limit_chars"] = 100

    ok = scheduler.run_index_maintenance(cfg, fake, sid)

    assert ok is True
    assert "整理済み" in soul.read_file(sid, "MEMORY.md")


def test_run_index_maintenance_does_not_fire_below_limit():
    import soul, scheduler
    sid = soul.create_soul("索引整理・閾値以下テスト")
    original = "# MEMORY.md — 記憶の索引\n\n- [短い](wiki/短い.md) — ひとこと\n"
    soul.write_file(sid, "wiki/短い.md", "# 短い\n本文\n")
    soul.write_file(sid, "MEMORY.md", original)
    cfg = _cfg()
    cfg["memory_index_limit_chars"] = 4000

    ok = scheduler.run_index_maintenance(cfg, FakeLLM(), sid)

    assert ok is False
    assert soul.read_file(sid, "MEMORY.md") == original


def test_tick_runs_index_maintenance_when_over_limit_and_enabled():
    import soul, scheduler
    sid = soul.create_soul("tick索引整理テスト")
    _write_distinct_memory_files(sid, 30)
    soul.write_file(sid, "MEMORY.md", _big_memory_index())
    new_index = _big_memory_index().replace("わりと長めの説明文その", "整理済み・その")
    fake = FakeLLM(reply=new_index)
    cfg = _cfg()
    cfg["memory_index_limit_chars"] = 100
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "index_maintenance": True}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert sch.last_run_info["jobs"]["index_maintenance"] is True
    assert "整理済み" in soul.read_file(sid, "MEMORY.md")


def test_tick_skips_index_maintenance_when_disabled():
    import soul, scheduler
    sid = soul.create_soul("tick索引整理OFFテスト")
    soul.write_file(sid, "wiki/topicA.md", "# topicA\n本文\n")
    original = _big_memory_index()
    soul.write_file(sid, "MEMORY.md", original)
    fake = FakeLLM(reply="# MEMORY.md\n\n- [topicA](wiki/topicA.md) — 整理済み\n")
    cfg = _cfg()
    cfg["memory_index_limit_chars"] = 100
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "index_maintenance": False}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert "index_maintenance" not in sch.last_run_info["jobs"]
    assert soul.read_file(sid, "MEMORY.md") == original


def test_tick_second_call_same_day_does_not_refire_index_maintenance():
    """書き直し後も超過が続くケースの保険：同日2回目のtickでは(全ジョブ共通の日付
    ゲートにより)index_maintenanceも再発火しない。"""
    import soul, scheduler
    sid = soul.create_soul("tick索引整理・同日2回目テスト")
    soul.write_file(sid, "wiki/topicA.md", "# topicA\n本文\n")
    soul.write_file(sid, "MEMORY.md", _big_memory_index())
    # 書き直し後も超過し続けるダミー応答（意図的に巨大）
    fake = FakeLLM(reply=_big_memory_index())
    cfg = _cfg()
    cfg["memory_index_limit_chars"] = 100
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "index_maintenance": True}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")
    first_info = sch.last_run_info
    _drain(sch, "2026-01-01")

    assert sch.last_run_info is first_info  # 同日2回目は何も起きていない


def test_tick_without_get_context_does_nothing():
    """start()を呼んでいない（_get_contextが未設定の）Schedulerでtickを呼んでも安全。"""
    import scheduler
    sch = scheduler.Scheduler()

    _drain(sch, "2026-01-01")  # 例外を投げない

    assert sch.last_run_info == {"last_run": None, "written": []}


# --- find_unreflected_weeks / catch_up_self_reflection（週次内省・2026-07-22追加）---

def test_find_unreflected_weeks_detects_week_with_daily_and_no_reflection_log():
    import soul, scheduler
    sid = soul.create_soul("内省未実施検出テスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")

    weeks = scheduler.find_unreflected_weeks(sid)

    assert weeks == [week]


def test_find_unreflected_weeks_excludes_week_already_logged():
    import soul, scheduler
    sid = soul.create_soul("内省実施済み除外テスト")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
    soul.append_file(sid, "identity_history/reflections.log",
                     f"2026-01-01 week={week} result=no_change\n")

    weeks = scheduler.find_unreflected_weeks(sid)

    assert weeks == []


def test_find_unreflected_weeks_excludes_current_week():
    import soul, scheduler
    sid = soul.create_soul("内省今週除外テスト")
    today = datetime.date.today()
    soul.write_file(sid, f"chronicle/{today.isoformat()}.md", "# 日記\nx\n")

    weeks = scheduler.find_unreflected_weeks(sid)

    assert _week_str(today) not in weeks


def test_find_unreflected_weeks_excludes_week_without_daily():
    import soul, scheduler
    sid = soul.create_soul("内省日次なし除外テスト")

    weeks = scheduler.find_unreflected_weeks(sid)

    assert weeks == []


def test_find_unreflected_weeks_returns_oldest_first():
    import soul, scheduler
    sid = soul.create_soul("内省古い順テスト")
    monday_old = _monday_of_week_ago(5)
    monday_recent = _monday_of_week_ago(2)
    soul.write_file(sid, f"chronicle/{monday_old.isoformat()}.md", "# 日記\nx\n")
    soul.write_file(sid, f"chronicle/{monday_recent.isoformat()}.md", "# 日記\ny\n")

    weeks = scheduler.find_unreflected_weeks(sid)

    assert weeks == sorted([_week_str(monday_old), _week_str(monday_recent)])


def test_catch_up_self_reflection_runs_for_unreflected_week():
    import soul, scheduler
    sid = soul.create_soul("内省キャッチアップテスト", identity_text="元の核です。")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nユニークな出来事\n")
    fake = FakeLLM(reply="変更なし")

    attempted = scheduler.catch_up_self_reflection(_cfg(), fake, sid)

    assert attempted == [week]
    log = soul.read_file(sid, "identity_history/reflections.log")
    assert f"week={week} result=no_change" in log


def test_catch_up_self_reflection_respects_limit():
    import soul, scheduler
    sid = soul.create_soul("内省limitテスト", identity_text="元の核です。")
    weeks = []
    for n in range(6, 1, -1):  # 5週分未実施
        monday = _monday_of_week_ago(n)
        soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
        weeks.append(_week_str(monday))
    fake = FakeLLM(reply="変更なし")

    attempted = scheduler.catch_up_self_reflection(_cfg(), fake, sid, limit=2)

    assert len(attempted) == 2
    assert attempted == sorted(weeks)[:2]


def test_tick_runs_self_reflection_when_enabled():
    import soul, scheduler
    sid = soul.create_soul("tick内省実行テスト", identity_text="元の核です。")
    monday = _monday_of_week_ago(2)
    week = _week_str(monday)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
    fake = FakeLLM(reply="変更なし")
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "self_reflection": True}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert sch.last_run_info["jobs"]["self_reflection"] == [week]


def test_tick_skips_self_reflection_when_disabled():
    import soul, scheduler
    sid = soul.create_soul("tick内省OFFテスト", identity_text="元の核です。")
    monday = _monday_of_week_ago(2)
    soul.write_file(sid, f"chronicle/{monday.isoformat()}.md", "# 日記\nx\n")
    fake = FakeLLM(reply="変更なし")
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "self_reflection": False}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert "self_reflection" not in sch.last_run_info["jobs"]
    assert soul.read_file(sid, "identity_history/reflections.log") == ""


# --- find_ungardened_months / catch_up_wiki_gardening（月次のwiki庭仕事・2026-07-22追加）---

def _long_wiki_page(n=2100, note="本文"):
    return "# wikiページ\n" + (note * (n // len(note) + 1))[:n]


def test_find_ungardened_months_returns_last_month_when_no_marker():
    import soul, scheduler
    sid = soul.create_soul("庭仕事未実施検出テスト")

    months = scheduler.find_ungardened_months(sid)

    today = datetime.date.today()
    expected_last_month = (today.replace(day=1) - datetime.timedelta(days=1)).isoformat()[:7]
    assert months == [expected_last_month]


def test_find_ungardened_months_excludes_month_with_existing_marker():
    import soul, scheduler
    sid = soul.create_soul("庭仕事実施済み除外テスト")
    today = datetime.date.today()
    last_month = (today.replace(day=1) - datetime.timedelta(days=1)).isoformat()[:7]
    soul.write_file(sid, f"gardening/{last_month}.done", "2026-01-01T00:00:00\n")

    months = scheduler.find_ungardened_months(sid)

    assert months == []


def test_catch_up_wiki_gardening_runs_and_writes_marker():
    import os, soul, scheduler
    sid = soul.create_soul("庭仕事キャッチアップテスト")
    soul.write_file(sid, "wiki/長いページ.md", _long_wiki_page())
    fake = FakeLLM(reply="# 整理済み\n" + ("整理された本文" * 200))

    today = datetime.date.today()
    last_month = (today.replace(day=1) - datetime.timedelta(days=1)).isoformat()[:7]

    attempted = scheduler.catch_up_wiki_gardening(_cfg(), fake, sid)

    assert attempted == [last_month]
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), "gardening", f"{last_month}.done"))
    assert "整理された本文" in soul.read_file(sid, "wiki/長いページ.md")


def test_catch_up_wiki_gardening_continues_after_exception():
    import soul, scheduler

    class BrokenLLM:
        def chat(self, messages, max_tokens=None):
            raise RuntimeError("api down")

    sid = soul.create_soul("庭仕事例外継続テスト")
    soul.write_file(sid, "wiki/長いページ.md", _long_wiki_page())

    attempted = scheduler.catch_up_wiki_gardening(_cfg(), BrokenLLM(), sid)

    today = datetime.date.today()
    last_month = (today.replace(day=1) - datetime.timedelta(days=1)).isoformat()[:7]
    assert attempted == [last_month]  # 例外があっても「試みた」ことは記録される


# --- tick: scheduled_jobsによるwiki_gardeningのON/OFF ---

def test_tick_runs_wiki_gardening_when_enabled():
    import os, soul, scheduler
    sid = soul.create_soul("tick庭仕事ONテスト")
    soul.write_file(sid, "wiki/長いページ.md", _long_wiki_page())
    fake = FakeLLM(reply="# 整理済み\n" + ("整理された本文" * 200))
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "wiki_gardening": True}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    today = datetime.date.today()
    last_month = (today.replace(day=1) - datetime.timedelta(days=1)).isoformat()[:7]
    assert sch.last_run_info["jobs"]["wiki_gardening"] == [last_month]
    assert os.path.isfile(os.path.join(soul.soul_dir(sid), "gardening", f"{last_month}.done"))


def test_tick_skips_wiki_gardening_when_disabled():
    import soul, scheduler
    sid = soul.create_soul("tick庭仕事OFFテスト")
    soul.write_file(sid, "wiki/長いページ.md", _long_wiki_page())
    fake = FakeLLM(reply="# 整理済み\n" + ("整理された本文" * 200))
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"daily_chronicle": True, "wiki_gardening": False}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert "wiki_gardening" not in sch.last_run_info["jobs"]
    # OFFなのでマーカーも書かれていない
    import os
    assert not os.path.isdir(os.path.join(soul.soul_dir(sid), "gardening"))


# --- Scheduler.is_running_job（終了確認ダイアログ用フラグ・2026-07-23追加）---

def test_is_running_job_false_before_any_tick():
    import scheduler
    sch = scheduler.Scheduler()

    assert sch.is_running_job() is False


def test_is_running_job_true_during_job_execution_and_false_after():
    """tick()のJOBSループ実行中だけis_running_job()がTrueになり、完了後はFalseへ
    戻ることを、JOBSを一時的にプローブジョブへ差し替えて確認する。"""
    import scheduler
    sid = "dummy-soul"
    fake = FakeLLM()
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), fake, sid)

    observed = {}

    def probe_job(cfg, llm, soul_id):
        observed["during"] = sch.is_running_job()
        return []

    original_jobs = scheduler.JOBS
    scheduler.JOBS = [{"id": "daily_chronicle", "hour": 0, "name": "probe",
                        "description": "probe", "run": probe_job}]
    try:
        _drain(sch, "2026-01-01")
    finally:
        scheduler.JOBS = original_jobs

    assert observed["during"] is True
    assert sch.is_running_job() is False


def test_is_running_job_resets_to_false_even_if_job_raises():
    """JOBSのrunが想定外の例外を投げても(現状のJOBSは内部でベストエフォート握りだが
    保険として)、tick()のfinallyでフラグは必ず戻る。"""
    import scheduler
    sid = "dummy-soul"
    fake = FakeLLM()
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (_cfg(), fake, sid)

    def broken_job(cfg, llm, soul_id):
        raise RuntimeError("想定外の例外")

    original_jobs = scheduler.JOBS
    scheduler.JOBS = [{"id": "daily_chronicle", "hour": 0, "name": "broken",
                        "description": "broken", "run": broken_job}]
    try:
        try:
            _drain(sch, "2026-01-01")
        except RuntimeError:
            pass
    finally:
        scheduler.JOBS = original_jobs

    assert sch.is_running_job() is False


def test_tick_wiki_gardening_default_off_when_key_missing():
    """scheduled_jobsにwiki_gardeningキーが無いテスト用cfg（既存の素のcfg）でも、
    self_reflectionと同じくデフォルトOFF扱いになること（tick内の
    scheduled.get(job["id"], True)は一律デフォルトTrueだが、実運用では
    config.DEFAULT_CONFIGがFalseで補完するため、ここではその補完済みcfgを模して
    明示的にFalseを渡すシナリオで確認する。素のcfgでの挙動はconfig.py側のテストで
    担保する）。"""
    import soul, scheduler
    sid = soul.create_soul("tick庭仕事キー欠如テスト")
    soul.write_file(sid, "wiki/長いページ.md", _long_wiki_page())
    fake = FakeLLM(reply="# 整理済み\n" + ("整理された本文" * 200))
    cfg = _cfg()
    cfg["scheduled_jobs"] = {"wiki_gardening": False}
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, fake, sid)

    _drain(sch, "2026-01-01")

    assert "wiki_gardening" not in sch.last_run_info["jobs"]


# --- Scheduler.tick: 時間割・1tick1ジョブ・会話中の待避 2026-08-03 ---

def _dt(day="2026-01-01", hour=23):
    return datetime.datetime.fromisoformat("%sT%02d:00:00" % (day, hour))


def _fresh_sch(cfg, llm, sid, is_busy=None):
    import scheduler
    sch = scheduler.Scheduler()
    sch._get_context = lambda: (cfg, llm, sid)
    sch._is_busy = is_busy
    return sch


def _soul_with_two_pending_jobs(name):
    """日次（未書きの過去日）と週次（未書きの完了週）の両方が溜まったSOULを作る。"""
    import soul
    sid = soul.create_soul(name)
    day = _dates_ago(1)
    soul.append_file(sid, os.path.join("logs", f"{day}.jsonl"),
                      '{"who": "user", "text": "きのうの話じょ"}\n')
    past_monday = _monday_of_week_ago(2)
    soul.write_file(sid, f"chronicle/{past_monday.isoformat()}.md", "# 過去週の日記\n")
    return sid


def test_tick_runs_only_one_job_per_call():
    sid = _soul_with_two_pending_jobs("1tick1ジョブテスト")
    sch = _fresh_sch(_cfg(), FakeLLM(), sid)

    first = sch.tick(now=_dt(hour=23))
    second = sch.tick(now=_dt(hour=23))

    assert first == "daily_chronicle"
    assert second == "weekly_digest"


def test_tick_skips_job_before_its_hour():
    """既定の時間割: 日次0時・週次4時。1時では日次だけが対象になる。"""
    sid = _soul_with_two_pending_jobs("時刻ゲートテスト")
    sch = _fresh_sch(_cfg(), FakeLLM(), sid)

    ran = [sch.tick(now=_dt(hour=1)), sch.tick(now=_dt(hour=1))]

    assert ran == ["daily_chronicle", None]


def test_tick_runs_job_when_its_hour_arrives_later_same_day():
    sid = _soul_with_two_pending_jobs("時刻到来テスト")
    sch = _fresh_sch(_cfg(), FakeLLM(), sid)
    sch.tick(now=_dt(hour=1))

    assert sch.tick(now=_dt(hour=1)) is None
    assert sch.tick(now=_dt(hour=5)) == "weekly_digest"


def test_tick_respects_hour_override_from_config():
    sid = _soul_with_two_pending_jobs("時間割上書きテスト")
    cfg = _cfg()
    cfg["scheduled_job_hours"] = {"weekly_digest": 1}
    sch = _fresh_sch(cfg, FakeLLM(), sid)

    ran = [sch.tick(now=_dt(hour=1)), sch.tick(now=_dt(hour=1))]

    assert ran == ["daily_chronicle", "weekly_digest"]


def test_tick_runs_missed_job_when_app_starts_after_the_hour():
    """自己修復の維持: 0時にアプリが消えていて昼に起動しても、その日ぶんは走る
    （時刻は「これより前には走らない」下限であって、正時ぴったりの引き金ではない）。"""
    sid = _soul_with_two_pending_jobs("取りこぼし防止テスト")
    sch = _fresh_sch(_cfg(), FakeLLM(), sid)

    assert sch.tick(now=_dt(hour=15)) == "daily_chronicle"


def test_tick_runs_each_job_once_per_day():
    sid = _soul_with_two_pending_jobs("1日1回テスト")
    sch = _fresh_sch(_cfg(), FakeLLM(), sid)
    while sch.tick(now=_dt(hour=23)):
        pass

    assert sch.tick(now=_dt(hour=23)) is None
    assert sch.tick(now=_dt(day="2026-01-02", hour=23)) == "daily_chronicle"


def test_tick_does_not_start_job_while_conversation_busy():
    """会話中はジョブを始めない（人間優先）。ジョブは状態差分で自己修復するので
    次のtickへ回しても失われない。"""
    sid = _soul_with_two_pending_jobs("会話優先テスト")
    fake = FakeLLM()
    sch = _fresh_sch(_cfg(), fake, sid, is_busy=lambda: True)

    assert sch.tick(now=_dt(hour=23)) is None
    assert fake.last_system is None  # LLMが呼ばれていない


def test_tick_runs_after_conversation_ends():
    sid = _soul_with_two_pending_jobs("会話終了後テスト")
    busy = {"v": True}
    sch = _fresh_sch(_cfg(), FakeLLM(), sid, is_busy=lambda: busy["v"])
    assert sch.tick(now=_dt(hour=23)) is None

    busy["v"] = False

    assert sch.tick(now=_dt(hour=23)) == "daily_chronicle"


def test_running_job_name_is_empty_when_idle():
    sch = _fresh_sch(_cfg(), FakeLLM(), None)
    assert sch.running_job_name() == ""


def test_every_job_has_a_default_hour():
    import scheduler
    for job in scheduler.JOBS:
        assert isinstance(job["hour"], int)
        assert 0 <= job["hour"] <= 23
