"""
Audio helpers for Twilio Media Streams.

Twilio sends audio as base64-encoded mu-law (G.711 ulaw), 8kHz, mono, in
~20ms chunks. We decode each chunk to 16-bit linear PCM (what Whisper/wave
expect) and use simple energy-based silence detection to figure out when
the carrier has stopped talking, so we know when to cut a segment and
transcribe it — this is the "silence detection to segment carrier speech
turns" piece from the roadmap.
"""
import audioop
import base64


def decode_twilio_chunk(payload_b64: str) -> bytes:
    """Base64 mu-law chunk from Twilio -> 16-bit linear PCM bytes."""
    mulaw_bytes = base64.b64decode(payload_b64)
    pcm_bytes = audioop.ulaw2lin(mulaw_bytes, 2)  # 2 = output sample width (16-bit)
    return pcm_bytes


def chunk_rms(pcm_bytes: bytes) -> int:
    """Root-mean-square amplitude of a PCM16 chunk — our silence detector signal."""
    if not pcm_bytes:
        return 0
    return audioop.rms(pcm_bytes, 2)


class SilenceSegmenter:
    """
    Tracks incoming PCM16 chunks and decides when a "turn" has ended based on
    a run of low-energy (silence) chunks following speech.

    Usage: call add_chunk() per incoming audio chunk. When it returns a
    non-None bytes blob, that's a complete speech segment ready to transcribe.
    """

    def __init__(
        self,
        silence_rms_threshold: int = 300,
        silence_chunks_to_end_turn: int = 25,  # ~25 * 20ms = ~500ms of silence
        min_speech_chunks: int = 10,           # ignore blips shorter than ~200ms
    ):
        self.silence_rms_threshold = silence_rms_threshold
        self.silence_chunks_to_end_turn = silence_chunks_to_end_turn
        self.min_speech_chunks = min_speech_chunks

        self._buffer = bytearray()
        self._speech_chunk_count = 0
        self._silence_run = 0
        self._in_speech = False

    def add_chunk(self, pcm_bytes: bytes):
        rms = chunk_rms(pcm_bytes)
        is_loud = rms >= self.silence_rms_threshold

        if is_loud:
            self._in_speech = True
            self._speech_chunk_count += 1
            self._silence_run = 0
            self._buffer.extend(pcm_bytes)
        elif self._in_speech:
            # still buffer a bit of trailing silence for natural cutoff
            self._buffer.extend(pcm_bytes)
            self._silence_run += 1
            if self._silence_run >= self.silence_chunks_to_end_turn:
                # Turn ended
                segment = bytes(self._buffer) if self._speech_chunk_count >= self.min_speech_chunks else None
                self._reset()
                return segment
        # else: silence before any speech started — nothing to do

        return None

    def flush(self):
        """Call at stream end to grab any trailing buffered speech."""
        if self._speech_chunk_count >= self.min_speech_chunks:
            segment = bytes(self._buffer)
            self._reset()
            return segment
        self._reset()
        return None

    def _reset(self):
        self._buffer = bytearray()
        self._speech_chunk_count = 0
        self._silence_run = 0
        self._in_speech = False
