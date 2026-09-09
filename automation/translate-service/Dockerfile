# No CUDA on purpose. This box has one 6 GB GPU and Whisper already sits on
# it; a 1.8B translator competing for that VRAM would slow down the stage that
# matters more. Q4_K_M on CPU is fast enough for a stage that runs once per
# video.
FROM python:3.12-slim

WORKDIR /app

# llama-cpp-python has no manylinux wheel for this platform, so pip compiles
# it. cmake and a compiler are build-time only, and are dropped again below to
# keep the image from carrying a toolchain it never uses at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential cmake git

# libllama.so links against libgomp at runtime, and --auto-remove above takes it
# away along with the toolchain: the image builds clean and then dies on the
# first translation with "cannot open shared object file". Kept as its own layer
# so re-adding it does not invalidate the pip layer above — that compile is
# close to three hours.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8002

# No --reload, unlike the other two services. Their work finishes inside the
# request; this one's runs for minutes in the background, and a reload lands as
# a graceful shutdown that blocks the port until the job drains — or, if the
# reload wins the race, strands the row at `running` for the reaper to fail.
# Edits here cost a `docker compose restart translate-service`, which is the
# honest price.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
