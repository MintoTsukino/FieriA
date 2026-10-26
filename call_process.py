"""Hidden, cancellable local child processes for voice calls. No shell input."""
import os
import subprocess
import threading
import time


class CallCancelled(Exception):
    pass


def run_child(args, cancel, input_bytes=None, cwd=None, timeout=120, env=None):
    options = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startup
    if cancel.is_set():
        raise CallCancelled()
    proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    deadline = time.monotonic() + timeout
    finished = threading.Event()
    expired = threading.Event()
    def watchdog():
        # On Windows communicate() can block while writing a large stdin buffer,
        # before its timeout is checked. Cancellation must still kill the child.
        while not finished.wait(0.05):
            if cancel.is_set() or time.monotonic() >= deadline:
                if not cancel.is_set():
                    expired.set()
                try:
                    proc.kill()
                except OSError:
                    pass
                return
    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    try:
        pending = input_bytes
        while True:
            if cancel.is_set():
                raise CallCancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("音声処理が時間内に終わらなかった")
            try:
                stdout, _stderr = proc.communicate(input=pending, timeout=0.15)
                break
            except subprocess.TimeoutExpired:
                pending = None
        if cancel.is_set():
            raise CallCancelled()
        if expired.is_set():
            raise TimeoutError("音声処理が時間内に終わらなかった")
        if proc.returncode:
            # Do not expose subprocess output (it can contain spoken content).
            raise RuntimeError(f"音声処理が終了コード {proc.returncode} で失敗した")
        return stdout
    finally:
        finished.set()
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        watcher.join(timeout=0.2)
