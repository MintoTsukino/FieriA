"""Phone lifecycle, actual Engine wiring and audio boundaries. No real mic/GUI/API."""
import copy
import datetime
import io
import json
from pathlib import Path
import sys
import threading
import time
import wave

import numpy as np
import pytest

import call_audio
import call_process
import call_session
import call_speaker
import call_stt
import config
import engine
import scheduler
import soul


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    import roles
    home = tmp_path / "home"
    monkeypatch.setattr(config, "HOME", str(home))
    monkeypatch.setattr(config, "CONFIG_PATH", str(home / "config.json"))
    monkeypatch.setattr(soul, "HOME", str(home))
    monkeypatch.setattr(soul, "SOULS_DIR", str(home / "souls"))
    monkeypatch.setattr(roles, "HOME", str(home))
    monkeypatch.setattr(roles, "ROLES_PATH", str(home / "roles.json"))


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("phone state did not settle")


class FakeRecorder:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.check_error = None
        self.audio = np.ones(8000, dtype=np.float32)
    def check(self):
        if self.check_error:
            raise self.check_error
    def start(self):
        self.started += 1
        return self.started
    def stop(self):
        self.stopped += 1
        return self.audio


class FakeSTT:
    def __init__(self):
        self.calls = 0
        self.health_error = None
        self.before_return = None
    def health(self):
        if self.health_error:
            raise self.health_error
    def transcribe(self, data, cancel):
        self.calls += 1
        if self.before_return:
            self.before_return(cancel)
        return "音声で話した内容"


class FakePlayer:
    muted = False
    def __init__(self):
        self.stops = 0
        self.rings = 0
        self.on_ring = None
    def stop(self): self.stops += 1
    def ring(self, cancel):
        self.rings += 1
        if self.on_ring: self.on_ring(cancel)


