"""Opt-in, low-cost live check for the explicitly selected OpenAI model.

This file is intentionally not named ``test_*.py`` so normal tests never make
network requests or spend API credit.
"""

import json
import os

from generator import INSTRUCTIONS
from errors import MetadataError
from openai_client import ResponsesClient
from schemas import YouTubeMetadata


def main() -> None:
    client = ResponsesClient(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model=os.environ.get("OPENAI_MODEL", ""),
    )
    result = client.generate(
        item_key="youtube_fixture",
        instructions=INSTRUCTIONS,
        input_text=json.dumps(
            {
                "source_title": "Vì sao cây nghiêng về phía cửa sổ?",
                "whole_transcript": (
                    "Một chậu cây đặt trong phòng dần nghiêng về phía cửa sổ. "
                    "Ánh sáng đi vào từ một hướng nên thân cây phát triển về "
                    "phía có nhiều ánh sáng hơn. Người chăm cây xoay chậu mỗi "
                    "tuần để cây phát triển cân đối."
                ),
            },
            ensure_ascii=False,
        ),
        output_model=YouTubeMetadata,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "response_id": result.response_id,
                "model": result.model,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "total_tokens": result.total_tokens,
                },
                "validated_output": result.value.model_dump(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except MetadataError as exc:
        print(json.dumps({"ok": False, "error_code": exc.code, "retryable": exc.retryable}))
        raise SystemExit(1)
