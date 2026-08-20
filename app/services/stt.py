"""
Local Whisper STT service.

Loads the model once at process startup (expensive) and reuses it for every
transcription call. Model size is controlled by WHISPER_MODEL in .env
(tiny/base/small/medium/large — 'base' is a good speed/accuracy tradeoff
for a CPU laptop).
"""
import os
import logging
import tempfile
import wave

logger = logging.getLogger("ccbot.stt")

_model = None
_model_name = os.getenv("WHISPER_MODEL", "base")


def get_model():
    """Lazy-load the Whisper model once, reuse across calls."""
    global _model
    if _model is None:
        import whisper  # imported lazily so the app can boot without it installed yet
        logger.info(f"Loading Whisper model '{_model_name}' (first call — this can take a bit)...")
        _model = whisper.load_model(_model_name)
        logger.info("Whisper model loaded.")
    return _model


def write_pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> str:
    """
    Write raw 16-bit PCM audio bytes to a temp .wav file and return its path.
    Whisper's loader (via ffmpeg) will resample this to 16kHz internally,
    so 8kHz telephony audio (what Twilio sends) is fine as input.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return tmp.name


def transcribe_pcm16(pcm_bytes: bytes, sample_rate: int = 8000) -> str:
    """
    Transcribe a chunk of raw 16-bit PCM audio and return the text.
    Returns an empty string on failure rather than raising, so a single bad
    chunk doesn't take down the call.
    """
    if len(pcm_bytes) < 1000:
        # Too short to contain meaningful speech — skip Whisper call entirely.
        return ""

    wav_path = write_pcm16_to_wav(pcm_bytes, sample_rate=sample_rate)
    try:
        model = get_model()
        result = model.transcribe(wav_path, fp16=False, language="en")
        text = result.get("text", "").strip()
        return text
    except Exception as e:
        logger.error(f"Whisper transcription failed: {e}")
        return ""
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
