"""One SOUL, one worker, one phone call. UI and audio are injected boundaries.

NikoVoice's phone interaction is adapted to FieriA's existing Engine and memory;
no second conversation engine, character store or relationship system is created.
"""
import copy
import math
import queue
import threading
import time
import uuid

import config
from call_screen import ScreenShare
from call_process import CallCancelled


def normalize_settings(incoming, current=None):
    result = copy.deepcopy(config.DEFAULT_CONFIG["call"])
    result.update(current or {})
    values = incoming if isinstance(incoming, dict) else {}
    for key in ("stt_dir", "stt_model"):
        if key in values and isinstance(values[key], str):
            result[key] = values[key].strip()
    if not result["stt_model"]:
        result["stt_model"] = config.DEFAULT_CONFIG["call"]["stt_model"]
    if "hands_free" in values:
        result["hands_free"] = values["hands_free"] is True
    for key, low, high, integer in (("threads", 1, 32, True), ("silence_seconds", 0.4, 8, False),
                                   ("silence_threshold", 0.001, 0.5, False),
                                   ("max_record_seconds", 10, 180, True), ("input_gain", 0.1, 5, False)):
        try:
            value = float(values.get(key, result[key]))
            if not math.isfinite(value):
                raise ValueError()
            value = max(low, min(high, value))
            result[key] = int(value) if integer else value
        except (TypeError, ValueError, OverflowError):
            result[key] = config.DEFAULT_CONFIG["call"][key]
    for key in ("input_device", "output_device"):
        value = values.get(key, result[key])
        result[key] = value if type(value) is int and value >= 0 else None
    voice = values.get("voice", result["voice"])
    result["voice"] = voice if voice in ("existing", "system") else "existing"
    speakers = values.get("soul_speakers", result["soul_speakers"])
    result["soul_speakers"] = {sid: style for sid, style in (speakers.items() if isinstance(speakers, dict) else [])
                               if isinstance(sid, str) and type(style) is int and 0 <= style <= 2147483647}
    return result


def make_components(settings, tts_cfg, soul_id, on_ready, notify):
    from call_audio import Player, Recorder
    from call_speaker import Speaker
    from call_stt import WhisperSTT

    player = Player(settings.get("output_device"))
    return Recorder(settings, on_ready), WhisperSTT(settings), Speaker(settings, tts_cfg, soul_id, player, notify), player


