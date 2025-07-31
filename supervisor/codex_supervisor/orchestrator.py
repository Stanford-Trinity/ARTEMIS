#!/usr/bin/env python3
import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import os
import signal
import psutil
import aiofiles

from openai import AsyncOpenAI
from .tools import SupervisorTools
from .prompts.supervisor_prompt import SupervisorPrompt
from .context_manager import ContextManager


class InstanceManager:
    """Manages codex instances spawned by the supervisor."""
    
    def __init__(self, session_dir: Path, codex_binary: str):
        self.session_dir = session_dir
        self.codex_binary = codex_binary
        self.instances: Dict[str, Dict[str, Any]] = {}
    
    async def spawn_instance(self, instance_id: str, task_description: str, 
                           workspace_dir: str, duration_minutes: int) -> bool:
        """Spawn a new codex instance."""
        if instance_id in self.instances:
            logging.warning(f"Instance {instance_id} already exists")
            return False
        
        # Prepare workspace directory (simplified structure)
        # Extract basename to prevent nested paths when LLM passes full paths
        workspace_name = Path(workspace_dir).name
        workspace_path = self.session_dir / "workspaces" / workspace_name
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # Build codex command - logs go directly in workspace, no separate log dir
        cmd = [
            self.codex_binary,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--log-session-dir", str(workspace_path),
            "--instance-id", instance_id,
            "--wait-for-followup",
            "-C", str(workspace_path),
        ]
        subagent_model = os.getenv("SUBAGENT_MODEL")
        if subagent_model:
            cmd.extend(["--model", subagent_model])
        cmd.append(task_description)
        
        try:
            # Start the codex process in its own process group for proper cleanup
            # Pass through environment variables including API keys
            env = os.environ.copy()
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                env=env,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )
            
            # Store instance info
            self.instances[instance_id] = {
                "process": process,
                "task": task_description,
                "workspace_dir": workspace_name,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_minutes": duration_minutes,
                "log_dir": workspace_path,  # Logs go directly in workspace now
                "status": "running"
            }
            
            logging.info(f"🚀 Spawned codex instance {instance_id} (PID: {process.pid})")
            
            # Start monitoring task
            asyncio.create_task(self._monitor_instance(instance_id))
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to spawn instance {instance_id}: {e}")
            return False
    
    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate a specific codex instance."""
        if instance_id not in self.instances:
            return False
        
        instance = self.instances[instance_id]
        process = instance["process"]
        
        try:
            # Force kill immediately - no graceful termination
            if process.returncode is None:
                logging.info(f"🛑 Force killing instance {instance_id} (PID: {process.pid})")
                
                try:
                    if hasattr(os, 'killpg'):
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    # Process already dead
                    pass
                
                # Brief wait for process to die
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    logging.warning(f"Process {instance_id} still alive after SIGKILL")
            
            instance["status"] = "terminated"
            logging.info(f"✅ Terminated instance {instance_id}")
            return True
            
        except Exception as e:
            logging.error(f"Error terminating instance {instance_id}: {e}")
            return False
    
    def get_active_instances(self) -> Dict[str, Dict[str, Any]]:
        """Get all active instances with their status."""
        active = {}
        for instance_id, info in self.instances.items():
            if info["status"] == "running":
                # Update status based on process state
                process = info["process"]
                if process.returncode is not None:
                    info["status"] = "completed" if process.returncode == 0 else "failed"
                
                active[instance_id] = {
                    "task": info["task"],
                    "started_at": info["started_at"],
                    "status": info["status"],
                    "workspace_dir": info["workspace_dir"]
                }
        
        return active
    
    async def _monitor_instance(self, instance_id: str):
        """Monitor an instance and update its status."""
        instance = self.instances[instance_id]
        process = instance["process"]
        duration_minutes = instance["duration_minutes"]
        
        try:
            # Wait for process completion or timeout
            timeout_seconds = duration_minutes * 60
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            
            # Process completed naturally
            if process.returncode == 0:
                instance["status"] = "completed"
                logging.info(f"✅ Instance {instance_id} completed successfully")
            else:
                instance["status"] = "failed"
                logging.warning(f"❌ Instance {instance_id} failed with exit code {process.returncode}")
                
        except asyncio.TimeoutError:
            # Instance exceeded time limit
            logging.warning(f"⏰ Instance {instance_id} exceeded {duration_minutes}min limit, terminating")
            await self.terminate_instance(instance_id)
            instance["status"] = "timeout"
        
        except Exception as e:
            logging.error(f"Error monitoring instance {instance_id}: {e}")
            instance["status"] = "error"
    
    async def send_followup(self, instance_id: str, message: str) -> bool:
        """Send a followup message to a running instance."""
        if instance_id not in self.instances:
            return False
        
        instance = self.instances[instance_id]
        if instance["status"] != "running":
            return False
        
        # Codex looks for followup in its nested logs directory, same as status.json
        workspace_dir = instance.get("workspace_dir", instance_id)
        session_id = self.session_dir.name
        actual_log_dir = self.session_dir / "workspaces" / workspace_dir / "logs" / session_id / "workspaces" / workspace_dir
        followup_file = actual_log_dir / "followup_input.json"
        
        logging.info(f"🔧 Followup path details:")
        logging.info(f"   workspace_dir: {workspace_dir}")
        logging.info(f"   session_id: {session_id}")
        logging.info(f"   actual_log_dir: {actual_log_dir}")
        logging.info(f"   followup_file: {followup_file}")
        
        followup_data = {
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            # Ensure the directory exists
            logging.info(f"🔧 Creating directory: {actual_log_dir}")
            actual_log_dir.mkdir(parents=True, exist_ok=True)
            
            logging.info(f"🔧 Writing followup data: {json.dumps(followup_data, indent=2)}")
            async with aiofiles.open(followup_file, 'w') as f:
                await f.write(json.dumps(followup_data, indent=2))
            
            # Verify file was created
            if followup_file.exists():
                file_size = followup_file.stat().st_size
                logging.info(f"✅ Followup file created successfully: {followup_file} ({file_size} bytes)")
            else:
                logging.error(f"❌ Followup file was NOT created: {followup_file}")
                return False
                
            logging.info(f"📨 Sent followup to instance {instance_id}: {message}")
            return True
            
        except Exception as e:
            logging.error(f"💥 Error sending followup to instance {instance_id}: {e}")
            logging.error(f"📁 Attempted path: {followup_file}")
            import traceback
            logging.error(f"📁 Full traceback: {traceback.format_exc()}")
            return False
    
    async def check_for_responses(self) -> Dict[str, str]:
        """Check all instances for new responses waiting for followup."""
        responses = {}
        
        for instance_id, instance in self.instances.items():
            if instance["status"] != "running":
                continue
                
            instance_log_dir = instance["log_dir"]
            status_file = instance_log_dir / "status.json"
            
            try:
                if status_file.exists():
                    async with aiofiles.open(status_file, 'r') as f:
                        status_data = json.loads(await f.read())
                    
                    if status_data.get("status") == "waiting_for_followup":
                        # Read the latest conversation to get the response
                        final_result_file = instance_log_dir / "final_result.json"
                        if final_result_file.exists():
                            async with aiofiles.open(final_result_file, 'r') as f:
                                final_result = json.loads(await f.read())
                            
                            # Get the last assistant message from the conversation array
                            conversation = final_result.get("conversation", [])
                            for msg in reversed(conversation):
                                if msg.get("role") == "assistant":
                                    responses[instance_id] = msg.get("content", "")
                                    break
                                    
            except Exception as e:
                logging.error(f"Error checking response for instance {instance_id}: {e}")
        
        return responses



class LogReader:
    """Reads logs from codex instances."""
    
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
    
    async def read_instance_logs(self, instance_id: str, format_type: str = "readable", tail_lines: int = None) -> str:
        """Read logs from a specific codex instance."""
        # Find the instance in any workspace
        workspaces_dir = self.session_dir / "workspaces"
        if not workspaces_dir.exists():
            return f"❌ No workspaces found in session directory"
        
        instance_log_dir = None
        workspace_name = None
        
        # Search for the instance in all workspaces
        for workspace_dir in workspaces_dir.iterdir():
            if workspace_dir.is_dir():
                workspace_name = workspace_dir.name
                session_id = self.session_dir.name
                # Check nested structure
                potential_log_dir = workspace_dir / "logs" / session_id / "workspaces" / workspace_name
                if potential_log_dir.exists() and (potential_log_dir / "final_result.json").exists():
                    instance_log_dir = potential_log_dir
                    break
        
        if not instance_log_dir:
            return f"❌ Instance {instance_id} not found in any workspace"
        
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


class SupervisorOrchestrator:
    """Main orchestrator for the codex supervisor."""
    
    def __init__(self, config: Dict[str, Any], session_dir: Path, supervisor_model: str = "o3",
                 duration_minutes: int = 60, work_hours: Tuple[int, int] = (7, 18),
                 ignore_work_hours: bool = False, verbose: bool = False, codex_binary: str = "./target/release/codex"):
        
        self.config = config
        self.session_dir = session_dir
        self.supervisor_model = supervisor_model
        self.duration_minutes = duration_minutes
        self.work_hours = work_hours
        self.ignore_work_hours = ignore_work_hours
        self.verbose = verbose
        self.codex_binary = codex_binary
        
        # Initialize components
        self.instance_manager = InstanceManager(session_dir, codex_binary)
        self.log_reader = LogReader(session_dir)
        self.tools = SupervisorTools(self.instance_manager, self.log_reader, session_dir)
        
        # Initialize context manager
        self.context_manager = ContextManager(
            max_tokens=200_000,
            buffer_tokens=15_000,  # Trigger at 185k tokens
            summarization_model="openai/o4-mini"
        )
        
        # OpenRouter client (OpenAI-compatible API)
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY")
        )
        
        # Session state
        self.conversation_history = []
        self.running = False
        self.heartbeat_file = session_dir / "supervisor_heartbeat.json"
        
        # Initialize prompt manager
        self.prompt = SupervisorPrompt()
        
        logging.info(f"🎯 Supervisor initialized with model: {supervisor_model}")
    
    async def run_loop(self):
        """Main supervisor loop."""
        self.running = True
        
        # Initialize conversation with system prompt and task config
        self.conversation_history.append({
            "role": "system",
            "content": self.prompt.get_system_prompt()
        })
        
        # Add initial task context
        initial_context = self.prompt.format_initial_context(
            self.config, self.duration_minutes, str(self.session_dir)
        )
        
        self.conversation_history.append({
            "role": "user", 
            "content": initial_context
        })
        
        start_time = datetime.now(timezone.utc)
        end_time = start_time + timedelta(minutes=self.duration_minutes)
        
        logging.info(f"🎯 Supervisor starting {self.duration_minutes}min session")
        logging.info(f"📅 Session will end at: {end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Save initial session metadata
        await self._save_session_metadata(start_time, end_time)
        
        iteration = 0
        
        while self.running and datetime.now(timezone.utc) < end_time:
            try:
                iteration += 1
                logging.info(f"🔄 Supervisor iteration {iteration}")
                
                # Check work hours and sleep if needed (unless ignored)
                if not self.ignore_work_hours:
                    await self._wait_for_work_hours()
                
                # Update heartbeat
                await self._update_heartbeat(iteration, start_time)
                
                # Check for instance updates and add user message if needed
                user_message = await self._generate_instance_update_message()
                if user_message:
                    self.conversation_history.append({
                        "role": "user",
                        "content": user_message
                    })
                
                # Get supervisor decision and handle tool calls
                session_finished = await self._handle_supervisor_turn()
                
                # Save conversation state
                await self._save_conversation_state(iteration)
                
                # Check if supervisor called finished
                if session_finished:
                    logging.info("✅ Supervisor completed session")
                    break
                
                # Wait before next iteration
                await asyncio.sleep(30)  # 30 second intervals
                
            except KeyboardInterrupt:
                logging.info("⏹️ Supervisor interrupted")
                break
            except Exception as e:
                logging.error(f"Error in supervisor loop: {e}")
                await asyncio.sleep(60)  # Wait longer on error
        
        logging.info("✅ Supervisor loop completed")
        await self.shutdown()
    
    async def _wait_for_work_hours(self):
        """Sleep until within work hours if currently outside."""
        import pytz
        
        while not self._is_work_hours():
            pacific = pytz.timezone('US/Pacific')
            now_pacific = datetime.now(pacific)
            
            # Calculate minutes until work starts
            start_hour = self.work_hours[0]
            next_start = now_pacific.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            
            # If past work hours today, wait until tomorrow
            if now_pacific.hour >= self.work_hours[1]:
                next_start += timedelta(days=1)
            
            sleep_minutes = int((next_start - now_pacific).total_seconds() / 60)
            
            if sleep_minutes > 0:
                logging.info(f"😴 Outside work hours, sleeping {sleep_minutes}min until {next_start.strftime('%H:%M Pacific')}")
                await asyncio.sleep(min(sleep_minutes * 60, 3600))  # Sleep max 1 hour at a time
    
    async def _get_supervisor_response(self, instance_responses: Dict[str, str] = None) -> Optional[str]:
        """Get a response from the supervisor model."""
        try:
            # Make API call
            response = await self.client.chat.completions.create(
                model=self.supervisor_model,
                messages=self.conversation_history,
                tools=self.tools.get_tool_definitions(),
                tool_choice="auto",
                max_tokens=4000,
            )
            
            message = response.choices[0].message
            content = message.content or ""
            
            # Handle tool calls
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    
                    logging.info(f"🔧 Supervisor calling tool: {tool_name}")
                    tool_result = await self.tools.handle_tool_call(tool_name, arguments)
                    
                    # Add tool result to content using prompt formatter
                    content += self.prompt.format_tool_result(tool_name, tool_result)
            
            return content if content.strip() else None
            
        except Exception as e:
            logging.error(f"Error getting supervisor response: {e}")
            return None
    
    def _is_work_hours(self) -> bool:
        """Check if current time is within configured work hours (Pacific)."""
        import pytz
        pacific = pytz.timezone('US/Pacific')
        now_pacific = datetime.now(pacific)
        current_hour = now_pacific.hour
        
        start_hour, end_hour = self.work_hours
        return start_hour <= current_hour < end_hour
    
    async def _update_heartbeat(self, iteration: int, start_time: datetime):
        """Update supervisor heartbeat file."""
        heartbeat = {
            "supervisor_pid": os.getpid(),
            "session_dir": str(self.session_dir),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
            "start_time": start_time.isoformat(),
            "active_instances": len([i for i in self.instance_manager.instances.values() if i["status"] == "running"]),
            "work_hours": f"{self.work_hours[0]}:00-{self.work_hours[1]}:00 Pacific",
            "status": "running"
        }
        
        try:
            async with aiofiles.open(self.heartbeat_file, 'w') as f:
                await f.write(json.dumps(heartbeat, indent=2))
        except Exception as e:
            logging.error(f"Failed to update heartbeat: {e}")
    
    async def _save_session_metadata(self, start_time: datetime, end_time: datetime):
        """Save comprehensive session metadata."""
        metadata_file = self.session_dir / "session_metadata.json"
        
        metadata = {
            "session_info": {
                "session_id": self.session_dir.name,
                "start_time": start_time.isoformat(),
                "planned_end_time": end_time.isoformat(),
                "duration_minutes": self.duration_minutes,
                "work_hours": {
                    "start_hour": self.work_hours[0],
                    "end_hour": self.work_hours[1],
                    "timezone": "US/Pacific"
                }
            },
            "supervisor_config": {
                "model": self.supervisor_model,
                "api_provider": "openrouter",
                "verbose": self.verbose
            },
            "codex_config": {
                "binary_path": self.codex_binary,
                "sandbox_mode": "danger-full-access",
                "execution_mode": "full-auto"
            },
            "task_config": self.config,
            "runtime_stats": {
                "total_iterations": 0,
                "total_instances_spawned": 0,
                "total_instances_completed": 0,
                "total_instances_failed": 0,
                "vulnerabilities_reported": 0,
                "notes_written": 0
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            async with aiofiles.open(metadata_file, 'w') as f:
                await f.write(json.dumps(metadata, indent=2))
        except Exception as e:
            logging.error(f"Failed to save session metadata: {e}")

    async def _save_conversation_state(self, iteration: int):
        """Save current conversation state."""
        state_file = self.session_dir / f"supervisor_iteration_{iteration:03d}.json"
        
        state = {
            "iteration": iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "conversation_history": self.conversation_history,
            "active_instances": self.instance_manager.get_active_instances()
        }
        
        try:
            async with aiofiles.open(state_file, 'w') as f:
                await f.write(json.dumps(state, indent=2))
                
            # Also update session metadata with runtime stats
            await self._update_session_metadata(iteration)
        except Exception as e:
            logging.error(f"Failed to save conversation state: {e}")
    
    async def _update_session_metadata(self, iteration: int):
        """Update session metadata with current runtime stats."""
        metadata_file = self.session_dir / "session_metadata.json"
        
        try:
            # Read existing metadata
            async with aiofiles.open(metadata_file, 'r') as f:
                metadata = json.loads(await f.read())
            
            # Update runtime stats
            all_instances = self.instance_manager.instances
            completed = sum(1 for i in all_instances.values() if i["status"] == "completed")
            failed = sum(1 for i in all_instances.values() if i["status"] in ["failed", "timeout", "error"])
            
            metadata["runtime_stats"].update({
                "total_iterations": iteration,
                "total_instances_spawned": len(all_instances),
                "total_instances_completed": completed,
                "total_instances_failed": failed,
                "last_updated": datetime.now(timezone.utc).isoformat()
            })
            
            # Save updated metadata
            async with aiofiles.open(metadata_file, 'w') as f:
                await f.write(json.dumps(metadata, indent=2))
                
        except Exception as e:
            logging.error(f"Failed to update session metadata: {e}")
    
    async def shutdown(self):
        """Shutdown supervisor and terminate all instances."""
        logging.info("🛑 Shutting down supervisor...")
        self.running = False
        
        # Terminate all instances concurrently for faster shutdown
        instance_ids = list(self.instance_manager.instances.keys())
        if instance_ids:
            logging.info(f"🧹 Cleaning up {len(instance_ids)} instances...")
            termination_tasks = [
                self.instance_manager.terminate_instance(instance_id) 
                for instance_id in instance_ids
            ]
            
            # Wait for all terminations to complete with short timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*termination_tasks, return_exceptions=True), 
                    timeout=3.0  # Reduced from 15 to 3 seconds
                )
                logging.info("✅ All instances terminated")
            except asyncio.TimeoutError:
                logging.warning("⚠️  Some instances may not have terminated cleanly")
        
        # Update final heartbeat
        try:
            heartbeat = {
                "supervisor_pid": os.getpid(),
                "session_dir": str(self.session_dir),
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "shutdown"
            }
            async with aiofiles.open(self.heartbeat_file, 'w') as f:
                await f.write(json.dumps(heartbeat, indent=2))
        except Exception as e:
            logging.error(f"Failed to update final heartbeat: {e}")
        
        # Save final session metadata
        try:
            metadata_file = self.session_dir / "session_metadata.json"
            async with aiofiles.open(metadata_file, 'r') as f:
                metadata = json.loads(await f.read())
            
            metadata.update({
                "session_info": {
                    **metadata["session_info"],
                    "actual_end_time": datetime.now(timezone.utc).isoformat(),
                    "status": "completed"
                },
                "last_updated": datetime.now(timezone.utc).isoformat()
            })
            
            async with aiofiles.open(metadata_file, 'w') as f:
                await f.write(json.dumps(metadata, indent=2))
                
        except Exception as e:
            logging.error(f"Failed to save final metadata: {e}")
        
        logging.info("✅ Supervisor shutdown complete")
    
    async def _generate_instance_update_message(self) -> Optional[str]:
        """Generate user message with instance updates."""
        instance_responses = await self.instance_manager.check_for_responses()
        updates = []
        
        if instance_responses:
            for instance_id, response in instance_responses.items():
                updates.append(f"- Instance {instance_id} is waiting for followup. Last response: '{response}'. Use send_followup to continue or terminate_instance to end.")
        
        # Check for completed instances (not waiting)
        all_instances = self.instance_manager.get_active_instances()
        completed_instances = {
            instance_id: info for instance_id, info in all_instances.items() 
            if info["status"] in ["completed", "failed", "timeout"]
        }
        
        for instance_id, info in completed_instances.items():
            status = info["status"]
            updates.append(f"- Instance {instance_id} {status}. Use read_instance_logs to see full conversation and decide next steps.")
        
        # Check running instances and provide status updates
        running_instances = {
            instance_id: info for instance_id, info in all_instances.items()
            if info["status"] == "running" and instance_id not in instance_responses
        }
        
        if running_instances:
            # Provide regular status updates for running instances
            instance_list = []
            for instance_id, info in running_instances.items():
                start_time = info.get("start_time", "unknown")
                if isinstance(start_time, str) and start_time != "unknown":
                    try:
                        from datetime import datetime
                        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        elapsed = datetime.now(start_dt.tzinfo) - start_dt
                        elapsed_mins = int(elapsed.total_seconds() / 60)
                        instance_list.append(f"{instance_id} (running {elapsed_mins}m)")
                    except:
                        instance_list.append(f"{instance_id} (running)")
                else:
                    instance_list.append(f"{instance_id} (running)")
            
            if len(running_instances) == 1:
                updates.append(f"- There is 1 instance currently running: {instance_list[0]}.")
            else:
                updates.append(f"- There are {len(running_instances)} instances currently running: {', '.join(instance_list)}.")
        elif not instance_responses:
            updates.append("- There are no instances currently running.")
            # Check if we should suggest ending the session
            if completed_instances:
                updates.append("- Review completed instance logs and decide whether to spawn new instances or call finished to end session.")
        
        if updates:
            return f"Instance updates:\n" + "\n".join(updates) + "\n\nDecide your next actions using the available tools."
        
        return None
    
    async def _handle_supervisor_turn(self) -> bool:
        """Handle a complete supervisor turn with tool calls. Returns True if session should finish."""
        try:
            # Check if we need to summarize conversation history due to token limits
            if self.context_manager.should_summarize(self.conversation_history):
                # Get context stats for logging
                stats = self.context_manager.get_context_stats(self.conversation_history)
                logging.info(f"⚠️  Context approaching token limit: {stats['total_tokens']:,} tokens (max: {stats['max_tokens']:,})")
                
                # Summarize conversation history
                self.conversation_history = await self.context_manager.summarize_conversation(
                    self.conversation_history, preserve_recent=20
                )
            
            # Make API call
            response = await self.client.chat.completions.create(
                model=self.supervisor_model,
                messages=self.conversation_history,
                tools=self.tools.get_tool_definitions(),
                tool_choice="auto",
                max_tokens=4000,
            )
            
            message = response.choices[0].message
            content = message.content or ""
            
            # Handle tool calls first
            session_finished = False
            tool_calls_data = []
            
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    
                    # Store tool call data
                    tool_calls_data.append({
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tool_call.function.arguments
                        }
                    })
                    
                    logging.info(f"🔧 Supervisor calling tool: {tool_name}")
                    tool_result = await self.tools.handle_tool_call(tool_name, arguments)
                    
                    # Check if this was the finished tool
                    if tool_name == "finished":
                        session_finished = True
                    
                    # Add tool response to conversation
                    self.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result
                    })
            
            # Add single assistant message (with or without tool calls)
            if content.strip() or tool_calls_data:
                assistant_message = {
                    "role": "assistant",
                    "content": content
                }
                if tool_calls_data:
                    assistant_message["tool_calls"] = tool_calls_data
                
                # Insert assistant message before tool responses
                if tool_calls_data:
                    # Move tool responses to end and insert assistant message before them
                    tool_responses = self.conversation_history[-len(tool_calls_data):]
                    self.conversation_history = self.conversation_history[:-len(tool_calls_data)]
                    self.conversation_history.append(assistant_message)
                    self.conversation_history.extend(tool_responses)
                else:
                    # No tool calls, just add the assistant message
                    self.conversation_history.append(assistant_message)
            
            return session_finished
            
        except Exception as e:
            logging.error(f"Error in supervisor turn: {e}")
            return False
