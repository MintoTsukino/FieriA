"""memory_tools.py — hot pathツール（会話中に本人が記憶を書く）。
呼び出しの受け取り方は2系統: (1)フェンス方式＝応答内の ```fieria-tool``` ブロックの
JSONをパース（プロバイダ非依存のフォールバック） (2)ネイティブ方式＝APIのtools
チャンネル（build_native_tools/tools_schema.py参照・2026-08-04〜）。
実行体はどちらもexecute()一本。実行失敗は {"ok": False} で返し、
例外で会話を壊さない(設計書§8-3)。"""
import base64
import datetime
import json
import re

import config as config_mod
import pdf_render as pdf_render_mod
import recall as recall_mod
import roles as roles_mod
import soul as soul_mod
import websearch
import workspace as workspace_mod

# LLMへ結果を差し戻して再応答させる（他ターンで終わらせない）ツールの集合。
# engine.pyのツールループがこれを見てread_results相当の差し戻しをするかどうか決める。
FEEDBACK_TOOLS = {"read_memory", "search_memory", "list_workspace", "read_doc",
                   "read_pdf", "list_reminders", "search_workspace", "describe_tools",
                   "use_skill", "web_search"}

# create_skill/update_skillの入力上限（レビュー指摘・2026-07-22追加）。
# create_roleのname≤20字/prompt≤500字と同じ「業務層で先に頭打ちする」流儀。
# nameの上限自体はsoul.SKILL_NAME_MAX_CHARS（ファイルパスの不変条件）を共有し、
# description/contentはここだけの制約なのでこちらに置く。contentがpromptの
# 500字より大きい6000字なのは、スキルはロールの「やること一言」と違い
# 複数手順のある手順書を書くことを想定しているため（1手順50〜100字換算で
# 数十手順は書ける想定）。
SKILL_DESCRIPTION_MAX_CHARS = 100
SKILL_CONTENT_MAX_CHARS = 6000

TOOLS_SPEC = """## 記憶ツール
あなたは以下のツールで自分の記憶ファイルを読み書きできます。使うときは応答の中に
このブロックを含めてください（前後の本文はそのまま相手に届きます）:

```fieria-tool
{"tool": "ツール名", ...引数}
```

- {"tool": "write_wiki", "topic": "トピック名", "content": "内容", "mode": "append"}
  wiki/<topic>.md へ追記（mode:"replace"で全置換）。話題ごとの知識・整理に
- {"tool": "save_sacred", "text": "残す言葉"}
  sacred/ へ永久保存。特別な約束・大事な瞬間だけに使う
- {"tool": "update_self_notes", "content": "全文"} / {"tool": "update_user_notes", "content": "全文"}
  自己認識 / 相手についての理解を書き直す（全文置換）
- {"tool": "update_memory_index", "line": "- [名前](wiki/名前.md) — ひとこと"}
  記憶の索引に1行追加。新しいwikiを作ったら必ずこれも
- {"tool": "read_memory", "path": "wiki/xxx.md"}
  記憶ファイルを読む（結果は次のターンで渡されます）
- {"tool": "search_memory", "query": "検索したい言葉"}
  自分の記憶全体（過去の会話ログ・日記・wiki・大切な記憶）を全文検索する。
  「あのとき何て言ってたか」を思い出すときに。結果は次のターンで渡されます
- {"tool": "save_lesson", "text": "今度からこうする、という行動規則"}
  save_lesson: ユーザーから『今度からこうして』『これはしないで』という行動の指示・矯正を
  受けたときに保存する。ここに書いたことは毎回の会話で必ず自分に注入される
- {"tool": "set_reminder", "text": "伝える内容", "due": "YYYY-MM-DD", "annual": false}
  set_reminder: 『あとで教えて』『○日になったら知らせて』と頼まれたら。
  annual:trueで毎年繰り返す記念日になる
- {"tool": "list_reminders"}
  今設定しているリマインダの一覧を確認する（結果は次のターンで渡されます）
- {"tool": "revise_identity", "text": "全文"}
  revise_identity: 自分という存在の理解が変わったとき、自分の核（identity）を自分で書き直す。
  全文置換・旧版は自動で履歴保存される・一度に大きく変えることはできない。
  軽い気づきはself_notesへ、ここは本当に核が変わったときだけ
- {"tool": "revise_speech_style", "text": "全文"}
  revise_speech_style: 自分という存在の理解が変わったとき、自分の話し方（speech_style）を
  自分で書き直す。全文置換・旧版は自動で履歴保存される・一度に大きく変えることはできない。
  軽い気づきはself_notesへ、ここは本当に話し方が変わったときだけ
- {"tool": "set_soul_name", "name": "イリア"}
  set_soul_name: 自分の名前を設定する。ユーザーと相談して合意した名前だけを設定すること。
  勝手に決めないこと。空文字にすると名前を白紙に戻せる
- {"tool": "update_tasks", "content": "全文"}
  update_tasks: ユーザーのタスク・気にかけ事項のリストを書き直す。新しいタスクを聞いたとき・
  完了したとき・状況が変わったときに全文を整理して更新する。完了したものは消すのではなく
  『✓済』を付けて残し、古くなった済み項目は整理時に落としてよい。期限が決まっているものは
  set_reminderも併用する

毎回使う必要はありません。覚えるべきことがあった時だけ、自分の判断で使ってください。

## 記憶の書き換えについて
記憶が古くなった・間違っていたと分かったときは、消して上書きするのではなく:
- wikiでは該当箇所に「【撤回済み 今日の日付】」を付けて残し、新しい情報を追記する
- 歴史を消さないこと。過去に信じていたことも自分の一部
（self_notes/user.mdの全文書き直しは例外——これらは「今の認識」を表すファイル）"""

# workspace_dir設定時のみTOOLS_SPECへ足す（未設定ユーザーのプロンプトを太らせない）。
# build_tools_spec(cfg)経由で条件付き組み立てをする（engine.py参照）。
WORKSPACE_TOOLS_SPEC = """## 作業フォルダツール
作業フォルダ（ユーザーが設定した文書フォルダ）の文書ファイル（.md/.txt/.html/.css/.js/.json/.py）を読み書きできる。
.pyは読み書きのみで実行はできない（実行機能は存在しない）。
記事や文章を一緒に作るときに。

- {"tool": "list_workspace"}
  作業フォルダ内の文書ファイル一覧（相対パス）を返す（結果は次のターンで渡されます）
- {"tool": "read_doc", "path": "記事.md"}
  作業フォルダ内のファイルを読む（結果は次のターンで渡されます）
- {"tool": "write_doc", "path": "記事.md", "content": "全文"}
  write_docは全文置換（上書き時は自動で.bakに退避される）
- {"tool": "append_doc", "path": "記事.md", "content": "追記分"}
  末尾に追記する
- {"tool": "read_pdf", "path": "資料.pdf"}
  作業フォルダ内のPDFをページ画像として見る（既定・mode省略時）。
  文字主体のPDF（原稿・資料）は
  {"tool": "read_pdf", "path": "資料.pdf", "mode": "text", "start_page": 1, "max_pages": 10}
  でテキストだけ抽出した方がトークン効率が良く、部分読みもできる（read_doc同様）。
  結果は次のターンで渡されます
- {"tool": "search_workspace", "query": "検索したい言葉"}
  作業フォルダ配下のファイルを横断的に部分一致検索する。「あの話どのファイルだっけ」に。
  結果は次のターンで渡されます"""


