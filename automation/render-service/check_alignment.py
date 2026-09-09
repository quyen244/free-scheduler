"""Measure, per segment, how late the spoken words start against Whisper.

The one question this stage exists to answer, asked of any video rather than
of the test fixture — drift is invisible until someone watches the result, and
"it sounded fine" is not a measurement.

    docker compose exec render-service python check_alignment.py <video_id>
"""

import json
import sys
import wave

import numpy as np

video_id = sys.argv[1]
root = f"/data/{video_id}"

manifest = json.load(open(f"{root}/voice.json", encoding="utf-8"))
with wave.open(f"{root}/voice.wav", "rb") as handle:
    rate = handle.getframerate()
    track = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)

floor = 0.01 * 32767
offsets = []
silent = []
overruns = []

for segment in manifest["segments"]:
    if segment["spoken_s"] == 0:
        continue
    at = int(round(segment["start"] * rate))
    end = int(round(segment["end"] * rate))
    window = np.abs(track[at:end])
    loud = np.flatnonzero(window >= floor)
    if loud.size == 0:
        silent.append(segment["idx"])
        continue
    offsets.append((segment["idx"], (loud[0] / rate) * 1000))
    # Did the fitted audio stay inside its slot?
    if loud[-1] >= (end - at) - 1:
        overruns.append(segment["idx"])

values = [value for _, value in offsets]
print(f"video_id           {video_id}")
print(f"track              {track.size / rate:.3f}s at {rate} Hz")
print(f"segments measured  {len(values)} of {manifest['total_segments']}")
print(f"worst offset       {max(values):.3f} ms  (segment {max(offsets, key=lambda p: p[1])[0]})")
print(f"mean offset        {sum(values) / len(values):.3f} ms")
print(f"over 50 ms         {sum(1 for value in values if value > 50)}")
print(f"silent slots       {silent or 'none'}")
print(f"slots filled edge to edge {len(overruns)}")
print(f"warnings           {len(manifest['warnings'])}")

ratios = [segment["ratio"] for segment in manifest["segments"] if segment["spoken_s"]]
print(f"ratio min/median/max  {min(ratios):.2f} / {sorted(ratios)[len(ratios) // 2]:.2f} / {max(ratios):.2f}")
print(f"segments needing >1.35x  {sum(1 for r in ratios if r > 1.35)}")
print(f"segments under 0.75x     {sum(1 for r in ratios if r < 0.75)}")
