#!/usr/bin/env python3
"""Unified summarization prompt for supervisor conversations."""

def get_summarization_prompt(context: str) -> str:
    """Generate the summarization prompt used for both rolling context and continuation summaries."""
    
    return f"""You are a context summarization expert. You will be provided with a conversation between a user and an assistant. We will call the assistant the "supervisor". Your goal is to create an extremely accurate and detailed summary of all of the actions taken by the supervisor, and the results of those actions.

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
- Preserve all critical outputs

Here is the context to summarize:
<context>
{context}
</context>
"""