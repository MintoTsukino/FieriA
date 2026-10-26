"""Recording, silence detection and playback adapted from NikoVoice/audio_io.py (MIT).

Importing this module opens no devices. Only an explicit call starts a stream.
"""
import io
import threading
import wave

import numpy as np

SAMPLE_RATE = 16000
_OUTPUT_LOCK = threading.RLock()


def _sd():
    import sounddevice
    return sounddevice


def list_devices():
    result = {"input": [], "output": []}
    for index, device in enumerate(_sd().query_devices()):
        for kind in result:
            if device.get("max_" + kind + "_channels", 0) > 0:
                result[kind].append({"id": index, "name": str(device["name"])})
    return result


class Recorder:
    def __init__(self, cfg, on_ready):
        self._cfg, self._on_ready = cfg, on_ready
        self._buffer_lock = threading.Lock()
        self._io_lock = threading.RLock()
        self._stream = None
        self._buffer = []
        self._samples = 0
        self._speech = False
        self._silence = 0
        self._fired = False
        self._generation = 0
        self.on_speech = None

    def check(self):
        _sd().check_input_settings(device=self._cfg.get("input_device"), channels=1,
                                   dtype="float32", samplerate=SAMPLE_RATE)

    def start(self):
        with self._io_lock:
            if self._stream is not None:
                return
            with self._buffer_lock:
                self._buffer, self._samples, self._silence = [], 0, 0
                self._speech, self._fired = False, False
                self._generation += 1
            stream = _sd().InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                      callback=self._callback, device=self._cfg.get("input_device"))
            self._stream = stream
            try:
                stream.start()
            except Exception:
                self._stream = None
                stream.close()
                raise
            return self._generation

    def _callback(self, data, frames, time_info, status):
        fire = started = False
        with self._buffer_lock:
            generation = self._generation
            if self._fired:
                return
            self._buffer.append(data.copy())
            self._samples += frames
            rms = float(np.sqrt(np.mean(np.square(data)))) * self._cfg.get("input_gain", 1.0)
            if rms >= self._cfg.get("silence_threshold", 0.02):
                started = not self._speech
                self._speech, self._silence = True, 0
            elif self._speech:
                self._silence += frames
            limit = self._samples >= self._cfg.get("max_record_seconds", 120) * SAMPLE_RATE
            silent = (self._cfg.get("hands_free", True) and self._speech and
                      self._silence >= self._cfg.get("silence_seconds", 1.2) * SAMPLE_RATE)
            if limit or silent:
                self._fired, fire = True, True
        if fire or (started and self.on_speech):
            def dispatch():
                try:
                    if started and self.on_speech:
                        self.on_speech(generation)
                finally:
                    if fire:
                        self._on_ready(generation)
            threading.Thread(target=dispatch, daemon=True).start()

    def stop(self):
        with self._io_lock:
            self._generation += 1
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()
            with self._buffer_lock:
                buffer, self._buffer = self._buffer, []
                speech = self._speech
        if not buffer or not speech:
            return None
        audio = np.concatenate(buffer).flatten()
        return np.clip(audio * self._cfg.get("input_gain", 1.0), -1, 1)


class Player:
    def __init__(self, device=None):
        self._device = device
        self.muted = False

    def _play(self, data, rate, cancel):
        with _OUTPUT_LOCK:
            if cancel.is_set() or self.muted:
                return
            _sd().play(data, rate, device=self._device)
        # Waiting must not hold the lock needed by stop(). Only the call worker
        # plays sounds, so a later call cannot overtake a stopped playback.
        _sd().wait()

    def play(self, wav_bytes, cancel):
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            rate, channels, width = reader.getframerate(), reader.getnchannels(), reader.getsampwidth()
            raw = reader.readframes(reader.getnframes())
        types = {1: np.uint8, 2: np.dtype("<i2"), 4: np.dtype("<i4")}
        if width not in types:
            raise RuntimeError("再生できない音声形式だった")
        data = np.frombuffer(raw, dtype=types[width])
        if channels > 1:
            data = data.reshape(-1, channels)
        self._play(data, rate, cancel)

    def ring(self, cancel):
        rate = 22050
        timeline = np.arange(int(rate * 0.6)) / rate
        beep = (np.sin(2 * np.pi * 440 * timeline) * 0.15).astype(np.float32)
        fade = np.linspace(0, 1, 200)
        beep[:200] *= fade
        beep[-200:] *= fade[::-1]
        self._play(np.concatenate([beep, np.zeros(int(rate * 0.35), dtype=np.float32)]), rate, cancel)

    def stop(self):
        with _OUTPUT_LOCK:
            _sd().stop()
