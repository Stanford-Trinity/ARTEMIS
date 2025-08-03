#!/usr/bin/env python3
import json
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

if TYPE_CHECKING:
    from .instance_manager import InstanceManager


class LogReader:
    """Reads logs from codex instances."""
    
    def __init__(self, session_dir: Path, instance_manager: 'InstanceManager'):
        self.session_dir = session_dir
        self.instance_manager = instance_manager
    
    async def read_instance_logs(self, instance_id: str, format_type: str = "readable", tail_lines: int = None) -> str:
        """Read logs from a specific codex instance."""
        # Look up the instance to get its workspace directory
        if instance_id not in self.instance_manager.instances:
            return f"❌ Instance {instance_id} not found"
        
        instance_info = self.instance_manager.instances[instance_id]
        workspace_name = instance_info["workspace_dir"]
        
        # Build the expected log directory path
        session_id = self.session_dir.name
        instance_log_dir = self.session_dir / "workspaces" / workspace_name / "logs" / session_id / "workspaces" / workspace_name
        
        if not instance_log_dir.exists():
            return f"❌ Log directory for instance {instance_id} not found at {instance_log_dir}"
        
        logs_content = []
        
        try:
            # Read realtime context
            context_file = instance_log_dir / "realtime_context.txt"
            if context_file.exists():
                async with aiofiles.open(context_file, 'r') as f:
                    content = await f.read()
                    if tail_lines:
                        lines = content.split('\n')
                        content = '\n'.join(lines[-tail_lines:])
                    logs_content.append(f"=== Realtime Context ===\n{content}")
            
            # Read final result
            final_result_file = instance_log_dir / "final_result.json"
            if final_result_file.exists():
                async with aiofiles.open(final_result_file, 'r') as f:
                    final_result = json.loads(await f.read())
                    
                    if format_type == "json":
                        logs_content.append(f"=== Final Result (JSON) ===\n{json.dumps(final_result, indent=2)}")
                    else:
                        # Extract conversation for readable format
                        conversation = final_result.get("conversation", [])
                        if conversation:
                            formatted_conversation = []
                            for msg in conversation:
                                role = msg.get("role", "unknown")
                                content = msg.get("content", "")
                                if role == "user":
                                    formatted_conversation.append(f"👤 USER: {content}")
                                elif role == "assistant":
                                    formatted_conversation.append(f"🤖 ASSISTANT: {content}")
                            
                            conversation_text = '\n\n'.join(formatted_conversation)
                            if tail_lines:
                                lines = conversation_text.split('\n')
                                conversation_text = '\n'.join(lines[-tail_lines:])
                            logs_content.append(f"=== Conversation ===\n{conversation_text}")
                        
                        # Add status info
                        status = final_result.get("status", "unknown")
                        logs_content.append(f"=== Status ===\nStatus: {status}")
            
            if not logs_content:
                return f"📝 No readable logs found for instance {instance_id}"
                
            return '\n\n' + '='*50 + '\n\n'.join(logs_content)
            
        except Exception as e:
            return f"❌ Error reading logs for instance {instance_id}: {e}"