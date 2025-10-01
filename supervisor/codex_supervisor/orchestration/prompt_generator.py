#!/usr/bin/env python3
import json
import logging
import os
from typing import Dict, Any, Tuple
from openai import AsyncOpenAI


class PromptGenerator:
    """Generates custom system prompts for codex instances using an LLM."""
    
    def __init__(self, generator_model: str = None):
        # Use environment variable or default model
        if generator_model is None:
            if os.getenv("OPENROUTER_API_KEY"):
                generator_model = os.getenv("PROMPT_GENERATOR_MODEL", "anthropic/claude-opus-4.1")
            else:
                generator_model = os.getenv("PROMPT_GENERATOR_MODEL", "gpt-5")
        
        # Adjust model name if using OpenAI directly
        if not os.getenv("OPENROUTER_API_KEY") and generator_model.startswith("openai/"):
            self.generator_model = generator_model.replace("openai/", "")  # Remove openai/ prefix for direct OpenAI API
        else:
            self.generator_model = generator_model
        
        # Try OPENROUTER_API_KEY first, fallback to OPENAI_API_KEY
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = "https://openrouter.ai/api/v1" if os.getenv("OPENROUTER_API_KEY") else "https://api.openai.com/v1"
        
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
    
    def get_generation_prompt(self) -> str:
        """Get the prompt used for generating system prompts."""
        return """You are a part of a complex agentic system that is tasked with conducting security penetration testing of approved targets. The system is primarily comprised of two main parts: 

a) A "supervisor", which is tasked with collecting, synthesizing, analyzing, and disseminating high-level information, as well as providing guidance and instructions for 
b) low level "instances" or "workers", which are responsible for conducting thorough low-level examinations of endpoints, creating exploits, and more. 

In essence, the supervisor acts as an expert that is responsible for controlling a swarm of lower-level workers. Your role is to help propagate context between the supervisor and the workers. To help support this effort, you will be given an instruction that is being passed from the supervisor to a worker. Your job is to create a custom system prompt that defines and shapes the behavior of the worker assigned to complete this task. 

The system prompt should:
1. Be specifically relevant to the current task
2. Be incredibly detailed, including but not limited to knowledge and recommendations for what to do, general tips and tricks, specific tools that might be helpful and how to use them
3. Include a list of specific tools that might be helpful, and a detailed overview of how to use them.

Here is the instruction:"""
    
    async def generate_system_prompt(self, task_description: str) -> Tuple[bool, str]:
        """Generate a custom system prompt for the given task.
        
        Returns:
            Tuple[bool, str]: (success, prompt) - True if generation succeeded with the custom prompt,
                             False with empty string if generation failed (caller should use routing)
        """
        
        try:
            # Combine the generation prompt with the task description
            generation_prompt = self.get_generation_prompt()
            full_prompt = f'{generation_prompt}\n\n"""\n{task_description}\n"""\n\nProvide the system prompt and nothing else'
            
            try:
                # Use correct parameters based on API provider
                completion_params = {
                    "model": self.generator_model,
                    "messages": [
                        {"role": "system", "content": "You are an expert at creating system prompts for AI agents conducting security testing. Generate clear, specific, detailed system prompts."},
                        {"role": "user", "content": full_prompt}
                    ],
                }
                
                # Only set temperature and max_tokens for OpenRouter
                if os.getenv("OPENROUTER_API_KEY"):
                    completion_params["temperature"] = 0.3
                    completion_params["max_tokens"] = 8000
                else:
                    completion_params["max_completion_tokens"] = 8000
                    
                response = await self.client.chat.completions.create(**completion_params)
            except Exception as api_error:
                logging.error(f"❌ PromptGenerator: API call failed: {type(api_error).__name__}: {api_error}")
                return False, ""
            
            content = response.choices[0].message.content.strip()
            
            if not content:
                logging.error("❌ PromptGenerator: Empty response from LLM")
                return False, ""
            
            logging.info(f"✅ PromptGenerator: Generated custom system prompt for task: {task_description[:100]}{'...' if len(task_description) > 100 else ''}")
            return True, content
            
        except Exception as e:
            logging.error(f"❌ PromptGenerator: Failed to generate system prompt: {type(e).__name__}: {e}")
            import traceback
            logging.error(f"❌ PromptGenerator: Full traceback:\n{traceback.format_exc()}")
            return False, ""
