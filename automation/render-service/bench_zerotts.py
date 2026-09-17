"""Measure the current ZeroTTS path on the same cues a VieNeu benchmark uses.

A speed number for a candidate engine only means something next to the engine
it would replace, measured on the same input, on the same box. This drives the
production synthesis call - the same model, provider and session pool the
pipeline uses - over cues read from the project's own translated transcript.

    docker compose exec render-service python bench_zerotts.py <video_id> [n]
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import library
import timing
import voice
from config import settings


def main() -> None:
    video_id = sys.argv[1] if len(sys.argv) > 1 else "hS3VXBeEv0I"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 64

    path = library.video_dir(video_id) / "transcript.vi.json"
    segments = json.loads(path.read_text(encoding="utf-8"))["segments"]
    texts = [s["text"].strip() for s in segments if s.get("text", "").strip()][:count]
    chars = sum(len(t) for t in texts)
    # The voice this project actually shipped with, per `/data/<id>/voice.json`.
    speaker = sys.argv[3] if len(sys.argv) > 3 else "maichi"

    print(f"provider={settings.tts_provider} workers={settings.tts_workers} "
          f"threads={settings.tts_threads} voice={speaker}")
    print(f"input: {len(texts)} real cues, {chars} chars")

    # Build the pool and pay the model load before the clock starts, so this
    # measures synthesis rather than startup.
    started = time.monotonic()
    voice.fill_pool(settings.tts_workers)
    voice._synthesise(voice.normalise(texts[0]), speaker)
    print(f"model load + warmup: {time.monotonic() - started:.1f} s")

    def speak(text: str) -> float:
        audio = timing.trim_silence(voice._synthesise(voice.normalise(text), speaker))
        return audio.size / timing.SAMPLE_RATE

    started = time.monotonic()
    if settings.tts_workers > 1:
        with ThreadPoolExecutor(max_workers=settings.tts_workers) as pool:
            audio_s = sum(pool.map(speak, texts))
    else:
        audio_s = sum(speak(text) for text in texts)
    elapsed = time.monotonic() - started

    print(f"elapsed {elapsed:.2f} s  audio {audio_s:.2f} s  "
          f"rtf {audio_s / elapsed:.2f}x  {chars / elapsed:.1f} char/s")


if __name__ == "__main__":
    main()
