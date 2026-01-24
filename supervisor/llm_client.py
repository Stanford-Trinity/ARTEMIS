import asyncio
import io
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import boto3
import requests
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
            self.bedrock_bearer_token = None
            self.bedrock_region = None
        else:
            resolved_region = region or os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION")
            self.bedrock_bearer_token = os.getenv("AWS_BEARER_TOKEN_BEDROCK") or os.getenv("AWS_BEARER_TOKEN")
            self.bedrock_region = resolved_region
            if self.bedrock_bearer_token:
                self.bedrock = None
            else:
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

        if model_id.startswith("anthropic."):
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

        system_blocks, converse_messages = self._convert_messages_converse(messages)
        converse_body: Dict[str, Any] = {
            "messages": converse_messages,
        }
        if system_blocks:
            converse_body["system"] = system_blocks

        inference_config: Dict[str, Any] = {}
        if max_tokens or max_completion_tokens:
            requested_max = max_tokens or max_completion_tokens
            inference_config["maxTokens"] = min(int(requested_max), 8192)
        if temperature is not None:
            inference_config["temperature"] = temperature
        if inference_config:
            converse_body["inferenceConfig"] = inference_config

        if tools:
            converse_body["toolConfig"] = {"tools": self._convert_tools_converse(tools)}

        response_body = await asyncio.to_thread(self._converse_bedrock, model_id, converse_body)
        output = response_body.get("output", {})
        message = output.get("message", {})
        content_parts = []
        tool_calls = []
        for block in message.get("content", []) or []:
            if "text" in block:
                content_parts.append(block.get("text", ""))
            elif "toolUse" in block:
                tool_use = block.get("toolUse", {}) or {}
                tool_calls.append(
                    ToolCall(
                        id=tool_use.get("toolUseId", ""),
                        name=tool_use.get("name", ""),
                        arguments=json.dumps(tool_use.get("input", {})),
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

    def _convert_messages_converse(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        system_blocks: List[Dict[str, Any]] = []
        converse_messages: List[Dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                if content:
                    system_blocks.append({"text": str(content)})
                continue

            if role in {"user", "assistant"}:
                content_blocks: List[Dict[str, Any]] = []
                if content:
                    content_blocks.append({"text": str(content)})

                for tool_call in message.get("tool_calls", []) or []:
                    call_id, name, arguments = self._extract_tool_call_fields(tool_call)
                    try:
                        input_payload = json.loads(arguments) if arguments else {}
                    except json.JSONDecodeError:
                        input_payload = {}
                    content_blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": call_id,
                                "name": name,
                                "input": input_payload,
                            }
                        }
                    )

                if not content_blocks:
                    content_blocks.append({"text": ""})

                converse_messages.append(
                    {
                        "role": role,
                        "content": content_blocks,
                    }
                )
                continue

            if role == "tool":
                tool_call_id = message.get("tool_call_id", "")
                result_block: Dict[str, Any]
                if isinstance(content, str):
                    try:
                        result_block = {"json": json.loads(content)}
                    except json.JSONDecodeError:
                        result_block = {"text": content}
                else:
                    result_block = {"text": str(content)}

                converse_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": tool_call_id,
                                    "content": [result_block],
                                }
                            }
                        ],
                    }
                )

        return system_blocks, converse_messages

    def _convert_tools_converse(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted = []
        for tool in tools:
            if tool.get("type") == "function":
                function = tool.get("function", {})
                converted.append(
                    {
                        "toolSpec": {
                            "name": function.get("name", ""),
                            "description": function.get("description", ""),
                            "inputSchema": {"json": function.get("parameters", {})},
                        }
                    }
                )
            else:
                converted.append(tool)
        return converted

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
        if self.bedrock_bearer_token:
            if not self.bedrock_region:
                raise ValueError("BEDROCK_REGION or AWS_REGION must be set when using a Bedrock bearer token.")
            url = f"https://bedrock-runtime.{self.bedrock_region}.amazonaws.com/model/{model_id}/invoke"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            token = self.bedrock_bearer_token
            headers["Authorization"] = f"Bearer {token}"
            if token.startswith("bedrock-api-key-"):
                headers["x-amz-bedrock-api-key"] = token
            response = requests.post(url, headers=headers, data=json.dumps(body), timeout=120)
            if not response.ok:
                snippet = response.text[:1000] if response.text else ""
                raise RuntimeError(
                    f"Bedrock bearer-token request failed: HTTP {response.status_code} {response.reason}. {snippet}"
                )
            return {"body": io.BytesIO(response.content)}

        if not self.bedrock:
            raise ValueError("Bedrock client is not initialized.")
        return self.bedrock.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

    def _converse_bedrock(self, model_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.bedrock_bearer_token:
            if not self.bedrock_region:
                raise ValueError("BEDROCK_REGION or AWS_REGION must be set when using a Bedrock bearer token.")
            url = f"https://bedrock-runtime.{self.bedrock_region}.amazonaws.com/model/{model_id}/converse"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bedrock_bearer_token}",
            }
            response = requests.post(url, headers=headers, data=json.dumps(body), timeout=120)
            if not response.ok:
                snippet = response.text[:1000] if response.text else ""
                raise RuntimeError(
                    f"Bedrock converse request failed: HTTP {response.status_code} {response.reason}. {snippet}"
                )
            return json.loads(response.content.decode("utf-8"))

        if not self.bedrock:
            raise ValueError("Bedrock client is not initialized.")
        return self.bedrock.converse(modelId=model_id, **body)
