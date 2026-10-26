"""Synthetic windows/images only: never enumerate or capture the real desktop."""
import base64
import ctypes
import io
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest

import call_screen as screen


class FakeWindows:
    def __init__(self):
        self.target = screen.Window(123, 456, 789, 111, "Editor", "原稿 <window>")
        self.calls = []
        self.wait = None
        self.error = None

    def windows(self):
        return [self.target]

    def capture(self, target):
        self.calls.append(target)
        if self.wait:
            self.wait.wait(3)
        if self.error:
            raise RuntimeError(self.error)
        return Image.new("RGB", (2200, 1100), (38, 80, 144))


@pytest.fixture
def sharing():
    events, backend = [], FakeWindows()
    share = screen.ScreenShare(lambda kind, **kw: events.append((kind, kw)), lambda: backend)
    token = share.windows()["windows"][0]["id"]
    yield share, backend, token, events
    if backend.wait:
        backend.wait.set()
    share.close()
    # Leave the global native slot clean for the next test, even after failure.
    assert screen._CAPTURE_SLOT.acquire(timeout=4)
    screen._CAPTURE_SLOT.release()


def arm(sharing, mode="every"):
    share, backend, token, events = sharing
    assert share.configure(token, mode)["ok"]
    return share


def test_default_off_and_window_list_is_not_capture(sharing):
    share, backend, token, _ = sharing
    assert share.snapshot()["mode"] == "off"
    assert share.request() is None
    assert not backend.calls
    assert share.configure("123", "every").get("error")  # HWND is not a capability
    assert share.configure(None, "once").get("error")
    assert share.configure(token, "desktop").get("error")
    assert share.windows()["windows"][0]["id"] == token


def test_one_shot_is_consumed_and_jpeg_is_bounded(sharing):
    share = arm(sharing, "once")
    frame = share.request()
    assert share.request() is None
    image, notice = share.take(frame, threading.Event())
    assert not notice
    with Image.open(io.BytesIO(base64.b64decode(image["b64"]))) as decoded:
        assert decoded.size == (1600, 800)
        assert decoded.format == "JPEG"
    assert share.take(frame, threading.Event()) == (None, "")
    assert share.snapshot()["mode"] == "off"
    assert not share.snapshot()["pending"]
    assert len(sharing[1].calls) == 1


@pytest.mark.parametrize("action", ["off", "retarget", "close", "discard"])
def test_pending_capture_cannot_reappear_after_invalidation(sharing, action):
    share = arm(sharing)
    backend, token, events = sharing[1:]
    backend.wait = threading.Event()
    frame = share.request()
    if action == "off":
        share.configure(None, "off")
    elif action == "retarget":
        share.configure(token, "once")
    elif action == "discard":
        share.discard_pending()
    else:
        share.close()
    backend.wait.set()
    assert frame.done.wait(2)
    assert share.take(frame, threading.Event()) == (None, "")
    assert frame.image is None
    assert not events


def test_hung_window_is_bounded_and_next_turn_never_gets_previous_image(sharing, monkeypatch):
    share = arm(sharing)
    backend = sharing[1]
    backend.wait = threading.Event()
    monkeypatch.setattr(screen, "CAPTURE_SECONDS", 0.06)
    frame = share.request()
    start = time.monotonic()
    image, error = share.take(frame, threading.Event())
    assert image is None and "間に合わなかった" in error
    assert time.monotonic() - start < 0.5
    second = share.request()
    assert second.done.is_set()
    assert share.take(second, threading.Event())[0] is None
    assert len(backend.calls) == 1  # no accumulating blocked capture workers
    backend.wait.set()
    assert frame.done.wait(2)
    assert frame.image is None


def test_failed_capture_does_not_reuse_last_success(sharing):
    share = arm(sharing)
    frame = share.request()
    assert share.take(frame, threading.Event())[0]
    sharing[1].error = "選んだ窓を撮影できない"
    second = share.request()
    image, notice = share.take(second, threading.Event())
    assert image is None and "選んだ窓" in notice


