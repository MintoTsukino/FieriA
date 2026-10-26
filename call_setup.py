"""Discover local STT, or install a pinned official runtime into FieriA's home.

No recursive disk search, background startup download, credentials or shell.
Downloads are size/hash checked before publishing; a cancelled install is never
selected. Existing NikoVoice installations are read-only discovery candidates.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import sysconfig
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

import config
from call_process import CallCancelled

ASSETS = json.loads(Path(__file__).with_name("stt_assets.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Runtime:
    directory: Path
    model: Path
    source: str


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise CallCancelled()


def _report(notify, stage, text, received=0, total=0):
    if notify:
        notify({"stage": stage, "text": text, "received": received, "total": total})


def _sha(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while data := source.read(1024 * 1024):
            _cancelled(cancel)
            digest.update(data)
    return digest.hexdigest()


def _link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def managed_root():
    return Path(config.HOME).resolve() / "voice"


def _safe_child(root, path):
    """Do not follow a link inserted below the application's chosen home."""
    root, path = Path(root), Path(path)
    relative = path.relative_to(root)
    current = root
    if _link(current):
        raise RuntimeError("音声の保存先がリンクになっている。保存先を確認してね")
    for part in relative.parts:
        current = current / part
        if _link(current):
            raise RuntimeError("音声の保存先にリンクがあるため準備を中止した")
    return path


def _valid_model_name(name):
    return (isinstance(name, str) and bool(name) and name not in (".", "..") and
            not any(c in name for c in "/\\:\x00") and name.lower().endswith(".bin"))


def _local_runtime(directory, model, source):
    try:
        directory = Path(directory)
        required = ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll")
        if not all((directory / name).is_file() and (directory / name).stat().st_size > 0 for name in required):
            return None
        if not any(p.is_file() and p.stat().st_size > 0 for p in directory.glob("ggml-cpu*.dll")):
            return None
        model_path = directory / model
        if not model_path.is_file() or model_path.stat().st_size <= 0:
            return None
        if model == ASSETS["model"]["name"] and model_path.stat().st_size != ASSETS["model"]["size"]:
            return None
        return Runtime(directory.resolve(), model_path.resolve(), source)
    except OSError:
        return None


def _managed_runtime():
    root = managed_root()
    try:
        _safe_child(root, root / "active.json")
        pointer = root / "active.json"
        if not pointer.is_file() or pointer.stat().st_size > 65536:
            return None
        state = json.loads(pointer.read_text(encoding="utf-8"))
        directory = state.get("directory", "")
        if not isinstance(directory, str) or not re.fullmatch(r"whisper-" + re.escape(ASSETS["build"]) + r"-[0-9a-f]{32}", directory):
            return None
        binary_dir = _safe_child(root, root / directory)
        for name, expected in ASSETS["runtime"]["files"].items():
            path = _safe_child(root, binary_dir / name)
            if not path.is_file() or path.stat().st_size != expected["size"] or _sha(path) != expected["sha256"]:
                return None
        model = _safe_child(root, root / "models" / ASSETS["model"]["name"])
        info = model.stat()
        if (info.st_size != ASSETS["model"]["size"] or info.st_mtime_ns != state.get("model_mtime_ns") or
                state.get("model_sha256") != ASSETS["model"]["sha256"]):
            return None
        return Runtime(binary_dir, model, "フィエリア専用の音声認識")
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def discovery_bases():
    start = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    bases = []
    current = start.absolute()
    for _ in range(4):
        if current.parent == current:
            break  # Never inspect a drive root.
        bases.append(current)
        current = current.parent
    return bases


def discover(configured="", model=None, bases=None):
    model = model or ASSETS["model"]["name"]
    if not _valid_model_name(model):
        return None
    if configured:
        found = _local_runtime(Path(configured).expanduser(), model, "指定した音声認識")
        if found:
            return found
    if model == ASSETS["model"]["name"]:
        found = _managed_runtime()
        if found:
            return found
    candidates = []
    for base in discovery_bases() if bases is None else bases:
        base = Path(base)
        candidates.extend([(base / "whisper-cpp", "アプリの近くの音声認識"),
                           (base / "_internal/whisper-cpp", "アプリに同梱された音声認識"),
                           (base / "NikoVoice-oss/whisper-cpp", "ニコボイスの音声認識"),
                           (base / "NikoVoice/whisper-cpp", "ニコボイスの音声認識")])
    for path, label in candidates:
        found = _local_runtime(path, model, label)
        if found:
            return found
    return None


def _trusted_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return False
    return host in ("github.com", "huggingface.co") or any(
        host.endswith("." + suffix) for suffix in ("githubusercontent.com", "huggingface.co", "hf.co"))


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise RuntimeError("公式配布先以外への転送を拒否した")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_download(url):
    if not _trusted_url(url):
        raise RuntimeError("音声認識のダウンロード先が不正だった")
    request = urllib.request.Request(url, headers={"User-Agent": "FieriA-STT-setup/1"})
    return urllib.request.build_opener(_OfficialRedirects()).open(request, timeout=5)


def _download(asset, path, cancel, notify, stage, label):
    _cancelled(cancel)
    size, digest = 0, hashlib.sha256()
    last_report = 0
    with _open_download(asset["url"]) as response, Path(path).open("xb") as output:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError("音声認識の取得に失敗した")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != asset["size"]:
            raise RuntimeError("取得ファイルのサイズが公式情報と違うため中止した")
        while True:
            _cancelled(cancel)
            data = response.read(1024 * 1024)
            if not data:
                break
            size += len(data)
            if size > asset["size"]:
                raise RuntimeError("取得ファイルが予定サイズを超えたため中止した")
            output.write(data)
            digest.update(data)
            if size == asset["size"] or time.monotonic() - last_report >= 0.15:
                _report(notify, stage, label, size, asset["size"])
                last_report = time.monotonic()
    _cancelled(cancel)
    if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
        raise RuntimeError("音声認識ファイルの検証に失敗した。未完了のファイルは使わないよ")


