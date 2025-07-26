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

class ContextManager:
    """Manages conversation context and token limits for the supervisor."""
    
    def __init__(self, max_tokens: int = 200_000, buffer_tokens: int = 500, 
                 summarization_model: str = "openai/o4-mini"):
        self.max_tokens = max_tokens
        self.buffer_tokens = buffer_tokens
        # Allow model override from environment
        self.summarization_model = os.getenv("SUMMARIZATION_MODEL", summarization_model)
        
        # Initialize tokenizer using o200k_base (same as existing Codex implementation)
        try:
            self.tokenizer = tiktoken.get_encoding("o200k_base")
        except KeyError:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        
        # Load summarization prompt template from supervisor prompts directory
        self.summarization_prompt_file = Path(__file__).parent / "prompts" / "summarization_prompt.txt"
        self.summarization_prompt_template = self._load_summarization_template()
        
        # OpenRouter client for summarization
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY")
        )
        
        logging.info(f"🧠 ContextManager initialized: {max_tokens:,} max tokens, {buffer_tokens} buffer")
    
    def _load_summarization_template(self) -> str:
        """Load the existing summarization prompt template."""
        try:
            with open(self.summarization_prompt_file, 'r') as f:
                return f.read()
        except Exception as e:
            logging.error(f"Failed to load summarization template from {self.summarization_prompt_file}: {e}")
            # Fallback template if file not found
            return """You are a context summarization expert. Your task is to create a concise summary of the provided conversation context that preserves all critical information while reducing verbosity.

PRESERVE THESE ELEMENTS:
- Current session state and objectives
- Key decisions made
- Important tool calls and their outcomes
- Any errors, failures, or security issues encountered
- Current iteration number and progress
- Active configurations and settings

Here is the context to summarize:
<context>
{context}
</context>

When you summarize, keep in mind that the summary is going to be provided to the supervisor. Focus on actionable information and current state.

Output a concise summary that maintains all critical context for continuing the session effectively."""
    
    def count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count tokens in a list of messages."""
        total_tokens = 0
        
        for message in messages:
            # Count tokens for role and content
            total_tokens += len(self.tokenizer.encode(message.get("role", "")))
            total_tokens += len(self.tokenizer.encode(message.get("content", "")))
            
            # Count tokens for tool calls if present
            if "tool_calls" in message:
                for tool_call in message["tool_calls"]:
                    total_tokens += len(self.tokenizer.encode(tool_call.get("function", {}).get("name", "")))
                    total_tokens += len(self.tokenizer.encode(tool_call.get("function", {}).get("arguments", "")))
            
            # Count tokens for tool_call_id if present
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
        
        # Keep system message, initial user message (with task config), and recent messages
        system_message = messages[0] if messages and messages[0]["role"] == "system" else None
        initial_user_message = None
        
        # Find the first user message (contains task configuration)
        for i, msg in enumerate(messages[1:], 1):  # Start from index 1, skip system message
            if msg.get("role") == "user":
                initial_user_message = msg
                initial_user_idx = i
                break
        
        recent_messages = messages[-preserve_recent:]
        
        # Messages to summarize (excluding system, initial user, and recent)
        start_idx = initial_user_idx + 1 if initial_user_message else (1 if system_message else 0)
        messages_to_summarize = messages[start_idx:-preserve_recent] if preserve_recent > 0 else messages[start_idx:]
        
        # Don't summarize if initial user message is already in recent messages
        if initial_user_message and initial_user_message in recent_messages:
            initial_user_message = None  # Don't duplicate it
        
        if not messages_to_summarize:
            return messages
        
        # Convert messages to text context (similar to existing Codex implementation)
        context_text = self._format_messages_for_summary(messages_to_summarize)
        
        # Use existing Codex summarization approach
        original_tokens = self.count_tokens(messages)
        logging.info(f"🔄 Context too long ({original_tokens:,} tokens), summarizing...")
        
        summary_content = await self._summarize_context(context_text)
        
        # Build new conversation history
        new_messages = []
        
        # Always preserve system message
        if system_message:
            new_messages.append(system_message)
        
        # Preserve initial user message with task configuration
        if initial_user_message:
            new_messages.append(initial_user_message)
        
        # Add summary as a user message (following Codex pattern)
        new_messages.append({
            "role": "user",
            "content": summary_content
        })
        
        # Add recent messages
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
                
                # Add tool calls if present
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
        summary_prompt = f"""You are a context summarization expert. Your task is to create a concise summary of the provided supervisor conversation context that preserves all critical information while reducing verbosity.

PRESERVE THESE ELEMENTS:
- Current session state and objectives  
- Key decisions made by the supervisor
- Important tool calls and their outcomes (spawned instances, followups sent, logs read)
- Any errors, failures, or security issues encountered
- Current iteration number and progress
- Active instances and their status
- Important findings or vulnerabilities discovered

SUMMARIZATION GUIDELINES:
- Maintain chronological order of important events
- Use bullet points and structured format for clarity
- Keep technical details that affect future decisions
- Remove redundant explanations and verbose descriptions
- Consolidate similar repeated actions into summaries
- Preserve exact error messages and critical outputs

Here is the context to summarize:
<context>
{context}
</context>

Output your summary in the following format exactly:
---
## Supervisor Session Summary
- **Current State**: [Where the supervisor is now]
- **Active Instances**: [List of running instances and their status]
- **Progress**: [Iterations completed, key milestones]

## Key Actions & Results
[Chronological list of important supervisor actions and outcomes]

## Important Findings
[Any vulnerabilities, errors, or critical discoveries]

## Context for Next Actions
[Information needed for supervisor to continue effectively]
---
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.summarization_model,
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=2000,
                temperature=0.1
            )
            
            return response.choices[0].message.content or "Summary generation failed"
            
        except Exception as e:
            logging.error(f"Failed to generate conversation summary: {e}")
            # Fallback summary
            return f"## Session Summary\nPrevious conversation context has been truncated due to length. {len(context.split())} words of supervisor activity occurred before this point."

    def truncate_text(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within token limit."""
        tokens = self.tokenizer.encode(text)
        if len(tokens) <= max_tokens:
            return text
        
        # Truncate and decode
        truncated_tokens = tokens[:max_tokens]
        truncated_text = self.tokenizer.decode(truncated_tokens)
        
        return f"{truncated_text}\n\n[... truncated due to length, {len(tokens) - max_tokens} tokens removed]"
    
    def truncate_command_output(self, output: str, max_lines: int = 20) -> str:
        """Truncate command output to maximum number of lines."""
        lines = output.split('\n')
        if len(lines) <= max_lines:
            return output
        
        truncated = '\n'.join(lines[:max_lines])
        remaining = len(lines) - max_lines
        return f"{truncated}\n\n[... {remaining} more lines truncated]"
    
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