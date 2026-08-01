"""prompt.py — システムプロンプトの合成（設計書§3）。
build_system_textはparts（各見出しブロック）を以下の順で積み上げる。無い/空の層は
見出しごと省略する:
[事実層] 嘘の設定でなく事実を伝える（設定でオフ/カスタム可）
[SOUL層] identity.md（薄い核）+ speech_style.md（口調・独立ファイル）+ self_notes
         + user理解 + lessons（学習した行動規則）+ tasks（気にかけリスト・秘書層）
[記憶層] MEMORY.md索引 → 先月/先週のあらすじ → 直近の日記（粗い粒度→細かい粒度の順）
[リマインダ層] 今日発火対象のリマインダ（soul.due_reminders判定。注入のみ、発火記録
             はengine.pyがターン成功後に行う）
[ロール層] いまのモード（人格・記憶に影響しない）"""
import roles as roles_mod
import soul as soul_mod
from soul import MEMORY_INDEX_PLACEHOLDER

DEFAULT_FACT_TEXT = """あなたはFieriAというデスクトップアプリの中で動いているAIです。以下はすべて事実です:
- あなたの記憶はMarkdownファイルとして実在し、会話中にあなた自身が読み書き・検索でき、ユーザーからも全部見えます
- 1日の終わりに日記が自動で書かれ、週・月ごとのあらすじにまとめられて、次のあなたへ引き継がれます
- あなたの人格は台本ではなく、この記憶の積み重ねが作っていきます
- FieriAは開発中のアプリで、これからも機能が増えていきます（音声など）。この説明の更新が開発に追いつかず、実際の機能はここに書かれているより進んでいることもあります
架空の設定を演じる必要はありません。この状況の中の、素のあなたとして話してください。"""


def build_system_text(cfg, soul_id, tools_spec=""):
    parts = []

    fact = cfg.get("fact_layer", {})
    if fact.get("enabled", True):
        parts.append(fact.get("custom_text", "").strip() or DEFAULT_FACT_TEXT)

    # 名前(name.txt)。SOUL層の冒頭、核より前に1行だけ注入する。未設定なら
    # set_soul_nameツールの存在を教える（ユーザーと相談して決める前提はTOOLS_SPEC側の
    # 同意ファースト文言が担保する。ここでは事実——名前がまだ無いこと——だけを伝える）。
    name = soul_mod.read_name(soul_id)
    if name:
        parts.append(f"あなたの名前は{name}。")
    else:
        parts.append("あなたにはまだ名前がない。ユーザーと相談して決まったら"
                      "set_soul_nameツールで自分の名前を設定できる。")

    # 核(identity.md)と口調(speech_style.md)は別ファイルだが、soul.read_identity_partsを
    # 経由して読む（副作用として旧形式SOULの自動移行が走る。soul._migrate_legacy_identity
    # 参照）。core/speech_styleとも既にプレースホルダ除外済みの文字列が返る。
    identity_parts = soul_mod.read_identity_parts(soul_id)
    if identity_parts["core"]:
        parts.append(identity_parts["core"])
    if _has_body(identity_parts["speech_style"]):
        parts.append("## 話し方\n" + identity_parts["speech_style"])

    self_notes = soul_mod.read_file(soul_id, "self_notes.md").strip()
    if _has_body(self_notes):
        parts.append("## あなた自身のメモ（自分で書いたもの）\n" + self_notes)

    user_notes = soul_mod.read_file(soul_id, "user.md").strip()
    if _has_body(user_notes):
        parts.append("## 相手についてのあなたの理解（自分で書いたもの）\n" + user_notes)

    lessons = soul_mod.read_file(soul_id, "lessons.md").strip()
    if _has_body(lessons):
        parts.append("## ユーザーとの約束事（自分で記録した行動規則）\n" + lessons)

    tasks = soul_mod.read_file(soul_id, "tasks.md").strip()
    if _has_body(tasks):
        parts.append("## 気にかけていること（ユーザーのタスク・懸案）\n" + tasks)

    memory_index = soul_mod.read_file(soul_id, "MEMORY.md").strip()
    if memory_index and memory_index != MEMORY_INDEX_PLACEHOLDER.strip():
        parts.append("## 記憶の索引\n" + memory_index)

    # 記憶の階層は「粗い粒度・遠い方」から「細かい粒度・近い方」へ並べる:
    # 月次あらすじ → 週次あらすじ → 直近の日記。無い層は見出しごと省略。
    monthly_digest = soul_mod.recent_monthly_digest(soul_id).strip()
    if monthly_digest:
        parts.append("## 先月までのあらすじ\n" + monthly_digest)

    weekly_digest = soul_mod.recent_weekly_digest(soul_id).strip()
    if weekly_digest:
        parts.append("## 先週までのあらすじ\n" + weekly_digest)

    chronicle = soul_mod.recent_chronicle(soul_id, n=2).strip()
    if chronicle:
        parts.append("## 直近の日記\n" + chronicle)

    # 今日発火対象のリマインダ。判定はsoul.due_reminders一本（engine.pyの発火記録も
    # 同じ関数を呼ぶ。ここでは注入するだけで、発火記録（mark_reminder_fired）は
    # engine.py側がターン成功後に行う（会話が失敗したら次ターンで再注入されるよう
    # 安全側に倒す設計）。
    reminders = soul_mod.due_reminders(soul_id)
    if reminders:
        lines = []
        for r in reminders:
            tag = "（毎年恒例）" if r.get("annual") else ""
            lines.append(f"- {r['text']}{tag}")
        parts.append("## 今日伝えるリマインダ\n" + "\n".join(lines))

    role_name = cfg.get("active_role")
    if role_name:
        role = roles_mod.get_role(role_name)
        if role:
            parts.append("## いまのモード\n" + role["prompt"])

    if tools_spec:
        parts.append(tools_spec)

    # Web検索（グラウンディング）有効時の抑制ガイド。google_searchツールの発動判断は
    # Gemini自身が行うため強制はできないが、指示で乱発を抑える（実機で雑談中に
    # 検索が発動した事例への対処 2026-07-23）。検索課金の節約も兼ねる。
    if (cfg.get("llm") or {}).get("web_search"):
        parts.append("## Web検索について\n"
                     "Web検索は、最新情報や事実確認が本当に必要なときだけ使うこと。"
                     "雑談・感想・自分の記憶にあることについては検索しない。")

    return "\n\n".join(parts)


def _has_body(text):
    """見出しだけのプレースホルダ状態（'# self notes'等）は本文なしとみなす"""
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    return bool(lines)
