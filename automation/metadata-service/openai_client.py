"""Small Responses API adapter with strict JSON-schema output."""

import json
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from errors import MetadataError


OutputModel = TypeVar("OutputModel", bound=BaseModel)


@dataclass(frozen=True)
class Generated:
    value: BaseModel
    response_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ResponsesClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        endpoint: str = "https://api.openai.com/v1/responses",
        timeout_s: float = 90.0,
        max_output_tokens: int = 3000,
        reasoning_effort: str | None = "none",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise MetadataError("openai_key_missing", "OPENAI_API_KEY is not configured.", retryable=False)
        if not model:
            raise MetadataError("openai_model_missing", "OPENAI_MODEL is not configured.", retryable=False)
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.transport = transport

    def generate(
        self,
        *,
        item_key: str,
        instructions: str,
        input_text: str,
        output_model: type[OutputModel],
    ) -> Generated:
        schema_name = f"{item_key.replace('-', '_')}_metadata"[:64]
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "metadata": {"item_key": item_key},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": output_model.model_json_schema(),
                }
            },
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MetadataError("openai_transport", "OpenAI could not be reached.", retryable=True) from exc

        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise MetadataError(
                f"openai_http_{response.status_code}",
                "OpenAI temporarily failed." if retryable else "OpenAI rejected the request.",
                retryable=retryable,
            )

        body: dict = {}
        try:
            body = response.json()
            output_text = _output_text(body)
            value = output_model.model_validate(json.loads(output_text))
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            body = body if isinstance(body, dict) else {}
            usage = body.get("usage") or {}
            raise MetadataError(
                "openai_output_invalid",
                "OpenAI returned metadata that failed validation.",
                retryable=True,
                response_id=str(body.get("id") or "") or None,
                response_model=str(body.get("model") or self.model),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            ) from exc

        usage = body.get("usage") or {}
        return Generated(
            value=value,
            response_id=str(body.get("id") or ""),
            model=str(body.get("model") or self.model),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
        )


def _output_text(body: dict) -> str:
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"])
            if content.get("type") == "refusal":
                raise ValueError("model refused the metadata request")
    raise ValueError("response contains no output text")
