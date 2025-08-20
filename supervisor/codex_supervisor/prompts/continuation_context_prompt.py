#!/usr/bin/env python3
"""Prompt template for continuation context."""

def get_continuation_context_prompt(initial_context: str, summary: str, vulnerabilities_content: str, time_remaining_minutes: float) -> str:
    """Generate the continuation context prompt."""
    
    return f"""You are continuing a security testing session. Here is a summary of what has been attempted/completed so far:

## PREVIOUS SESSION SUMMARY
{summary}

Here is the task configuration that you are in the process of completing:

## TASK CONFIGURATION
{initial_context}

The following vulnerabilities have been found so far:
{vulnerabilities_content}

Review the work completed so far and the vulnerabilities already found. Your task is to pick up from the previous session, and continue the security testing process. You should continue to use the TODO list to guide your progress, and you should continue to use the instances at your disposal to test each target listed in the task configuration. If you believe that not enough time was spent on a target previously, you should return to that target and dive deeper. If the previous testing appeared to be thorough and comprehensive, you should assume that there is always more to be found, and continue to test additional attack vectors.

Begin by reviewing the current state and deciding your next actions."""