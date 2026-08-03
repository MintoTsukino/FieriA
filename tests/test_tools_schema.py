import json
import re


def test_every_known_tool_has_a_schema():
    import memory_tools
    import tools_schema
    missing = sorted(set(memory_tools.KNOWN_TOOL_NAMES) - set(tools_schema.SCHEMAS))
    assert missing == []


def test_no_schema_for_unknown_tools():
    import memory_tools
    import tools_schema
    extra = sorted(set(tools_schema.SCHEMAS) - set(memory_tools.KNOWN_TOOL_NAMES))
    assert extra == []


def test_schemas_are_valid_shape():
    import tools_schema
    for name, s in tools_schema.SCHEMAS.items():
        assert s["description"], name
        p = s["parameters"]
        assert p["type"] == "object", name
        assert isinstance(p.get("properties"), dict), name
        assert isinstance(p.get("required"), list), name
        for req in p["required"]:
            assert req in p["properties"], f"{name}: requiredの{req}がpropertiesに無い"


def test_schema_args_match_documented_examples():
    """既存ドキュメント（_TOOL_ONELINERS/_TOOL_DETAILS内の例JSON）とスキーマの
    引数がズレていないことを、ソースから機械抽出して照合する。スキーマを
    手書きしたときの写し間違いを、既存の正から自動検出する自己検証。"""
    import io
    import memory_tools
    import tools_schema
    src = io.open(memory_tools.__file__, encoding="utf-8").read()
    documented = {}
    for m in re.finditer(r'["\']tool["\']\s*:\s*["\'](\w+)["\']((?:[^}\n]|\n)*?)\}', src):
        name = m.group(1)
        keys = set(re.findall(r'["\'](\w+)["\']\s*:', m.group(2)))
        if name in memory_tools.KNOWN_TOOL_NAMES:
            documented.setdefault(name, set()).update(keys)
    assert len(documented) >= 20  # 抽出自体が壊れていないことの下限ガード
    for name, keys in documented.items():
        props = set(tools_schema.SCHEMAS[name]["parameters"]["properties"])
        missing = keys - props
        assert not missing, f"{name}: 例に出る引数がスキーマに無い: {missing}"


def test_openai_tool_def_wraps_in_function_format():
    import tools_schema
    d = tools_schema.openai_tool_def("write_wiki")
    assert d["type"] == "function"
    assert d["function"]["name"] == "write_wiki"
    assert d["function"]["parameters"]["type"] == "object"
    json.dumps(d)  # JSONシリアライズ可能であること
