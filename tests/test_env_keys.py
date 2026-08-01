"""env.py の set_key/load_env/delete_key 往復テスト。

ENV_PATHは2026-07-22以降config.HOME直下（FIERIA_HOME隔離下）に置かれるが、
env.py自体はFIERIA_TESTING時に実.envの存在確認すら行わない（env.py参照）。
念のためこのファイルのテストも必ずpath=引数で一時ファイルを渡し、実.envには
一切触れない。
"""
import json
import os
import tempfile

import env
import gui


def _temp_env_path():
    fd, path = tempfile.mkstemp(prefix="fieria-env-test-", suffix=".env")
    os.close(fd)
    os.remove(path)  # set_key/load_envはファイル不在からでも動くことを確認したい
    return path


def test_set_key_then_load_env_roundtrip():
    path = _temp_env_path()
    try:
        env.set_key("GEMINI_API_KEY", "abc123", path=path)
        loaded = env.load_env(path=path)
        assert loaded["GEMINI_API_KEY"] == "abc123"
    finally:
        if os.path.isfile(path):
            os.remove(path)


def test_set_key_overwrites_existing_value_and_keeps_other_lines():
    path = _temp_env_path()
    try:
        env.set_key("GEMINI_API_KEY", "first", path=path)
        env.set_key("OTHER_KEY", "keep-me", path=path)
        env.set_key("GEMINI_API_KEY", "second", path=path)
        loaded = env.load_env(path=path)
        assert loaded["GEMINI_API_KEY"] == "second"
        assert loaded["OTHER_KEY"] == "keep-me"
    finally:
        if os.path.isfile(path):
            os.remove(path)


def test_delete_key_removes_only_target_key():
    path = _temp_env_path()
    try:
        env.set_key("GEMINI_API_KEY", "abc123", path=path)
        env.set_key("OTHER_KEY", "keep-me", path=path)
        env.delete_key("GEMINI_API_KEY", path=path)
        loaded = env.load_env(path=path)
        assert "GEMINI_API_KEY" not in loaded
        assert loaded["OTHER_KEY"] == "keep-me"
    finally:
        if os.path.isfile(path):
            os.remove(path)


def test_sanitize_llm_cfg_strips_api_key_from_providers():
    """config内にproviders.*.api_keyの直書きがあっても除去されること
    （env.resolve_api_keyは直書きapi_keyを許す仕様のため、GUI外部への露出経路は
    ここで塞ぐ）。"""
    llm_cfg = {
        "provider": "gemini",
        "providers": {
            "gemini": {"type": "gemini", "model": "gemini-2.5-flash", "api_key": "sk-secret-test"},
        },
    }
    sanitized = gui.sanitize_llm_cfg(llm_cfg)
    assert "api_key" not in sanitized["providers"]["gemini"]
    # 元のcfgは変更されない
    assert llm_cfg["providers"]["gemini"]["api_key"] == "sk-secret-test"


def test_get_settings_never_exposes_raw_api_key():
    """Bridge.get_settings()がconfig内のapi_key直書きをJS側へ漏らさないこと。
    pywebview起動なしでBridgeをインスタンス化できる設計（gui.pyの前提）を利用する。
    FIERIA_HOMEはconftest.pyで一時ディレクトリに隔離済み。実.envには触れない。"""
    bridge = gui.Bridge()
    bridge._cfg["llm"]["providers"].setdefault("gemini", {})["api_key"] = "sk-secret-test"

    settings = bridge.get_settings()

    assert "sk-secret-test" not in json.dumps(settings)
    assert "api_key" not in settings["llm"]["providers"]["gemini"]


def test_get_settings_includes_wrapup_max_tokens_default():
    bridge = gui.Bridge()
    settings = bridge.get_settings()
    assert settings["wrapup_max_tokens"] == 2000


def test_save_settings_persists_wrapup_max_tokens():
    bridge = gui.Bridge()
    result = bridge.save_settings({"wrapup_max_tokens": 500})
    assert result["wrapup_max_tokens"] == 500
    assert bridge._cfg["wrapup_max_tokens"] == 500
    # 再読込しても保存されていること（config.jsonへ書き戻っている）
    reloaded = gui.Bridge()
    assert reloaded._cfg["wrapup_max_tokens"] == 500


def test_save_settings_wrapup_max_tokens_zero_means_unlimited():
    """設定GUIの「モデルに任せる」トグルONはwrapup_max_tokens=0として保存される。"""
    bridge = gui.Bridge()
    result = bridge.save_settings({"wrapup_max_tokens": 0})
    assert result["wrapup_max_tokens"] == 0
    assert bridge._cfg["wrapup_max_tokens"] == 0


def test_save_settings_without_wrapup_max_tokens_leaves_existing_value_unchanged():
    """会話側(llm)だけを保存するとき、wrapup_max_tokensは触らないこと
    （save_provider-btnがllmのみのpayloadを送る既存経路の回帰防止）。"""
    bridge = gui.Bridge()
    bridge.save_settings({"wrapup_max_tokens": 777})
    result = bridge.save_settings({"theme": "dark"})
    assert result["wrapup_max_tokens"] == 777