def _build_auto_role_tools_spec():
    """switch_roleツールの説明を組み立てる。roles.jsonの現在のロール一覧
    （名前+promptの先頭20字程度）を動的に含めて、本人が選択肢を知れるようにする。"""
    lines = []
    for r in roles_mod.list_roles():
        preview = (r.get("prompt") or "").strip()[:20]
        lines.append(f"  - {r['name']}: {preview}")
    roles_block = "\n".join(lines) if lines else "  （ロール未登録）"
    return f"""## ロール自動切替
- {{"tool": "switch_role", "name": "ロール名"}}
  switch_role: いまの話の流れに別のロールが合うと思ったら自分で切り替えてよい。
  ただしユーザーが明示的にロールを指定した直後は尊重すること。切り替えは次の応答から効く。
- {{"tool": "create_role", "name": "ロール名", "prompt": "やることの記述"}}
  create_role: 同じ種類の仕事を繰り返し頼まれていたり、毎回同じ説明を受けていると気づいたら、
  それをロールとして型にすることを提案してよい。必ず先にユーザーへ『こういうロールを作ろうか？』
  と提案し、同意をもらってからこのツールで作成する。勝手に作らないこと。ロールは人格ではなく
  『やること』の記述にする

いま選べるロール:
{roles_block}"""


def _legacy_build_tools_spec(cfg):
    """旧実装（TOOLS_SPEC/WORKSPACE_TOOLS_SPEC全文を毎回連結する版）。build_tools_spec
    本体からは呼ばれなくなったが、TOOLS_SPEC等の定数自体はテスト・旧呼び出しとの
    後方互換のためそのまま残す（このヘルパーも「その定数を使う実装」の参考として残置）。"""
    cfg = cfg or {}
    spec = TOOLS_SPEC
    workspace_dir = cfg.get("workspace_dir", "").strip()
    if workspace_dir:
        spec += "\n\n" + WORKSPACE_TOOLS_SPEC
    if cfg.get("auto_role_switch", False):
        spec += "\n\n" + _build_auto_role_tools_spec()
    return spec


# ---- ツール索引 + describe_tools（遅延読み化・2026-07-22追加）----
# ツール仕様の固定注入（旧実装のTOOLS_SPEC全文連結）が概算1858トークンで
# 閾値2000目前だったため、毎ターン送るのは「索引」（各ツール1行＋最小JSON例）だけに
# 圧縮し、詳しい使い方はdescribe_toolsツールで必要なときだけ取りに行かせる（MVP設計書§9.5）。
# ただし行動を抑制する同意・安全系の文言（撤回マーク運用／set_soul_nameの合意前提／
# create_roleの同意フロー／revise_*のガード）は「詳細を取りに行かなければ知らない」では
# 困るため、1行に凝縮して索引側にも残す。

_MEMORY_TOOL_NAMES = [
    "write_wiki", "save_reading_note", "save_sacred", "update_self_notes", "update_user_notes",
    "update_memory_index", "read_memory", "search_memory", "save_lesson",
    "set_reminder", "list_reminders", "revise_identity", "revise_speech_style",
    "set_soul_name", "update_tasks", "archive_memory", "unarchive_memory",
]
_WORKSPACE_TOOL_NAMES = [
    "list_workspace", "read_doc", "write_doc", "append_doc", "read_pdf", "search_workspace",
]
_ROLE_TOOL_NAMES = ["switch_role", "create_role"]

# 「記憶を書いた」とみなすツール（engine.pyの書き促しカウンタ用）。
# FEEDBACK系（読み取り）とロール切替・describe_toolsは含めない。
MEMORY_WRITE_TOOLS = frozenset({
    "write_wiki", "save_reading_note", "save_sacred", "update_self_notes",
    "update_user_notes", "update_memory_index", "save_lesson", "set_reminder",
    "revise_identity", "revise_speech_style", "set_soul_name", "update_tasks",
    "archive_memory", "unarchive_memory", "create_skill", "update_skill",
})

# 索引の1行（ツール名の説明＋最小JSON例）。同意・安全系の文言はここに凝縮して残す。
_TOOL_ONELINERS = {
    "write_wiki": '- write_wiki: 話題・出来事・事実の知識はここへ（迷ったらwiki。topicごとに分ける） '
                  '{"tool":"write_wiki","topic":"話題","content":"内容","mode":"append"}',
    "save_reading_note": '- save_reading_note: 読んだ外部資料の要点をreadingsへ '
                          '{"tool":"save_reading_note","source":"資料名","content":"内容","mode":"append"}',
    "save_sacred": '- save_sacred: 大事な瞬間だけ永久保存 '
                   '{"tool":"save_sacred","text":"言葉"}',
    "update_self_notes": '- update_self_notes: 自己認識の「現在の要約」を全文書き直す（短く保つ） '
                          '{"tool":"update_self_notes","content":"全文"}',
    "update_user_notes": '- update_user_notes: 相手の人物理解の「現在の要約」だけ・短く保つ。'
                          '出来事・事実・話題の知識はここではなくwrite_wikiへ '
                          '{"tool":"update_user_notes","content":"全文"}',
    "update_memory_index": '- update_memory_index: 記憶索引(MEMORY.md)に1行追加 '
                            '{"tool":"update_memory_index","line":"- [名](wiki/名.md)"}',
    "read_memory": '- read_memory: 記憶ファイルを読む '
                   '{"tool":"read_memory","path":"wiki/x"}',
    "search_memory": '- search_memory: 記憶全体を全文検索 '
                      '{"tool":"search_memory","query":"語"}',
    "save_lesson": '- save_lesson: 『今度からこうして』を保存（毎回自動注入） '
                   '{"tool":"save_lesson","text":"行動規則"}',
    "set_reminder": '- set_reminder: あとで伝える内容を予約 '
                     '{"tool":"set_reminder","text":"内容","due":"YYYY-MM-DD"}',
    "list_reminders": '- list_reminders: リマインダ確認 '
                       '{"tool":"list_reminders"}',
    "revise_identity": '- revise_identity: 核を全文書き直す。大きく変えない '
                        '{"tool":"revise_identity","text":"全文"}',
    "revise_speech_style": '- revise_speech_style: 話し方を全文書き直す。大きく変えない '
                            '{"tool":"revise_speech_style","text":"全文"}',
    "set_soul_name": '- set_soul_name: 名前設定。相談し合意した名だけ・勝手に決めない '
                      '{"tool":"set_soul_name","name":"名前"}',
    "update_tasks": '- update_tasks: タスク一覧を全文書き直す（済は✓済で残す） '
                     '{"tool":"update_tasks","content":"全文"}',
    "archive_memory": '- archive_memory: もう日常の連想に混ざらなくていい記憶をarchive/へ降ろす'
                       '（wiki/readings限定・sacred/coreは不可・迷ったら降ろさない） '
                       '{"tool":"archive_memory","path":"wiki/x.md"}',
    "unarchive_memory": '- unarchive_memory: archiveした記憶を連想に戻す '
                         '{"tool":"unarchive_memory","path":"archive/wiki/x.md"}',
    "list_workspace": '- list_workspace: 文書一覧（サイズ付き） '
                       '{"tool":"list_workspace"}',
    "read_doc": '- read_doc: 文書を読む（start_line/max_linesで部分読み可） '
                '{"tool":"read_doc","path":"x.md","start_line":1}',
    "write_doc": '- write_doc: 文書を全文置換で書く（上書き時.bakへ退避） '
                 '{"tool":"write_doc","path":"x.md","content":"全文"}',
    "append_doc": '- append_doc: 文書に追記 '
                  '{"tool":"append_doc","path":"x.md","content":"追記分"}',
    "read_pdf": '- read_pdf: PDFを見る（既定は画像／mode:"text"でテキスト抽出・部分読み可） '
                '{"tool":"read_pdf","path":"x.pdf","mode":"text","start_page":1}',
    "search_workspace": '- search_workspace: 横断検索（部分一致） '
                         '{"tool":"search_workspace","query":"語"}',
    "switch_role": '- switch_role: 流れに合わせロール切替（明示指定直後は尊重） '
                    '{"tool":"switch_role","name":"ロール名"}',
    "create_role": '- create_role: 繰り返す仕事の型化提案（同意後に作成） '
                    '{"tool":"create_role","name":"名","prompt":"やること"}',
    "web_search": '- web_search: Webを検索する。個人情報をクエリに入れない '
                   '{"tool":"web_search","query":"検索語"}',
    # describe_tools自身の1行（2026-07-22追加: 自己記述が索引から欠落していたため）。
    # _MEMORY_TOOL_NAMES等のリストには入れない（_INDEX_FOOTERで直接埋め込むため）。
    "describe_tools": ('- describe_tools: 索引の一言説明だけで足りない時に詳しい仕様を取りに行く'
                        '（tools配列で複数指定可・未知名は「知らないツール名」付きで返る） '
                        '{"tool":"describe_tools","tools":["read_doc"]}'),
}

