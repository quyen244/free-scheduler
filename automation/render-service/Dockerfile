# No CUDA. ZeroTTS is ONNX Runtime on the CPU by design — that is what keeps
# all 6 GB of this box's one GPU with Whisper — and ffmpeg encodes with
# libx264 unless a GPU turns out to be reachable, which render.py decides by
# trying rather than by assuming.
FROM python:3.12-slim

WORKDIR /app

# ffmpeg does the cutting, blurring, compositing and encoding; libass burns the
# subtitles; fontconfig resolves the preset's font family to a file. DejaVu
# covers Vietnamese diacritics, which most default sans faces render as boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fontconfig \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8003

# No --reload, for the same reason the translate service has none: the work
# here runs for tens of minutes in a background task, and a reload lands either
# as a graceful shutdown holding the port until the job drains or as a restart
# that strands the row at `running`. Edits cost a `docker compose restart`.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
