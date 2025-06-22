"""Local Text-to-Speech helper.

Loads the Hugging Face `ResembleAI/chatterbox` model once and exposes an
async `synthesize` function that returns 16-kHz WAV audio **bytes** ready for
returning from an API endpoint.

Uses GPU (CUDA) automatically if available – device = 0, else CPU.

Dependencies: transformers, torch (CUDA build preferred), soundfile,
scipy, sentencepiece.
"""
from __future__ import annotations

import asyncio
import base64
import io
import threading
from functools import lru_cache

import torch
from transformers import pipeline
import soundfile as sf

# Model name is kept constant for now; could be moved to settings later.
_MODEL_NAME = "ResembleAI/chatterbox"

# A lock to make sure only one thread initialises the pipeline.
_init_lock = threading.Lock()


def _load_pipeline():
    """Load the HF TTS pipeline lazily with GPU if available."""
    with _init_lock:
        # double-checked locking
        if _load_pipeline.cache_info().currsize:
            return _load_pipeline()  # type: ignore recursion for cache
        device = 0 if torch.cuda.is_available() else -1
        pipe = pipeline("text-to-speech", model=_MODEL_NAME, device=device)
        return pipe


# Turn function into cached Singleton
_load_pipeline = lru_cache(maxsize=1)(_load_pipeline)  # type: ignore reassignment


async def synthesize(text: str) -> bytes:
    """Asynchronously synthesize *text* to WAV bytes (16 kHz, mono)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _synthesize_sync, text)


def _synthesize_sync(text: str) -> bytes:
    pipe = _load_pipeline()
    output = pipe(text)
    audio = output["audio"]  # type: ignore[index]
    sampling_rate = output["sampling_rate"]  # type: ignore[index]

    # resample to 16 kHz if necessary
    if sampling_rate != 16000:
        import scipy.signal  # heavy import only if needed

        audio = scipy.signal.resample_poly(audio, 16000, sampling_rate)
        sampling_rate = 16000

    # Write to in-memory WAV
    with io.BytesIO() as buf:
        sf.write(buf, audio, samplerate=sampling_rate, format="WAV", subtype="PCM_16")
        wav_bytes = buf.getvalue()
    return wav_bytes


def synthesize_base64(text: str) -> str:
    """Convenience sync helper used by API: returns base64 string."""
    wav_bytes = _synthesize_sync(text)
    return base64.b64encode(wav_bytes).decode("ascii")