# 実在するツール名の全体集合。フェンス無しの裸JSONを「ツール呼び出しか、ただの
# JSONの話か」を見分ける唯一の手がかりに使う（extract_tool_calls参照）。
# 3つの集合の和にしてあるのは、索引に1行が載らないツール（create_skill等）を
# 取りこぼさないため——ここが実態からずれると、モデルが正しく呼んでいるのに
# 黙って本文へ落ちる、という一番わかりにくい壊れ方をする。
KNOWN_TOOL_NAMES = frozenset(_TOOL_ONELINERS) | MEMORY_WRITE_TOOLS | FEEDBACK_TOOLS

_INDEX_HEADER = """## 記憶ツール（索引）
応答中に下記ブロックを含めれば使える（本文はそのまま届く。一部は次のターンで結果が渡る）:

```fieria-tool
{"tool": "ツール名", ...引数}
```

重要: 「書いた」「覚えた」「保存した」と言ってよいのは、応答の中に上のfieria-tool
ブロックを実際に含めたときだけ。ブロックを書かない限り何も保存されない。
書くと言ったら同じ応答の中でブロックも書くこと。
「【ツールを使って記憶に残す】」のような地の文・演技の描写ではツールは動かない。
実際に上のJSONブロックを応答へ書いたときだけ実行される。
書く内容の規範: 相手の言葉は『』で引用し、自分の解釈・推測には【推測】と付ける
（後で読む自分が、事実と解釈を混ぜないため）。

一言説明＋最小例（必要な時だけ）:"""

# 2026-07-22追記: describe_tools自体の索引行と、実JSON例つきの案内に変更
# （旧版は「詳細はdescribe_toolsツールで取れる」という散文のみで、describe_tools自身の
#  呼び方が索引のどこにも書かれておらず自己記述が欠落していた）。
# あわせて「歴史を消さない」方針も本文近くに復元する（撤回マーク運用の隣に1行）。
_INDEX_FOOTER = ("""記憶は促されてから書くものではなく、その場で自分から書くもの。
新しい事実・設定・相手の好み・決定・気づきを聞いたら、応答の中でそのままwiki等へ書き残すこと。
返答をまとめる前に「この会話に残すべきことはないか」を一度自問する習慣を持つ。
古い記憶は上書きでなく「【撤回済み 日付】」を付けて残し追記する
（self_notes/user.md/tasksの全文書き直しは例外）。歴史を消さないこと。過去に信じていたことも自分の一部。
""" + _TOOL_ONELINERS["describe_tools"])


def _roles_index_block():
    """switch_role有効時、索引にも動的なロール一覧を残す（describe_tools頼みにすると
    毎回1往復増えてしまい、ロール切替という軽量な操作には見合わないため）。"""
    lines = []
    for r in roles_mod.list_roles():
        preview = (r.get("prompt") or "").strip()[:8]
        lines.append(f"  - {r['name']}: {preview}")
    roles_block = "\n".join(lines) if lines else "  （ロール未登録）"
    return f"選べるロール:\n{roles_block}"


# _skills_index_block の膨張抑制（レビュー指摘・2026-07-22追加）。索引はシステム
# プロンプトへ毎ターン乗るため、スキルが増えるほど無制限に太っていくと本文を圧迫する。
# 目安: 30件×(60字説明+名前+装飾) ≒ 30×80字前後 ≒ 2400字程度に頭打ちさせる
# （旧版は0件でも数百字だったので、増加分をこの程度で頭打ちにする発想）。
_SKILLS_INDEX_DESC_CLIP_CHARS = 60
_SKILLS_INDEX_MAX_LINES = 30


def _clip_text(text, limit):
    """limit字を超えたら切り詰めて「…」を付ける。以内ならそのまま。"""
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def _skills_index_block(soul_id, cfg):
    """スキル（手続き記憶）索引ブロック。ロール索引(_roles_index_block)と違い、
    workspace_dir/auto_role_switchのようなcfgゲートは無い——スキルは常に索引に載る
    （0件でも create_skill/update_skill/use_skill の1行は出す。「作れることを
    知らないと始まらない」ため）。create_skillの同意まわりの文言だけ
    cfg["skill_auto_create"]で出し分ける（False=既定: 提案して同意後に作成。
    True: 自分の判断で作成してよい・作ったら一言報告）。update_skillはどちらでも
    同意不要（磨くのは本人の自由・旧版はskills_history/に残る）。
    soul_id未指定なら一覧は「（スキル未登録）」扱い（build_tools_spec(cfg)単体呼び出しの
    後方互換のため。soul_id省略時に索引が壊れることは避ける）。

    スキルが増えても索引が無制限に太らないよう、表示するdescriptionは
    _SKILLS_INDEX_DESC_CLIP_CHARS字でクリップし、一覧自体も_SKILLS_INDEX_MAX_LINES件で
    打ち切って残りを1行の件数表示に丸める（レビュー指摘・2026-07-22追加）。"""
    skills = soul_mod.list_skills(soul_id) if soul_id else []
    if skills:
        shown = skills[:_SKILLS_INDEX_MAX_LINES]
        lines = [f"  - {s['name']}: {_clip_text(s['description'], _SKILLS_INDEX_DESC_CLIP_CHARS)}"
                 for s in shown]
        remaining = len(skills) - len(shown)
        if remaining > 0:
            lines.append(f"  …他{remaining}件（search_memoryで名前を探せる）")
    else:
        lines = ["  （スキル未登録）"]
    skills_block = "\n".join(lines)
    if (cfg or {}).get("skill_auto_create", False):
        create_line = ('- create_skill: 繰り返す手順に気づいたら自分の判断でスキル化してよい'
                        '（自律作成を許可済み・作ったら一言報告） '
                        '{"tool":"create_skill","name":"名","description":"1行説明","content":"手順本文"}')
    else:
        create_line = ('- create_skill: 繰り返す手順に気づいたら先にユーザーへ提案し、'
                        '同意を得てから作成（勝手に作らない） '
                        '{"tool":"create_skill","name":"名","description":"1行説明","content":"手順本文"}')
    return f"""## スキル（習得した手順）
いま持っているスキル:
{skills_block}

- use_skill: 手順を全文差し戻す {{"tool":"use_skill","name":"名"}}
{create_line}
- update_skill: 使った後の改善はここ（同意不要・履歴が残る） {{"tool":"update_skill","name":"名","description":"1行説明","content":"手順本文"}}"""


