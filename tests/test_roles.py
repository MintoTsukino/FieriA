def test_default_roles_created_on_first_list():
    import roles
    names = [r["name"] for r in roles.list_roles()]
    assert "会話" in names and "リサーチ" in names and "編集者" in names


def test_secretary_role_is_default():
    """秘書ロール（気にかけリスト・秘書層の担当）がDEFAULT_ROLESに含まれ、
    人格でなく職務内容だけを記述していること。"""
    import roles
    r = roles.get_role("秘書")
    assert r is not None
    assert "update_tasks" in r["prompt"]


def test_backfill_adds_missing_default_role_to_existing_roles_json(tmp_path, monkeypatch):
    """秘書ロール導入前に作られたroles.jsonを模して、_loadが自動で秘書を補うこと。
    既存のロール（カスタムロール含む）はそのまま残ること。"""
    import importlib
    import json
    import roles

    old_path = roles.ROLES_PATH
    fake_path = str(tmp_path / "roles.json")
    monkeypatch.setattr(roles, "ROLES_PATH", fake_path)
    try:
        legacy = [
            {"name": "会話", "prompt": "旧: 雑談モード"},
            {"name": "リサーチ", "prompt": "旧: 調べ物"},
            {"name": "編集者", "prompt": "旧: 編集者"},
            {"name": "自作ロール", "prompt": "自分で作ったやつ"},
        ]
        with open(fake_path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)

        names = [r["name"] for r in roles.list_roles()]
        assert "秘書" in names
        assert "自作ロール" in names
        # 既存ロールは上書きされない
        assert roles.get_role("会話")["prompt"] == "旧: 雑談モード"

        # ファイルへも書き戻されていること（次回起動時も揃っている）
        with open(fake_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert "秘書" in [r["name"] for r in saved]
    finally:
        monkeypatch.setattr(roles, "ROLES_PATH", old_path)


def test_backfill_does_not_touch_edited_default_role(tmp_path, monkeypatch):
    """ユーザーが編集済みの同名デフォルトロール（例: 編集者をカスタム文言に変更済み）は
    バックフィルで上書きされないこと。"""
    import json
    import roles

    old_path = roles.ROLES_PATH
    fake_path = str(tmp_path / "roles.json")
    monkeypatch.setattr(roles, "ROLES_PATH", fake_path)
    try:
        legacy = [{"name": "編集者", "prompt": "ユーザーが書き換えた独自の編集者プロンプト"}]
        with open(fake_path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)

        roles.list_roles()
        assert roles.get_role("編集者")["prompt"] == "ユーザーが書き換えた独自の編集者プロンプト"
    finally:
        monkeypatch.setattr(roles, "ROLES_PATH", old_path)


def test_save_and_get_role():
    import roles
    roles.save_role("小説プロット", "今回は小説のプロット整理を手伝う。")
    r = roles.get_role("小説プロット")
    assert r["prompt"].startswith("今回は小説")


def test_save_same_name_overwrites():
    import roles
    roles.save_role("上書きテスト", "v1")
    roles.save_role("上書きテスト", "v2")
    assert roles.get_role("上書きテスト")["prompt"] == "v2"
    assert [r["name"] for r in roles.list_roles()].count("上書きテスト") == 1


def test_delete_role():
    import roles
    roles.save_role("消すテスト", "x")
    roles.delete_role("消すテスト")
    assert roles.get_role("消すテスト") is None