def test_cancel_does_not_wait_for_blocked_capture(sharing):
    share = arm(sharing)
    sharing[1].wait = threading.Event()
    frame = share.request()
    cancel = threading.Event()
    cancel.set()
    assert share.take(frame, cancel) == (None, "")


def native_fake(target, fail=""):
    """Stub native DC calls, including pixel extraction and resource disposal."""
    calls = []
    def rect(hwnd, result):
        assert hwnd == target.hwnd != 0
        result._obj.left, result._obj.top, result._obj.right, result._obj.bottom = 0, 0, 4, 3
        return 1
    def dc(hwnd):
        assert hwnd == target.hwnd != 0
        calls.append(("GetDC", hwnd))
        return 0 if fail == "dc" else 10
    def print_window(hwnd, memory, flags):
        calls.append(("PrintWindow", hwnd, memory, flags))
        assert hwnd == target.hwnd != 0
        return fail != "print"
    def pixels(dc, bitmap, start, height, buffer, header, colors):
        assert header._obj.height == -3
        ctypes.memset(buffer, 80, 4 * 3 * 4)
        return height
    backend = screen.WindowBackend.__new__(screen.WindowBackend)
    backend.identify = lambda hwnd: target
    backend.u = SimpleNamespace(GetWindowRect=rect, GetDC=dc, PrintWindow=print_window,
                                ReleaseDC=lambda *a: calls.append(("ReleaseDC", *a)))
    backend.g = SimpleNamespace(CreateCompatibleDC=lambda dc: 20, CreateCompatibleBitmap=lambda *a: 30,
        SelectObject=lambda *a: 40, PatBlt=lambda *a: 1, GetDIBits=pixels,
        DeleteObject=lambda *a: calls.append(("DeleteObject", *a)),
        DeleteDC=lambda *a: calls.append(("DeleteDC", *a)))
    return backend, calls


@pytest.mark.parametrize("failure", ["", "dc", "print"])
def test_native_capture_only_uses_selected_hwnd_and_releases_resources(failure):
    target = FakeWindows().target
    backend, calls = native_fake(target, failure)
    if failure:
        with pytest.raises(RuntimeError):
            backend.capture(target)
    else:
        with backend.capture(target) as image:
            assert image.size == (4, 3)
    if failure != "dc":
        assert ("DeleteObject", 30) in calls
        assert ("DeleteDC", 20) in calls
        assert ("ReleaseDC", target.hwnd, 10) in calls
    assert all(row[1] == target.hwnd for row in calls if row[0] in ("GetDC", "PrintWindow"))


@pytest.mark.parametrize("change", ["closed", "pid", "thread", "process_birth", "class"])
def test_replaced_or_unavailable_window_never_falls_back_to_desktop(change):
    target = FakeWindows().target
    backend, calls = native_fake(target)
    replacement = {"pid": screen.Window(123, 999, 789, 111, "Editor", "new"),
        "thread": screen.Window(123, 456, 999, 111, "Editor", "new"),
        "process_birth": screen.Window(123, 456, 789, 222, "Editor", "new"),
        "class": screen.Window(123, 456, 789, 111, "Other", "new")}.get(change)
    backend.identify = lambda hwnd: replacement
    with pytest.raises(RuntimeError):
        backend.capture(target)
    assert not calls


def test_window_disappearing_during_capture_discards_pixels():
    target = FakeWindows().target
    backend, calls = native_fake(target)
    identities = iter([target, None])
    backend.identify = lambda hwnd: next(identities)
    with pytest.raises(RuntimeError):
        backend.capture(target)
    assert ("DeleteObject", 30) in calls


def test_window_title_change_preserves_identity():
    target = FakeWindows().target
    assert target == screen.Window(target.hwnd, target.pid, target.tid, target.born, target.klass, "保存後のタイトル")
