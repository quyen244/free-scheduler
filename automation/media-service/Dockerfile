# No CUDA here on purpose: this service only downloads and remuxes. Keeping it
# off the GPU image means a yt-dlp refresh (which happens often) rebuilds a
# ~150 MB layer instead of a multi-gigabyte CUDA one.
FROM python:3.12-slim

WORKDIR /app

# ffmpeg extracts the audio track from the downloaded video.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
