from fastapi import FastAPI

from schemas import ChunkMetadata, YouTubeMetadata


app = FastAPI(title="Re-up metadata service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schemas/youtube")
def youtube_schema() -> dict:
    return YouTubeMetadata.model_json_schema()


@app.get("/schemas/chunk")
def chunk_schema() -> dict:
    return ChunkMetadata.model_json_schema()
