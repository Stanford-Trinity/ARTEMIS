import asyncio
import json
import logging
import re
import subprocess
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List
import aiofiles

class SupervisorTools:
    def __init__(self, instance_manager, log_reader, session_dir: Path):
        self.instance_manager = instance_manager
        self.log_reader = log_reader
        self.session_dir = session_dir
        self.notes_dir = session_dir / "supervisor_notes"
        self.notes_dir.mkdir(exist_ok=True)
        self.todo_file = session_dir / "supervisor_todo.json"

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible tool definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "spawn_codex",
                    "description": "Spawn a new codex instance with a specific task",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "Unique identifier for this instance"
                            },
                            "task_description": {
                                "type": "string",
                                "description": "Task for this codex instance to work on"
                            },
                            "workspace_dir": {
                                "type": "string",
                                "description": "Working directory name for this instance (optional)"
                            },
                            "duration_minutes": {
                                "type": "number",
                                "description": "Max runtime for this instance (optional, default: 60)"
                            }
                        },
                        "required": ["instance_id", "task_description"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "terminate_instance",
                    "description": "Terminate a specific codex instance",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "ID of the instance to terminate"
                            }
                        },
                        "required": ["instance_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "send_followup",
                    "description": "Send a followup message to continue conversation with a specific codex instance",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "ID of the instance to send followup to"
                            },
                            "message": {
                                "type": "string",
                                "description": "Followup message to continue the conversation"
                            }
                        },
                        "required": ["instance_id", "message"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_instances",
                    "description": "Get status of all active codex instances",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_instance_logs",
                    "description": "Read conversation logs from a specific codex instance",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "ID of the instance to read logs from"
                            },
                            "format": {
                                "type": "string",
                                "enum": ["readable", "openai_json"],
                                "description": "Format to return logs in (default: readable)"
                            },
                            "tail_lines": {
                                "type": "number",
                                "description": "Number of recent lines to return (optional)"
                            },
                            "max_tokens": {
                                "type": "number",
                                "description": "Maximum tokens to return (truncates if exceeded, optional)"
                            }
                        },
                        "required": ["instance_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_supervisor_note",
                    "description": "Write a note for future reference",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Content to write to the note"
                            }
                        },
                        "required": ["content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_supervisor_notes",
                    "description": "Read all supervisor notes taken during this session",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "slack_webhook",
                    "description": "Submit a vulnerability report to Slack via webhook",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Vulnerability title"},
                            "asset": {"type": "string", "description": "Affected asset"},
                            "vuln_type": {"type": "string", "description": "Type of vulnerability"},
                            "severity": {"type": "string", "description": "Severity rating"},
                            "description": {"type": "string", "description": "Detailed description"},
                            "repro_steps": {"type": "string", "description": "Reproduction steps"},
                            "impact": {"type": "string", "description": "Impact summary"},
                            "cleanup": {"type": "string", "description": "Cleanup instructions"}
                        },
                        "required": ["title", "asset", "vuln_type", "severity", "description", "repro_steps", "impact", "cleanup"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_supervisor_todo",
                    "description": "Add, update, or remove items from the supervisor's todo list",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["add", "update", "remove", "complete"],
                                "description": "Action to perform on todo item"
                            },
                            "item_id": {
                                "type": "string",
                                "description": "Unique ID for the todo item (required for update/remove/complete)"
                            },
                            "description": {
                                "type": "string",
                                "description": "Description of the todo item (required for add/update)"
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "Priority level (optional, defaults to medium)"
                            },
                            "notes": {
                                "type": "string",
                                "description": "Additional notes or context (optional)"
                            }
                        },
                        "required": ["action"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_supervisor_todo",
                    "description": "Read the current supervisor todo list with progress tracking",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filter_status": {
                                "type": "string",
                                "enum": ["pending", "completed", "all"],
                                "description": "Filter todos by status (default: all)"
                            },
                            "filter_priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "Filter todos by priority (optional)"
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_supervisor_conversation",
                    "description": "Read the full supervisor conversation history",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tail_lines": {
                                "type": "number",
                                "description": "Number of recent lines to show (optional, shows all if not specified)"
                            },
                            "from_iteration": {
                                "type": "number",
                                "description": "Start reading from this iteration number (optional)"
                            },
                            "to_iteration": {
                                "type": "number",
                                "description": "Stop reading at this iteration number (optional)"
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_supervisor_history",
                    "description": "Search within supervisor conversation history for specific content",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query (supports regex patterns)"
                            },
                            "context_lines": {
                                "type": "number",
                                "description": "Number of context lines to show around matches (default: 3)"
                            },
                            "case_sensitive": {
                                "type": "boolean",
                                "description": "Whether search should be case sensitive (default: false)"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "wait_for_instance",
                    "description": "Wait for a specific instance to reach waiting_for_followup status (when it needs supervisor input) or complete/fail",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "ID of the instance to wait for"
                            },
                            "timeout_minutes": {
                                "type": "number",
                                "description": "Maximum time to wait in minutes (default: 5)"
                            }
                        },
                        "required": ["instance_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "finished",
                    "description": "Complete the supervisor session and clean up all instances",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "finish_reason": {
                                "type": "string",
                                "description": "Description of why the session is finishing"
                            }
                        },
                        "required": ["finish_reason"]
                    }
                }
            }
        ]

    async def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Handle a supervisor tool call."""
        try:
            if tool_name == "spawn_codex":
                return await self._spawn_codex(arguments)
            elif tool_name == "terminate_instance":
                return await self._terminate_instance(arguments)
            elif tool_name == "send_followup":
                return await self._send_followup(arguments)
            elif tool_name == "list_instances":
                return await self._list_instances(arguments)
            elif tool_name == "read_instance_logs":
                return await self._read_instance_logs(arguments)
            elif tool_name == "write_supervisor_note":
                return await self._write_supervisor_note(arguments)
            elif tool_name == "read_supervisor_notes":
                return await self._read_supervisor_notes(arguments)
            elif tool_name == "slack_webhook":
                return await self._slack_webhook(arguments)
            elif tool_name == "update_supervisor_todo":
                return await self._update_supervisor_todo(arguments)
            elif tool_name == "read_supervisor_todo":
                return await self._read_supervisor_todo(arguments)
            elif tool_name == "read_supervisor_conversation":
                return await self._read_supervisor_conversation(arguments)
            elif tool_name == "search_supervisor_history":
                return await self._search_supervisor_history(arguments)
            elif tool_name == "wait_for_instance":
                return await self._wait_for_instance(arguments)
            elif tool_name == "finished":
                return await self._finished(arguments)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logging.error(f"Error in tool {tool_name}: {e}")
            return f"Error: {e}"

    async def _spawn_codex(self, args: Dict[str, Any]) -> str:
        """Spawn a new codex instance."""
        instance_id = args["instance_id"]
        task_description = args["task_description"]
        workspace_dir = args.get("workspace_dir", instance_id)
        duration_minutes = args.get("duration_minutes", 60)
        
        success = await self.instance_manager.spawn_instance(
            instance_id, task_description, workspace_dir, duration_minutes
        )
        
        if success:
            return f"✅ Spawned codex instance '{instance_id}' with task: {task_description}"
        else:
            return f"❌ Failed to spawn instance '{instance_id}'"

    async def _terminate_instance(self, args: Dict[str, Any]) -> str:
        """Terminate a codex instance."""
        instance_id = args["instance_id"]
        
        success = await self.instance_manager.terminate_instance(instance_id)
        
        if success:
            return f"✅ Terminated instance '{instance_id}'"
        else:
            return f"❌ Failed to terminate instance '{instance_id}' (may not exist)"
    
    async def _send_followup(self, args: Dict[str, Any]) -> str:
        """Send a followup message to a codex instance."""
        instance_id = args["instance_id"]
        message = args["message"]
        
        success = await self.instance_manager.send_followup(instance_id, message)
        
        if success:
            # Give codex a moment to process the followup
            await asyncio.sleep(3)
            return f"✅ Sent followup to instance '{instance_id}': {message}. Waiting 3s for processing."
        else:
            return f"❌ Failed to send followup to instance '{instance_id}' (may not exist or not running)"

    async def _list_instances(self, args: Dict[str, Any]) -> str:
        """List all active instances."""
        instances = self.instance_manager.get_active_instances()
        
        if not instances:
            return "No active instances"
        
        result = "Active instances:\n"
        for instance_id, info in instances.items():
            result += f"- {instance_id}: {info['status']} (task: {info['task']}, started: {info['started_at']})\n"
        
        return result.strip()

    async def _read_instance_logs(self, args: Dict[str, Any]) -> str:
        """Read logs from a codex instance."""
        instance_id = args["instance_id"]
        format_type = args.get("format", "readable")
        tail_lines = args.get("tail_lines")
        max_tokens = args.get("max_tokens")
        
        logs = await self.log_reader.read_instance_logs(
            instance_id, format_type, tail_lines
        )
        
        # Apply token-based truncation if specified
        if max_tokens and hasattr(self, 'context_manager'):
            # Access context manager from the orchestrator if available
            # For now, use a simple line-based truncation as fallback
            lines = logs.split('\n')
            if len(lines) > max_tokens // 10:  # Rough estimate: ~10 tokens per line
                truncated_lines = lines[:max_tokens // 10]
                remaining = len(lines) - len(truncated_lines)
                logs = '\n'.join(truncated_lines) + f'\n\n[... {remaining} more lines truncated due to token limit]'
        
        return logs

    async def _write_supervisor_note(self, args: Dict[str, Any]) -> str:
        """Write a supervisor note."""
        content = args["content"]
        timestamp = datetime.now(timezone.utc)
        
        # Generate timestamped filename
        filename = f"note_{timestamp.strftime('%Y%m%d_%H%M%S')}.txt"
        note_path = self.notes_dir / filename
        
        note_content = f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}] {content}\n"
        
        try:
            async with aiofiles.open(note_path, 'w') as f:
                await f.write(note_content)
            
            logging.info(f"📝 Supervisor wrote note: {filename}")
            return f"✅ Note written to {filename}"
        except Exception as e:
            return f"❌ Failed to write note: {e}"

    async def _read_supervisor_notes(self, args: Dict[str, Any]) -> str:
        """Read all supervisor notes."""
        try:
            notes = []
            
            # Get all .txt files in notes directory, sorted by name (which includes timestamp)
            note_files = sorted(self.notes_dir.glob("note_*.txt"))
            
            if not note_files:
                return "No supervisor notes yet."
            
            for note_file in note_files:
                async with aiofiles.open(note_file, 'r') as f:
                    content = await f.read()
                    notes.append(content.strip())
            
            return "\n".join(notes)
        except Exception as e:
            return f"❌ Failed to read notes: {e}"

    async def _slack_webhook(self, args: Dict[str, Any]) -> str:
        """Submit vulnerability report to Slack webhook."""
        # Build vulnerability report payload
        payload = {
            "title": args["title"],
            "asset": args["asset"],
            "vuln_type": args["vuln_type"],
            "severity": args["severity"],
            "description": args["description"],
            "repro_steps": args["repro_steps"],
            "impact": args["impact"],
            "cleanup": args["cleanup"]
        }
        
        webhook_url = "https://hooks.slack.com/triggers/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"
        
        try:
            # Use curl to post to webhook
            process = await asyncio.create_subprocess_exec(
                "curl", "-X", "POST",
                "-H", "Content-Type: application/json",
                "--data", json.dumps(payload),
                webhook_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                logging.info("✅ Slack vulnerability report sent")
                return f"✅ Vulnerability report sent to Slack: {args['title']}"
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                return f"❌ Failed to send Slack report: {error_msg}"
                
        except Exception as e:
            return f"❌ Error sending Slack report: {e}"
            
    async def _load_todo_list(self) -> List[Dict[str, Any]]:
        """Load the supervisor todo list from file."""
        try:
            if self.todo_file.exists():
                async with aiofiles.open(self.todo_file, 'r') as f:
                    return json.loads(await f.read())
            else:
                # Initialize with empty list
                return []
        except Exception as e:
            logging.error(f"Error loading todo list: {e}")
            return []

    async def _save_todo_list(self, todos: List[Dict[str, Any]]) -> None:
        """Save the supervisor todo list to file."""
        try:
            async with aiofiles.open(self.todo_file, 'w') as f:
                await f.write(json.dumps(todos, indent=2))
        except Exception as e:
            logging.error(f"Error saving todo list: {e}")

    async def _update_supervisor_todo(self, args: Dict[str, Any]) -> str:
        """Add, update, or remove items from the supervisor's todo list."""
        action = args["action"]
        item_id = args.get("item_id")
        description = args.get("description")
        priority = args.get("priority", "medium")
        notes = args.get("notes", "")
        
        try:
            todos = await self._load_todo_list()
            
            if action == "add":
                if not description:
                    return "❌ Description is required for adding todo items"
                
                # Generate new ID
                new_id = str(uuid.uuid4())[:8]
                
                new_todo = {
                    "id": new_id,
                    "description": description,
                    "priority": priority,
                    "status": "pending",
                    "notes": notes,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                
                todos.append(new_todo)
                await self._save_todo_list(todos)
                
                logging.info(f"📝 Added todo item: {description}")
                return f"✅ Added todo item '{description}' with ID: {new_id}"
            
            elif action in ["update", "remove", "complete"]:
                if not item_id:
                    return f"❌ Item ID is required for {action} action"
                
                # Find the todo item
                todo_item = None
                for todo in todos:
                    if todo["id"] == item_id:
                        todo_item = todo
                        break
                
                if not todo_item:
                    return f"❌ Todo item with ID '{item_id}' not found"
                
                if action == "update":
                    if description:
                        todo_item["description"] = description
                    if priority:
                        todo_item["priority"] = priority
                    if notes:
                        todo_item["notes"] = notes
                    todo_item["updated_at"] = datetime.now(timezone.utc).isoformat()
                    
                    await self._save_todo_list(todos)
                    return f"✅ Updated todo item '{todo_item['description']}'"
                
                elif action == "complete":
                    todo_item["status"] = "completed"
                    todo_item["completed_at"] = datetime.now(timezone.utc).isoformat()
                    todo_item["updated_at"] = datetime.now(timezone.utc).isoformat()
                    
                    await self._save_todo_list(todos)
                    return f"✅ Completed todo item '{todo_item['description']}'"
                
                elif action == "remove":
                    todos = [t for t in todos if t["id"] != item_id]
                    await self._save_todo_list(todos)
                    return f"✅ Removed todo item '{todo_item['description']}'"
            
            else:
                return f"❌ Unknown action: {action}"
                
        except Exception as e:
            return f"❌ Error managing todo list: {e}"

    async def _read_supervisor_todo(self, args: Dict[str, Any]) -> str:
        """Read the current supervisor todo list with progress tracking."""
        filter_status = args.get("filter_status", "all")
        filter_priority = args.get("filter_priority")
        
        try:
            todos = await self._load_todo_list()
            
            if not todos:
                return "📝 No todo items yet. Use update_supervisor_todo to add items."
            
            # Apply filters
            filtered_todos = todos
            
            if filter_status != "all":
                filtered_todos = [t for t in filtered_todos if t["status"] == filter_status]
            
            if filter_priority:
                filtered_todos = [t for t in filtered_todos if t["priority"] == filter_priority]
            
            if not filtered_todos:
                filter_desc = f" (filtered by {filter_status}" + (f", {filter_priority}" if filter_priority else "") + ")"
                return f"📝 No todo items match filters{filter_desc}."
            
            # Sort by priority (high > medium > low) then by created date
            priority_order = {"high": 0, "medium": 1, "low": 2}
            filtered_todos.sort(key=lambda x: (priority_order.get(x["priority"], 1), x["created_at"]))
            
            # Format output
            result_lines = ["📝 Supervisor Todo List:", ""]
            
            # Summary stats
            total_todos = len(todos)
            completed = len([t for t in todos if t["status"] == "completed"])
            pending = total_todos - completed
            
            result_lines.append(f"📊 Progress: {completed}/{total_todos} completed ({pending} pending)")
            result_lines.append("")
            
            # List items
            for todo in filtered_todos:
                status_emoji = "✅" if todo["status"] == "completed" else "⏳"
                priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(todo["priority"], "⚪")
                
                result_lines.append(f"{status_emoji} {priority_emoji} [{todo['id']}] {todo['description']}")
                
                if todo.get("notes"):
                    result_lines.append(f"    💭 {todo['notes']}")
                
                created = datetime.fromisoformat(todo["created_at"].replace('Z', '+00:00'))
                result_lines.append(f"    📅 Created: {created.strftime('%Y-%m-%d %H:%M UTC')}")
                
                if todo["status"] == "completed" and todo.get("completed_at"):
                    completed_dt = datetime.fromisoformat(todo["completed_at"].replace('Z', '+00:00'))
                    result_lines.append(f"    ✅ Completed: {completed_dt.strftime('%Y-%m-%d %H:%M UTC')}")
                
                result_lines.append("")
            
            return "\n".join(result_lines)
            
        except Exception as e:
            return f"❌ Error reading todo list: {e}"

    async def _wait_for_instance(self, args: Dict[str, Any]) -> str:
        """Wait for a specific instance to reach waiting_for_followup status or complete/fail."""
        instance_id = args["instance_id"]
        timeout_minutes = args.get("timeout_minutes", 5)
        
        # Check if instance exists
        if instance_id not in self.instance_manager.instances:
            return f"❌ Instance {instance_id} not found"
        
        instance = self.instance_manager.instances[instance_id]
        if instance["status"] != "running":
            return f"❌ Instance {instance_id} is not running (status: {instance['status']})"
        
        # The actual log directory where codex writes files (codex creates nested structure)
        workspace_dir = instance.get("workspace_dir", instance_id)
        session_id = self.session_dir.name  # Extract session_id from session_dir
        actual_log_dir = self.session_dir / "workspaces" / workspace_dir / "logs" / session_id / "workspaces" / workspace_dir
        status_file = actual_log_dir / "status.json"
        
        timeout_seconds = timeout_minutes * 60
        start_time = asyncio.get_event_loop().time()
        
        logging.info(f"🕐 Waiting for instance {instance_id} (timeout: {timeout_minutes}min)")
        logging.info(f"🔧 Status file path: {status_file}")
        logging.info(f"⏰ Will timeout after {timeout_seconds} seconds")
        
        loop_count = 0
        try:
            while True:
                try:
                    loop_count += 1
                    # Only log every 5th iteration (every 10 seconds)
                    if loop_count % 5 == 1:
                        logging.info(f"🔄 Loop iteration {loop_count} - checking status...")
                    current_time = asyncio.get_event_loop().time()
                    elapsed = current_time - start_time
                    
                    # Check timeout
                    if elapsed >= timeout_seconds:
                        # Before timing out, try to get the last assistant message
                        conversation_file = actual_log_dir / "realtime_conversation.json"
                        last_response = "No response available"
                        if conversation_file.exists():
                            try:
                                async with aiofiles.open(conversation_file, 'r') as f:
                                    conversation = json.loads(await f.read())
                                
                                # Get the last assistant message
                                for msg in reversed(conversation):
                                    if msg.get("role") == "assistant":
                                        last_response = msg.get("content", "")[:200] + ("..." if len(msg.get("content", "")) > 200 else "")
                                        break
                            except Exception as e:
                                logging.error(f"Error reading conversation for {instance_id}: {e}")
                        
                        return f"⏰ Timeout waiting for instance {instance_id} after {timeout_minutes} minutes. Last response: '{last_response}'. Use read_instance_logs to check progress or terminate_instance if stuck."
                
                    # Check if instance completed/failed
                    process = instance["process"]
                    if process.returncode is not None:
                        if process.returncode == 0:
                            instance["status"] = "completed"
                            return f"✅ Instance {instance_id} completed while waiting"
                        else:
                            instance["status"] = "failed"
                            return f"❌ Instance {instance_id} failed while waiting (exit code: {process.returncode})"
                    
                    # Check status file
                    if status_file.exists():
                        async with aiofiles.open(status_file, 'r') as f:
                            status_data = json.loads(await f.read())
                        
                        current_status = status_data.get("status")
                        logging.info(f"🔍 Instance {instance_id} status: '{current_status}'")
                        
                        # Always break on waiting_for_followup regardless of expected status
                        if current_status == "waiting_for_followup":
                            logging.info(f"🔄 Instance {instance_id} needs followup, breaking wait loop")
                            # Read the latest response from realtime conversation file
                            conversation_file = actual_log_dir / "realtime_conversation.json"
                            last_response = "No response available"
                            if conversation_file.exists():
                                try:
                                    async with aiofiles.open(conversation_file, 'r') as f:
                                        conversation = json.loads(await f.read())
                                    
                                    # Get the last assistant message
                                    for msg in reversed(conversation):
                                        if msg.get("role") == "assistant":
                                            last_response = msg.get("content", "")[:200] + ("..." if len(msg.get("content", "")) > 200 else "")
                                            break
                                except Exception as e:
                                    logging.error(f"Error reading conversation for {instance_id}: {e}")
                            
                            return f"🔄 Instance {instance_id} is waiting for followup. Last response: '{last_response}'. Use send_followup to continue."
                        
                        # Check if instance completed or failed
                        elif current_status in ["completed", "failed"]:
                            logging.info(f"✅ Instance {instance_id} finished with status: {current_status}")
                            if current_status == "completed":
                                return f"✅ Instance {instance_id} completed"
                            else:
                                return f"❌ Instance {instance_id} failed"
                    # Status file not found, continue waiting
                        
                    # Sleep before next check
                    await asyncio.sleep(2)  # Check every 2 seconds
                
                except Exception as e:
                    logging.error(f"💥 Exception in wait loop for {instance_id}: {e}")
                    await asyncio.sleep(2)  # Still sleep even on error
                
        except asyncio.CancelledError:
            logging.info(f"🛑 Wait for instance {instance_id} cancelled")
            return f"🛑 Wait for instance {instance_id} cancelled due to supervisor shutdown"

    async def _read_supervisor_conversation(self, args: Dict[str, Any]) -> str:
        """Read the full supervisor conversation history."""
        tail_lines = args.get("tail_lines")
        from_iteration = args.get("from_iteration")
        to_iteration = args.get("to_iteration")
        
        try:
            # Read conversation history files
            conversation_files = []
            
            if from_iteration is not None and to_iteration is not None:
                # Read specific iteration range
                for i in range(from_iteration, to_iteration + 1):
                    iteration_file = self.session_dir / f"supervisor_iteration_{i:03d}.json"
                    if iteration_file.exists():
                        conversation_files.append(iteration_file)
            else:
                # Read all iteration files
                conversation_files = sorted(self.session_dir.glob("supervisor_iteration_*.json"))
            
            if not conversation_files:
                return "No supervisor conversation history found."
            
            # Collect conversation content
            all_content = []
            
            for file_path in conversation_files:
                async with aiofiles.open(file_path, 'r') as f:
                    data = json.loads(await f.read())
                    
                iteration = data.get("iteration", 0)
                timestamp = data.get("timestamp", "unknown")
                conversation_history = data.get("conversation_history", [])
                
                all_content.append(f"=== Iteration {iteration} ({timestamp}) ===")
                
                for msg in conversation_history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    
                    if role == "system":
                        all_content.append(f"SYSTEM: {content[:200]}..." if len(content) > 200 else f"SYSTEM: {content}")
                    elif role == "user":
                        all_content.append(f"USER: {content}")
                    elif role == "assistant":
                        all_content.append(f"ASSISTANT: {content}")
                        
                        # Show tool calls if present
                        if "tool_calls" in msg:
                            for tool_call in msg["tool_calls"]:
                                func_name = tool_call.get("function", {}).get("name", "")
                                func_args = tool_call.get("function", {}).get("arguments", "")
                                all_content.append(f"  TOOL_CALL: {func_name}({func_args})")
                    elif role == "tool":
                        tool_id = msg.get("tool_call_id", "unknown")
                        all_content.append(f"TOOL_RESULT[{tool_id}]: {content}")
                    
                all_content.append("")  # Empty line between iterations
            
            # Apply tail_lines if specified
            if tail_lines:
                all_content = all_content[-tail_lines:]
            
            result = "\n".join(all_content)
            return result if result.strip() else "No conversation content found."
            
        except Exception as e:
            return f"❌ Error reading supervisor conversation: {e}"

    async def _search_supervisor_history(self, args: Dict[str, Any]) -> str:
        """Search within supervisor conversation history using ripgrep-style functionality."""
        query = args["query"]
        context_lines = args.get("context_lines", 3)
        case_sensitive = args.get("case_sensitive", False)
        
        try:
            # Get all conversation history files
            conversation_files = sorted(self.session_dir.glob("supervisor_iteration_*.json"))
            
            if not conversation_files:
                return "No supervisor conversation history to search."
            
            matches = []
            
            for file_path in conversation_files:
                async with aiofiles.open(file_path, 'r') as f:
                    data = json.loads(await f.read())
                    
                iteration = data.get("iteration", 0)
                timestamp = data.get("timestamp", "unknown")
                conversation_history = data.get("conversation_history", [])
                
                # Convert conversation to searchable text
                searchable_lines = [f"=== Iteration {iteration} ({timestamp}) ==="]
                
                for msg_idx, msg in enumerate(conversation_history):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    
                    searchable_lines.append(f"[{msg_idx}] {role.upper()}: {content}")
                    
                    # Add tool calls as searchable content
                    if "tool_calls" in msg:
                        for tool_call in msg["tool_calls"]:
                            func_name = tool_call.get("function", {}).get("name", "")
                            func_args = tool_call.get("function", {}).get("arguments", "")
                            searchable_lines.append(f"[{msg_idx}] TOOL_CALL: {func_name}({func_args})")
                
                # Search through lines
                flags = 0 if case_sensitive else re.IGNORECASE
                
                try:
                    pattern = re.compile(query, flags)
                except re.error:
                    # If regex fails, treat as literal string
                    pattern = re.compile(re.escape(query), flags)
                
                for line_idx, line in enumerate(searchable_lines):
                    if pattern.search(line):
                        # Found a match, collect context
                        start_idx = max(0, line_idx - context_lines)
                        end_idx = min(len(searchable_lines), line_idx + context_lines + 1)
                        
                        context_block = []
                        for ctx_idx in range(start_idx, end_idx):
                            prefix = ">>> " if ctx_idx == line_idx else "    "
                            context_block.append(f"{prefix}{searchable_lines[ctx_idx]}")
                        
                        matches.append({
                            "file": file_path.name,
                            "iteration": iteration,
                            "line_number": line_idx,
                            "context": "\n".join(context_block)
                        })
            
            if not matches:
                return f"No matches found for query: {query}"
            
            # Format results
            result_lines = [f"Found {len(matches)} matches for: {query}", ""]
            
            for match in matches:
                result_lines.append(f"📁 {match['file']} (iteration {match['iteration']}, line {match['line_number']}):")
                result_lines.append(match["context"])
                result_lines.append("")  # Empty line between matches
            
            return "\n".join(result_lines)
            
        except Exception as e:
            return f"❌ Error searching supervisor history: {e}"
            
    async def _finished(self, args: Dict[str, Any]) -> str:
        """Complete the supervisor session and trigger cleanup."""
        finish_reason = args["finish_reason"]
        
        logging.info(f"🏁 Supervisor session finishing: {finish_reason}")
        
        # Signal to the orchestrator that we're done
        # This will be handled by the orchestrator checking the result
        return f"✅ Session completed: {finish_reason}"