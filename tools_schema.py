"""tools_schema.py — ネイティブfunction calling用のツール定義（JSON Schema）。

フェンス方式のツール索引（memory_tools.build_tools_spec）と対になる、
APIの`tools`パラメータへ渡す構造化定義。実行体はどちらの経路でも
memory_tools.execute一本——ここは「呼び出しの受け取り方」の定義だけを持つ。
引数の正は memory_tools の _TOOL_ONELINERS/_TOOL_DETAILS 内の例JSONで、
tests/test_tools_schema.py がソースから機械抽出して照合する（写し間違い防御）。
"""


def _p(props, required):
    return {"type": "object", "properties": props, "required": required}


def _s(desc=""):
    return {"type": "string", "description": desc}


SCHEMAS = {
    "write_wiki": {
        "description": "話題・出来事・事実の知識をwikiへ書く（迷ったらwiki。topicごとに分ける）",
        "parameters": _p({"topic": _s("トピック名"), "content": _s("内容"),
                          "mode": {"type": "string", "enum": ["append", "replace"],
                                    "description": "append=追記(既定)/replace=全置換"}},
                         ["topic", "content"]),
    },
    "save_reading_note": {
        "description": "読んだ外部資料の要点・所感をreadingsへ残す（本人の知識wikiとは別置き）",
        "parameters": _p({"source": _s("資料名（ファイル名や書名）"), "content": _s("要点・所感"),
                          "mode": {"type": "string", "enum": ["append", "replace"]}},
                         ["source", "content"]),
    },
    "save_sacred": {
        "description": "特別な約束・大事な瞬間だけを永久保存する（乱用しない）",
        "parameters": _p({"text": _s("残す言葉")}, ["text"]),
    },
    "update_self_notes": {
        "description": "自己認識メモを全文置換で書き直す",
        "parameters": _p({"content": _s("全文")}, ["content"]),
    },
    "update_user_notes": {
        "description": "相手についての理解を全文置換で書き直す",
        "parameters": _p({"content": _s("全文")}, ["content"]),
    },
    "update_tasks": {
        "description": "気にかけていること（ユーザーのタスク・懸案）を全文置換で書き直す",
        "parameters": _p({"content": _s("全文")}, ["content"]),
    },
    "update_memory_index": {
        "description": "記憶の索引(MEMORY.md)へ1行追加する。新しいwikiを作ったら必ず",
        "parameters": _p({"line": _s("- [名前](wiki/名前.md) — ひとこと")}, ["line"]),
    },
    "save_lesson": {
        "description": "ユーザーとの約束事・行動規則を1行追記する",
        "parameters": _p({"text": _s("約束・規則")}, ["text"]),
    },
    "revise_identity": {
        "description": "自分の核(identity)を全文置換で書き直す（重い操作。本人の意思で）",
        "parameters": _p({"text": _s("新しい全文")}, ["text"]),
    },
    "revise_speech_style": {
        "description": "自分の話し方(speech_style)を全文置換で書き直す",
        "parameters": _p({"text": _s("新しい全文")}, ["text"]),
    },
    "set_soul_name": {
        "description": "自分の名前を設定する（ユーザーと相談して決めてから）",
        "parameters": _p({"name": _s("名前")}, ["name"]),
    },
    "set_reminder": {
        "description": "指定日に思い出すリマインダを登録する",
        "parameters": _p({"text": _s("内容"), "due": _s("YYYY-MM-DD"),
                          "annual": {"type": "boolean", "description": "毎年繰り返すか"}},
                         ["text", "due"]),
    },
    "list_reminders": {
        "description": "登録済みリマインダの一覧を見る",
        "parameters": _p({}, []),
    },
    "read_memory": {
        "description": "自分の記憶ファイル（wiki/日記/sacred等）を読む",
        "parameters": _p({"path": _s("例: wiki/名前.md")}, ["path"]),
    },
    "search_memory": {
        "description": "自分の記憶全体をキーワード・意味で検索する",
        "parameters": _p({"query": _s("検索語")}, ["query"]),
    },
    "archive_memory": {
        "description": "wiki/readingsのページをarchiveへ降ろす（忘れる＝連想から外す。削除ではない）",
        "parameters": _p({"path": _s("wiki/〜.md か readings/〜.md")}, ["path"]),
    },
    "unarchive_memory": {
        "description": "archiveへ降ろしたページを元の場所へ戻す",
        "parameters": _p({"path": _s("archive内のパス")}, ["path"]),
    },
    "create_skill": {
        "description": "繰り返す手順をスキルとして保存する",
        "parameters": _p({"name": _s("スキル名"), "description": _s("1行説明"),
                          "content": _s("手順本文")}, ["name", "description", "content"]),
    },
    "update_skill": {
        "description": "既存スキルを書き直す",
        "parameters": _p({"name": _s("スキル名"), "description": _s("1行説明"),
                          "content": _s("手順本文")}, ["name", "description", "content"]),
    },
    "use_skill": {
        "description": "保存済みスキルの本文を読み込んで使う",
        "parameters": _p({"name": _s("スキル名")}, ["name"]),
    },
    "create_role": {
        "description": "新しいロール（その場のモード）を作る",
        "parameters": _p({"name": _s("ロール名(20字まで)"), "prompt": _s("やること(500字まで)")},
                         ["name", "prompt"]),
    },
    "switch_role": {
        "description": "ロールを切り替える",
        "parameters": _p({"name": _s("ロール名")}, ["name"]),
    },
    "list_workspace": {
        "description": "作業フォルダのファイル一覧を見る",
        "parameters": _p({}, []),
    },
    "read_doc": {
        "description": "作業フォルダの文書を読む",
        "parameters": _p({"path": _s("相対パス"),
                          "start_line": {"type": "integer", "description": "開始行(既定1)"},
                          "max_lines": {"type": "integer", "description": "最大行数"}},
                         ["path"]),
    },
    "write_doc": {
        "description": "作業フォルダへ文書を書く（全置換）",
        "parameters": _p({"path": _s("相対パス"), "content": _s("内容")}, ["path", "content"]),
    },
    "append_doc": {
        "description": "作業フォルダの文書へ追記する",
        "parameters": _p({"path": _s("相対パス"), "content": _s("内容")}, ["path", "content"]),
    },
    "search_workspace": {
        "description": "作業フォルダを全文検索する",
        "parameters": _p({"query": _s("検索語")}, ["query"]),
    },
    "read_pdf": {
        "description": "作業フォルダのPDFを読む",
        "parameters": _p({"path": _s("相対パス"),
                          "mode": {"type": "string", "description": "text=テキスト抽出/省略時=ページ画像"},
                          "start_page": {"type": "integer", "description": "開始ページ"},
                          "max_pages": {"type": "integer", "description": "最大ページ数"}},
                         ["path"]),
    },
    "web_search": {
        "description": "Webを検索する（最新情報・事実確認が本当に必要なときだけ）",
        "parameters": _p({"query": _s("検索語")}, ["query"]),
    },
    "describe_tools": {
        "description": "ツールの詳しい仕様を取りに行く",
        "parameters": _p({"tools": {"type": "array", "items": {"type": "string"},
                                     "description": "ツール名の配列"}}, ["tools"]),
    },
}


def openai_tool_def(name):
    s = SCHEMAS[name]
    return {"type": "function",
            "function": {"name": name, "description": s["description"],
                          "parameters": s["parameters"]}}