def _extract(archive_path, directory, cancel):
    directory.mkdir()
    expected = ASSETS["runtime"]["files"]
    selected, expanded = {}, 0
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 512:
            raise RuntimeError("音声認識の圧縮ファイルに項目が多すぎる")
        for item in entries:
            name = item.orig_filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if ("\x00" in name or name.startswith("/") or ":" in name or ".." in parts or
                    stat.S_ISLNK(item.external_attr >> 16) or item.external_attr & 0x400):
                raise RuntimeError("音声認識の圧縮ファイルに不正なパス・リンクがあった")
            expanded += item.file_size
            if item.file_size > 128 * 1024 * 1024 or expanded > 256 * 1024 * 1024:
                raise RuntimeError("音声認識の圧縮ファイルが大きすぎる")
            basename = PurePosixPath(name).name
            if item.is_dir() or basename not in expected:
                continue
            if basename in selected:
                raise RuntimeError("音声認識の圧縮ファイルに同じ名前が重複している")
            selected[basename] = item
        if set(selected) != set(expected):
            raise RuntimeError("音声認識の配布ファイルに必要な部品が足りない")
        for name, item in selected.items():
            _cancelled(cancel)
            target = directory / name
            with archive.open(item) as source, target.open("xb") as output:
                while data := source.read(1024 * 1024):
                    _cancelled(cancel)
                    output.write(data)
            if target.stat().st_size != expected[name]["size"] or _sha(target, cancel) != expected[name]["sha256"]:
                raise RuntimeError("音声認識の展開後の検証に失敗した")
    license_file = Path(__file__).parent / "ui/licenses/whisper-LICENSE.txt"
    if _sha(license_file) != ASSETS["license_sha256"]:
        raise RuntimeError("音声認識のライセンス情報が壊れている")
    shutil.copyfile(license_file, directory / "LICENSE.txt")


@contextmanager
def _install_lock(root, cancel, notify):
    path = _safe_child(root, root / "setup.lock")
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        acquired = False
        try:
            while not acquired:
                _cancelled(cancel)
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    _report(notify, "waiting", "別のフィエリアで音声認識を準備中。完了を待っているよ")
                    if cancel.wait(0.2):
                        raise CallCancelled()
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_runtime(configured, model, cancel, notify=None):
    _cancelled(cancel)
    _report(notify, "discovering", "既存の音声認識を探しているよ")
    existing = discover(configured, model)
    if existing:
        _report(notify, "ready", existing.source + "を使うよ")
        return existing
    if model != ASSETS["model"]["name"]:
        raise RuntimeError("指定した認識モデルが見つからない。標準モデルへ戻すか、認識フォルダを指定してね")
    if sysconfig.get_platform() != "win-amd64":
        raise RuntimeError("この版の自動取得はWindows x64用。対応する認識フォルダを指定してね")
    root = managed_root()
    _safe_child(root.parent, root)
    root.mkdir(parents=True, exist_ok=True)
    with _install_lock(root, cancel, notify):
        existing = _managed_runtime()
        if existing:
            return existing
        models = _safe_child(root, root / "models")
        models.mkdir(exist_ok=True)
        model_file = _safe_child(root, models / model)
        model_valid = (model_file.is_file() and model_file.stat().st_size == ASSETS["model"]["size"])
        if model_valid:
            _report(notify, "verifying", "取得済みの認識モデルを検証しているよ")
            model_valid = _sha(model_file, cancel) == ASSETS["model"]["sha256"]
        required = (0 if model_valid else ASSETS["model"]["size"]) + ASSETS["runtime"]["size"] + 256 * 1024 * 1024
        if shutil.disk_usage(root).free < required:
            raise RuntimeError("音声認識を準備する空き容量が足りない。約1.9GB以上の空きを用意してね")
        # This newly created private staging directory is the only cleanup target.
        with tempfile.TemporaryDirectory(prefix=".stt-setup-", dir=root) as temporary:
            staging = Path(temporary)
            _safe_child(root, staging)
            package = staging / "runtime.zip"
            _download(ASSETS["runtime"], package, cancel, notify, "runtime", "音声認識の本体を取得中")
            _report(notify, "extracting", "音声認識の本体を検証・展開しているよ")
            _extract(package, staging / "bin", cancel)
            if not model_valid:
                _download(ASSETS["model"], staging / "model.part", cancel, notify, "model", "認識モデルを取得中（初回は約1.6GB）")
                _cancelled(cancel)
                os.replace(staging / "model.part", model_file)
            _cancelled(cancel)
            final = root / ("whisper-" + ASSETS["build"] + "-" + uuid.uuid4().hex)
            os.rename(staging / "bin", final)
            state = {"directory": final.name, "model_sha256": ASSETS["model"]["sha256"],
                     "model_mtime_ns": model_file.stat().st_mtime_ns}
            pointer = staging / "active.json"
            pointer.write_text(json.dumps(state), encoding="utf-8")
            _cancelled(cancel)
            os.replace(pointer, _safe_child(root, root / "active.json"))
        _report(notify, "ready", "音声認識の準備ができたよ")
        return Runtime(final, model_file, "フィエリア専用の音声認識")
