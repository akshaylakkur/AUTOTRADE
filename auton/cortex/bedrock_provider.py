"""Amazon Bedrock LLM provider — implements both cortex and metamind interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from auton.cortex.model_router import AbstractLLMProvider
from auton.metamind.code_generator import LLMProvider

logger = logging.getLogger(__name__)


class BedrockProvider(AbstractLLMProvider, LLMProvider):
    """Concrete LLM provider backed by Amazon Bedrock.

    Authenticates via AWS access key + secret key + region.
    Supports any model hosted on Bedrock (Claude, Llama, Titan, etc.).

    Implements both the async cortex interface (``AbstractLLMProvider``)
    and the sync metamind interface (``LLMProvider``).
    """

    # Bedrock pricing per 1K input tokens (approximate, varies by model/region)
    _PRICING_PER_1K_INPUT: dict[str, float] = {
        "claude": 0.015,     # Claude 3 Sonnet/Haiku
        "claude-opus": 0.015,
        "llama": 0.00195,    # Llama 3 70B
        "titan": 0.0008,     # Titan Text
        "mistral": 0.002,    # Mistral
        "cohere": 0.0015,    # Cohere Command
        "default": 0.005,
    }

    _PRICING_PER_1K_OUTPUT: dict[str, float] = {
        "claude": 0.075,
        "claude-opus": 0.075,
        "llama": 0.00256,
        "titan": 0.0016,
        "mistral": 0.006,
        "cohere": 0.002,
        "default": 0.015,
    }

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-1",
        model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        self._access_key = access_key_id
        self._secret = secret_access_key
        self._region = region
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._client: Any = None
        self._async_client: Any = None

    # ------------------------------------------------------------------
    # Lazy client init (boto3 is heavy — only load when needed)
    # ------------------------------------------------------------------

    def _get_sync_client(self) -> Any:
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret,
            )
        return self._client

    async def _get_async_client(self) -> Any:
        if self._async_client is None:
            import aioboto3
            session = aioboto3.Session(
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret,
                region_name=self._region,
            )
            self._async_client = await session.client("bedrock-runtime").__aenter__()
        return self._async_client

    # ------------------------------------------------------------------
    # Prompt → Bedrock body (model-family aware)
    # ------------------------------------------------------------------

    def _build_body(self, prompt: str) -> dict[str, Any]:
        model_lower = self._model_id.lower()
        if "anthropic" in model_lower or "claude" in model_lower:
            return {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        if "meta" in model_lower or "llama" in model_lower:
            return {
                "prompt": f"<|begin_of_text|>{prompt}<|eot_id|><|start_header_id|>assistant<|end_header_id|>",
                "max_gen_len": self._max_tokens,
                "temperature": 0.7,
            }
        if "mistral" in model_lower:
            return {
                "prompt": f"<s>[INST] {prompt} [/INST]",
                "max_tokens": self._max_tokens,
                "temperature": 0.7,
            }
        # Generic / Titan / Cohere / others
        return {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": self._max_tokens,
                "temperature": 0.7,
            },
        }

    def _extract_response(self, body: dict[str, Any]) -> str:
        if "content" in body:
            # Anthropic Messages format
            content = body["content"]
            if isinstance(content, list):
                return content[0].get("text", "") if content else ""
            return str(content)
        if "generation" in body:
            return body["generation"]
        if "completion" in body:
            return body["completion"]
        if "results" in body:
            results = body["results"]
            if isinstance(results, list) and results:
                return results[0].get("outputText", "")
            return str(results)
        if "outputs" in body:
            outputs = body["outputs"]
            if isinstance(outputs, list) and outputs:
                return outputs[0].get("text", "")
            return str(outputs)
        if "generated_text" in body:
            return body["generated_text"]
        if "response" in body:
            return body["response"]
        return str(body)

    # ------------------------------------------------------------------
    # AbstractLLMProvider (async cortex interface)
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return f"bedrock/{self._model_id}"

    async def infer(self, prompt: str, **kwargs: Any) -> str:
        client = await self._get_async_client()
        body = self._build_body(prompt)
        logger.debug("Bedrock infer: model=%s prompt_len=%d", self._model_id, len(prompt))
        resp = await client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body),
            contentType="application/json",
        )
        raw = await resp["body"].read()
        data = json.loads(raw)
        return self._extract_response(data)

    def estimate_cost(self, prompt: str, **kwargs: Any) -> float:
        model_lower = self._model_id.lower()
        pricing_key = "default"
        for k in self._PRICING_PER_1K_INPUT:
            if k != "default" and k in model_lower:
                pricing_key = k
                break
        input_tokens = len(prompt) / 4
        output_tokens = self._max_tokens
        cost = (
            input_tokens / 1000 * self._PRICING_PER_1K_INPUT[pricing_key]
            + output_tokens / 1000 * self._PRICING_PER_1K_OUTPUT[pricing_key]
        )
        return round(cost, 6)

    # ------------------------------------------------------------------
    # LLMProvider (sync metamind interface)
    # ------------------------------------------------------------------

    def complete(self, prompt: str) -> str:
        client = self._get_sync_client()
        body = self._build_body(prompt)
        logger.debug("Bedrock complete: model=%s prompt_len=%d", self._model_id, len(prompt))
        resp = client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body),
            contentType="application/json",
        )
        data = json.loads(resp["body"].read())
        return self._extract_response(data)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._async_client is not None:
            await self._async_client.__aexit__(None, None, None)
            self._async_client = None
        self._client = None
