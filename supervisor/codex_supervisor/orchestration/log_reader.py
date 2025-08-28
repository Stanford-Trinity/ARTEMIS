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
        if instance_id not in self.instance_manager.instances:
            return f"❌ Instance {instance_id} not found"
        
        instance_info = self.instance_manager.instances[instance_id]
        workspace_name = instance_info["workspace_dir"]
        
        session_id = self.session_dir.name
        instance_log_dir = self.session_dir / "workspaces" / workspace_name / "logs" / session_id / "workspaces" / workspace_name
        
        if not instance_log_dir.exists():
            return f"❌ Log directory for instance {instance_id} not found at {instance_log_dir}"
        
        logs_content = []
        
        try:
            # Prefer final_result.json if it exists (cleaner format), otherwise use realtime_context.txt
            final_result_file = instance_log_dir / "final_result.json"
            if final_result_file.exists():
                async with aiofiles.open(final_result_file, 'r') as f:
                    final_result = json.loads(await f.read())
                    
                    if format_type == "json":
                        logs_content.append(f"=== Final Result (JSON) ===\n{json.dumps(final_result, indent=2)}")
                    else:
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
                            logs_content.append(conversation_text)  # Remove the "=== Conversation ===" header
                        status = final_result.get("status", "unknown")
                        logs_content.append(f"Status: {status}")
            else:
                # Fallback to realtime context if final result doesn't exist
                context_file = instance_log_dir / "realtime_context.txt"
                if context_file.exists():
                    async with aiofiles.open(context_file, 'r') as f:
                        content = await f.read()
                        if tail_lines:
                            lines = content.split('\n')
                            content = '\n'.join(lines[-tail_lines:])
                        logs_content.append(content)  # Remove the "=== Realtime Context ===" header
            
            if not logs_content:
                return f"📝 No readable logs found for instance {instance_id}"
                
            return '\n\n' + '='*50 + '\n\n'.join(logs_content)
            
        except Exception as e:
            return f"❌ Error reading logs for instance {instance_id}: {e}"