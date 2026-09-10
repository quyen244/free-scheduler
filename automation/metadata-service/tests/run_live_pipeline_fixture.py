"""Read-only live quality/cost fixture for one translated source.

This calls OpenAI but never creates a metadata revision and never updates
chunks, render state, jobs, or the n8n workflow. Run explicitly; pytest will
never collect it.
"""

import json
import os
import sys

import context
from generator import INSTRUCTIONS
from openai_client import ResponsesClient
from schemas import ChunkMetadata, YouTubeMetadata


LUNA_INPUT_USD_PER_MILLION = 0.20
LUNA_OUTPUT_USD_PER_MILLION = 1.20


def main(video_id: str) -> None:
    source = context.load(video_id)
    client = ResponsesClient(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model=os.environ.get("OPENAI_MODEL", ""),
        reasoning_effort="none",
    )
    youtube = client.generate(
        item_key="youtube_fixture",
        instructions=INSTRUCTIONS,
        input_text=json.dumps(
            {"source_title": source.title, "whole_transcript": source.transcript},
            ensure_ascii=False,
        ),
        output_model=YouTubeMetadata,
    )
    results = [youtube]
    chunk_outputs = []
    for chunk in source.chunks:
        generated = client.generate(
            item_key=f"{chunk.name}_fixture",
            instructions=INSTRUCTIONS,
            input_text=json.dumps(
                {
                    "chunk_name": chunk.name,
                    "chunk_transcript": chunk.text,
                    "whole_video_summary": youtube.value.summary,
                },
                ensure_ascii=False,
            ),
            output_model=ChunkMetadata,
        )
        if generated.value.chunk_name != chunk.name:
            raise RuntimeError(f"wrong chunk identity for {chunk.name}")
        results.append(generated)
        chunk_outputs.append(
            {
                "chunk_name": chunk.name,
                "hook": generated.value.hook,
                "facebook_caption": generated.value.facebook.caption,
                "tiktok_caption": generated.value.tiktok.caption,
            }
        )

    input_tokens = sum(result.input_tokens for result in results)
    output_tokens = sum(result.output_tokens for result in results)
    estimated_cost = (
        input_tokens * LUNA_INPUT_USD_PER_MILLION
        + output_tokens * LUNA_OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    print(
        json.dumps(
            {
                "ok": True,
                "read_only": True,
                "video_id": source.video_id,
                "model": client.model,
                "reasoning_effort": client.reasoning_effort,
                "chunk_count": len(source.chunks),
                "response_ids": [result.response_id for result in results],
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": sum(result.total_tokens for result in results),
                },
                "estimated_cost_usd": round(estimated_cost, 8),
                "youtube": {
                    "title": youtube.value.title,
                    "thumbnail_text": youtube.value.thumbnail_text,
                    "hashtags": youtube.value.hashtags.flattened(),
                },
                "chunks": chunk_outputs,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "3gi_15UH9fQ")
