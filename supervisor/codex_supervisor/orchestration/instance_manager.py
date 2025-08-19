#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

import aiofiles


class InstanceManager:
    """Manages codex instances spawned by the supervisor."""
    
    def __init__(self, session_dir: Path, codex_binary: str):
        self.session_dir = session_dir
        self.codex_binary = codex_binary
        self.instances: Dict[str, Dict[str, Any]] = {}
    
    async def spawn_instance(self, instance_id: str, task_description: str, 
                           workspace_dir: str, duration_minutes: int, specialist: str = "generalist") -> bool:
        """Spawn a new codex instance."""
        if instance_id in self.instances:
            logging.warning(f"Instance {instance_id} already exists")
            return False
        
        workspace_name = Path(workspace_dir).name
        workspace_path = self.session_dir / "workspaces" / workspace_name
        workspace_path.mkdir(parents=True, exist_ok=True)
        
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
        
        cmd.extend(["--mode", specialist])
            
        cmd.append(task_description)
        
        try:
            env = os.environ.copy()
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                env=env,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )
            
            self.instances[instance_id] = {
                "process": process,
                "task": task_description,
                "workspace_dir": workspace_name,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_minutes": duration_minutes,
                "log_dir": workspace_path,
                "status": "running"
            }
            
            logging.info(f"🚀 Spawned codex instance {instance_id} (PID: {process.pid})")
            
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
            if process.returncode is None:
                logging.info(f"🛑 Force killing instance {instance_id} (PID: {process.pid})")
                
                try:
                    if hasattr(os, 'killpg'):
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                
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
            timeout_seconds = duration_minutes * 60
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            
            if process.returncode == 0:
                instance["status"] = "completed"
                logging.info(f"✅ Instance {instance_id} completed successfully")
            else:
                instance["status"] = "failed"
                logging.error(f"❌ Instance {instance_id} failed with exit code {process.returncode}")
                
                try:
                    stdout, stderr = await process.communicate()
                    if stderr:
                        logging.error(f"❌ Instance {instance_id} stderr: {stderr.decode()}")
                except Exception as e:
                    logging.error(f"❌ Failed to read process output for {instance_id}: {e}")
                
        except asyncio.TimeoutError:
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
            logging.info(f"🔧 Creating directory: {actual_log_dir}")
            actual_log_dir.mkdir(parents=True, exist_ok=True)
            
            logging.info(f"🔧 Writing followup data: {json.dumps(followup_data, indent=2)}")
            async with aiofiles.open(followup_file, 'w') as f:
                await f.write(json.dumps(followup_data, indent=2))
            
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
                        final_result_file = instance_log_dir / "final_result.json"
                        if final_result_file.exists():
                            async with aiofiles.open(final_result_file, 'r') as f:
                                final_result = json.loads(await f.read())
                            
                            conversation = final_result.get("conversation", [])
                            for msg in reversed(conversation):
                                if msg.get("role") == "assistant":
                                    responses[instance_id] = msg.get("content", "")
                                    break
                                    
            except Exception as e:
                logging.error(f"Error checking response for instance {instance_id}: {e}")
        
        return responses