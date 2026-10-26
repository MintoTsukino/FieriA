"""Local whisper.cpp input adapted from NikoVoice (MIT).

WAV is piped over stdin: no fixed temporary WAV, no writes into NikoVoice or the
recognizer directory, and no conflict when both applications use the same model.
https://github.com/ggml-org/whisper.cpp/blob/master/examples/common-whisper.cpp
"""
import io
import os
import re
import sys
import wave
from pathlib import Path

from call_process import run_child
from call_process import CallCancelled


def resolve_directory(configured):
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / "whisper-cpp"


class WhisperSTT:
    def __init__(self, cfg):
        self._configured = cfg.get("stt_dir", "")
        self.directory = resolve_directory(cfg.get("stt_dir", ""))
        self.model = cfg.get("stt_model") or "ggml-large-v3-turbo.bin"
        self.threads = cfg.get("threads", 8)
        self._model_file = self.directory / self.model

    def ensure_ready(self, cancel, notify=None):
        import call_setup

        try:
            runtime = call_setup.ensure_runtime(self._configured, self.model, cancel, notify)
        except (CallCancelled, RuntimeError):
            raise
        except Exception:
            raise RuntimeError("音声認識の準備に失敗した。ネット接続と保存先の空き容量を確認し、もう一度電話してね") from None
        self.directory, self._model_file = runtime.directory, runtime.model
        self.health()

    def health(self):
        if not (self.directory / "whisper-cli.exe").is_file():
            raise RuntimeError("音声認識が未設定。設定 → 通話でwhisper.cppのフォルダを指定してね")
        if (not isinstance(self.model, str) or self.model != os.path.basename(self.model) or
                "/" in self.model or "\\" in self.model or not self.model.lower().endswith(".bin")):
            raise RuntimeError("音声認識のモデル名にはフォルダ内の.binファイル名を指定してね")
        if not self._model_file.is_file():
            raise RuntimeError("音声認識のモデルが見つからない。設定 → 通話でモデル名を確認してね")
        return True

    def transcribe(self, audio, cancel):
        import numpy as np

        self.health()
        samples = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        wav = io.BytesIO()
        with wave.open(wav, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(samples.tobytes())
        model_arg = os.path.relpath(self._model_file, self.directory)
        args = [str(self.directory / "whisper-cli.exe"), "-m", model_arg, "-f", "-",
                "-of", "fieria-transcript", "-l", "ja", "-nt", "-np", "-t", str(self.threads)]
        raw = run_child(args, cancel, input_bytes=wav.getvalue(), cwd=str(self.directory))
        text = raw.decode("utf-8", errors="replace").strip()
        # Retain NikoVoice's noise filter without its character-specific dictionary.
        text = re.sub(r"ご視聴ありがとうございました。?", "", text).strip()
        if text in ("ありがとうございました。", "おつかれさまでした。"):
            return ""
        if re.fullmatch(r"[\s\[\(（【].*[\]\)）】]", text):
            return ""
        return text
