"""Benchmark one injected translation profile against a transcript slice.

This is deliberately a service-level harness, not another inference backend:
it calls ``translator.translate_segments`` so prompt injection, timing budgets,
alignment checks and llama.cpp GPU offload are identical to production.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import budget
import translator


class TimedModel:
    """Decorate the injected llama.cpp model without changing the translator."""

    def __init__(self, model):
        self._model = model
        self.calls: list[float] = []

    def create_chat_completion(self, **kwargs):
        started = time.perf_counter()
        try:
            return self._model.create_chat_completion(**kwargs)
        finally:
            self.calls.append(time.perf_counter() - started)


def gpu_memory_mib() -> int | None:
    """Return current device memory inside the GPU-enabled container."""
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
        )
        return int(output.splitlines()[0].strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    source = json.loads(args.transcript.read_text(encoding="utf-8"))
    if source.get("language") != "en":
        raise ValueError(f"benchmark expects English input, got {source.get('language')!r}")
    segments = source["segments"][: args.count]
    if len(segments) != args.count:
        raise ValueError(f"requested {args.count} segments, only found {len(segments)}")

    load_started = time.perf_counter()
    loaded = translator.get_model()
    load_seconds = time.perf_counter() - load_started
    timed = TimedModel(loaded)
    translator.get_model = lambda: timed

    budget_report: list[dict] = []
    run_started = time.perf_counter()
    translated = translator.translate_segments(segments, "en", budget_report)
    run_seconds = time.perf_counter() - run_started

    rows = []
    for index, (source_segment, translated_segment) in enumerate(zip(segments, translated), 1):
        slot_seconds = float(source_segment["end"]) - float(source_segment["start"])
        predicted_seconds = budget.speech_seconds(str(translated_segment["text"]))
        rows.append(
            {
                "index": index,
                "start": source_segment["start"],
                "end": source_segment["end"],
                "source": source_segment["text"],
                "translation": translated_segment["text"],
                "source_chars": len(source_segment["text"].strip()),
                "translation_chars": len(str(translated_segment["text"]).strip()),
                "char_budget": budget.chars_for_slot(slot_seconds, translator.settings.target_ratio),
                "predicted_spoken_s": round(predicted_seconds, 3),
                "allowed_spoken_s": round(slot_seconds * translator.settings.target_ratio, 3),
                "fits_timing": budget.fits(
                    str(translated_segment["text"]), slot_seconds, translator.settings.target_ratio
                ),
            }
        )

    profile = translator.get_profile()
    print(
        json.dumps(
            {
                "profile": profile.name,
                "model": str(translator.settings.model_path),
                "gpu_layers": translator.settings.gpu_layers,
                "budget_first_pass": profile.budget_first_pass,
                "segments": len(rows),
                "model_load_seconds": round(load_seconds, 3),
                "translation_seconds": round(run_seconds, 3),
                "model_calls": len(timed.calls),
                "mean_model_call_seconds": round(sum(timed.calls) / len(timed.calls), 3),
                "segments_per_second": round(len(rows) / run_seconds, 3),
                "fit_count": sum(row["fits_timing"] for row in rows),
                "fit_rate": round(sum(row["fits_timing"] for row in rows) / len(rows), 3),
                "mean_source_chars": round(sum(row["source_chars"] for row in rows) / len(rows), 1),
                "mean_translation_chars": round(
                    sum(row["translation_chars"] for row in rows) / len(rows), 1
                ),
                "mean_char_ratio": round(
                    sum(row["translation_chars"] for row in rows)
                    / sum(row["source_chars"] for row in rows),
                    3,
                ),
                "budget_retry_rows": len(budget_report),
                "gpu_memory_after_load_mib": gpu_memory_mib(),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