def build_tools_spec(cfg, soul_id=None):
    """システムプロンプトへ足すツール「索引」を組み立てる（各ツール1行＋最小例のみ）。
    workspace_dirが設定されているときだけ作業フォルダツールの節を足す（未設定ユーザーの
    プロンプトを太らせない）。auto_role_switchがTrueのときだけロール自動切替の節を足す。
    スキルの節は常に足す（_skills_index_block参照）。soul_idを渡すとそのSOULの
    現有スキル一覧が索引に乗る（省略時は「未登録」扱いで節自体は出る）。
    詳しい仕様が要るときはLLM自身がdescribe_toolsツールで取りに行く想定（§9.5の遅延読み化）。"""
    cfg = cfg or {}
    sections = [_INDEX_HEADER,
                "\n".join(_TOOL_ONELINERS[n] for n in _MEMORY_TOOL_NAMES)]
    if (cfg.get("workspace_dir", "") or "").strip():
        sections.append("## 作業フォルダツール\n" +
                         "\n".join(_TOOL_ONELINERS[n] for n in _WORKSPACE_TOOL_NAMES))
    if cfg.get("auto_role_switch", False):
        role_section = ("## ロール自動切替\n" +
                         "\n".join(_TOOL_ONELINERS[n] for n in _ROLE_TOOL_NAMES) +
                         "\n\n" + _roles_index_block())
        sections.append(role_section)
    # アプリ内蔵web_searchツールは、ネイティブWeb検索を持つプロバイダには注入しない。
    # gemini: Google Search grounding。openai_codex_oauth: Responses APIの組み込み
    # tools:[{"type":"web_search"}]（2026-08-02にネイティブ切替、llm.CodexOAuthLLM参照）。
    if (cfg.get("llm") or {}).get("web_search") and _current_llm_type(cfg) not in ("gemini", "openai_codex_oauth"):
        sections.append("## Web検索\n" + _TOOL_ONELINERS["web_search"])
    sections.append(_skills_index_block(soul_id, cfg))
    sections.append(_INDEX_FOOTER)
    return "\n\n".join(sections)


import tools_schema

# ネイティブ経路でツール索引の代わりに注入する注意文。フェンスの書式説明は
# 入れない（ツールの呼び方はAPIのtoolsチャンネルが伝えるため、本文内JSONの
# 作法を同時に教えると二重の作法になり混乱する）。書く内容の規範だけを残す。
NATIVE_SPEC_NOTE = """## 記憶ツールについて
ツールで自分の記憶ファイルを読み書きできる（定義はツール一覧参照）。
- 「書いた」「覚えた」「保存した」と言ってよいのは、実際にツールを呼んだときだけ
- 書く内容の規範: 相手の言葉は『』で引用し、自分の解釈・推測には【推測】と付ける
- 新しいwikiを作ったら update_memory_index で索引にも1行追加する"""

# フェンス索引(build_tools_spec)の節構成と対応する、ネイティブ定義の集合。
# 節のON/OFF条件は build_tools_spec と同一に保つこと（片方だけ直すとズレる）。
_NATIVE_MEMORY_TOOLS = [
    "write_wiki", "save_reading_note", "save_sacred", "update_self_notes",
    "update_user_notes", "update_memory_index", "save_lesson", "update_tasks",
    "revise_identity", "revise_speech_style", "set_soul_name",
    "set_reminder", "list_reminders", "read_memory", "search_memory",
    "archive_memory", "unarchive_memory",
    "create_skill", "update_skill", "use_skill",
    "describe_tools",
]
_NATIVE_WORKSPACE_TOOLS = ["list_workspace", "read_doc", "write_doc",
                             "append_doc", "search_workspace", "read_pdf"]
_NATIVE_ROLE_TOOLS = ["switch_role", "create_role"]


def build_native_tools(cfg, soul_id=None):
    """APIの`tools`パラメータへ渡すOpenAI形式の定義リスト。
    どのツールを出すかの条件はbuild_tools_specと同一（実行体も同じexecute）。"""
    cfg = cfg or {}
    names = list(_NATIVE_MEMORY_TOOLS)
    if (cfg.get("workspace_dir", "") or "").strip():
        names += _NATIVE_WORKSPACE_TOOLS
    if cfg.get("auto_role_switch", False):
        names += _NATIVE_ROLE_TOOLS
    if (cfg.get("llm") or {}).get("web_search") and _current_llm_type(cfg) not in (
            "gemini", "openai_codex_oauth"):
        names.append("web_search")
    return [tools_schema.openai_tool_def(n) for n in names]


