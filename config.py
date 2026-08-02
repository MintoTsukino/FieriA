"""config.py — fieria_home/config.json の読み書き。

HOME決定の優先順:
  1. FIERIA_HOME 環境変数（既存・テスト隔離用）
  2. frozen実行時（exe化されている）は %LOCALAPPDATA%\\FieriA\\fieria_home
     —— exeの差し替え・フォルダ削除・上書き解凍でユーザーデータ（魂＝会話・記憶）が
     巻き込まれないよう、exeフォルダの外の安定した場所に置く（2026-07-22の
     データ消失事故の恒久対策・第二弾）。
  3. 開発実行時は従来どおりこのファイルの隣の fieria_home/（開発と生活の分離維持）

旧exeユーザー（データが `<exeの隣>\\_internal\\fieria_home` にある）は、新パスに
まだデータが無ければ初回起動時に自動でコピー移行する。旧データは移行成功後も
消さない（安全側）。APIキーはここに書かない（.env）。

migrate_secret_file()は同じ「HOMEの外→中へ」移行の考え方を、.env・OAuthトークン
（xai_oauth.json/openai_codex_oauth.json、env.py/xai_oauth.py/openai_codex_oauth.py
が使う）にも適用するための共通ヘルパー（2026-07-22 秘匿ファイル移設の恒久対策）。"""
import copy
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _migrate_or_use(new_home, old_home):
    """new_homeを優先して使う。new_homeが無く、old_homeにデータがあれば
    new_homeへコピーして移行する。移行に失敗したら（失敗しても起動は必ず
    成功させるため）new_homeの中途半端なコピーを削除し、old_homeを使い続ける。"""
    if os.path.isdir(new_home):
        return new_home
    if not os.path.isdir(old_home):
        return new_home
    # コピー中の電源断・強制終了で中途半端なnew_homeが残ると、次回起動は
    # 「new_home優先」でそれを採用し続けてしまう（中身の検証はしない）。
    # 一時パスへコピーしてから最後にrenameすることで、new_homeは
    # 「完成しているか、存在しないか」のどちらかにしかならないようにする。
    tmp_home = new_home + ".migrating"
    try:
        if os.path.isdir(tmp_home):
            shutil.rmtree(tmp_home, ignore_errors=True)
        shutil.copytree(old_home, tmp_home)
        os.rename(tmp_home, new_home)
        print(f"[MIGRATE] 旧データを新しい保存場所へコピーした: {old_home} -> {new_home}")
        return new_home
    except OSError as e:
        print(f"[MIGRATE] 移行失敗、旧パスを継続使用する: {e}")
        if os.path.isdir(tmp_home):
            shutil.rmtree(tmp_home, ignore_errors=True)
        if os.path.isdir(new_home):
            shutil.rmtree(new_home, ignore_errors=True)
        return old_home


def _resolve_home():
    env_home = os.environ.get("FIERIA_HOME")
    if env_home:
        return env_home
    if not getattr(sys, "frozen", False):
        return os.path.join(HERE, "fieria_home")
    appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    new_home = os.path.join(appdata, "FieriA", "fieria_home")
    old_home = os.path.join(os.path.dirname(sys.executable), "_internal", "fieria_home")
    return _migrate_or_use(new_home, old_home)


HOME = _resolve_home()
CONFIG_PATH = os.path.join(HOME, "config.json")


def migrate_secret_file(old_dir, filename):
    """秘匿ファイル(.env・xai_oauth.json・openai_codex_oauth.json)の一時移行。
    _migrate_or_useと同じ思想: 新位置(HOME直下)を優先して使う。新位置に無く、
    旧位置(old_dir直下、従来はコード隣に置かれていた)にあれば新位置へコピーして
    移行する(moveではなくcopy、旧は消さない——破壊的処理は退避先行の原則)。
    移行に失敗したら(起動を必ず成功させるため)新位置の中途半端なコピーを削除し、
    旧位置を使い続ける。両方に無ければ新位置を返す(これから作られる想定)。
    呼び出し側(env.py/xai_oauth.py/openai_codex_oauth.py)は自分のHEREを渡す。"""
    new_path = os.path.join(HOME, filename)
    old_path = os.path.join(old_dir, filename)
    if os.path.isfile(new_path):
        return new_path
    if not os.path.isfile(old_path):
        return new_path
    # _migrate_or_use同様、コピー中断で不完全なnew_pathが残ると次回起動が
    # 「新位置優先」で壊れた方を恒久採用してしまうため、一時ファイルへ書いて
    # から os.replace でアトミックに確定させる（set_key/save_tokensと同方式）。
    tmp_path = new_path + ".migrating"
    try:
        os.makedirs(HOME, exist_ok=True)
        shutil.copy2(old_path, tmp_path)
        os.replace(tmp_path, new_path)
        print(f"[MIGRATE] 秘匿ファイルを新しい保存場所へコピーした: {old_path} -> {new_path}")
        return new_path
    except OSError as e:
        print(f"[MIGRATE] 秘匿ファイルの移行に失敗、旧位置を継続使用する: {e}")
        for p in (tmp_path, new_path):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return old_path