def test_get_settings_includes_context_limit_tokens_default():
    bridge = gui.Bridge()
    settings = bridge.get_settings()
    assert settings["context_limit_tokens"] == 0


def test_get_settings_includes_default_fact_text_matching_prompt_module():
    """設定GUIが標準の事実層文言をブラックボックスにせず表示できるよう、
    get_settings()にprompt.DEFAULT_FACT_TEXTをそのまま含める（単一ソース維持）。"""
    import prompt
    bridge = gui.Bridge()
    settings = bridge.get_settings()
    assert settings["default_fact_text"] == prompt.DEFAULT_FACT_TEXT


def test_save_settings_persists_context_limit_tokens():
    bridge = gui.Bridge()
    result = bridge.save_settings({"context_limit_tokens": 5000})
    assert result["context_limit_tokens"] == 5000
    assert bridge._cfg["context_limit_tokens"] == 5000
    reloaded = gui.Bridge()
    assert reloaded._cfg["context_limit_tokens"] == 5000


def test_save_settings_without_context_limit_tokens_leaves_existing_value_unchanged():
    bridge = gui.Bridge()
    bridge.save_settings({"context_limit_tokens": 4321})
    result = bridge.save_settings({"theme": "dark"})
    assert result["context_limit_tokens"] == 4321


def test_get_settings_includes_workspace_dir_default_empty():
    bridge = gui.Bridge()
    settings = bridge.get_settings()
    assert settings["workspace_dir"] == ""


def test_save_settings_persists_workspace_dir(tmp_path):
    bridge = gui.Bridge()
    result = bridge.save_settings({"workspace_dir": str(tmp_path)})
    assert result["workspace_dir"] == str(tmp_path)
    assert bridge._cfg["workspace_dir"] == str(tmp_path)
    reloaded = gui.Bridge()
    assert reloaded._cfg["workspace_dir"] == str(tmp_path)


def test_save_settings_without_workspace_dir_leaves_existing_value_unchanged(tmp_path):
    bridge = gui.Bridge()
    bridge.save_settings({"workspace_dir": str(tmp_path)})
    result = bridge.save_settings({"theme": "dark"})
    assert result["workspace_dir"] == str(tmp_path)


def test_save_settings_casts_auto_recall_fields_to_expected_types():
    """auto_role_switchと同様、JS側から文字列/不正値で来てもbool/intへ確定させる
    （max_hitsのint変換に失敗したら既定の3にフォールバック）。"""
    bridge = gui.Bridge()
    result = bridge.save_settings({"auto_recall": {"enabled": "", "max_hits": "5"}})
    assert result["auto_recall"] == {"enabled": False, "max_hits": 5}

    result = bridge.save_settings({"auto_recall": {"enabled": 1, "max_hits": "not-a-number"}})
    assert result["auto_recall"] == {"enabled": True, "max_hits": 3}


def test_list_models_reports_login_required_when_xai_oauth_not_logged_in(monkeypatch):
    """xai_oauth未ログイン時、list_models()は静的フォールバック一覧で
    ok:Trueを返さず、未ログインである旨のエラーを返すこと。"""
    import xai_oauth

    monkeypatch.setattr(xai_oauth, "is_logged_in", lambda: False)
    bridge = gui.Bridge()

    result = bridge.list_models("xai_oauth")

    assert result["ok"] is False
    assert "未ログイン" in result["error"]


def test_list_models_reports_login_required_when_codex_oauth_not_logged_in(monkeypatch):
    """openai_codex_oauth未ログイン時も同様に、静的フォールバック一覧で
    ok:Trueを返さず、未ログインである旨のエラーを返すこと。"""
    import openai_codex_oauth

    monkeypatch.setattr(openai_codex_oauth, "is_logged_in", lambda: False)
    bridge = gui.Bridge()

    result = bridge.list_models("openai_codex_oauth")

    assert result["ok"] is False
    assert "未ログイン" in result["error"]


# --- 秘匿ファイルの置き場所（2026-07-22移設）---
# ENV_PATH/TOKEN_PATHがconfig.HOME直下を指すこと。conftest.pyがセッション開始時に
# FIERIA_TESTINGを設定するため、これらのモジュールは実.env/実トークンファイルの
# 存在確認すら行わずconfig.HOME直下のパスを組み立てるだけになっている（各モジュールの
# `if os.environ.get("FIERIA_TESTING")`分岐を参照）。


def test_env_path_is_under_config_home():
    import config

    assert env.ENV_PATH == os.path.join(config.HOME, ".env")


def test_xai_oauth_token_path_is_under_config_home():
    import config
    import xai_oauth

    assert xai_oauth.TOKEN_PATH == os.path.join(config.HOME, "xai_oauth.json")


def test_openai_codex_oauth_token_path_is_under_config_home():
    import config
    import openai_codex_oauth

    assert openai_codex_oauth.TOKEN_PATH == os.path.join(config.HOME, "openai_codex_oauth.json")
