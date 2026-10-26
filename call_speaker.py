"""Cancellable sentence playback, adapted from NikoVoice's Speaker and SapiTTS.

Use FieriA's existing VOICEVOX/AivisSpeech settings; Windows voices are a local
fallback. No VOICEVOX CORE, old Python environment, OAuth, or cloud TTS is imported.
"""
import base64
import os
from pathlib import Path
import re
import tempfile

from call_process import CallCancelled, run_child
import tts


def system_wav(text, speed, cancel):
    if os.name != "nt":
        raise RuntimeError("Windows内蔵音声が使えない。音声エンジンを設定してね")
    # Spoken text is a data file, never PowerShell source or a command argument.
    with tempfile.TemporaryDirectory(prefix="fieria-voice-") as folder:
        root = Path(folder)
        (root / "text.txt").write_text(text, encoding="utf-8")
        script = """
$TextFile = $env:FIERIA_VOICE_TEXT
$WaveFile = $env:FIERIA_VOICE_WAV
$Rate = [int]$env:FIERIA_VOICE_RATE
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voice = $speaker.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'ja*' } | Select-Object -First 1
  if ($voice) { $speaker.SelectVoice($voice.VoiceInfo.Name) }
  $speaker.Rate = $Rate
  $speaker.SetOutputToWaveFile($WaveFile)
  $speaker.Speak([System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8))
} finally { $speaker.Dispose() }
"""
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        rate = max(-10, min(10, round((speed - 1) * 10)))
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        child_env = dict(os.environ, FIERIA_VOICE_TEXT=str(root / "text.txt"),
                         FIERIA_VOICE_WAV=str(root / "voice.wav"), FIERIA_VOICE_RATE=str(rate))
        run_child([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                  cancel, timeout=60, env=child_env)
        return (root / "voice.wav").read_bytes()


class Speaker:
    def __init__(self, call_cfg, tts_cfg, soul_id, player, notify):
        self.player, self.notify = player, notify
        self.cfg = dict(tts_cfg)
        self.cfg["speaker"] = call_cfg.get("soul_speakers", {}).get(soul_id, self.cfg.get("speaker", 0))
        self._system = call_cfg.get("voice") == "system"

    def speak(self, text, cancel):
        prepared = tts.prepare_text(text, limit=8000)
        sentences = re.split(r"(?<=[。！？!?])|\n+", prepared)
        for sentence in sentences:
            for offset in range(0, len(sentence), 120):
                if cancel.is_set() or self.player.muted:
                    return
                piece = sentence[offset:offset + 120].strip()
                if not piece:
                    continue
                if not self._system:
                    try:
                        wav = tts.synthesize(self.cfg.get("engine_url", "http://127.0.0.1:10101"), piece,
                                             self.cfg.get("speaker", 0), speed=self.cfg.get("speed", 1), timeout=5)
                    except Exception:
                        self._system = True
                        self.notify("音声エンジンに接続できないため、Windows内蔵音声で話すよ")
                if self._system:
                    wav = system_wav(piece, self.cfg.get("speed", 1), cancel)
                if cancel.is_set() or self.player.muted:
                    return
                self.player.play(wav, cancel)
