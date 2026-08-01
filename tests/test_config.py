import importlib
import json
import os
import shutil
import sys


def _fresh_config():
    import config
    importlib.reload(config)
    return config


def test_load_creates_default_config():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["active_soul"] is None
    assert cfg["fact_layer"]["enabled"] is True
    assert cfg["fact_layer"]["custom_text"] == ""
    assert cfg["wrapup_max_tokens"] == 2000
    assert "llm" in cfg
    assert os.path.isfile(os.path.join(config.HOME, "config.json"))


def test_save_and_reload_roundtrip():
    config = _fresh_config()
    cfg = config.load_config()
    cfg["active_soul"] = "soul-1"
    cfg["fact_layer"]["enabled"] = False
    config.save_config(cfg)
    cfg2 = config.load_config()
    assert cfg2["active_soul"] == "soul-1"
    assert cfg2["fact_layer"]["enabled"] is False


def test_default_context_limit_tokens_is_zero():
    """0=無制限（圧縮しない）が既定であること。"""
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["context_limit_tokens"] == 0


def test_load_config_backfills_missing_context_limit_tokens_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["context_limit_tokens"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["context_limit_tokens"] == 0
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["context_limit_tokens"] == 0


def test_default_theme_is_light():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["theme"] == "light"


def test_default_pet_enabled_is_true():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["pet_enabled"] is True


def test_load_config_backfills_missing_pet_enabled_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["pet_enabled"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["pet_enabled"] is True
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["pet_enabled"] is True


def test_default_pet_size_is_64():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["pet_size"] == 64


# --- FieriA拡張: ストリーミング表示のON/OFF設定 ---

def test_default_streaming_is_true():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["streaming"] is True


def test_load_config_backfills_missing_streaming_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["streaming"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["streaming"] is True
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["streaming"] is True


def test_load_config_preserves_existing_streaming_false():
    config = _fresh_config()
    cfg = config.load_config()
    cfg["streaming"] = False
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["streaming"] is False


def test_load_config_backfills_missing_pet_size_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["pet_size"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["pet_size"] == 64
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["pet_size"] == 64


def test_default_pet_pos_is_right_20_bottom_92():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["pet_pos"] == {"right": 20, "bottom": 92}


def test_load_config_backfills_missing_pet_pos_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["pet_pos"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["pet_pos"] == {"right": 20, "bottom": 92}
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["pet_pos"] == {"right": 20, "bottom": 92}


def test_load_config_backfills_missing_field_in_existing_pet_pos():
    """auto_recallと同じ入れ子補完パターン：pet_pos自体はあるがbottomだけ欠けている
    旧形式configでも、既存のright値は保持しつつbottomだけ補完される。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["pet_pos"] = {"right": 500}
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["pet_pos"]["right"] == 500
    assert reloaded["pet_pos"]["bottom"] == 92


def test_load_config_preserves_explicit_pet_pos():
    """既に位置がドラッグ保存済みのconfigをloadしても上書きされないこと。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["pet_pos"] = {"right": 300, "bottom": 150}
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["pet_pos"] == {"right": 300, "bottom": 150}


def test_home_respects_env_var():
    config = _fresh_config()
    assert config.HOME == os.environ["FIERIA_HOME"]


def test_default_config_has_all_ten_providers():
    config = _fresh_config()
    expected = {
        "ollama", "opencode_go", "opencode_zen", "gemini", "deepseek",
        "xai_oauth", "openrouter", "openai_codex_oauth", "groq", "custom",
    }
    assert set(config.DEFAULT_CONFIG["llm"]["providers"].keys()) == expected
    assert set(config.DEFAULT_LLM_PROVIDERS.keys()) == expected


def test_load_config_backfills_missing_providers_without_overwriting_existing():
    config = _fresh_config()
    cfg = config.load_config()
    # geminiだけを残し、他は消した上でmodelを書き換えておく（既存値の保護を検証するため）
    cfg["llm"]["providers"] = {
        "gemini": {"type": "gemini", "model": "gemini-custom-model", "env_key": "GEMINI_API_KEY"},
    }
    config.save_config(cfg)

    reloaded = config.load_config()
    providers = reloaded["llm"]["providers"]
    assert set(providers.keys()) == set(config.DEFAULT_LLM_PROVIDERS.keys())
    # 既存entryの値は上書きされない
    assert providers["gemini"]["model"] == "gemini-custom-model"
    # 補完されたプロバイダーはデフォルト値と一致する
    assert providers["ollama"] == config.DEFAULT_LLM_PROVIDERS["ollama"]
    assert providers["deepseek"] == config.DEFAULT_LLM_PROVIDERS["deepseek"]


def test_load_config_backfills_missing_fields_in_existing_provider():
    """実機で発覚したバグの再現：providers.geminiにtypeが無い旧形式のconfigを読み込むと、
    typeだけが補完され、既存のmodel値は上書きされないこと。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["providers"]["gemini"] = {"model": "custom-model", "env_key": "GEMINI_API_KEY"}
    config.save_config(cfg)

    reloaded = config.load_config()
    gemini = reloaded["llm"]["providers"]["gemini"]
    assert gemini["type"] == "gemini"
    assert gemini["model"] == "custom-model"
    assert gemini["env_key"] == "GEMINI_API_KEY"

    # ファイルにも書き戻されていること
    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["llm"]["providers"]["gemini"]["type"] == "gemini"


def test_load_config_leaves_custom_provider_entry_untouched():
    """DEFAULT_LLM_PROVIDERSに無い自作プロバイダーentryは補完処理の対象外で、
    そのまま変化しないこと。"""
    config = _fresh_config()
    cfg = config.load_config()
    custom_entry = {"type": "openai_compat", "base_url": "http://x", "model": "m"}
    cfg["llm"]["providers"]["myai"] = dict(custom_entry)
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["llm"]["providers"]["myai"] == custom_entry


def test_default_config_llm_max_tokens_is_2000():
    """実機バグ：max_tokensがconfigのllm直下に無いとllm.pyのDEFAULTS(400)が使われ、
    音声通話アプリ向けの短さのままテキストチャットの文が途中で切れる（2026-07-20発覚）。"""
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["llm"]["max_tokens"] == 2000


def test_load_config_backfills_missing_llm_max_tokens_without_overwriting_provider():
    """llm直下にmax_tokensが無い旧形式configをloadすると2000が補完され、
    provider等の既存値は保持されること。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"] = {
        "provider": "gemini",
        "providers": cfg["llm"]["providers"],
    }
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["llm"]["max_tokens"] == 2000
    assert reloaded["llm"]["provider"] == "gemini"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["llm"]["max_tokens"] == 2000


def test_default_restore_turns_is_50():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["restore_turns"] == 50


def test_load_config_backfills_missing_restore_turns_without_overwriting_others():
    """restore_turnsを持たない旧形式configをloadすると50が補完され、
    既存の他フィールドは保持されること。"""
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["restore_turns"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["restore_turns"] == 50
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["restore_turns"] == 50


def test_default_scheduled_jobs_includes_monthly_digest_enabled():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["scheduled_jobs"]["monthly_digest"] is True


def test_load_config_backfills_missing_monthly_digest_without_overwriting_others():
    """monthly_digestを持たない旧形式configをloadすると補完され、既存の
    weekly_digest等の値は保持されること（fact_layer等と同じ入れ子補完パターン）。
    FIERIA_HOMEはテストセッション全体で共有される（conftest.py参照）ので、
    他テスト（test_gui.pyの「全ジョブ既定でON」前提など）を汚さないよう
    検証後にweekly_digestをTrueへ戻しておく。"""
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["scheduled_jobs"]["monthly_digest"]
    cfg["scheduled_jobs"]["weekly_digest"] = False
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["scheduled_jobs"]["monthly_digest"] is True
    assert reloaded["scheduled_jobs"]["weekly_digest"] is False

    reloaded["scheduled_jobs"]["weekly_digest"] = True
    config.save_config(reloaded)


def test_default_scheduled_jobs_wiki_gardening_is_opt_in_off():
    """wiki_gardeningはLLMがwiki本文を書き換える機能なのでself_reflectionと同じく
    デフォルトOFF（オプトイン）。"""
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["scheduled_jobs"]["wiki_gardening"] is False


def test_load_config_backfills_missing_wiki_gardening_as_false_without_overwriting_others():
    """wiki_gardeningを持たない旧形式configをloadすると補完され、その値はFalse
    （バックフィルで既存ユーザーが意図せずONにならない）。既存のweekly_digest等の
    値は保持されること（monthly_digestの同種テストと同じパターン）。"""
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["scheduled_jobs"]["wiki_gardening"]
    cfg["scheduled_jobs"]["weekly_digest"] = False
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["scheduled_jobs"]["wiki_gardening"] is False
    assert reloaded["scheduled_jobs"]["weekly_digest"] is False

    reloaded["scheduled_jobs"]["weekly_digest"] = True
    config.save_config(reloaded)


def test_default_workspace_dir_is_empty():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["workspace_dir"] == ""


def test_load_config_backfills_missing_workspace_dir_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["workspace_dir"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["workspace_dir"] == ""
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["workspace_dir"] == ""


def test_default_llm_config_has_empty_fallback_fields():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["llm"]["fallback_provider"] == ""
    assert cfg["llm"]["fallback_model"] == ""


def test_load_config_backfills_missing_fallback_fields_without_overwriting_others():
    """fallback_provider/fallback_modelを持たない旧形式configをloadすると
    空文字が補完され、既存の他フィールド（provider等）は保持されること。"""
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["llm"]["fallback_provider"]
    del cfg["llm"]["fallback_model"]
    cfg["llm"]["provider"] = "gemini"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["llm"]["fallback_provider"] == ""
    assert reloaded["llm"]["fallback_model"] == ""
    assert reloaded["llm"]["provider"] == "gemini"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["llm"]["fallback_provider"] == ""
    assert on_disk["llm"]["fallback_model"] == ""


def test_load_config_backfill_preserves_existing_fallback_value():
    """既にfallback_providerが設定済みのconfigをloadしても上書きされないこと。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["fallback_provider"] = "deepseek"
    cfg["llm"]["fallback_model"] = "custom-fallback-model"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["llm"]["fallback_provider"] == "deepseek"
    assert reloaded["llm"]["fallback_model"] == "custom-fallback-model"


def test_default_llm_config_has_web_search_disabled():
    """FieriA拡張: Web検索。課金が絡むため既定OFF（オプトイン）。"""
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["llm"]["web_search"] is False


def test_load_config_backfills_missing_web_search_without_overwriting_others():
    """web_searchを持たない旧形式configをloadするとFalseが補完され、
    既存の他フィールド（provider等）は保持されること。"""
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["llm"]["web_search"]
    cfg["llm"]["provider"] = "gemini"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["llm"]["web_search"] is False
    assert reloaded["llm"]["provider"] == "gemini"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["llm"]["web_search"] is False


def test_load_config_backfill_preserves_existing_web_search_value():
    """既にweb_searchがTrueで設定済みのconfigをloadしても上書きされないこと。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["web_search"] = True
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["llm"]["web_search"] is True


def test_default_llm_config_builds_gemini_provider():
    """DEFAULT_CONFIGのllm設定は type: gemini が無いと build_provider が
    openai_compat とみなして OpenAICompatLLM(base_url="") を作ってしまい、
    実際に送信すると「unknown url type」で即死する（2026-07-20発覚のCritical）。"""
    import llm
    config = _fresh_config()
    cfg = config.load_config()
    created = llm.create_llm(cfg["llm"], {})
    # フォールバックが設定されていなければそのまま、設定されていればFallbackLLM.primaryを見る
    target = created.primary if isinstance(created, llm.FallbackLLM) else created
    assert isinstance(target, llm.GeminiLLM)


def test_create_llm_without_fallback_provider_returns_plain_provider():
    """fallback_providerが空文字なら、FallbackLLMでラップされないこと。
    config.jsonは同一FIERIA_HOME内の他テストと共有されるため、既定値に頼らず
    明示的に空文字へ設定してから検証する（他テストの実行順に依存しないため）。"""
    import llm
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["provider"] = "gemini"
    cfg["llm"]["fallback_provider"] = ""
    config.save_config(cfg)
    reloaded = config.load_config()
    created = llm.create_llm(reloaded["llm"], {})
    assert not isinstance(created, llm.FallbackLLM)
    assert isinstance(created, llm.GeminiLLM)


def test_create_llm_with_fallback_provider_returns_fallback_llm():
    """fallback_providerを設定したcfgでcreate_llmすると、FallbackLLMでラップされ、
    primary/fallbackがそれぞれ正しいプロバイダー型で組み立てられること。
    ネットワーク呼び出しは発生しない（プロバイダーの生成のみ）。"""
    import llm
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["provider"] = "gemini"
    cfg["llm"]["fallback_provider"] = "deepseek"
    created = llm.create_llm(cfg["llm"], {})
    assert isinstance(created, llm.FallbackLLM)
    assert isinstance(created.primary, llm.GeminiLLM)
    assert isinstance(created.fallback, llm.OpenAICompatLLM)


def test_create_llm_with_fallback_model_override():
    """fallback_modelを指定した場合、フォールバック先プロバイダのmodelがそれで
    上書きされること（本来のprovidersエントリのmodelは変更しない）。"""
    import llm
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["provider"] = "gemini"
    cfg["llm"]["fallback_provider"] = "deepseek"
    cfg["llm"]["fallback_model"] = "custom-fallback-model"
    original_deepseek_model = cfg["llm"]["providers"]["deepseek"]["model"]
    created = llm.create_llm(cfg["llm"], {})
    assert created.fallback.model == "custom-fallback-model"
    assert cfg["llm"]["providers"]["deepseek"]["model"] == original_deepseek_model


def test_default_auto_role_switch_is_true():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["auto_role_switch"] is True


def test_load_config_backfills_missing_auto_role_switch_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["auto_role_switch"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["auto_role_switch"] is True
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["auto_role_switch"] is True


def test_load_config_preserves_explicit_auto_role_switch_false():
    config = _fresh_config()
    cfg = config.load_config()
    cfg["auto_role_switch"] = False
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["auto_role_switch"] is False


def test_default_skill_auto_create_is_false():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["skill_auto_create"] is False


def test_load_config_backfills_missing_skill_auto_create_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["skill_auto_create"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["skill_auto_create"] is False
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["skill_auto_create"] is False


def test_load_config_preserves_explicit_skill_auto_create_true():
    config = _fresh_config()
    cfg = config.load_config()
    cfg["skill_auto_create"] = True
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["skill_auto_create"] is True


# --- migrate_secret_file: .env/xai_oauth.json/openai_codex_oauth.jsonの
# 「コード隣→HOME直下」移行ロジック（2026-07-22 秘匿ファイル移設）。
# ダミーのファイル名・ダミー値のみ使用し、実.env/実oauthトークンには一切触れない。


def test_migrate_secret_file_copies_from_old_when_new_absent(tmp_path):
    """旧位置にのみ秘匿ファイルがあれば、新位置(HOME)へコピーされ、
    旧位置のファイルは削除されず残ること（moveではなくcopy）。"""
    config = _fresh_config()
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_file = old_dir / "dummy-a.secret"
    old_file.write_text("dummy-value-a", encoding="utf-8")
    new_path = os.path.join(config.HOME, "dummy-a.secret")
    try:
        result = config.migrate_secret_file(str(old_dir), "dummy-a.secret")

        assert result == new_path
        assert os.path.isfile(new_path)
        with open(new_path, "r", encoding="utf-8") as f:
            assert f.read() == "dummy-value-a"
        assert old_file.is_file()  # 旧側は消えない
    finally:
        if os.path.isfile(new_path):
            os.remove(new_path)


def test_migrate_secret_file_prefers_new_and_does_not_overwrite(tmp_path):
    """新位置に既にファイルがあれば、旧位置にもあっても新側の内容を優先し
    上書きしないこと。"""
    config = _fresh_config()
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "dummy-b.secret").write_text("old-value-b", encoding="utf-8")
    new_path = os.path.join(config.HOME, "dummy-b.secret")
    with open(new_path, "w", encoding="utf-8") as f:
        f.write("new-value-b")
    try:
        result = config.migrate_secret_file(str(old_dir), "dummy-b.secret")

        assert result == new_path
        with open(new_path, "r", encoding="utf-8") as f:
            assert f.read() == "new-value-b"
    finally:
        os.remove(new_path)


def test_migrate_secret_file_noop_when_neither_exists(tmp_path):
    """どちらにも無ければ何もせず、新位置のパスだけを返すこと（これから作られる想定）。"""
    config = _fresh_config()
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_path = os.path.join(config.HOME, "dummy-c.secret")

    result = config.migrate_secret_file(str(old_dir), "dummy-c.secret")

    assert result == new_path
    assert not os.path.isfile(new_path)


def test_migrate_secret_file_falls_back_to_old_path_on_copy_failure(tmp_path, monkeypatch):
    """コピー中に例外が起きたら、新位置の中途半端なコピーを削除し、旧位置の
    パスを返して使い続けること（起動を必ず成功させる原則。config.HOMEの
    _migrate_or_useと同じ思想）。"""
    config = _fresh_config()
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_file = old_dir / "dummy-d.secret"
    old_file.write_text("dummy-value-d", encoding="utf-8")
    new_path = os.path.join(config.HOME, "dummy-d.secret")

    def _boom(_src, _dst, *_a, **_kw):
        raise OSError("simulated copy failure")
    monkeypatch.setattr(shutil, "copy2", _boom)

    result = config.migrate_secret_file(str(old_dir), "dummy-d.secret")

    assert result == str(old_file)
    assert not os.path.isfile(new_path)


def test_migrate_secret_file_is_atomic_and_cleans_stale_tmp(tmp_path):
    """移行はtmp+os.replace方式（コピー中断で不完全なnew_pathが恒久採用される
    のを防ぐ）。前回中断の残骸（.migrating）が転がっていても、完全な内容で
    移行が成功し、残骸が残らないこと。"""
    config = _fresh_config()
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "dummy-e.secret").write_text("full-value-e", encoding="utf-8")
    new_path = os.path.join(config.HOME, "dummy-e.secret")
    stale_tmp = new_path + ".migrating"
    with open(stale_tmp, "w", encoding="utf-8") as f:
        f.write("TRUNC")  # 前回中断で残った不完全な一時ファイルを模擬
    try:
        result = config.migrate_secret_file(str(old_dir), "dummy-e.secret")

        assert result == new_path
        with open(new_path, "r", encoding="utf-8") as f:
            assert f.read() == "full-value-e"
        assert not os.path.isfile(stale_tmp)
    finally:
        for p in (new_path, stale_tmp):
            if os.path.isfile(p):
                os.remove(p)


def test_migrate_or_use_is_atomic_and_cleans_stale_tmp(tmp_path):
    """HOMEディレクトリ自体の移行(_migrate_or_use)も同様にtmp+rename方式で、
    前回中断の残骸ディレクトリ（.migrating）があっても成功し、残骸が残らないこと。"""
    config = _fresh_config()
    old_home = tmp_path / "old_home"
    old_home.mkdir()
    (old_home / "config.json").write_text("{}", encoding="utf-8")
    new_home = tmp_path / "new_home"
    stale_tmp = tmp_path / "new_home.migrating"
    stale_tmp.mkdir()
    (stale_tmp / "broken.txt").write_text("TRUNC", encoding="utf-8")

    result = config._migrate_or_use(str(new_home), str(old_home))

    assert result == str(new_home)
    assert (new_home / "config.json").is_file()
    assert not stale_tmp.exists()
    assert (old_home / "config.json").is_file()  # 旧側は消えない


def test_default_auto_recall_is_enabled_with_max_hits_three():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["auto_recall"]["enabled"] is True
    assert cfg["auto_recall"]["max_hits"] == 3


def test_load_config_backfills_missing_auto_recall_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["auto_recall"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["auto_recall"] == {"enabled": True, "max_hits": 3}
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["auto_recall"] == {"enabled": True, "max_hits": 3}


def test_load_config_backfills_missing_field_in_existing_auto_recall():
    """fact_layerと同じ入れ子補完パターン：auto_recall自体はあるがmax_hitsだけ
    欠けている旧形式configでも、既存のenabled値は保持しつつmax_hitsだけ補完される。"""
    config = _fresh_config()
    cfg = config.load_config()
    cfg["auto_recall"] = {"enabled": False}
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["auto_recall"]["enabled"] is False
    assert reloaded["auto_recall"]["max_hits"] == 3


def test_create_llm_ignores_fallback_provider_same_as_main():
    """fallback_providerがメインと同名の場合、llm.py側で無視されFallbackLLMに
    ラップされないこと（create_llmのfb_name != name判定）。"""
    import llm
    config = _fresh_config()
    cfg = config.load_config()
    cfg["llm"]["provider"] = "gemini"
    cfg["llm"]["fallback_provider"] = "gemini"
    created = llm.create_llm(cfg["llm"], {})
    assert not isinstance(created, llm.FallbackLLM)
    assert isinstance(created, llm.GeminiLLM)


# --- HOME決定ロジック（exeフォルダの外へ・2026-07-22の恒久対策第二弾） -----------------
#
# conftest.py がセッション全体でFIERIA_HOME環境変数を設定しているため、以下のテストは
# 各テスト内で monkeypatch.delenv/setattr により一時的にそれを外し、frozen実行を模擬する。
# 他のテストファイル（test_gui.py等）は `import config as config_mod` を毎回reloadせずに
# 使うため、config.HOMEがこのテストの模擬状態のまま残ると後続テストを汚染する。
# そのため各テストの finally で必ず monkeypatch.undo() → 再reload して通常状態
# （FIERIA_HOME環境変数ベース）に戻す。


def test_home_uses_localappdata_when_frozen_and_no_legacy_data(monkeypatch, tmp_path):
    """frozen実行時、FIERIA_HOME未設定・旧データも無ければ
    %LOCALAPPDATA%\\FieriA\\fieria_home を使うこと。"""
    monkeypatch.delenv("FIERIA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe_dir = tmp_path / "exe_dir"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(exe_dir / "FieriA.exe"))
    appdata = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    try:
        config = _fresh_config()
        expected = os.path.join(str(appdata), "FieriA", "fieria_home")
        assert config.HOME == expected
    finally:
        monkeypatch.undo()
        _fresh_config()


def test_home_migrates_legacy_internal_data_when_new_path_absent(monkeypatch, tmp_path):
    """旧exe（<exeの隣>\\_internal\\fieria_home）にデータがあり、新パスにまだ無い場合は
    新パスへコピー移行し、旧データはそのまま残ること（安全側・削除しない）。"""
    monkeypatch.delenv("FIERIA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe_dir = tmp_path / "exe_dir"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(exe_dir / "FieriA.exe"))
    appdata = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    old_home = exe_dir / "_internal" / "fieria_home"
    old_home.mkdir(parents=True)
    (old_home / "config.json").write_text('{"marker": "legacy-data"}', encoding="utf-8")

    try:
        config = _fresh_config()
        expected_new = os.path.join(str(appdata), "FieriA", "fieria_home")
        assert config.HOME == expected_new
        with open(os.path.join(expected_new, "config.json"), encoding="utf-8") as f:
            assert json.load(f)["marker"] == "legacy-data"
        # 旧データは消されず残っている（安全側）
        assert old_home.is_dir()
        assert (old_home / "config.json").is_file()
    finally:
        monkeypatch.undo()
        _fresh_config()


def test_home_does_not_migrate_when_new_path_already_has_data(monkeypatch, tmp_path):
    """新パスに既にfieria_homeがあれば、旧データがあっても上書き移行しないこと。"""
    monkeypatch.delenv("FIERIA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe_dir = tmp_path / "exe_dir"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(exe_dir / "FieriA.exe"))
    appdata = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    old_home = exe_dir / "_internal" / "fieria_home"
    old_home.mkdir(parents=True)
    (old_home / "config.json").write_text('{"marker": "legacy-data"}', encoding="utf-8")

    new_home = appdata / "FieriA" / "fieria_home"
    new_home.mkdir(parents=True)
    (new_home / "config.json").write_text('{"marker": "current-data"}', encoding="utf-8")

    try:
        config = _fresh_config()
        assert config.HOME == str(new_home)
        with open(os.path.join(str(new_home), "config.json"), encoding="utf-8") as f:
            assert json.load(f)["marker"] == "current-data"
    finally:
        monkeypatch.undo()
        _fresh_config()


def test_home_falls_back_to_legacy_path_when_migration_copy_fails(monkeypatch, tmp_path):
    """コピー中に例外が起きたら、新パスの中途半端なコピーを削除し、旧パスを
    継続使用すること（起動を絶対に成功させる原則。会話を壊さない原則の起動版）。"""
    monkeypatch.delenv("FIERIA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe_dir = tmp_path / "exe_dir"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(exe_dir / "FieriA.exe"))
    appdata = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    old_home = exe_dir / "_internal" / "fieria_home"
    old_home.mkdir(parents=True)
    (old_home / "config.json").write_text('{"marker": "legacy-data"}', encoding="utf-8")

    def _boom(_src, dst, *_a, **_kw):
        os.makedirs(dst, exist_ok=True)
        with open(os.path.join(dst, "partial.txt"), "w", encoding="utf-8") as f:
            f.write("partial")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(shutil, "copytree", _boom)

    try:
        config = _fresh_config()
        expected_new = os.path.join(str(appdata), "FieriA", "fieria_home")
        assert config.HOME == str(old_home)
        assert not os.path.isdir(expected_new)
    finally:
        monkeypatch.undo()
        _fresh_config()


def test_default_pet_character_is_konoha():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["pet_character"] == "konoha"


def test_load_config_backfills_missing_pet_character():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["pet_character"]
    config.save_config(cfg)
    reloaded = config.load_config()
    assert reloaded["pet_character"] == "konoha"


def test_default_reply_se_is_se_poko():
    config = _fresh_config()
    cfg = config.load_config()
    assert cfg["reply_se"] == "se-poko.mp3"


def test_load_config_backfills_missing_reply_se_without_overwriting_others():
    config = _fresh_config()
    cfg = config.load_config()
    del cfg["reply_se"]
    cfg["active_soul"] = "soul-1"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["reply_se"] == "se-poko.mp3"
    assert reloaded["active_soul"] == "soul-1"

    with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["reply_se"] == "se-poko.mp3"


def test_load_config_preserves_explicit_reply_se():
    config = _fresh_config()
    cfg = config.load_config()
    cfg["reply_se"] = "se-kachi.mp3"
    config.save_config(cfg)

    reloaded = config.load_config()
    assert reloaded["reply_se"] == "se-kachi.mp3"


def test_reply_se_choices_contains_all_six_files_and_empty():
    config = _fresh_config()
    assert config.REPLY_SE_CHOICES == (
        "", "se-kachi.mp3", "se-poyo.mp3", "se-koto.mp3",
        "se-pofun.mp3", "se-poko.mp3", "se-pichon.mp3",
    )