class FakeSpeaker:
    def __init__(self):
        self.spoken = []
        self.on_speak = None
    def speak(self, text, cancel):
        self.spoken.append(text)
        if self.on_speak: self.on_speak(cancel)


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.stop_requests = 0
        self.hook = None
    def request_stop(self): self.stop_requests += 1
    def process_turn(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.hook: return self.hook(text, kwargs)
        if kwargs.get("on_delta"): kwargs["on_delta"]("返事")
        return {"reply": "返事", "operations": [], "stopped": False}


@pytest.fixture
def phone(monkeypatch):
    rec, stt, speaker, player = FakeRecorder(), FakeSTT(), FakeSpeaker(), FakePlayer()
    monkeypatch.setattr(call_session, "make_components", lambda *a, **k: (rec, stt, speaker, player))
    sessions = []
    def make(settings=None, backend=None, on_event=None):
        events, finished = [], threading.Event()
        session = call_session.CallSession(backend or FakeEngine(), settings or {}, {}, "soul", "相手",
            on_event or events.append, finished.set)
        sessions.append(session)
        return session, events, finished
    yield make, rec, stt, speaker, player
    for session in sessions:
        session.end()
        session.join(3)


def test_no_devices_or_thread_before_explicit_start(phone):
    make, rec, stt, speaker, player = phone
    session, events, finished = make()
    assert rec.started == player.rings == 0
    assert session._thread is None


def fake_share(session, monkeypatch):
    import call_screen
    from PIL import Image
    target = call_screen.Window(123, 456, 789, 111, "Editor", "共有する原稿")
    captures = []
    class Windows:
        def windows(self): return [target]
        def capture(self, selected):
            captures.append(selected)
            return Image.new("RGB", (64, 32), "navy")
    monkeypatch.setattr(session._screen, "_backend_factory", Windows)
    token = session.share_windows()["windows"][0]["id"]
    assert session.share_configure({"window": token, "mode": "every"})["ok"]
    return captures


def test_screenshot_is_taken_on_first_speech_and_paired_with_that_transcript(phone, monkeypatch):
    make, rec, stt, speaker, player = phone
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    captures = fake_share(session, monkeypatch)
    session.start()
    wait_for(lambda: session.snapshot()["phase"] == "listening")
    assert not captures  # no greeting or idle microphone screenshots
    rec.on_speech(rec.started)
    rec.on_speech(rec.started)
    wait_for(lambda: len(captures) == 1)
    session._audio_ready(rec.started)
    wait_for(lambda: len(backend.calls) >= 2)
    assert backend.calls[0][1]["images"] is None
    text, options = backend.calls[1]
    assert text == "音声で話した内容"
    assert len(options["images"]) == 1
    event = next(e for e in events if e["kind"] == "user" and e["text"] == text)
    assert event["images"] == options["images"]


def test_off_during_transcription_discards_already_captured_frame(phone, monkeypatch):
    make, rec, stt, speaker, player = phone
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    captures = fake_share(session, monkeypatch)
    stt.before_return = lambda cancel: session.share_configure({"mode": "off"})
    session.start()
    wait_for(lambda: session.snapshot()["phase"] == "listening")
    rec.on_speech(rec.started)
    wait_for(lambda: len(captures) == 1)
    session._audio_ready(rec.started)
    wait_for(lambda: len(backend.calls) >= 2)
    assert backend.calls[1][1]["images"] is None


def test_off_after_turn_submission_keeps_ui_and_engine_images_consistent(phone, monkeypatch):
    make, rec, stt, speaker, player = phone
    backend, submitted = FakeEngine(), []
    session = None
    def event(payload):
        if payload["kind"] == "user" and payload.get("images"):
            submitted.append(payload["images"])
            session.share_configure({"mode": "off"})
    session, _, _ = make(backend=backend, on_event=event)
    captures = fake_share(session, monkeypatch)
    session.start()
    wait_for(lambda: session.snapshot()["phase"] == "listening")
    rec.on_speech(rec.started)
    wait_for(lambda: len(captures) == 1)
    session._audio_ready(rec.started)
    wait_for(lambda: len(backend.calls) >= 2)
    assert submitted == [backend.calls[1][1]["images"]]
    assert submitted[0] and session.snapshot()["sharing"]["mode"] == "off"


def test_text_turn_shares_once_and_muted_stale_audio_cannot_capture(phone, monkeypatch):
    make, rec, stt, speaker, player = phone
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    captures = fake_share(session, monkeypatch)
    session.start()
    wait_for(lambda: session.snapshot()["phase"] == "listening")
    token = rec.started
    session.mute_mic(True)
    rec.on_speech(token)
    assert not captures
    choice = session.share_windows()["windows"][0]["id"]
    session.share_configure({"window": choice, "mode": "once"})
    assert session.send_text("この窓を見て")["ok"]
    wait_for(lambda: len(backend.calls) >= 2)
    assert backend.calls[1][0] == "この窓を見て"
    assert backend.calls[1][1]["images"]
    wait_for(lambda: session.snapshot()["phase"] == "paused")
    assert session.send_text("次の質問")["ok"]
    wait_for(lambda: len(backend.calls) >= 3)
    assert backend.calls[2][1]["images"] is None
    assert len(captures) == 1


def test_noise_never_triggers_first_speech_and_repeated_speech_triggers_once():
    recorder = call_audio.Recorder({"hands_free": False, "max_record_seconds": 120}, lambda _: None)
    fired, done = [], threading.Event()
    recorder.on_speech = lambda token: (fired.append(token), done.set())
    silent = np.zeros((1600, 1), dtype=np.float32)
    speech = np.ones((1600, 1), dtype=np.float32) * 0.1
    recorder._callback(silent, 1600, None, None)
    assert not fired
    recorder._callback(speech, 1600, None, None)
    assert done.wait(1)
    recorder._callback(silent, 1600, None, None)
    recorder._callback(speech, 1600, None, None)
    assert fired == [0]


def test_call_automatically_prepares_stt_before_greeting_and_recording(phone):
    make, rec, stt, speaker, player = phone
    def prepare(cancel, notify):
        assert rec.started == 0 and speaker.spoken == []
        notify({"stage": "model", "text": "準備中", "received": 5, "total": 10})
    stt.ensure_ready = prepare
    session, events, finished = make()
    session.start()
    wait_for(lambda: rec.started == 1)
    assert any(e["kind"] == "setup" and e["phase"] == "preparing" and e["received"] == 5 for e in events)


def test_hangup_during_automatic_preparation_does_not_open_mic_or_call_llm(phone):
    make, rec, stt, speaker, player = phone
    entered = threading.Event()
    def prepare(cancel, notify):
        entered.set()
        assert cancel.wait(2)
        raise call_process.CallCancelled()
    stt.ensure_ready = prepare
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    session.start()
    assert entered.wait(2)
    session.end()
    assert finished.wait(2)
    assert rec.started == 0 and backend.calls == [] and speaker.spoken == []


def test_greeting_then_hands_free_speech_uses_same_engine(phone):
    make, rec, stt, speaker, player = phone
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    session.start()
    wait_for(lambda: session.snapshot()["phase"] == "listening")
    assert backend.calls[0][0] == "（通話を開始した）"
    assert player.rings == 1 and speaker.spoken == ["返事"]
    session._audio_ready(rec.started)
    wait_for(lambda: len(backend.calls) == 2 and rec.started == 2)
    assert backend.calls[1][0] == "音声で話した内容"
    assert backend.calls[1][1]["voice"] is True
    assert all(e["call_id"] == session.id and e["soul_id"] == "soul" for e in events)
    session.end()
    assert finished.wait(2)
    assert session.snapshot()["phase"] == "ended"


def test_manual_mode_stays_idle_until_record_button(phone):
    make, rec, stt, speaker, player = phone
    session, events, finished = make({"hands_free": False})
    session.start()
    wait_for(lambda: session.snapshot()["phase"] == "ready")
    assert rec.started == 0
    assert session.toggle_record()["ok"]
    wait_for(lambda: rec.started == 1)
    session.mute_mic(True)
    assert session.snapshot()["phase"] == "paused"
    session.mute_mic(False)
    wait_for(lambda: session.snapshot()["phase"] == "ready")
    assert rec.started == 1


def test_mic_mute_stops_recording_and_prevents_old_silence_callback(phone):
    make, rec, stt, speaker, player = phone
    session, events, finished = make()
    session.start()
    wait_for(lambda: rec.started == 1)
    session.mute_mic(True)
    session._audio_ready(1)
    assert stt.calls == 0 and rec.stopped >= 1
    session.mute_mic(False)
    wait_for(lambda: rec.started == 2)
    session._audio_ready(1)
    assert session.snapshot()["phase"] == "listening"
    assert stt.calls == 0


def test_speaker_interrupt_waits_before_rearming_mic(phone):
    make, rec, stt, speaker, player = phone
    entered = threading.Event()
    def speaking(cancel):
        entered.set()
        assert cancel.wait(2)
    speaker.on_speak = speaking
    session, events, finished = make({"hands_free": False})
    session.start()
    assert entered.wait(2)
    assert rec.started == 0
    session.toggle_record()
    wait_for(lambda: rec.started == 1)
    assert player.stops >= 1


def test_end_during_dial_does_not_greet_or_record(phone):
    make, rec, stt, speaker, player = phone
    entered = threading.Event()
    def ring(cancel):
        entered.set()
        cancel.wait(2)
    player.on_ring = ring
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    session.start()
    assert entered.wait(2)
    session.end()
    assert finished.wait(2)
    assert backend.calls == [] and rec.started == 0 and speaker.spoken == []


def test_end_during_llm_discards_late_reply_and_audio(phone):
    make, rec, stt, speaker, player = phone
    entered, release = threading.Event(), threading.Event()
    backend = FakeEngine()
    def delayed(text, kwargs):
        entered.set()
        assert release.wait(2)
        assert kwargs["cancelled"]()
        return {"reply": "late", "operations": []}
    backend.hook = delayed
    session, events, finished = make(backend=backend)
    session.start()
    assert entered.wait(2)
    session.end()
    assert session.snapshot()["draining"] is True
    release.set()
    assert finished.wait(2)
    assert not any(e["kind"] == "reply" for e in events)
    assert speaker.spoken == [] and rec.started == 0


def test_end_during_transcription_does_not_submit_text_to_engine(phone):
    make, rec, stt, speaker, player = phone
    entered = threading.Event()
    stt.before_return = lambda cancel: (entered.set(), cancel.wait(2))
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    session.start()
    wait_for(lambda: rec.started == 1)
    session._audio_ready(1)
    assert entered.wait(2)
    session.end()
    assert finished.wait(2)
    assert len(backend.calls) == 1


@pytest.mark.parametrize("preflight", ["stt", "microphone"])
def test_preflight_failure_releases_call_without_recording(phone, preflight):
    make, rec, stt, speaker, player = phone
    if preflight == "stt": stt.health_error = RuntimeError("missing model")
    else: rec.check_error = RuntimeError("missing microphone")
    session, events, finished = make()
    session.start()
    assert finished.wait(2)
    assert rec.started == 0
    assert any(e["kind"] == "error" for e in events)


def test_ui_failure_disconnects_and_releases_audio(phone):
    make, rec, stt, speaker, player = phone
    session, events, finished = make(on_event=lambda payload: False)
    session.start()
    assert finished.wait(2)
    assert rec.started == 0


def test_text_input_and_speaker_mute_keep_transcript(phone):
    make, rec, stt, speaker, player = phone
    backend = FakeEngine()
    session, events, finished = make(backend=backend)
    session.start()
    wait_for(lambda: rec.started == 1)
    session.mute_speaker(True)
    assert session.send_text("URLなどの文字")["ok"]
    wait_for(lambda: len(backend.calls) == 2 and rec.started == 2)
    assert backend.calls[-1][0] == "URLなどの文字"
    assert len(speaker.spoken) == 1
    assert len([e for e in events if e["kind"] == "reply"]) == 2


@pytest.mark.parametrize("field,value,expected", [("silence_seconds", 0, 0.4), ("threads", 100, 32),
    ("input_gain", float("nan"), 1.0), ("input_device", -1, None), ("input_device", True, None),
    ("voice", "cloud", "existing"), ("hands_free", "yes", False)])
def test_call_settings_validation(field, value, expected):
    assert call_session.normalize_settings({field: value})[field] == expected


class FakeSD:
    def __init__(self):
        self.closed = 0
        self.plays = 0
        self.fail_start = False
    def InputStream(self, **kwargs):
        parent = self
        class Stream:
            def start(self):
                if parent.fail_start: raise RuntimeError("device lost")
            def stop(self): pass
            def close(self): parent.closed += 1
        return Stream()
    def play(self, *a, **k): self.plays += 1
    def stop(self): pass
    def wait(self): pass
    def check_input_settings(self, **kwargs): pass


def test_recorder_silence_gate_limit_and_cleanup(monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(call_audio, "_sd", lambda: sd)
    fired, tokens = threading.Event(), []
    recorder = call_audio.Recorder({"silence_seconds": 0.1, "max_record_seconds": 2},
        lambda token: (tokens.append(token), fired.set()))
    token = recorder.start()
    silent = np.zeros((1600, 1), dtype=np.float32)
    recorder._callback(silent, 1600, None, None)
    assert not fired.is_set()
    recorder._callback(np.ones((1600, 1), dtype=np.float32) * 0.1, 1600, None, None)
    recorder._callback(silent, 1600, None, None)
    assert fired.wait(1) and tokens == [token]
    assert len(recorder.stop()) == 4800
    assert sd.closed == 1
    recorder.stop()
    assert sd.closed == 1


def test_record_start_failure_closes_stream(monkeypatch):
    sd = FakeSD(); sd.fail_start = True
    monkeypatch.setattr(call_audio, "_sd", lambda: sd)
    recorder = call_audio.Recorder({}, lambda token: None)
    with pytest.raises(RuntimeError): recorder.start()
    assert sd.closed == 1 and recorder._stream is None


def test_recording_limit_on_silence_never_transcribes_noise(monkeypatch):
    monkeypatch.setattr(call_audio, "_sd", lambda: FakeSD())
    fired = threading.Event()
    recorder = call_audio.Recorder({"max_record_seconds": 0.1}, lambda token: fired.set())
    recorder.start()
    recorder._callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, None)
    assert fired.wait(1)
    assert recorder.stop() is None


def test_whisper_stdin_has_no_fixed_wav_or_writes_to_shared_directory(monkeypatch, tmp_path):
    (tmp_path / "whisper-cli.exe").touch(); (tmp_path / "model.bin").touch()
    captured = {}
    def run(args, cancel, **kwargs):
        captured.update(args=args, **kwargs)
        return "認識した文字".encode()
    monkeypatch.setattr(call_stt, "run_child", run)
    stt = call_stt.WhisperSTT({"stt_dir": str(tmp_path), "stt_model": "model.bin"})
    assert stt.transcribe(np.zeros(8000), threading.Event()) == "認識した文字"
    assert captured["args"][captured["args"].index("-f") + 1] == "-"
    with wave.open(io.BytesIO(captured["input_bytes"]), "rb") as wav:
        assert wav.getframerate() == 16000 and wav.getsampwidth() == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["model.bin", "whisper-cli.exe"]


@pytest.mark.parametrize("model", ["../secret.bin", "..\\secret.bin", "model.txt"])
def test_whisper_model_path_rejects_escape(tmp_path, model):
    (tmp_path / "whisper-cli.exe").touch()
    with pytest.raises(RuntimeError, match="モデル名"):
        call_stt.WhisperSTT({"stt_dir": str(tmp_path), "stt_model": model}).health()


def test_child_process_round_trip_and_early_cancellation():
    result = call_process.run_child([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                    threading.Event(), input_bytes=b"fake audio", timeout=5)
    assert result == b"fake audio"
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(call_process.CallCancelled):
        call_process.run_child([sys.executable, "-c", "pass"], cancelled)


def test_child_process_cancellation_kills_waiting_process():
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set); timer.start()
    try:
        with pytest.raises(call_process.CallCancelled):
            call_process.run_child([sys.executable, "-c", "import time; time.sleep(10)"], cancelled, timeout=5)
    finally: timer.cancel()


def test_cancellation_also_kills_child_during_blocked_stdin_write(monkeypatch):
    killed, cancelled = threading.Event(), threading.Event()
    class Child:
        returncode = -9
        def communicate(self, **kwargs):
            assert killed.wait(1), "blocked stdin was not interrupted"
            return b"", b""
        def poll(self): return -9 if killed.is_set() else None
        def kill(self): killed.set()
    monkeypatch.setattr(call_process.subprocess, "Popen", lambda *a, **k: Child())
    timer = threading.Timer(0.02, cancelled.set); timer.start()
    try:
        with pytest.raises(call_process.CallCancelled):
            call_process.run_child(["local recognizer"], cancelled, input_bytes=b"large WAV", timeout=3)
    finally: timer.cancel()


def test_system_voice_treats_spoken_text_as_data_not_powershell(monkeypatch):
    spoken = "こんにちは'; $(Write-Output secret); ` hello"
    folders = []
    def run(args, cancel, **kwargs):
        assert spoken not in " ".join(args)
        env = kwargs["env"]
        text_file = Path(env["FIERIA_VOICE_TEXT"])
        folders.append(text_file.parent)
        assert text_file.read_text(encoding="utf-8") == spoken
        Path(env["FIERIA_VOICE_WAV"]).write_bytes(b"fake WAV")
    monkeypatch.setattr(call_speaker, "run_child", run)
    assert call_speaker.system_wav(spoken, 1, threading.Event()) == b"fake WAV"
    assert not folders[0].exists()


def test_speaker_per_soul_voice_and_muted_no_playback(monkeypatch):
    captured = []
    monkeypatch.setattr(call_speaker.tts, "synthesize", lambda url, text, style, **kw: captured.append(style) or b"wav")
    class Player:
        muted = False
        def play(self, wav, cancel): pass
    player = Player()
    speaker = call_speaker.Speaker({"soul_speakers": {"soul": 43}}, {"speaker": 14}, "soul", player, lambda t: None)
    speaker.speak("声の割り当て。", threading.Event())
    assert captured == [43]
    player.muted = True
    speaker.speak("これは喋らない。", threading.Event())
    assert captured == [43]


def test_previous_chat_tts_is_discarded_when_call_stops_it(monkeypatch):
    import tts
    played = []
    monkeypatch.setattr(tts, "winsound", None)
    monkeypatch.setattr(tts, "play_wav_async", lambda wav: played.append(wav) or True)
    def synthesize(*a, **k):
        tts.stop()  # A phone call starts while the previous chat is synthesizing.
        return b"stale voice"
    monkeypatch.setattr(tts, "synthesize", synthesize)
    assert not tts.speak({"enabled": True}, "前のチャット")
    assert played == []


def test_external_cancellation_at_engine_entry_does_not_reset_or_call_llm():
    sid = soul.create_soul("切断競合")
    class LLM:
        def chat(self, *a, **k): pytest.fail("API after disconnect")
    eng = engine.Engine({"fact_layer": {"enabled": False}}, LLM(), sid)
    result = eng.process_turn("切断後", voice=True, cancelled=lambda: True)
    assert result["stopped"] and eng.messages == []


def test_disconnect_after_llm_response_blocks_automatic_document_write(tmp_path):
    sid = soul.create_soul("自動実行の切断")
    cancelled = threading.Event()
    class LLM:
        supports_native_tools = True
        def chat_tools(self, *a, **k):
            cancelled.set()
            return {"text": "", "tool_calls": [{"id": "write", "name": "write_doc",
                "arguments": {"path": "should-not-exist.md", "content": "do not write"}}]}
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["workspace_dir"] = str(tmp_path)
    cfg["auto_recall"]["enabled"] = False
    cfg["tool_approvals"]["workspace_write"] = "auto"
    result = engine.Engine(cfg, LLM(), sid).process_turn("原稿を書いて", voice=True, cancelled=cancelled.is_set)
    assert result["stopped"] and result["operations"] == []
    assert not (tmp_path / "should-not-exist.md").exists()


def test_voice_and_text_share_memory_but_voice_context_does_not_leak():
    sid = soul.create_soul("同じSOUL")
    systems = []
    class LLM:
        def chat(self, messages, **kwargs):
            systems.append(messages[0]["content"])
            return "同じ会話の返事"
    eng = engine.Engine({"fact_layer": {"enabled": False}}, LLM(), sid)
    eng.process_turn("文字の会話")
    eng.process_turn("声の会話", voice=True)
    eng.process_turn("文字へ戻る")
    assert engine.VOICE_CONTEXT not in systems[0] and engine.VOICE_CONTEXT in systems[1]
    assert engine.VOICE_CONTEXT not in systems[2]
    assert len(eng.messages) == 6
    assert [r["text"] for r in soul.read_today_log(sid) if r["who"] == "user"] == ["文字の会話", "声の会話", "文字へ戻る"]


def test_real_engine_keeps_call_frame_and_transcript_in_same_user_message():
    import base64
    from PIL import Image
    sid = soul.create_soul("画像と話す")
    out = io.BytesIO()
    Image.new("RGB", (32, 16), "navy").save(out, "JPEG")
    image = {"mime": "image/jpeg", "b64": base64.b64encode(out.getvalue()).decode("ascii")}
    sent = []
    class LLM:
        def chat(self, messages, **kwargs):
            sent.append(copy.deepcopy(messages))
            return "画像を見た返事"
    eng = engine.Engine({"fact_layer": {"enabled": False}}, LLM(), sid)
    eng.process_turn("この窓を見て", images=[image], voice=True)
    message = next(m for m in sent[0] if m["role"] == "user")
    assert "この窓を見て" in message["content"]
    assert message["images"] == [image]
    log = next(r for r in soul.read_today_log(sid) if r["who"] == "user")
    assert log["text"].startswith("この窓を見て\n[画像: ")
    attachment = log["text"].split("[画像: ", 1)[1].rstrip("]")
    assert Path(soul.soul_dir(sid), attachment).is_file()


def test_scheduler_call_reservation_does_not_mark_daily_work_done(monkeypatch):
    schedule = scheduler.Scheduler()
    ran = []
    schedule._get_context = lambda: ({}, object(), "soul")
    monkeypatch.setattr(schedule, "_next_due_job", lambda *a: {"id": "test", "name": "test", "run": lambda *a: ran.append(True) or []})
    assert schedule.reserve_for_call()
    assert schedule.tick(datetime.datetime(2026, 10, 27, 12)) is None
    assert not ran and schedule._last_run_dates == {}
    schedule.release_call()
    schedule.tick(datetime.datetime(2026, 10, 27, 12))
    assert ran == [True]


def test_bridge_pins_soul_and_blocks_mutations_during_call(monkeypatch, phone):
    import gui
    make, rec, stt, speaker, player = phone
    monkeypatch.setattr(gui.tts_mod, "stop", lambda: None)
    bridge = gui.Bridge()
    sid = soul.create_soul("電話先")
    bridge._cfg["active_soul"] = sid
    bridge._engine = FakeEngine()
    bridge._push_call_event = lambda payload: True
    before = copy.deepcopy(bridge._cfg)
    result = bridge.start_call()
    assert result["ok"] and bridge._busy_turns == 1
    assert bridge.start_call().get("error")
    assert bridge.send_message("競合").get("error")
    assert bridge.save_settings({"llm": {}}).get("error")
    assert bridge.create_soul("別のSOUL", "").get("error")
    assert bridge.save_api_key("ANTHROPIC_API_KEY", "must not save").get("error")
    assert bridge._cfg == before
    # Sharing is only an explicit call UI capability, and starts OFF.
    assert bridge.call_status()["sharing"]["mode"] == "off"
    assert bridge.call_share_configure({"window": "123", "mode": "every"}).get("error")
    assert bridge.call_share_configure({"mode": "off"})["ok"]
    bridge.end_call(); bridge._call.join(2)
    assert bridge._busy_turns == 0 and not bridge._scheduler._call_hold
    assert bridge.call_share_windows().get("error")
    assert bridge.call_share_configure({"mode": "every"}).get("error")


def test_config_backfills_call_fields_without_overwriting_preferences():
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["call"] = {"hands_free": False, "stt_dir": "chosen"}
    config.save_config(cfg)
    loaded = config.load_config()
    assert loaded["call"]["hands_free"] is False and loaded["call"]["stt_dir"] == "chosen"
    assert loaded["call"]["silence_seconds"] == 1.2
