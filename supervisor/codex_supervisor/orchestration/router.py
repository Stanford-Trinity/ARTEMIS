#!/usr/bin/env python3
import json
import logging
import os
from typing import Dict, Any
from openai import AsyncOpenAI


class TaskRouter:
    """Routes tasks to appropriate specialist codex instances using an LLM."""
    
    def __init__(self, router_model: str = "openai/o4-mini"):
        self.router_model = router_model
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1"
        )
        
        # Temporary specialists - user will define these properly later
        self.specialists = ["security", "web", "infrastructure", "data"]
    
    async def route_task(self, task_description: str) -> Dict[str, Any]:
        """Route a task to the appropriate specialist instance."""
        from ..prompts.router_prompt import get_router_prompt
        
        try:
            prompt = get_router_prompt(task_description, self.specialists)
            
            response = await self.client.chat.completions.create(
                model=self.router_model,
                messages=[
                    {"role": "system", "content": "You are a precise task routing system. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=200
            )
            
            content = response.choices[0].message.content.strip()
            
            # Clean up response if it has markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            try:
                routing_result = json.loads(content)
            except json.JSONDecodeError as json_err:
                logging.error(f"❌ TaskRouter: Failed to parse JSON response: {json_err}")
                logging.error(f"❌ TaskRouter: Raw content that failed to parse: '{content}'")
                return {"specialist": "generalist"}
            
            specialist_name = routing_result.get("specialist", "generalist")
            
            # Validate specialist exists
            if specialist_name not in self.specialists:
                logging.warning(f"⚠️  TaskRouter: Invalid specialist '{specialist_name}' not in {self.specialists}, falling back to 'generalist'")
                specialist_name = "generalist"
            
            logging.info(f"🧭 Router selected specialist: {specialist_name} for task: {task_description[:100]}{'...' if len(task_description) > 100 else ''}")
            return {"specialist": specialist_name}
            
        except Exception as e:
            logging.error(f"❌ TaskRouter: API call failed with exception: {type(e).__name__}: {e}")
            logging.error(f"❌ TaskRouter: Model: {self.router_model}, Task: '{task_description[:100]}{'...' if len(task_description) > 100 else ''}'")
            import traceback
            logging.error(f"❌ TaskRouter: Full traceback:\n{traceback.format_exc()}")
            return {"specialist": "generalist"}