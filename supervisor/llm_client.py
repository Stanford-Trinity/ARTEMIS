import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import boto3
from openai import AsyncOpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[ToolCall]
    raw: Optional[Dict[str, Any]] = None


def get_provider() -> str:
    provider = os.getenv("LLM_PROVIDER")
    if provider:
        return provider.lower()
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("BEDROCK_MODEL_ID"):
        return "bedrock"
    return "openai"


DEFAULT_BEDROCK_MODEL_ID = "anthropic.claude-opus-4-5-20240620-v1:0"


class LLMClient:
    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.provider = (provider or get_provider()).lower()

        if self.provider in {"openai", "openrouter"}:
            resolved_key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
            resolved_url = base_url
            if not resolved_url and self.provider == "openrouter":
                resolved_url = "https://openrouter.ai/api/v1"
            if not resolved_url and self.provider == "openai":
                resolved_url = "https://api.openai.com/v1"
            self.client = AsyncOpenAI(api_key=resolved_key, base_url=resolved_url)
            self.bedrock = None
        else:
            resolved_region = region or os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION")
            self.bedrock = boto3.client("bedrock-runtime", region_name=resolved_region)
            self.client = None

    async def chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_completion_tokens: Optional[int] = None,
    ) -> LLMResponse:
        if self.provider in {"openai", "openrouter"}:
            params: Dict[str, Any] = {
                "model": model,
                "messages": messages,
            }
            if tools:
                params["tools"] = tools
            if tool_choice:
                params["tool_choice"] = tool_choice

            if self.provider == "openrouter":
                if max_tokens is None and max_completion_tokens is not None:
                    max_tokens = max_completion_tokens
                if max_tokens is not None:
                    params["max_tokens"] = max_tokens
                if temperature is not None:
                    params["temperature"] = temperature
            else:
                if max_completion_tokens is None and max_tokens is not None:
                    max_completion_tokens = max_tokens
                if max_completion_tokens is not None:
                    params["max_completion_tokens"] = max_completion_tokens

            response = await self.client.chat.completions.create(**params)
            raw = None
            try:
                raw = response.model_dump()
            except Exception:
                raw = None

            message = response.choices[0].message
            content = message.content or ""
            tool_calls = []
            if message.tool_calls:
                for call in message.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id=call.id,
                            name=call.function.name,
                            arguments=call.function.arguments or "{}",
                        )
                    )
            return LLMResponse(content=content, tool_calls=tool_calls, raw=raw)

        model_id = model or os.getenv("BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID
        if not model_id:
            raise ValueError("BEDROCK_MODEL_ID must be set when using Bedrock provider.")

        system, bedrock_messages = self._convert_messages(messages)
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": bedrock_messages,
            "max_tokens": max_tokens or max_completion_tokens or 10_000,
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = self._convert_tools(tools)
        if tool_choice:
            if tool_choice == "auto":
                body["tool_choice"] = {"type": "auto"}
            else:
                body["tool_choice"] = tool_choice

        response = await asyncio.to_thread(self._invoke_bedrock, model_id, body)
        response_body = json.loads(response["body"].read().decode("utf-8"))

        content_parts = []
        tool_calls = []
        for block in response_body.get("content", []):
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=json.dumps(block.get("input", {})),
                    )
                )

        return LLMResponse(content="".join(content_parts), tool_calls=tool_calls, raw=response_body)

    def format_tool_calls(self, tool_calls: List[ToolCall]) -> List[Dict[str, Any]]:
        return [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments,
                },
            }
            for call in tool_calls
        ]

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> tuple[Optional[str], List[Dict[str, Any]]]:
        system_messages = []
        bedrock_messages: List[Dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                system_messages.append(str(content))
                continue

            if role in {"user", "assistant"}:
                content_blocks = []
                if content:
                    content_blocks.append({"type": "text", "text": str(content)})

                for tool_call in message.get("tool_calls", []) or []:
                    call_id, name, arguments = self._extract_tool_call_fields(tool_call)
                    try:
                        input_payload = json.loads(arguments) if arguments else {}
                    except json.JSONDecodeError:
                        input_payload = {}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": name,
                            "input": input_payload,
                        }
                    )

                if not content_blocks:
                    content_blocks.append({"type": "text", "text": ""})

                bedrock_messages.append(
                    {
                        "role": role,
                        "content": content_blocks,
                    }
                )
                continue

            if role == "tool":
                tool_call_id = message.get("tool_call_id", "")
                bedrock_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": str(content),
                            }
                        ],
                    }
                )

        system = "\n\n".join(system_messages) if system_messages else None
        return system, bedrock_messages

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted = []
        for tool in tools:
            if tool.get("type") == "function":
                function = tool.get("function", {})
                converted.append(
                    {
                        "name": function.get("name", ""),
                        "description": function.get("description", ""),
                        "input_schema": function.get("parameters", {}),
                    }
                )
            else:
                converted.append(tool)
        return converted

    def _extract_tool_call_fields(self, tool_call: Any) -> tuple[str, str, str]:
        if hasattr(tool_call, "function"):
            return (
                getattr(tool_call, "id", ""),
                getattr(tool_call.function, "name", ""),
                getattr(tool_call.function, "arguments", "") or "{}",
            )

        if isinstance(tool_call, dict):
            function = tool_call.get("function", {})
            return (
                tool_call.get("id", ""),
                function.get("name", ""),
                function.get("arguments", "") or "{}",
            )

        return ("", "", "{}")

    def _invoke_bedrock(self, model_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.bedrock.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
