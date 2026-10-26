"""Automatic STT setup: fake official files, isolated home, no live network/audio."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace
import zipfile

import pytest

import call_setup
from call_process import CallCancelled
import call_stt
import config

ORIGINAL_BASES = call_setup.discovery_bases


def packed(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


def description(data, url):
    return {"url": url, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class Response(io.BytesIO):
    status = 200
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    assets = copy.deepcopy(call_setup.ASSETS)
    files = {name: (b"MZ-test-runtime-" + name.encode()) for name in (
        "whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll")}
    archive = packed({"Release/" + name: data for name, data in files.items()})
    model = b"model-test-data"
    assets["runtime"] = {**description(archive, "https://github.com/official/runtime.zip"),
                         "files": {name: {k:v for k,v in description(data, "unused").items() if k != "url"} for name,data in files.items()}}
    assets["model"] = {**assets["model"], **description(model, "https://huggingface.co/official/model.bin")}
    monkeypatch.setattr(config, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(call_setup, "ASSETS", assets)
    monkeypatch.setattr(call_setup, "discovery_bases", lambda: [])
    monkeypatch.setattr(call_setup.sysconfig, "get_platform", lambda: "win-amd64")
    requests = []
    bodies = {assets["runtime"]["url"]: archive, assets["model"]["url"]: model}
    def opened(url):
        requests.append(url)
        return Response(bodies[url])
    monkeypatch.setattr(call_setup, "_open_download", opened)
    return SimpleNamespace(assets=assets, files=files, archive=archive, model=model, bodies=bodies,
                           requests=requests, home=tmp_path / "home", work=tmp_path / "work")


def local_runtime(setup, directory, model_name=None):
    directory.mkdir(parents=True)
    for name, data in setup.files.items():
        (directory / name).write_bytes(data)
    (directory / (model_name or setup.assets["model"]["name"])).write_bytes(setup.model)
    return directory


def ensure(setup, cancel=None, notify=None):
    return call_setup.ensure_runtime("", setup.assets["model"]["name"], cancel or threading.Event(), notify)


def test_detects_nikovoice_without_downloading_or_writing_it(setup):
    directory = local_runtime(setup, setup.work / "NikoVoice-oss/whisper-cpp")
    before = {p.name:p.read_bytes() for p in directory.iterdir()}
    found = call_setup.discover(bases=[setup.work])
    assert found.directory == directory.resolve() and "ニコボイス" in found.source
    assert setup.requests == []
    assert {p.name:p.read_bytes() for p in directory.iterdir()} == before
    assert not setup.home.exists()


def test_explicit_directory_wins_and_custom_model_is_respected(setup):
    directory = local_runtime(setup, setup.work / "chosen", "custom.bin")
    found = call_setup.discover(str(directory), "custom.bin", bases=[])
    assert found.model.name == "custom.bin"
    assert setup.requests == []


def test_frozen_discovery_is_bounded_and_reaches_nearby_nikovoice(setup, monkeypatch):
    monkeypatch.setattr(call_setup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(call_setup.sys, "executable", str(setup.work / "FieriA-build/dist/FieriA/FieriA.exe"))
    bases = ORIGINAL_BASES()
    assert len(bases) == 4 and bases[-1] == setup.work
    assert all(p.parent != p for p in bases)
    local_runtime(setup, setup.work / "NikoVoice/whisper-cpp")
    assert call_setup.discover(bases=bases).source == "ニコボイスの音声認識"


def test_incomplete_or_partial_existing_runtime_is_not_selected(setup):
    directory = local_runtime(setup, setup.work / "NikoVoice-oss/whisper-cpp")
    (directory / "whisper.dll").unlink()
    assert call_setup.discover(bases=[setup.work]) is None
    (directory / "whisper.dll").write_bytes(b"DLL")
    (directory / setup.assets["model"]["name"]).write_bytes(b"partial")
    assert call_setup.discover(bases=[setup.work]) is None


def test_fresh_install_is_verified_atomic_and_reused_offline(setup, monkeypatch):
    events = []
    installed = ensure(setup, notify=events.append)
    root = call_setup.managed_root()
    assert installed.directory.parent == root and installed.model.parent == root / "models"
    assert installed.model.read_bytes() == setup.model
    assert (installed.directory / "LICENSE.txt").is_file()
    assert len(setup.requests) == 2
    assert (root / "active.json").is_file()
    assert not list(root.glob(".stt-setup-*"))
    monkeypatch.setattr(call_setup, "_open_download", lambda url: pytest.fail("network on next call"))
    assert ensure(setup) == installed
    assert any(e["stage"] == "model" and e["received"] == len(setup.model) for e in events)


@pytest.mark.parametrize("which", ["runtime", "model"])
def test_hash_mismatch_never_publishes_ready_runtime(setup, which):
    asset = setup.assets[which]
    data = setup.bodies[asset["url"]]
    setup.bodies[asset["url"]] = b"X" + data[1:]
    with pytest.raises(RuntimeError, match="検証"):
        ensure(setup)
    assert not (call_setup.managed_root() / "active.json").exists()
    assert not list(call_setup.managed_root().glob(".stt-setup-*"))
    assert call_setup.discover() is None


def test_cancelled_install_cleans_partial_files_and_can_retry(setup):
    cancelled = threading.Event()
    def progress(event):
        if event["stage"] == "model": cancelled.set()
    with pytest.raises(CallCancelled):
        ensure(setup, cancelled, progress)
    root = call_setup.managed_root()
    assert not (root / "active.json").exists()
    assert not list(root.glob(".stt-setup-*"))
    assert not (root / "models" / setup.assets["model"]["name"]).exists()
    assert ensure(setup).model.is_file()


def test_managed_runtime_tampering_is_repaired_without_overwriting_old_binary(setup):
    first = ensure(setup)
    executable = first.directory / "whisper-cli.exe"
    executable.write_bytes(b"corrupted executable")
    assert call_setup.discover() is None
    count = len(setup.requests)
    second = ensure(setup)
    assert second.directory != first.directory
    assert executable.read_bytes() == b"corrupted executable"
    assert setup.requests[count:] == [setup.assets["runtime"]["url"]]  # valid model is cached


def test_changed_managed_model_is_rechecked_before_use(setup):
    installed = ensure(setup)
    installed.model.write_bytes(b"X" * len(setup.model))
    assert call_setup.discover() is None
    ensure(setup)
    assert installed.model.read_bytes() == setup.model


def test_unsafe_managed_pointer_cannot_select_external_program(setup):
    root = call_setup.managed_root(); root.mkdir(parents=True)
    (root / "active.json").write_text(json.dumps({"directory": "../../elsewhere"}))
    assert call_setup.discover() is None


def test_low_disk_space_stops_before_network(setup, monkeypatch):
    monkeypatch.setattr(call_setup.shutil, "disk_usage", lambda path: SimpleNamespace(free=1))
    with pytest.raises(RuntimeError, match="空き容量"):
        ensure(setup)
    assert setup.requests == []


def test_missing_custom_model_is_not_silently_replaced(setup):
    with pytest.raises(RuntimeError, match="指定した認識モデル"):
        call_setup.ensure_runtime("", "custom.bin", threading.Event())
    assert setup.requests == []


@pytest.mark.parametrize("path", ["../escape.dll", "C:/escape.dll", "\\\\server\\share\\escape.dll", "Release/file.dll:stream"])
def test_zip_paths_are_rejected_before_extraction(setup, tmp_path, path):
    contents = {"Release/" + name:data for name,data in setup.files.items()}
    contents[path] = b"bad"
    archive = tmp_path / "unsafe.zip"; archive.write_bytes(packed(contents))
    with pytest.raises(RuntimeError, match="不正"):
        call_setup._extract(archive, tmp_path / "extracted", threading.Event())
    assert not (tmp_path / "escape.dll").exists()


def test_zip_symlink_is_rejected(setup, tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        info = zipfile.ZipInfo("Release/link.dll")
        info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, "../../elsewhere")
    with pytest.raises(RuntimeError, match="リンク"):
        call_setup._extract(archive, tmp_path / "extracted", threading.Event())


def test_zip_duplicate_dll_names_are_rejected(setup, tmp_path):
    files = {"Release/" + name:data for name,data in setup.files.items()}
    files["Other/ggml.dll"] = b"other DLL"
    archive = tmp_path / "unsafe.zip"; archive.write_bytes(packed(files))
    with pytest.raises(RuntimeError, match="重複"):
        call_setup._extract(archive, tmp_path / "extracted", threading.Event())


@pytest.mark.parametrize("url,expected", [("https://github.com/ggml-org/whisper.cpp/x", True),
    ("https://release-assets.githubusercontent.com/blob", True), ("https://cas-bridge.xethub.hf.co/blob", True),
    ("http://github.com/blob", False), ("https://github.com.evil.example/blob", False),
    ("https://huggingface.co@evil.example/blob", False), ("file:///secret", False),
    ("https://github.com:123/blob", False), ("https://github.com:bad/blob", False)])
def test_only_https_official_hosts_and_cdns_are_allowed(url, expected):
    assert call_setup._trusted_url(url) is expected


def test_redirect_outside_official_hosts_is_refused():
    with pytest.raises(RuntimeError, match="転送"):
        call_setup._OfficialRedirects().redirect_request(None, None, 302, "found", {}, "https://evil.example/file")


def test_parallel_setup_waits_and_cancellation_releases_lock(setup):
    root = call_setup.managed_root(); root.mkdir(parents=True)
    cancelled, waiting = threading.Event(), threading.Event()
    outcomes = []
    def second():
        try:
            with call_setup._install_lock(root, cancelled, lambda event: waiting.set()):
                outcomes.append("unexpected lock")
        except CallCancelled:
            outcomes.append("cancelled")
    with call_setup._install_lock(root, threading.Event(), None):
        thread = threading.Thread(target=second); thread.start()
        assert waiting.wait(2)
        cancelled.set(); thread.join(2)
    assert outcomes == ["cancelled"]
    with call_setup._install_lock(root, threading.Event(), None):
        pass


def test_stt_uses_managed_model_in_sibling_directory(setup, monkeypatch):
    runtime = ensure(setup)
    captured = []
    monkeypatch.setattr(call_stt, "run_child", lambda args, *a, **k: captured.append(args) or "認識".encode())
    stt = call_stt.WhisperSTT({"stt_model": setup.assets["model"]["name"]})
    stt.ensure_ready(threading.Event())
    import numpy as np
    assert stt.transcribe(np.zeros(100), threading.Event()) == "認識"
    model_arg = captured[0][captured[0].index("-m") + 1]
    assert (runtime.directory / model_arg).resolve() == runtime.model.resolve()


def test_gui_status_is_read_only_and_contains_no_download(setup, monkeypatch):
    import gui
    # Use a minimal bridge; status must not construct an Engine or read secrets.
    bridge = object.__new__(gui.Bridge)
    bridge._cfg = {"call": {"stt_dir": "", "stt_model": setup.assets["model"]["name"]}}
    assert bridge.call_stt_status()["ready"] is False
    assert setup.requests == [] and not setup.home.exists()


def test_linked_managed_cache_is_not_written(setup, monkeypatch):
    root = call_setup.managed_root()
    monkeypatch.setattr(call_setup, "_link", lambda path: path == root)
    with pytest.raises(RuntimeError, match="リンク"):
        ensure(setup)
    assert setup.requests == [] and not root.exists()


@pytest.mark.parametrize("failure", ["size_header", "early_eof", "extra_bytes"])
def test_invalid_download_length_never_becomes_ready(setup, monkeypatch, failure):
    original = setup.bodies[setup.assets["runtime"]["url"]]
    def opened(url):
        data = original[:-1] if failure == "early_eof" else original + b"extra" if failure == "extra_bytes" else original
        response = Response(data)
        if failure != "size_header": response.headers = {}
        else: response.headers["Content-Length"] = "1"
        return response
    monkeypatch.setattr(call_setup, "_open_download", opened)
    with pytest.raises(RuntimeError): ensure(setup)
    assert not (call_setup.managed_root() / "active.json").exists()


def test_network_failure_exposes_no_remote_details(setup, monkeypatch):
    def failure(*a, **k): raise OSError("private proxy information")
    monkeypatch.setattr(call_setup, "ensure_runtime", failure)
    with pytest.raises(RuntimeError, match="ネット接続") as caught:
        call_stt.WhisperSTT({}).ensure_ready(threading.Event())
    assert "private proxy" not in str(caught.value)
