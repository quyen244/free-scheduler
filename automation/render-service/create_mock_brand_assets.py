"""Create the local, non-copyrighted signature-music fixture for mock-brand."""

from __future__ import annotations

import argparse
import subprocess

from config import settings


def create(*, force: bool = False) -> str:
    output = settings.data_dir / "music" / "mock-signature.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        return str(output)

    command = [
        "ffmpeg",
        "-y" if force else "-n",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:sample_rate=48000:duration=8",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=329.63:sample_rate=48000:duration=8",
        "-filter_complex",
        "[0:a]volume=0.7[a0];[1:a]volume=0.3[a1];"
        "[a0][a1]amix=inputs=2:duration=shortest:normalize=0,"
        "afade=t=in:st=0:d=0.4,afade=t=out:st=7.4:d=0.6[aout]",
        "-map",
        "[aout]",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return str(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(create(force=args.force))
