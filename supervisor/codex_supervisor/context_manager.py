#!/usr/bin/env python3
"""Context management utilities for the supervisor system."""

import asyncio
import json
import logging
import tiktoken
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from openai import AsyncOpenAI
import os
from .prompts.summarization_prompt import get_summarization_prompt

class ContextManager:
    """Manages conversation context and token limits for the supervisor."""
    
    def __init__(self, max_tokens: int = 200_000, buffer_tokens: int = 15_000, 
                 summarization_model: str = "openai/o4-mini"):
        self.max_tokens = max_tokens
        self.buffer_tokens = buffer_tokens
        self.summarization_model = os.getenv("SUMMARIZATION_MODEL", summarization_model)
        
        try:
            self.tokenizer = tiktoken.get_encoding("o200k_base")
        except KeyError:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        
        
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY")
        )
        
        logging.info(f"🧠 ContextManager initialized: {max_tokens:,} max tokens, {buffer_tokens:,} buffer (triggers at {max_tokens - buffer_tokens:,})")
    
    def count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count tokens in a list of messages."""
        total_tokens = 0
        
        for message in messages:
            total_tokens += len(self.tokenizer.encode(message.get("role", "")))
            total_tokens += len(self.tokenizer.encode(message.get("content", "")))
            
            if "tool_calls" in message:
                for tool_call in message["tool_calls"]:
                    total_tokens += len(self.tokenizer.encode(tool_call.get("function", {}).get("name", "")))
                    total_tokens += len(self.tokenizer.encode(tool_call.get("function", {}).get("arguments", "")))
            
            if "tool_call_id" in message:
                total_tokens += len(self.tokenizer.encode(message["tool_call_id"]))
        
        return total_tokens
    
    def should_summarize(self, messages: List[Dict[str, Any]]) -> bool:
        """Check if conversation should be summarized due to token limit."""
        token_count = self.count_tokens(messages)
        return token_count >= (self.max_tokens - self.buffer_tokens)
    
    async def summarize_conversation(self, messages: List[Dict[str, Any]], 
                                   preserve_recent: int = 20) -> List[Dict[str, Any]]:
        """Summarize conversation history while preserving system, initial user, and recent messages."""
        if len(messages) <= preserve_recent + 2:  # +2 for system message and initial user message
            return messages
        
        system_message = messages[0] if messages and messages[0]["role"] == "system" else None
        initial_user_message = None
        
        for i, msg in enumerate(messages[1:], 1):
            if msg.get("role") == "user":
                initial_user_message = msg
                initial_user_idx = i
                break
        
        recent_messages = messages[-preserve_recent:]
        
        start_idx = initial_user_idx + 1 if initial_user_message else (1 if system_message else 0)
        messages_to_summarize = messages[start_idx:-preserve_recent] if preserve_recent > 0 else messages[start_idx:]
        
        if initial_user_message and initial_user_message in recent_messages:
            initial_user_message = None  # Don't duplicate it
        
        if not messages_to_summarize:
            return messages
        
        context_text = self._format_messages_for_summary(messages_to_summarize)
        
        original_tokens = self.count_tokens(messages)
        logging.info(f"🔄 Context too long ({original_tokens:,} tokens), summarizing...")
        
        summary_content = await self._get_summary(context_text)
        
        new_messages = []
        
        if system_message:
            new_messages.append(system_message)
        
        if initial_user_message:
            new_messages.append(initial_user_message)
        
        new_messages.append({
            "role": "user",
            "content": summary_content
        })
        
        new_messages.extend(recent_messages)
        
        new_tokens = self.count_tokens(new_messages)
        logging.info(f"✅ Context summarized from {original_tokens:,} to {new_tokens:,} tokens")
        
        return new_messages
    
    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages for summarization prompt."""
        formatted_lines = []
        
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if role == "user":
                formatted_lines.append(f"USER: {content}")
            elif role == "assistant":
                formatted_lines.append(f"ASSISTANT: {content}")
                
                if "tool_calls" in msg:
                    for tool_call in msg["tool_calls"]:
                        func_name = tool_call.get("function", {}).get("name", "")
                        func_args = tool_call.get("function", {}).get("arguments", "")
                        formatted_lines.append(f"  TOOL_CALL: {func_name}({func_args})")
            elif role == "tool":
                tool_id = msg.get("tool_call_id", "unknown")
                formatted_lines.append(f"TOOL_RESULT[{tool_id}]: {content}")
        
        return "\n".join(formatted_lines)
    
    async def _get_summary(self, context: str) -> str:
        """Get conversation summary from LLM."""
        summary_prompt = get_summarization_prompt(context)
        
        try:
            response = await self.client.chat.completions.create(
                model=self.summarization_model,
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=2000,
                temperature=0.1
            )
            
            return response.choices[0].message.content or "Summary generation failed"
            
        except Exception as e:
            logging.error(f"❌ ContextManager: Summarization failed: {type(e).__name__}: {e}")
            return f"## Session Summary\nPrevious conversation context has been truncated due to length. {len(context.split())} words of supervisor activity occurred before this point."

    
    def get_context_stats(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get context statistics for monitoring."""
        token_count = self.count_tokens(messages)
        return {
            "total_messages": len(messages),
            "total_tokens": token_count,
            "max_tokens": self.max_tokens,
            "buffer_remaining": max(0, self.max_tokens - self.buffer_tokens - token_count),
            "should_summarize": self.should_summarize(messages),
            "utilization_percent": round((token_count / self.max_tokens) * 100, 1)
        }