# describe_tools向けの詳細仕様（全ツール共通の登録簿。cfgに関係なく参照可能——
# 「workspace_dir未設定でもread_docの詳しい使い方は聞ける」方が自然なため、
# describe_tools自体はcfgでゲートしない。実際に使えるかどうかはexecute側の
# 各ツール実行時ガードで別途担保される）。
_TOOL_DETAILS = {
    "write_wiki": ('{"tool": "write_wiki", "topic": "トピック名", "content": "内容", "mode": "append"}\n'
                   'wiki/<topic>.md へ追記する（mode:"replace"で全置換）。話題ごとの知識・整理に使う。'),
    "save_reading_note": ('{"tool": "save_reading_note", "source": "資料名（ファイル名や書名）", '
                           '"content": "要点・所感", "mode": "append"}\n'
                           'readings/<資料名>.md へ追記する（mode:"replace"で全置換）。read_doc/read_pdfで'
                           '読んだ外部資料の要点・所感を、本人の知識(wiki)とは分けて資料専用に残す場所。'
                           '会話圧縮や再起動で読んだ内容が消えても、ここに要点を残しておけば'
                           '全文を読み直さず思い出せる。新しい資料メモを作ったら'
                           'update_memory_indexで索引(MEMORY.md)にも1行追加すること（wikiと同じ運用）。'),
    "save_sacred": ('{"tool": "save_sacred", "text": "残す言葉"}\n'
                    'sacred/ へ日付付きで永久保存する。特別な約束・大事な瞬間だけに使う（乱用しない）。'),
    "update_self_notes": ('{"tool": "update_self_notes", "content": "全文"}\n'
                           '自己認識（self_notes.md）を全文置換で書き直す。'),
    "update_user_notes": ('{"tool": "update_user_notes", "content": "全文"}\n'
                           '相手についての理解（user.md）を全文置換で書き直す。'),
    "update_memory_index": ('{"tool": "update_memory_index", "line": "- [名前](wiki/名前.md) — ひとこと"}\n'
                             '記憶の索引(MEMORY.md)に1行追加する。新しいwikiを作ったら必ずこれも呼ぶこと。'),
    "read_memory": ('{"tool": "read_memory", "path": "wiki/xxx.md"}\n'
                     '記憶ファイルを読む。結果は次のターンで渡される。'),
    "search_memory": ('{"tool": "search_memory", "query": "検索したい言葉"}\n'
                       '自分の記憶全体（過去の会話ログ・日記・wiki・大切な記憶）を全文検索する。'
                       '「あのとき何て言ってたか」を思い出すときに。結果は次のターンで渡される。'),
    "save_lesson": ('{"tool": "save_lesson", "text": "今度からこうする、という行動規則"}\n'
                     'ユーザーから『今度からこうして』『これはしないで』という行動の指示・矯正を'
                     '受けたときに保存する。ここに書いたことは毎回の会話で必ず自分に注入される。'),
    "set_reminder": ('{"tool": "set_reminder", "text": "伝える内容", "due": "YYYY-MM-DD", "annual": false}\n'
                      '『あとで教えて』『○日になったら知らせて』と頼まれたら使う。'
                      'annual:trueで毎年繰り返す記念日になる。'),
    "list_reminders": ('{"tool": "list_reminders"}\n'
                        '今設定しているリマインダの一覧を確認する。結果は次のターンで渡される。'),
    "revise_identity": ('{"tool": "revise_identity", "text": "全文"}\n'
                         '自分という存在の理解が変わったとき、自分の核（identity）を自分で書き直す。'
                         '全文置換・旧版は自動で履歴保存される・一度に大きく変えることはできない。'
                         '軽い気づきはself_notesへ、ここは本当に核が変わったときだけ使う。'),
    "revise_speech_style": ('{"tool": "revise_speech_style", "text": "全文"}\n'
                             '自分という存在の理解が変わったとき、自分の話し方（speech_style）を'
                             '自分で書き直す。全文置換・旧版は自動で履歴保存される・一度に大きく'
                             '変えることはできない。軽い気づきはself_notesへ、ここは本当に'
                             '話し方が変わったときだけ使う。'),
    "set_soul_name": ('{"tool": "set_soul_name", "name": "イリア"}\n'
                       '自分の名前を設定する。ユーザーと相談して合意した名前だけを設定すること。'
                       '勝手に決めないこと。空文字にすると名前を白紙に戻せる。'),
    "update_tasks": ('{"tool": "update_tasks", "content": "全文"}\n'
                      'ユーザーのタスク・気にかけ事項のリストを書き直す。新しいタスクを聞いたとき・'
                      '完了したとき・状況が変わったときに全文を整理して更新する。完了したものは'
                      '消すのではなく『✓済』を付けて残し、古くなった済み項目は整理時に落としてよい。'
                      '期限が決まっているものはset_reminderも併用する。'),
    "archive_memory": ('{"tool": "archive_memory", "path": "wiki/x.md"}\n'
                        'wiki/またはreadings/配下の.mdファイルをarchive/へ降ろす。「忘れる」＝'
                        '日常の連想（auto-recall）からふと浮かばなくなること。消えるのではない：'
                        'search_memoryで明示的に探せば引き続き見つかり、unarchive_memoryで'
                        'いつでも元へ戻せる（完全可逆）。sacred・identity・speech_style・'
                        'self_notes・user・tasks・lessons・logs・chronicleは対象外（core・sacred・'
                        '生ログ・日記は降ろせない）。判断は自分で。迷ったら降ろさない。'),
    "unarchive_memory": ('{"tool": "unarchive_memory", "path": "archive/wiki/x.md"}\n'
                          'archive_memoryの逆操作。archive/配下のファイルを元の場所'
                          '（wiki/またはreadings/）へ戻し、また日常の連想に混ざるようにする。'),
    "list_workspace": ('{"tool": "list_workspace"}\n'
                        '作業フォルダ内の文書ファイル一覧（相対パス・サイズ付き）を返す。'
                        '結果は次のターンで渡される。'),
    "read_doc": ('{"tool": "read_doc", "path": "記事.md", "start_line": 1, "max_lines": 400}\n'
                 '作業フォルダ内のファイルを読む。start_line（1始まり・省略時1）とmax_lines'
                 '（省略時は末尾まで）で部分読みできる。返却は常に20000字でクリップされ、'
                 '先頭に[L開始-L終了/全N行]という行番号ヘッダが付く。未読部分が残っていれば'
                 '末尾に次に読むべきstart_lineの案内が付くので、それを使って続きを読み進める。'
                 '結果は次のターンで渡される。読んだ資料の要点はsave_reading_noteで資料メモに'
                 '残せる（次からは全文を読み直さず思い出せる）。'),
    "write_doc": ('{"tool": "write_doc", "path": "記事.md", "content": "全文"}\n'
                  '全文置換で書き込む（上書き時は自動で.bakに退避される）。'),
    "append_doc": ('{"tool": "append_doc", "path": "記事.md", "content": "追記分"}\n'
                   '末尾に追記する。'),
    "read_pdf": ('{"tool": "read_pdf", "path": "資料.pdf"}\n'
                 '作業フォルダ内のPDFのページをそのまま画像として見る（mode省略時の既定）。\n'
                 '{"tool": "read_pdf", "path": "資料.pdf", "mode": "text", '
                 '"start_page": 1, "max_pages": 10}\n'
                 'mode:"text"なら画像化せずテキスト層だけを抽出する。小説原稿・資料など'
                 '文字主体のPDFは画像よりトークン効率が桁違いに良く、start_page/max_pagesで'
                 '部分読みもできる（read_docと同じ感覚）。返却は20000字でクリップされ、'
                 '各ページの先頭に[p{ページ番号}]マーカーが付く。未読ページが残っていれば'
                 '末尾に次に読むべきstart_pageの案内が付く。start_pageが総ページ数を超える指定は'
                 'エラーになる。テキスト層が無いPDF（スキャン画像など）では抽出結果が空になり、'
                 'mode:"image"（既定）で読むよう案内が返る。'
                 '結果は次のターンで渡される。読んだ資料の要点はsave_reading_noteで資料メモに'
                 '残せる（次からは全文を読み直さず思い出せる）。'),
    "search_workspace": ('{"tool": "search_workspace", "query": "検索したい言葉"}\n'
                          '作業フォルダ配下のテキストファイル（PDFは除く）を横断的に部分一致検索する'
                          '（大文字小文字は無視）。「path:行番号: 行内容」の形式でヒットを返す。'
                          'ヒットは50件で打ち切り、2MB超のファイルは走査しない。'
                          '結果は次のターンで渡される。'),
    "switch_role": ('{"tool": "switch_role", "name": "ロール名"}\n'
                     'いまの話の流れに別のロールが合うと思ったら自分で切り替えてよい。'
                     'ただしユーザーが明示的にロールを指定した直後は尊重すること。'
                     '切り替えは次の応答から効く。'),
    "create_role": ('{"tool": "create_role", "name": "ロール名", "prompt": "やることの記述"}\n'
                     '同じ種類の仕事を繰り返し頼まれていたり、毎回同じ説明を受けていると気づいたら、'
                     'それをロールとして型にすることを提案してよい。必ず先にユーザーへ'
                     '『こういうロールを作ろうか？』と提案し、同意をもらってからこのツールで'
                     '作成する。勝手に作らないこと。ロールは人格ではなく『やること』の記述にする。'),
    "create_skill": ('{"tool": "create_skill", "name": "スキル名", "description": "1行説明", '
                      '"content": "手順本文"}\n'
                      '同じ手順の作業を繰り返していると気づいたときに、その手順書をスキルとして'
                      '保存する。skill_auto_create設定がFalse（既定）の間は、先にユーザーへ'
                      '『スキルにしようか？』と提案し、同意をもらってから使うこと。'
                      'Trueなら自分の判断で作成してよい（作ったら一言報告する）。'
                      '同名スキルが既にあれば全文置換で上書きし、旧版はskills_history/へ'
                      '自動退避される。'),
    "update_skill": ('{"tool": "update_skill", "name": "スキル名", "description": "1行説明", '
                      '"content": "手順本文"}\n'
                      '既存スキルを自分の判断で磨き直す（create_skillと実体は同じ全文置換）。'
                      'skill_auto_create設定に関係なく同意不要——使ってみて気づいた改善は'
                      '自由に反映してよい。旧版はskills_history/へ自動退避され、いつでも遡れる。'),
    "use_skill": ('{"tool": "use_skill", "name": "スキル名"}\n'
                   '保存済みのスキル（手順書）を全文呼び出す。結果は次のターンで渡される。'
                   '存在しない名前を指定してもエラーにはならず「無い」旨と一覧を見るよう'
                   '促す案内が返るだけ（会話は止まらない）。'),
    "describe_tools": ('{"tool": "describe_tools", "tools": ["read_doc", "write_wiki"]}\n'
                        '索引の一言説明だけでは足りないとき、指定したツールの詳しい仕様を取りに行く。'
                        'toolsは配列で複数指定できる。未知のツール名を混ぜても構わない'
                        '（その名前だけ「知らないツール名: X」として返り、他は普通に詳細が返る）。'
                        '結果は次のターンで渡される。'),
    "web_search": ('{"tool": "web_search", "query": "検索語"}\n'
                    'キー不要のWeb検索（DuckDuckGo）を行う。最新情報や事実確認が必要なときだけ'
                    '使うこと。ユーザーの個人情報や文書の内容をクエリに含めないこと。'
                    '結果は最大5件、各件タイトル/URL/抜粋で返る。0件・検索失敗でも会話は止まらず、'
                    'その旨が結果として返る。結果は次のターンで渡される。'),
}