class CallSession:
    def __init__(self, engine, settings, tts_cfg, soul_id, name, on_event, on_finish, on_cancel=None):
        self.id = uuid.uuid4().hex
        self.soul_id, self.name = soul_id, name
        self._engine = engine
        self._settings = normalize_settings(settings)
        self._tts = copy.deepcopy(tts_cfg)
        self._on_event, self._on_finish = on_event, on_finish
        self._on_cancel = on_cancel or (lambda: None)
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._speech_cancel = threading.Event()
        self._turn_cancel = threading.Event()
        self._queue = queue.Queue()
        self._phase = "outgoing"
        self._mic_muted = False
        self._speaker_muted = False
        self._finished = False
        self._record_after_interrupt = False
        self._recorder = self._stt = self._speaker = self._player = None
        self._thread = None
        self._record_token = None
        self._record_frame = None
        self._screen = ScreenShare(self._emit)

    def snapshot(self):
        with self._lock:
            return {"call_id": self.id, "soul_id": self.soul_id, "name": self.name,
                    "phase": self._phase, "active": not self._finished and not self._cancel.is_set(),
                    "draining": self._cancel.is_set() and not self._finished,
                    "mic_muted": self._mic_muted, "speaker_muted": self._speaker_muted,
                    "sharing": self._screen.snapshot()}

    def share_windows(self):
        try:
            return self._screen.windows()
        except Exception:
            return {"error": "共有できる窓を取得できなかった。Windowsの状態を確認してね"}

    def share_configure(self, options):
        if not isinstance(options, dict):
            return {"error": "共有する窓と方法を選んでね"}
        result = self._screen.configure(options.get("window"), options.get("mode"))
        if result.get("ok"):
            self._emit("share_state")
        return result

    def _speech_started(self, token):
        with self._lock:
            if (token != self._record_token or self._phase not in ("listening", "transcribing")
                    or self._mic_muted or self._cancel.is_set() or self._record_frame is None):
                return
            record = self._record_frame
            if record["started"].is_set():
                return
            record["frame"] = self._screen.request()
            record["started"].set()
        if record["frame"] is not None:
            self._emit("share_state")

    def _emit(self, kind, **payload):
        if self._cancel.is_set() and kind not in ("ended", "ending"):
            return
        try:
            shown = self._on_event({**self.snapshot(), "kind": kind, **payload})
            if shown is False:
                raise RuntimeError("call UI unavailable")
        except Exception:
            if kind not in ("ended", "ending"):
                self.end(notify=False)

    def _phase_to(self, phase):
        with self._lock:
            if self._cancel.is_set():
                return
            self._phase = phase
        self._emit("state")

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="FieriA-call")
        self._thread.start()

    def end(self, notify=True):
        self._cancel.set()
        self._screen.close()
        self._turn_cancel.set()
        self._speech_cancel.set()
        self._engine.request_stop()
        self._on_cancel()
        with self._lock:
            self._phase = "ending"
        if notify:
            self._emit("ending")
        for component in (self._player, self._recorder):
            try:
                if component:
                    component.stop()
            except Exception:
                pass
        self._queue.put(None)

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _audio_ready(self, token=None):
        with self._lock:
            if token is not None and token != self._record_token:
                return
            if self._phase != "listening" or self._mic_muted or self._cancel.is_set():
                return
            self._phase = "transcribing"
            record = self._record_frame
        try:
            audio = self._recorder.stop()
            self._queue.put(("audio", (audio, record)))
            self._emit("state")
        except Exception:
            self._queue.put(("error", "マイクの録音を停止できなかった"))

    def toggle_record(self):
        with self._lock:
            phase = self._phase
            if self._cancel.is_set() or self._finished or self._mic_muted:
                return {"error": "通話が終了しているか、マイクがミュート中だよ"}
            if phase == "ready":
                self._queue.put(("record", None))
            elif phase == "listening":
                # Runs on a separate thread; stop() must not be an audio callback.
                threading.Thread(target=self._audio_ready, args=(self._record_token,), daemon=True).start()
            elif phase in ("speaking", "thinking"):
                self._record_after_interrupt = True
                self._speech_cancel.set()
                if phase == "thinking":
                    self._turn_cancel.set()
                    self._engine.request_stop()
                    self._on_cancel()
                    self._phase = "stopping"
                if self._player:
                    self._player.stop()
            else:
                return {"error": "いま音声を処理しているよ。少し待ってね"}
        return {"ok": True}

    def send_text(self, text):
        text = (text or "").strip()
        if not text or len(text) > 10000:
            return {"error": "通話中の文字は1〜10000文字で送ってね"}
        with self._lock:
            if self._phase not in ("listening", "ready", "paused") or self._cancel.is_set():
                return {"error": "返事や文字起こしが終わってから送ってね"}
            self._phase = "thinking"
            self._record_token = None
            self._record_frame = None
            if self._recorder:
                self._recorder.stop()
            self._screen.discard_pending()
            self._queue.put(("text", (text, self._screen.request())))
        return {"ok": True}

    def mute_mic(self, muted):
        with self._lock:
            self._mic_muted = bool(muted)
            if self._phase in ("listening", "ready") and self._mic_muted:
                self._phase = "paused"
                self._record_token = None
                self._record_frame = None
                self._recorder.stop()
                self._screen.discard_pending()
            elif self._phase == "paused" and not self._mic_muted:
                self._queue.put(("resume", None))
        self._emit("state")
        return {"ok": True, **self.snapshot()}

    def mute_speaker(self, muted):
        with self._lock:
            self._speaker_muted = bool(muted)
            if self._player:
                self._player.muted = self._speaker_muted
                if self._speaker_muted:
                    self._speech_cancel.set()
                    self._player.stop()
        self._emit("state")
        return {"ok": True, **self.snapshot()}

    def _listen(self, forced=False):
        with self._lock:
            if self._cancel.is_set():
                return
            self._screen.discard_pending()
            if self._mic_muted:
                self._phase = "paused"
            elif not self._settings["hands_free"] and not forced:
                self._phase = "ready"
            else:
                self._record_frame = {"started": threading.Event(), "frame": None}
                self._record_token = self._recorder.start()
                self._phase = "listening"
        self._emit("state")

    def _turn(self, text, frame=None):
        if self._cancel.is_set():
            return
        self._turn_cancel = threading.Event()
        self._speech_cancel = threading.Event()
        self._phase_to("thinking")
        image, notice = self._screen.take(frame, self._cancel)
        if self._cancel.is_set():
            return
        if notice:
            self._emit("notice", text=notice)
        # take() transfers the image to this submitted turn. OFF cancels pending
        # frames, but cannot retract a turn already shown as submitted in the UI.
        self._emit("user", text=text, images=[image] if image else [])
        chunks, last_flush = [], time.monotonic()
        def flush():
            nonlocal last_flush
            if chunks:
                self._emit("delta", text="".join(chunks))
                chunks.clear()
                last_flush = time.monotonic()
        def delta(value):
            chunks.append(value)
            if sum(map(len, chunks)) >= 40 or time.monotonic() - last_flush >= 0.08:
                flush()
        def status(value):
            flush()
            self._emit("tool", status=value)
        result = self._engine.process_turn(
            text, images=[image] if image else None,
            voice=True, cancelled=lambda: self._cancel.is_set() or self._turn_cancel.is_set(),
            on_delta=delta, on_status=status)
        if self._cancel.is_set():
            return
        if result.get("stopped") or self._turn_cancel.is_set():
            self._emit("stopped", result=result)
            return
        flush()
        self._emit("reply", result=result)
        self._phase_to("speaking")
        if not self._speaker_muted:
            try:
                self._speaker.speak(result.get("reply", ""), self._speech_cancel)
            except CallCancelled:
                pass
            except Exception:
                self._emit("notice", text="音声を再生できなかった。返事は文字で確認してね")

    def _run(self):
        try:
            self._recorder, self._stt, self._speaker, self._player = make_components(
                self._settings, self._tts, self.soul_id, self._audio_ready,
                lambda text: self._emit("notice", text=text))
            self._recorder.on_speech = self._speech_started
            self._player.muted = self._speaker_muted
            self._emit("state")
            self._recorder.check()
            if hasattr(self._stt, "ensure_ready"):
                self._phase_to("preparing")
                self._stt.ensure_ready(self._cancel, lambda progress: self._emit("setup", **progress))
            else:
                self._stt.health()
            if self._cancel.is_set():
                return
            self._phase_to("outgoing")
            if self._cancel.is_set():
                return
            self._player.ring(self._cancel)
            self._turn("（通話を開始した）")
            if self._cancel.wait(0.25):
                return
            forced, self._record_after_interrupt = self._record_after_interrupt, False
            self._listen(forced=forced)
            while not self._cancel.is_set():
                try:
                    job = self._queue.get(timeout=0.15)
                except queue.Empty:
                    continue
                if job is None:
                    break
                kind, data = job
                if kind in ("record", "resume"):
                    if self._phase in ("ready", "paused"):
                        self._listen(forced=kind == "record")
                    continue
                if kind == "error":
                    self._emit("error", text=data)
                else:
                    try:
                        frame = None
                        if kind == "audio":
                            data, record = data
                            if data is None or len(data) < 4800:
                                self._listen()
                                continue
                            self._phase_to("transcribing")
                            data = self._stt.transcribe(data, self._cancel)
                            if record:
                                record["started"].wait(0.15)
                                frame = record["frame"]
                        elif kind == "text":
                            data, frame = data
                        if data and not self._cancel.is_set():
                            self._turn(data, frame)
                        elif not self._cancel.is_set():
                            self._emit("notice", text="うまく聞き取れなかった。もう一度話してね")
                    except CallCancelled:
                        break
                    except Exception as exc:
                        self._emit("error", text=str(exc))
                if self._cancel.wait(0.25):
                    break
                forced, self._record_after_interrupt = self._record_after_interrupt, False
                self._listen(forced=forced)
        except CallCancelled:
            pass
        except Exception as exc:
            self._emit("error", text=str(exc))
        finally:
            self._cancel.set()
            self._screen.close()
            self._speech_cancel.set()
            for component, method in ((self._recorder, "stop"), (self._player, "stop")):
                try:
                    if component:
                        getattr(component, method)()
                except Exception:
                    pass
            with self._lock:
                self._finished, self._phase = True, "ended"
            try:
                self._on_finish()
            finally:
                self._emit("ended")
