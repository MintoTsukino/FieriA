"""Explicit, window-only call sharing. Importing this module captures nothing.

No desktop/foreground fallback. Native capture is globally bounded to one daemon
worker because PrintWindow can block inside another application's paint handler.
"""
import base64
import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass, field
import io
import os
import threading
import time
import uuid

from PIL import Image

_CAPTURE_SLOT = threading.Lock()
CAPTURE_SECONDS = 1.5


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    tid: int
    born: int
    klass: str
    title: str = field(compare=False)


class WindowBackend:
    """Win32 APIs are loaded only after an explicit window-list request."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("画面共有はWindows版で使えるよ")
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.g = ctypes.WinDLL("gdi32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi")
        self.callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        signatures = [
            (self.u, "EnumWindows", [self.callback_type, wt.LPARAM], wt.BOOL),
            (self.u, "IsWindow", [wt.HWND], wt.BOOL),
            (self.u, "IsWindowVisible", [wt.HWND], wt.BOOL),
            (self.u, "IsIconic", [wt.HWND], wt.BOOL),
            (self.u, "GetWindowThreadProcessId", [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            (self.u, "GetWindowTextW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.u, "GetClassNameW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.u, "GetWindowRect", [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            (self.u, "GetDC", [wt.HWND], wt.HDC),
            (self.u, "ReleaseDC", [wt.HWND, wt.HDC], ctypes.c_int),
            (self.u, "PrintWindow", [wt.HWND, wt.HDC, wt.UINT], wt.BOOL),
            (self.k, "OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (self.k, "CloseHandle", [wt.HANDLE], wt.BOOL),
            (self.k, "GetProcessTimes", [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            (self.g, "CreateCompatibleDC", [wt.HDC], wt.HDC),
            (self.g, "CreateCompatibleBitmap", [wt.HDC, ctypes.c_int, ctypes.c_int], wt.HBITMAP),
            (self.g, "SelectObject", [wt.HDC, wt.HANDLE], wt.HANDLE),
            (self.g, "DeleteObject", [wt.HANDLE], wt.BOOL),
            (self.g, "DeleteDC", [wt.HDC], wt.BOOL),
            (self.g, "PatBlt", [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD], wt.BOOL),
            (self.g, "GetDIBits", [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT], ctypes.c_int),
            (self.dwm, "DwmGetWindowAttribute", [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD], ctypes.c_long),
        ]
        for lib, name, args, result in signatures:
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result

    def identify(self, hwnd):
        if not hwnd or not self.u.IsWindow(hwnd) or not self.u.IsWindowVisible(hwnd) or self.u.IsIconic(hwnd):
            return None
        cloaked = wt.DWORD()
        if self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return None
        pid = wt.DWORD()
        tid = self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not tid or not pid.value or pid.value == os.getpid():
            return None
        title, klass = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
        if not self.u.GetWindowTextW(hwnd, title, len(title)):
            return None
        self.u.GetClassNameW(hwnd, klass, len(klass))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            born = (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            self.k.CloseHandle(handle)
        return Window(int(hwnd), pid.value, tid, born, klass.value, title.value)

    def windows(self):
        windows = []
        @self.callback_type
        def collect(hwnd, _):
            try:
                target = self.identify(hwnd)
                if target and len(windows) < 500:
                    windows.append(target)
            except Exception:
                pass
            return True
        if not self.u.EnumWindows(collect, 0):
            raise RuntimeError("ウィンドウ一覧を取得できなかった")
        return sorted(windows, key=lambda w: w.title.casefold())

    def capture(self, target):
        if self.identify(target.hwnd) != target:
            raise RuntimeError("共有する窓が閉じたか最小化された。窓を選び直してね")
        rect = wt.RECT()
        if not self.u.GetWindowRect(target.hwnd, ctypes.byref(rect)):
            raise RuntimeError("共有する窓のサイズを取得できなかった")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 1 or height < 1 or width * height > 24_000_000:
            raise RuntimeError("このサイズの窓は共有できなかった")

        class Header(ctypes.Structure):
            _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                        ("planes", wt.WORD), ("bits", wt.WORD), ("compression", wt.DWORD),
                        ("image_size", wt.DWORD), ("xppm", wt.LONG), ("yppm", wt.LONG),
                        ("used", wt.DWORD), ("important", wt.DWORD)]

        dc = memory_dc = bitmap = old = None
        try:
            # GetDC is always supplied the selected nonzero HWND, never NULL.
            dc = self.u.GetDC(target.hwnd)
            if not dc:
                raise RuntimeError("この窓は撮影できなかった")
            memory_dc = self.g.CreateCompatibleDC(dc)
            bitmap = self.g.CreateCompatibleBitmap(dc, width, height)
            if not memory_dc or not bitmap:
                raise RuntimeError("撮影用メモリを確保できなかった")
            old = self.g.SelectObject(memory_dc, bitmap)
            if not old or old == ctypes.c_void_p(-1).value:
                old = None
                raise RuntimeError("この窓は撮影できなかった")
            self.g.PatBlt(memory_dc, 0, 0, width, height, 0x00000042)  # BLACKNESS
            if not self.u.PrintWindow(target.hwnd, memory_dc, 2):
                raise RuntimeError("この窓は撮影に対応していなかった。別の窓を選んでね")
            self.g.SelectObject(memory_dc, old)
            old = None
            header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            if self.g.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0) != height:
                raise RuntimeError("窓の画像を取得できなかった")
            if self.identify(target.hwnd) != target:
                raise RuntimeError("共有する窓の状態が変わった。窓を選び直してね")
            return Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
        finally:
            if old and memory_dc:
                self.g.SelectObject(memory_dc, old)
            if bitmap:
                self.g.DeleteObject(bitmap)
            if memory_dc:
                self.g.DeleteDC(memory_dc)
            if dc:
                self.u.ReleaseDC(target.hwnd, dc)


def _jpeg(image, edge, quality):
    with image.convert("RGB") as copy:
        copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        copy.save(out, "JPEG", quality=quality, optimize=True)
    data = out.getvalue()
    if len(data) > 2_000_000:
        raise RuntimeError("画像が大きすぎたので、この発言は音声だけ送るよ")
    return {"mime": "image/jpeg", "b64": base64.b64encode(data).decode("ascii")}


@dataclass
class Frame:
    epoch: int
    target: Window
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    image: dict | None = None
    error: str = ""
    consumed: bool = False


class ScreenShare:
    def __init__(self, notify, backend_factory=WindowBackend):
        self._notify, self._backend_factory = notify, backend_factory
        self._backend = None
        self._lock = threading.RLock()
        self._choices = {}
        self._target = None
        self._mode = "off"
        self._epoch = 0
        self._pending = None
        self._closed = False

    def snapshot(self):
        with self._lock:
            return {"mode": self._mode, "title": self._target.title if self._target else "",
                    "pending": self._pending is not None, "epoch": self._epoch}

    def windows(self):
        with self._lock:
            if self._closed:
                return {"error": "通話は終了しているよ"}
            if self._backend is None:
                self._backend = self._backend_factory()
            old = {w: token for token, w in self._choices.items()}
            self._choices = {old.get(w, uuid.uuid4().hex): w for w in self._backend.windows()}
            return {"windows": [{"id": token, "title": w.title} for token, w in self._choices.items()]}

    def configure(self, token, mode):
        if mode not in ("off", "every", "once"):
            return {"error": "共有方法を選んでね"}
        with self._lock:
            if self._closed:
                return {"error": "通話は終了しているよ"}
            target = self._choices.get(token) if isinstance(token, str) else None
            if mode != "off" and target is None:
                return {"error": "先に共有するウィンドウを選んでね"}
            self._invalidate()
            if mode != "off":
                self._target = target
            self._mode = mode
            return {"ok": True, "sharing": self.snapshot()}

    def _invalidate(self):
        self._epoch += 1
        if self._pending:
            self._pending.image = None
        self._pending = None

    def close(self):
        with self._lock:
            self._closed, self._mode = True, "off"
            self._invalidate()
            self._choices.clear()
            self._target = None

    def discard_pending(self):
        with self._lock:
            if self._pending is not None:
                self._invalidate()

    def request(self):
        """Called once at the first speech threshold, or when sending text."""
        with self._lock:
            if self._closed or self._mode == "off" or self._target is None:
                return None
            self._invalidate()
            frame = Frame(self._epoch, self._target, time.monotonic() + CAPTURE_SECONDS)
            self._pending = frame
            if self._mode == "once":
                self._mode = "off"
            if not _CAPTURE_SLOT.acquire(blocking=False):
                frame.error = "撮影が間に合わなかった。この発言は音声だけ送るよ"
                frame.done.set()
                return frame
            try:
                threading.Thread(target=self._capture, args=(frame,), daemon=True, name="FieriA-window-frame").start()
            except Exception:
                _CAPTURE_SLOT.release()
                frame.error = "撮影を開始できなかった。この発言は音声だけ送るよ"
                frame.done.set()
            return frame

    def _capture(self, frame):
        preview = None
        try:
            with self._backend.capture(frame.target) as image:
                payload, preview = _jpeg(image, 1600, 82), _jpeg(image, 360, 65)
            with self._lock:
                if not self.valid(frame) or time.monotonic() > frame.deadline:
                    return
                frame.image = payload
        except Exception as exc:
            with self._lock:
                frame.error = str(exc)
        finally:
            _CAPTURE_SLOT.release()
            frame.done.set()
        if preview:
            with self._lock:
                current = self.valid(frame)
            if current:
                # UI also checks this epoch: OFF may race the UI delivery.
                self._notify("share_frame", preview=preview, title=frame.target.title,
                             epoch=frame.epoch)

    def valid(self, frame):
        with self._lock:
            return bool(frame and not self._closed and frame.epoch == self._epoch)

    def take(self, frame, cancel):
        if frame is None:
            return None, ""
        while not frame.done.wait(0.025):
            if cancel.is_set() or not self.valid(frame):
                return None, ""
            if time.monotonic() >= frame.deadline:
                break
        with self._lock:
            if cancel.is_set() or not self.valid(frame) or frame.consumed:
                return None, ""
            frame.consumed = True
            self._pending = None
            image, frame.image = frame.image, None
            return image, "" if image else frame.error or "撮影が間に合わなかった。この発言は音声だけ送るよ"