# NikoVoice(core.py)から輸入した全プロバイダー定義。値は一字も変えない。
DEFAULT_LLM_PROVIDERS = {
    "ollama": {
        "type": "openai_compat",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "gemma3:4b",
        "env_key": "",
    },
    "opencode_go": {
        "type": "openai_compat",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "kimi-k2.7-code",
        "env_key": "OPENCODE_GO_API_KEY",
    },
    "opencode_zen": {
        "type": "openai_compat",
        "base_url": "https://opencode.ai/zen/v1",
        "model": "claude-sonnet-5",
        "env_key": "OPENCODE_ZEN_API_KEY",
    },
    "gemini": {
        "type": "gemini",
        "model": "gemini-2.5-flash",
        "env_key": "GEMINI_API_KEY",
    },
    "deepseek": {
        # 公式APIはbase_urlに/v1を付けない
        "type": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "xai_oauth": {
        # APIキーではなくOAuthログイン。トークンはxai_oauth.pyが管理するのでenv_keyは無し
        "type": "xai_oauth",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "env_key": "",
    },
    "openrouter": {
        # 多数のモデルを1キーで横断できるゲートウェイ。modelは"provider/model"形式
        "type": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-5",
        "env_key": "OPENROUTER_API_KEY",
        "free_only": False,
    },
    "openai_codex_oauth": {
        # ChatGPT Plus/Pro/Team/EnterpriseのサブスクをOAuthログインで使う。env_keyは無し(OAuth)
        "type": "openai_codex_oauth",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "model": "gpt-5.5",
        "env_key": "",
    },
    "groq": {
        # xAIのGrokとは別会社。OpenAI互換でbase_urlに/openai/v1を含む
        "type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
    },
    "custom": {
        "type": "openai_compat",
        "base_url": "",
        "model": "",
        "env_key": "CUSTOM_API_KEY",
    },
}

PROVIDER_LABELS = {
    "ollama": "Ollama（ローカル・キー不要）",
    "opencode_go": "OpenCode Go（$10/月 定額）",
    "opencode_zen": "OpenCode Zen（従量・プレミアム）",
    "gemini": "Gemini（Google AI Studio）",
    "deepseek": "DeepSeek（公式API）",
    "xai_oauth": "Grok（X Premium+/SuperGrokでログイン）",
    "openrouter": "OpenRouter（多数のモデルを1キーで横断）",
    "openai_codex_oauth": "ChatGPT Plus/Pro（サブスク経由・非公式）",
    "groq": "Groq（高速推論・xAIのGrokとは別会社）",
    "custom": "カスタム（OpenAI互換）",
}

DEFAULT_CONFIG = {
    "llm": {
        "provider": "gemini",
        # NikoVoice(llm.py)のDEFAULTS由来のmax_tokens=400は音声通話アプリの遺産で、
        # テキストチャットには短すぎる（thinkingモデルだと内部思考トークンにも食われ本文がさらに縮む）。
        # llm.create_llmはllm_cfg直下の"max_tokens"をそのまま読む（llm.py 505行目付近）。
        "max_tokens": 2000,
        # 空文字=フォールバックなし。設定済みならメインのchat失敗時にこのプロバイダへ
        # 自動切替する（llm.py create_llm参照）。fallback_modelは任意のモデル上書き
        # （空ならフォールバック先プロバイダの既定モデルを使う）。
        "fallback_provider": "",
        "fallback_model": "",
        # FieriA拡張: Web検索。プロバイダ組み込みの検索機能（Gemini: Google Search
        # grounding / Grok: Live Search）を使うかのグローバルトグル。プロバイダごとの
        # providers[name]エントリではなく、この階層（llm直下）に置く——temperature等と
        # 同じく「今使っているプロバイダに対する挙動設定」だからで、対応外プロバイダ
        # （openai_compat等）では単に無視される（llm.create_llm/build_provider参照）。
        # 課金が絡みうるため既定OFF（オプトイン）。
        "web_search": False,
        "providers": copy.deepcopy(DEFAULT_LLM_PROVIDERS),
    },
    "active_soul": None,
    "active_role": None,
    "fact_layer": {"enabled": True, "custom_text": ""},
    # 定期処理のON/OFF（scheduler.py JOBSのidをキーにする）。GUIの「定期処理」カードから
    # 切り替える（gui.py get_scheduled_jobs/set_scheduled_job参照）。
    "scheduled_jobs": {
        "daily_chronicle": True,
        "weekly_digest": True,
        "monthly_digest": True,
        "index_maintenance": True,
        # 人格の核（identity/speech_style）を本人が書き換える機能なのでデフォルトOFF
        # （オプトイン）。既存configへ補完される場合もFalseで入る（load_configの
        # scheduled_jobs補完ロジック参照）。
        "self_reflection": False,
        # LLMがwiki本文を書き換える機能なのでself_reflectionと同じくデフォルトOFF
        # （オプトイン）。既存configへの補完（load_configのscheduled_jobs補完ロジック）
        # もFalseで入る＝バックフィル（過去に遡っての自動ON化）は一切しない。
        "wiki_gardening": False,
    },
    "wrapup_max_tokens": 2000,
    # MEMORY.md（記憶の索引・常時プロンプト注入）がこの文字数を超えたら、定期処理
    # （scheduler.py index_maintenanceジョブ）がLLMに整理・書き直しを依頼する
    # （Claude Code方式：索引が上限超過したらハーネスが書き直しを強制する、のFieriA版）。
    # 0=無効（書き直さない）。wrapup.rewrite_memory_index参照。
    "memory_index_limit_chars": 4000,
    # セッション内圧縮のしきい値（概算トークン数）。0=無制限（圧縮しない・現状どおり
    # モデル窓に任せる）。process_turn冒頭でこれを超えたら古い前半を要約に畳む。
    "context_limit_tokens": 0,
    "theme": "light",
    # 起動時にengine.messages（LLM実会話コンテキスト）へ復元する今日のログの最大件数。
    # 0=復元しない。画面表示の復元（today_log）とは別軸。
    "restore_turns": 50,
    # ユーザーが指定した作業フォルダ（記事執筆等）の絶対パス。空文字=機能OFF。
    # workspace.pyがこの直下の.md/.txtだけを読み書き対象にする（自動注入はしない。
    # 会話中にフィエリア自身がツールで能動的に読む/書く専用）。
    "workspace_dir": "",
    # AIが会話の流れに応じて自分でロールを切り替えてよいか（switch_roleツール）。
    # Claudeのスキル自動発動と同型。Falseにすると手動切替（gui.switch_role）のみになる。
    "auto_role_switch": True,
    # 連想記憶の自動注入（auto-recall）。ユーザー発言のたびに既存のFTS全文検索
    # (search.py)で関連記憶を裏引きし、system_text末尾に注入する（recall.py参照）。
    # max_hits=注入する断片の上限件数。
    "auto_recall": {"enabled": True, "max_hits": 3},
    # AIが会話の中で「同じ手順を繰り返している」と気づいたとき、自分の判断でスキル
    # （手続き記憶・souls/<id>/skills/配下）を作成してよいか。既定False（同意ファースト:
    # 先に提案し、同意をもらってから作成する。auto_role_switchのcreate_roleと同じ
    # 「勝手に作らない」思想）。Trueにすると自分の判断で作成し、作ったら一言報告する。
    # update_skill（既存スキルを磨く）はこの設定に関係なく常に同意不要（履歴が残るため）。
    "skill_auto_create": False,
    # チャット画面隅に常駐するドット絵マスコット（ペット）の表示ON/OFF。
    # 既定True。SOULごとの見た目差し替え（souls/<id>/pet/配下のPNG）はgui.get_pet_skinが
    # 別途担い、このフラグは表示そのものの有無だけを制御する。
    "pet_enabled": True,
    # ペットの大きさ（px・正方形の一辺）。設定画面のスライダーで48〜128の範囲。
    # 実際のクランプ・int化はgui.get_settings/save_settingsが担う（ここは既定値のみ）。
    "pet_size": 64,
    # 組み込みペットのキャラ選択（"konoha"=黒 / "mokora"=白）。SOUL固有pet/が最優先
    "pet_character": "konoha",
    # ペットの表示位置。チャットペイン（ui/index.html #chat-view）右下からの
    # オフセット(px)。ドラッグ終了ごとにgui.save_pet_posが即時保存する
    # （設定画面全体の保存フロー=get_settings/save_settingsとは独立の軽量経路）。
    "pet_pos": {"right": 20, "bottom": 92},
    # FieriA拡張: ストリーミング。AI応答の文字を逐次表示するか（既定True）。
    # OFFなら従来どおり全文完成後に一括表示（gui.send_messageがon_delta=Noneで渡す）。
    "streaming": True,
    # LLM返信完了時の効果音。ファイルは ui/se/ に同梱（値はそのファイル名、""=鳴らさない）
    "reply_se": "se-poko.mp3",
    # FieriA拡張: チャット入力欄にフォーカスした瞬間、Windows IMEを日本語入力
    # （ひらがな）へ自動切替するか（既定True）。pywebview(WebView2)は別プロセスの
    # ため、Web側のime-mode相当は効かず、gui.Bridge.ensure_ime_japaneseがWin32 API
    # で切り替える。ime-mode廃止の代替なので既定ON（他のオプトイン設定とは逆）。
    "ime_auto_ja": True,
    # 読み上げ（AivisSpeech/VOICEVOX互換HTTPエンジン）。既定OFF（エンジン未導入ユーザーが
    # 大半のため）。engine_urlはAivisSpeech既定（VOICEVOXなら:50021）。speakerはスタイルID
    # （/speakersのstyles[].id）、speedは0.5〜2.0（tts.py参照）。
    "tts": {
        "enabled": False,
        "engine_url": "http://127.0.0.1:10101",
        "speaker": 0,
        "speed": 1.0,
    },
}

# LLM返信完了時の効果音。ファイルは ui/se/ に同梱（値はそのファイル名、""=鳴らさない）
REPLY_SE_CHOICES = (
    "", "se-kachi.mp3", "se-poyo.mp3", "se-koto.mp3",
    "se-pofun.mp3", "se-poko.mp3", "se-pichon.mp3",
)

# 着せ替えテーマの既知id。既存light/darkに加え、後続タスクで実装する8案の枠を
# 先に確保しておく（2026-08-02 テーマ登録制への一般化）。gui.save_settingsの
# ホワイトリスト正規化とui/index.htmlのTHEMES配列が参照する共通の正。
THEME_IDS = (
    "light", "dark", "neon", "cyberpunk", "vapor", "toxic",
    "washi", "cli", "crt", "glitch",
)


def load_config():
    os.makedirs(HOME, exist_ok=True)
    if not os.path.isfile(CONFIG_PATH):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(cfg)
        return cfg
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    changed = False
    # 欠けたキーはデフォルトで補完（将来の設定追加に耐える）
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = copy.deepcopy(value)
            changed = True
    for key, value in DEFAULT_CONFIG["fact_layer"].items():
        if key not in cfg["fact_layer"]:
            cfg["fact_layer"][key] = value
            changed = True
    # auto_recallも同様のフィールド単位補完（fact_layerと同じ入れ子補完パターン）。
    cfg.setdefault("auto_recall", {})
    for key, value in DEFAULT_CONFIG["auto_recall"].items():
        if key not in cfg["auto_recall"]:
            cfg["auto_recall"][key] = value
            changed = True
    # pet_posも同様のフィールド単位補完（right/bottomの片方だけ欠けた旧形式にも耐える）。
    cfg.setdefault("pet_pos", {})
    for key, value in DEFAULT_CONFIG["pet_pos"].items():
        if key not in cfg["pet_pos"]:
            cfg["pet_pos"][key] = value
            changed = True
    # ttsも同様のフィールド単位補完（fact_layer/auto_recallと同じ入れ子補完パターン）。
    cfg.setdefault("tts", {})
    for key, value in DEFAULT_CONFIG["tts"].items():
        if key not in cfg["tts"]:
            cfg["tts"][key] = value
            changed = True
    # scheduled_jobsも同様：将来ジョブが増えた時に既存configへ欠けたキーだけ補完する
    # （既存の値は上書きしない。fact_layerと同じ入れ子補完パターン）。
    cfg.setdefault("scheduled_jobs", {})
    for key, value in DEFAULT_CONFIG["scheduled_jobs"].items():
        if key not in cfg["scheduled_jobs"]:
            cfg["scheduled_jobs"][key] = value
            changed = True
    # llm直下のフィールド（provider/max_tokens等。providersは別扱い）が欠けていれば補完。
    # 既存値は上書きしない（providersのフィールド補完と同じ思想）。
    for key, value in DEFAULT_CONFIG["llm"].items():
        if key == "providers":
            continue
        if key not in cfg["llm"]:
            cfg["llm"][key] = copy.deepcopy(value)
            changed = True
    # 新しいLLMプロバイダーがデフォルトに増えた場合の補完。既存entryの値は上書きしない
    providers = cfg["llm"].setdefault("providers", {})
    for name, entry in DEFAULT_LLM_PROVIDERS.items():
        if name not in providers:
            providers[name] = copy.deepcopy(entry)
            changed = True
        else:
            # 既存entry内で欠けたフィールドだけをデフォルトで補完する（既存の値は上書きしない）。
            # 実機事故：旧DEFAULT_CONFIGが生成したgeminiエントリに"type"が無く、
            # llm.pyがopenai_compat扱い→base_url空→送信時に即死した（2026-07-20）。
            entry_dict = providers[name]
            for field, field_value in entry.items():
                if field not in entry_dict:
                    entry_dict[field] = copy.deepcopy(field_value)
                    changed = True
    if changed:
        save_config(cfg)
    return cfg


def save_config(cfg):
    os.makedirs(HOME, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
