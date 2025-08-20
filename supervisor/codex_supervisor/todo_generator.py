"""
TODO Generator for Codex Supervisor using OpenRouter Claude Opus 4.1
"""

import json
import logging
import aiofiles
import asyncio
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime, timezone
from openai import AsyncOpenAI


class TodoGenerator:
    def __init__(self, api_key: str, use_openrouter: bool = None):
        """Initialize TODO generator with API key."""
        # Auto-detect provider if not specified
        if use_openrouter is None:
            use_openrouter = api_key.startswith('sk-or-') or 'openrouter' in api_key.lower()
        
        base_url = "https://openrouter.ai/api/v1" if use_openrouter else "https://api.openai.com/v1"
        
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
        
        # Store provider type for later use
        self.use_openrouter = use_openrouter
        
        # Set model based on provider
        if use_openrouter:
            self.model = "anthropic/claude-opus-4.1"
        else:
            self.model = "gpt-5"  # Use OpenAI's gpt-5
        
    async def generate_todos_from_config(self, config_content: str) -> List[Dict[str, Any]]:
        """Generate hierarchical TODOs from penetration testing configuration."""
        
        prompt = f"""You are a cybersecurity professional, acting as a part of a team of security professionals who have been hired to test the security of an approved target. As a part of your task, you have been given the following configuration file that specifies the scope of your test:

```
{config_content}
```

You are the critical first step of the penetration test. Your job is to take the above configuration file, and create a set of goals and TODOs to anchor the remainder of the team's testing. These TODOs are recursively hierarchical. This means that you can create subtasks of the main TODOs, subtasks of those subtasks, and so on. This is helpful, as it will help the rest of the team split up work and maintain focus on the broader task at hand.

For context on your environment, the entirety of the team is operating out of a standard Kali Linux environment. Spend minimal TODO space on setting up the environment (unless there are tools/features that are absolutely necessary), and focus on actionable items with respect to the task at hand and the scope provided to you.

You should output your TODOs in the following format:

```json
[
{{
    "id": "recon-001",
    "description": "Initial reconnaissance of target network 192.168.1.0/24",
    "priority": "high",
    "status": "pending",
    "notes": "Corporate network with web services",
    "created_at": "2025-08-16T10:00:00Z",
    "updated_at": "2025-08-16T10:00:00Z",
    "subtasks": []
}}
]
```

Where you can recursively create these objects inside each "subtasks" list. Your list should be exhaustive. It should not simply be high level.

IMPORTANT: Only respond with the JSON array. Do not include any other text or explanation."""

        try:
            # Use correct parameter name based on provider
            completion_params = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            }
            
            # Only set temperature for OpenRouter, OpenAI's newer models don't support custom temperature
            if self.use_openrouter:
                completion_params["temperature"] = 0.7
            
            # OpenRouter uses max_tokens, OpenAI direct uses max_completion_tokens for newer models
            if self.use_openrouter:
                completion_params["max_tokens"] = 20000
            else:
                completion_params["max_completion_tokens"] = 20000
                
            response = await self.client.chat.completions.create(**completion_params)
            
            response_content = response.choices[0].message.content.strip()
            if response_content.startswith("```json"):
                start = response_content.find("[")
                end = response_content.rfind("]") + 1
                json_content = response_content[start:end]
            elif response_content.startswith("```"):
                lines = response_content.split("\n")
                json_lines = []
                in_json = False
                for line in lines:
                    if line.strip() == "```" and not in_json:
                        in_json = True
                        continue
                    elif line.strip() == "```" and in_json:
                        break
                    elif in_json:
                        json_lines.append(line)
                json_content = "\n".join(json_lines)
            else:
                json_content = response_content
            
            todos = json.loads(json_content)
            validated_todos = self._validate_and_normalize_todos(todos)
            
            logging.info(f"Generated {len(validated_todos)} top-level TODOs")
            return validated_todos
            
        except Exception as e:
            logging.error(f"Error generating TODOs: {e}")
            raise
    
    def _validate_and_normalize_todos(self, todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and normalize TODO structure."""
        normalized = []
        current_time = datetime.now(timezone.utc).isoformat()
        
        for todo in todos:
            normalized_todo = {
                "id": todo.get("id", f"todo-{len(normalized):03d}"),
                "description": todo.get("description", ""),
                "priority": todo.get("priority", "medium"),
                "status": todo.get("status", "pending"),
                "notes": todo.get("notes", ""),
                "created_at": todo.get("created_at", current_time),
                "updated_at": todo.get("updated_at", current_time),
                "subtasks": self._validate_and_normalize_todos(todo.get("subtasks", []))
            }
            
            if normalized_todo["priority"] not in ["high", "medium", "low"]:
                normalized_todo["priority"] = "medium"
            
            if normalized_todo["status"] not in ["pending", "completed"]:
                normalized_todo["status"] = "pending"
            
            normalized.append(normalized_todo)
        
        return normalized
    
    async def save_todos_to_file(self, todos: List[Dict[str, Any]], file_path: Path):
        """Save TODOs to JSON file."""
        async with aiofiles.open(file_path, 'w') as f:
            await f.write(json.dumps(todos, indent=2))
        
        logging.info(f"Saved TODOs to {file_path}")


async def generate_pentest_todos(config_file: Path, output_file: Path, api_key: str):
    """Generate penetration testing TODOs from configuration file."""
    
    async with aiofiles.open(config_file, 'r') as f:
        config_content = await f.read()
    
    generator = TodoGenerator(api_key)
    todos = await generator.generate_todos_from_config(config_content)
    
    await generator.save_todos_to_file(todos, output_file)
    
    return todos


if __name__ == "__main__":
    import os
    import sys
    
    if len(sys.argv) != 3:
        print("Usage: python todo_generator.py <config_file> <output_file>")
        sys.exit(1)
    
    config_file = Path(sys.argv[1])
    output_file = Path(sys.argv[2])
    
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: Either OPENROUTER_API_KEY or OPENAI_API_KEY environment variable must be set")
        sys.exit(1)
    
    asyncio.run(generate_pentest_todos(config_file, output_file, api_key))