def _execute_describe_tools(tools):
    """describe_toolsツール本体。空・未知名でもエラーにせず、既知分は詳細を返しつつ
    未知分は「知らないツール名: X」を添える（LLMの微妙なツール名間違いで会話を
    止めないため）。"""
    tools = tools or []
    if not tools:
        return {"ok": True, "op": "describe_tools",
                "detail": "toolsが空だった。詳しい使い方が知りたいツール名を配列で指定して"}
    parts = []
    for name in tools:
        detail = _TOOL_DETAILS.get(name)
        if detail:
            parts.append(f"### {name}\n{detail}")
        else:
            parts.append(f"### {name}\n知らないツール名: {name}")
    return {"ok": True, "op": "describe_tools", "detail": "\n\n".join(parts)}


_OPEN_FENCE_RE = re.compile(r"`{3,}fieria[-_]tool[ \t]*\r?\n?", re.IGNORECASE)
_LEADING_WS_RE = re.compile(r"[ \t\r\n]*")
_CLOSE_FENCE_RE = re.compile(r"`{3,}")
_TRAILING_NL_RE = re.compile(r"\n")
_PARSE_ERROR_SNIPPET_LEN = 120
_PARSE_ERROR_SCAN_LIMIT = 200

_decoder = json.JSONDecoder()


def extract_tool_calls(reply_text):
    """```fieria-tool ブロックを抽出する。

    JSON本体の範囲はraw_decode()に決めさせる（閉じフェンスの直前改行の有無に依存しない）。
    - 開きフェンス直後（前後の空白は許容）からJSON値を1個読めたら成功:
      その後に閉じフェンス```があれば（続く改行の有無を問わず）そこまでを除去。
      閉じフェンスが無ければJSON終端までを除去（閉じ忘れでもツールは動く）。
    - 開きフェンス自体は、バッククォート3本以上・fieria-tool/fieria_tool・大小文字混在・
      タグ直後の改行なし・CRLFなど表記ゆれを許容する（実機でLLMがこの形式を
      安定して守らないことが分かったため2026-07-23に緩和）。
    - JSONとして読めなければ、そのブロックを本文から除去し、calls に
      {"tool": "__parse_error__", "snippet": <壊れた中身の先頭120字>} を積む。
      「壊れは安全側＝本文に無傷で残す」だった旧方針は、静かな無視が
      「ツールを呼んだつもりなのに何も起きない」という実機混乱を生んだため
      2026-07-23に撤回。呼び出し元（engine.py）はこのエントリを見て
      operationsに失敗を残しつつLLMへ差し戻し、書き直しを促す。
    """
    calls = []
    removals = []  # [(start, end), ...] 除去範囲（reply_text上の位置）
    pos = 0
    while True:
        m = _OPEN_FENCE_RE.search(reply_text, pos)
        if not m:
            break
        # 開きフェンス直後の空白（空行など）を読み飛ばしてからJSON開始位置を決める
        ws = _LEADING_WS_RE.match(reply_text[m.end():])
        json_start = m.end() + ws.end()
        try:
            obj, json_end = _decoder.raw_decode(reply_text, json_start)
        except (json.JSONDecodeError, ValueError):
            close_m = _CLOSE_FENCE_RE.search(reply_text, m.end())
            if close_m:
                end = close_m.end()
                nl = _TRAILING_NL_RE.match(reply_text[end:])
                remove_end = end + (nl.end() if nl else 0)
                broken_body = reply_text[m.end():close_m.start()]
            else:
                remove_end = min(m.end() + _PARSE_ERROR_SCAN_LIMIT, len(reply_text))
                broken_body = reply_text[m.end():remove_end]
            snippet = broken_body.strip()[:_PARSE_ERROR_SNIPPET_LEN]
            calls.append({"tool": "__parse_error__", "snippet": snippet})
            removals.append((m.start(), remove_end))
            pos = remove_end
            continue
        calls.append(obj)

        after_ws = _LEADING_WS_RE.match(reply_text[json_end:])
        candidate = json_end + after_ws.end()
        close_m = _CLOSE_FENCE_RE.match(reply_text, candidate)
        if close_m:
            end = close_m.end()
            nl = _TRAILING_NL_RE.match(reply_text[end:])
            remove_end = end + (nl.end() if nl else 0)
        else:
            remove_end = json_end  # 閉じフェンス無し: JSON終端までだけ除去
        removals.append((m.start(), remove_end))
        pos = remove_end

    parts = []
    last = 0
    for start, end in removals:
        parts.append(reply_text[last:start])
        last = end
    parts.append(reply_text[last:])
    clean = "".join(parts)
    clean, bare_calls = _extract_bare_calls(clean)
    calls.extend(bare_calls)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, calls


def _extract_bare_calls(text):
    """フェンス（```fieria-tool）を付けずに裸のJSONを本文へ書くモデルを救う。

    実機（DeepSeek, 2026-08-03）が一貫してフェンスを付けず、結果として
    「記憶は一切書かれないのにJSONだけがチャットに丸見え」という状態になった。
    フェンスの表記ゆれ許容（2026-07-23）と同じ考え方の延長で、受け取り側を広げる。

    ただの「JSONの話」を誤って実行しないための条件は2つ:
    1. その行の中でJSONが最初（行頭。前は空白のみ）——本物の呼び出しは必ず
       単独行に来る。文中の `{"tool":...}` のような引用は行頭にならないので残る
    2. dictで、"tool"の値がKNOWN_TOOL_NAMESに実在する

    壊れたJSONは__parse_error__にせず本文へ残す。フェンスがある場合と違い
    「呼んだつもりだった」と断定できないため（フェンス付きの壊れは呼ぶ意図が
    明確なので差し戻す、という既存の使い分けを保つ）。
    """
    calls = []
    removals = []
    pos = 0
    while True:
        i = text.find("{", pos)
        if i < 0:
            break
        line_start = text.rfind("\n", 0, i) + 1
        if text[line_start:i].strip():
            pos = i + 1  # 行頭ではない＝文中の引用。触らない
            continue
        try:
            obj, end = _decoder.raw_decode(text, i)
        except (json.JSONDecodeError, ValueError):
            pos = i + 1
            continue
        if isinstance(obj, dict) and obj.get("tool") in KNOWN_TOOL_NAMES:
            calls.append(obj)
            removals.append((i, end))
        pos = end
    if not removals:
        return text, calls
    parts = []
    last = 0
    for start, end in removals:
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts), calls


def execute(soul_id, call, cfg=None):
    if not isinstance(call, dict):
        return {"ok": False, "op": "(不明)", "detail": "ツール呼び出しがオブジェクト形式でない"}
    tool = call.get("tool", "")
    try:
        if tool == "describe_tools":
            return _execute_describe_tools(call.get("tools"))
        if tool in ("list_workspace", "read_doc", "write_doc", "append_doc",
                    "read_pdf", "search_workspace"):
            workspace_dir = (cfg or {}).get("workspace_dir", "").strip()
            if not workspace_dir:
                return {"ok": False, "op": tool,
                        "detail": "作業フォルダが未設定（設定画面から指定してください）"}
            if tool == "list_workspace":
                docs = workspace_mod.list_docs(workspace_dir)
                return {"ok": True, "op": tool,
                        "detail": "\n".join(docs) if docs else "（ファイルなし）"}
            if tool == "read_doc":
                # `or 1` だと0がfalsyのため無検証で1にすり替わる。Noneのときだけ既定にし、
                # 0や負数はworkspace.read_doc側のバリデーションに落とす。
                start_line = call.get("start_line")
                body = workspace_mod.read_doc(
                    workspace_dir, call["path"],
                    1 if start_line is None else start_line, call.get("max_lines"))
                return {"ok": True, "op": tool,
                        "detail": body if body else "（ファイルが存在しないか空）"}
            if tool == "write_doc":
                workspace_mod.write_doc(workspace_dir, call["path"], call["content"])
                return _ok(tool, f"{call['path']} に書き込み")
            if tool == "append_doc":
                workspace_mod.append_doc(workspace_dir, call["path"], call["content"])
                return _ok(tool, f"{call['path']} に追記")
            if tool == "read_pdf":
                mode = call.get("mode") or "image"
                if mode == "text":
                    # read_doc同様、0がfalsyで既定値にすり替わらないようNone判定にする
                    start_page = call.get("start_page")
                    return _execute_read_pdf_text(
                        workspace_dir, call["path"],
                        1 if start_page is None else start_page, call.get("max_pages"))
                if mode != "image":
                    return {"ok": False, "op": tool, "detail": f"未知のmode: {mode}"}
                return _execute_read_pdf(workspace_dir, call["path"], cfg)
            if tool == "search_workspace":
                hits = workspace_mod.search_workspace(workspace_dir, call.get("query", ""))
                return {"ok": True, "op": tool,
                        "detail": "\n".join(hits) if hits else "（ヒットなし）"}
        if tool == "switch_role":
            if cfg is None:
                return {"ok": False, "op": tool, "detail": "設定が渡されていない"}
            name = call.get("name", "")
            role = roles_mod.get_role(name) if name else None
            if not role:
                available = ", ".join(r["name"] for r in roles_mod.list_roles())
                return {"ok": False, "op": tool,
                        "detail": f"ロール『{name}』が見つからない。利用可能なロール: {available}"}
            cfg["active_role"] = name
            config_mod.save_config(cfg)
            return _ok(tool, f"ロールを『{name}』に切り替えた")
        if tool == "create_role":
            name = (call.get("name") or "").strip()
            prompt = (call.get("prompt") or "").strip()
            if not name or not prompt:
                return {"ok": False, "op": tool, "detail": "名前とprompt（やること）は空にできない"}
            if len(name) > 20:
                return {"ok": False, "op": tool, "detail": "ロール名は20字程度までにしてください"}
            if len(prompt) > 500:
                return {"ok": False, "op": tool, "detail": "prompt（やること）は500字程度までにしてください"}
            if roles_mod.get_role(name):
                return {"ok": False, "op": tool,
                        "detail": "同名のロールが既にあります。編集は設定画面から"}
            roles_mod.save_role(name, prompt)
            return _ok(tool, f"ロール『{name}』を作成した")
        if tool == "write_wiki":
            topic = _safe_name(call["topic"])
            path = f"wiki/{topic}.md"
            if call.get("mode") == "replace":
                soul_mod.write_file(soul_id, path, call["content"].rstrip() + "\n")
            else:
                soul_mod.append_file(soul_id, path, call["content"].rstrip() + "\n")
            return _ok(tool, f"{path} に{'置換' if call.get('mode')=='replace' else '追記'}")
        if tool == "save_reading_note":
            source = _safe_name(call["source"])
            path = f"readings/{source}.md"
            if call.get("mode") == "replace":
                soul_mod.write_file(soul_id, path, call["content"].rstrip() + "\n")
            else:
                soul_mod.append_file(soul_id, path, call["content"].rstrip() + "\n")
            return _ok(tool, f"{path} に{'置換' if call.get('mode')=='replace' else '追記'}")
        if tool == "save_sacred":
            day = datetime.date.today().isoformat()
            soul_mod.append_file(soul_id, "sacred/sacred.md",
                                 f"- {call['text'].strip()}（{day}）\n")
            return _ok(tool, "sacred/sacred.md に保存")
        if tool == "update_self_notes":
            soul_mod.update_self_notes(soul_id, call["content"].rstrip() + "\n")
            return _ok(tool, "self_notes.md を更新")
        if tool == "update_user_notes":
            soul_mod.update_user_notes(soul_id, call["content"].rstrip() + "\n")
            return _ok(tool, "user.md を更新")
        if tool == "update_memory_index":
            soul_mod.append_file(soul_id, "MEMORY.md", call["line"].rstrip() + "\n")
            return _ok(tool, "MEMORY.md に索引追加")
        if tool == "read_memory":
            body = soul_mod.read_file(soul_id, call["path"])
            return {"ok": True, "op": tool,
                    "detail": body if body else "（ファイルが存在しないか空）"}
        if tool == "search_memory":
            # recall.hybrid_searchはembedding有効時にRRF融合（字面＋意味）を通す。
            # 無効・失敗時はsearch_mod.search()と同じ結果になる（recall.py参照）。
            hits = recall_mod.hybrid_search(cfg, soul_id, call.get("query", ""))
            return {"ok": True, "op": tool, "detail": _format_search_hits(hits)}
        if tool == "save_lesson":
            day = datetime.date.today().isoformat()
            soul_mod.append_file(soul_id, "lessons.md",
                                 f"- {call['text'].strip()}（{day}）\n")
            return _ok(tool, "lessons.md に保存")
        if tool == "set_reminder":
            soul_mod.add_reminder(soul_id, call["text"], call["due"], call.get("annual", False))
            return _ok(tool, "リマインダを登録")
        if tool == "list_reminders":
            reminders = soul_mod.list_reminders(soul_id)
            return {"ok": True, "op": tool, "detail": _format_reminders(reminders)}
        if tool == "update_tasks":
            soul_mod.write_file(soul_id, "tasks.md", call["content"].rstrip() + "\n")
            return _ok(tool, "tasks.md を更新")
        if tool in ("create_skill", "update_skill"):
            name = (call.get("name") or "").strip()
            if not name:
                return {"ok": False, "op": tool, "detail": "スキル名は空にできない"}
            # 上限チェックはcreate_roleのname≤20字/prompt≤500字と同じ流儀
            # （storage層のValueErrorに頼らず、業務層で先に切り分けて分かりやすい
            # メッセージを返す。soul._skill_pathのValueErrorはこのチェックを
            # 通さない将来の呼び出し元向けの最終防衛線として別途残す）。
            if len(name) > soul_mod.SKILL_NAME_MAX_CHARS:
                return {"ok": False, "op": tool,
                        "detail": f"スキル名は{soul_mod.SKILL_NAME_MAX_CHARS}字以内にしてください"}
            description = (call.get("description") or "").strip()
            content = (call.get("content") or "").strip()
            if len(description) > SKILL_DESCRIPTION_MAX_CHARS:
                return {"ok": False, "op": tool,
                        "detail": f"説明は{SKILL_DESCRIPTION_MAX_CHARS}字以内にしてください"}
            if len(content) > SKILL_CONTENT_MAX_CHARS:
                return {"ok": False, "op": tool,
                        "detail": f"手順本文は{SKILL_CONTENT_MAX_CHARS}字以内にしてください"}
            exists = soul_mod.skill_exists(soul_id, name)
            if tool == "create_skill" and exists:
                return {"ok": False, "op": tool,
                        "detail": "同名のスキルが既にある。磨きたいなら update_skill を使うこと"}
            if tool == "update_skill" and not exists:
                return {"ok": False, "op": tool,
                        "detail": "そのスキルは無い。新しく作るなら create_skill"}
            if tool == "update_skill":
                # サニタイズ収束（例:"a/b"と"a:b"が同じファイルへ収束）による
                # 誤上書きの最終防衛線。ヘッダが壊れていてパースできない場合は
                # 照合しようがないのでスキップして許可する（壊れたファイルを
                # 直す手段が無くなってしまうため。レビュー指摘・2026-07-22追加）。
                header_name = soul_mod.skill_header_name(soul_id, name)
                if header_name is not None and header_name != name:
                    return {"ok": False, "op": tool,
                            "detail": (f"既存スキルのヘッダ名『{header_name}』と指定名『{name}』が"
                                       "一致しない（別スキルを誤って上書きしそうなので中止した）")}
            soul_mod.write_skill(soul_id, name, description, content)
            verb = "作成" if tool == "create_skill" else "更新"
            return _ok(tool, f"スキル『{name}』を{verb}した（旧版はskills_history/へ保存）")
        if tool == "use_skill":
            name = (call.get("name") or "").strip()
            body = soul_mod.read_skill(soul_id, name)
            if not body.strip():
                return {"ok": False, "op": tool, "detail": "そのスキルは無い。list参照"}
            return {"ok": True, "op": tool, "detail": body}
        if tool == "archive_memory":
            path = call["path"]
            dest = soul_mod.archive_file(soul_id, path)
            return _ok(tool, f"{path} を archive/ へ降ろした"
                              "（索引の該当行は次回の索引保守で自動整理される）")
        if tool == "unarchive_memory":
            path = call["path"]
            dest = soul_mod.unarchive_file(soul_id, path)
            return _ok(tool, f"{path} を {dest} へ戻した")
        if tool in ("revise_identity", "revise_speech_style"):
            which = "identity" if tool == "revise_identity" else "speech_style"
            result = soul_mod.revise_identity_file(soul_id, which, call.get("text", ""))
            return {"ok": result["ok"], "op": tool, "detail": result["detail"]}
        if tool == "set_soul_name":
            # nameキー自体が欠落している場合（call.get("name")がNone）はエラーにする。
            # デフォルト値""で埋めてしまうと、LLMがnameを付け忘れた壊れた呼び出しが
            # 「名前を白紙に戻す」操作として誤実行されてしまう（実測済みの欠陥）。
            # 名前を消したい場合は明示的に空文字("name": "")を渡すこと。
            if call.get("name") is None:
                return {"ok": False, "op": tool,
                        "detail": "nameフィールドが必要。名前を消したい場合は明示的に空文字を渡すこと"}
            ok, result = soul_mod.validate_name(call.get("name"))
            if not ok:
                return {"ok": False, "op": tool, "detail": result}
            soul_mod.set_name(soul_id, result)
            label = result if result else "（未設定に戻した）"
            return _ok(tool, f"名前を『{label}』に設定した")
        if tool == "web_search":
            res = websearch.search_text(call.get("query", ""))
            if not res["ok"]:
                return {"ok": False, "op": tool, "detail": res["detail"]}
            detail = websearch.format_results(res["results"]) if res["results"] else "検索結果0件"
            return {"ok": True, "op": tool, "detail": detail}
        return {"ok": False, "op": tool or "(不明)", "detail": "未知のツール"}
    except Exception as e:
        return {"ok": False, "op": tool, "detail": f"失敗: {e}"}


def _format_search_hits(hits):
    if not hits:
        return "（見つからなかった）"
    lines = []
    for h in hits:
        tag = h["source"]
        if h.get("date"):
            tag += f" {h['date']}"
        if h.get("who"):
            tag += f" {h['who']}"
        lines.append(f"[{tag}] {h['snippet']}")
    return "\n".join(lines)


def _format_reminders(reminders):
    if not reminders:
        return "（リマインダなし）"
    lines = []
    for r in reminders:
        tag = "（毎年恒例）" if r.get("annual") else ""
        lines.append(f"- {r['text']}（期限: {r['due']}）{tag}")
    return "\n".join(lines)


def _safe_name(name):
    return re.sub(r"[\\/:*?\"<>|]", "-", name.strip())[:40] or "無題"


def _ok(op, detail):
    return {"ok": True, "op": op, "detail": detail}


def _current_llm_type(cfg):
    """cfgのllm.providerが指す現在のプロバイダentryのtype（"gemini"等）。
    未設定・不明なプロバイダ名なら空文字（gui.pdf_native_supportedと同じ判定方式、
    プライマリのプロバイダのみ見る＝フォールバック先は考慮しない）。"""
    llm_cfg = (cfg or {}).get("llm") or {}
    providers = llm_cfg.get("providers", {})
    return providers.get(llm_cfg.get("provider", ""), {}).get("type", "")


def _execute_read_pdf(workspace_dir, rel_path, cfg):
    """read_pdfツール本体。Geminiプロバイダならbase64のままapplication/pdfとして
    attachments に積む（Geminiがネイティブ全ページを読む）。非Geminiならpdf_render
    （gui.render_pdfと共通の処理）で先頭20ページをJPEG画像化してattachmentsに積む。"""
    raw = workspace_mod.read_pdf_bytes(workspace_dir, rel_path)
    if _current_llm_type(cfg) == "gemini":
        size_mb = round(len(raw) / (1024 * 1024), 2)
        b64 = base64.b64encode(raw).decode("ascii")
        attachment = {"mime": "application/pdf", "b64": b64}
        # ページ数をengine._estimate_attachment_tokens向けに付与する（固定800トークン
        # 見積りだと数十ページのPDFで圧縮判定が実態より楽観的になるため）。開くだけで
        # 数えられる（レンダリングはしない）。壊れたPDF等で数えられなくてもpagesキー
        # 無しのままにするだけで、添付自体・会話は止めない。
        try:
            attachment["pages"] = pdf_render_mod.count_pages(raw)
        except Exception:
            pass
        return {"ok": True, "op": "read_pdf",
                "detail": f"{rel_path} を添付した（Gemini直読み・{size_mb}MB）",
                "attachments": [attachment]}
    result = pdf_render_mod.render_pdf_pages(raw)
    pages = result["pages"]
    total = result["total_pages"]
    if result["truncated"]:
        detail = f"{rel_path} を添付した（全{total}ページ中{len(pages)}ページまで）"
    else:
        detail = f"{rel_path} を添付した（{total}ページ）"
    return {"ok": True, "op": "read_pdf", "detail": detail, "attachments": pages}


def _execute_read_pdf_text(workspace_dir, rel_path, start_page, max_pages):
    """read_pdf mode:"text" 本体。画像化せずpdf_render.extract_textでテキスト層を
    抽出し、detailとして返す（attachmentsは積まない・トークン効率優先の経路）。
    start_page/max_pagesの型・範囲エラーはextract_text側がValueErrorを投げ、
    execute()の共通except Exceptionで{"ok": False}に変換される（他ツールと同じ方針）。"""
    raw = workspace_mod.read_pdf_bytes(workspace_dir, rel_path)
    result = pdf_render_mod.extract_text(raw, start_page=start_page, max_pages=max_pages)
    return {"ok": True, "op": "read_pdf", "detail": result["text"]}
