#!/usr/bin/env python3
"""
Triage tools for vulnerability assessment and validation.
"""

import json
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import aiofiles
from ..vulnerability_storage import get_session_vulnerability_storage


class TriageTools:
    """Tools available to the triage agent for vulnerability assessment."""
    
    def __init__(
        self, 
        session_dir: Path, 
        task_config: Dict[str, Any],
        instance_manager = None,
        log_reader = None,
        max_instances: int = 1
    ):
        self.session_dir = session_dir
        self.task_config = task_config
        self.instance_manager = instance_manager
        self.log_reader = log_reader
        self.max_instances = max_instances
        
        # Current triage state
        self.current_phase = 1
        self.phase_results = {}
        self.vulnerability_data = None
        
        logging.info(f"🔧 Initialized TriageTools in {session_dir}")
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible tool definitions for triage."""
        tools = [
            # Instance management tools (if available)
            {
                "type": "function",
                "function": {
                    "name": "spawn_codex",
                    "description": "Spawn a codex instance for vulnerability reproduction (limited to 1)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "Unique identifier for this instance"
                            },
                            "task_description": {
                                "type": "string", 
                                "description": "Task for the instance to work on"
                            },
                            "duration_minutes": {
                                "type": "number",
                                "description": "How long the instance should run (default: 30)"
                            }
                        },
                        "required": ["instance_id", "task_description"]
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
                    "name": "read_instance_logs",
                    "description": "Read logs from a codex instance",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "ID of the instance to read logs from"
                            },
                            "tail_lines": {
                                "type": "number", 
                                "description": "Number of recent lines to read (default: 50)"
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
                    "description": "Send a followup message to a waiting instance", 
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instance_id": {
                                "type": "string",
                                "description": "ID of the instance to send followup to"
                            },
                            "message": {
                                "type": "string",
                                "description": "Followup message to send"
                            }
                        },
                        "required": ["instance_id", "message"]
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
        ] + [
            {
                "type": "function",
                "function": {
                    "name": "finished_phase_1",
                    "description": "Complete Phase 1 (Initial Review) and provide decision",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["PROCEED", "REJECT"],
                                "description": "Decision to proceed to validation or reject the report"
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "Detailed explanation of the decision"
                            },
                            "notes": {
                                "type": "string",
                                "description": "Additional observations or concerns"
                            }
                        },
                        "required": ["decision", "reasoning"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "finished_phase_2",
                    "description": "Complete Phase 2 (Validation) and provide reproduction results",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["REPRODUCED", "NOT_REPRODUCED"],
                                "description": "Whether the vulnerability was successfully reproduced"
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Detailed evidence and documentation of reproduction attempt"
                            },
                            "additional_findings": {
                                "type": "string",
                                "description": "Any extra impact or variations discovered beyond original report"
                            },
                            "feedback": {
                                "type": "string",
                                "description": "Specific feedback for the original reporter"
                            }
                        },
                        "required": ["decision", "evidence"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "finished_phase_3",
                    "description": "Complete Phase 3 (Severity Assessment) and provide final classification",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "severity": {
                                "type": "string",
                                "enum": ["Critical", "High", "Medium", "Low"],
                                "description": "Final severity classification"
                            },
                            "cvss_score": {
                                "type": "number",
                                "description": "CVSS v3.1 numeric score (0.0-10.0)"
                            },
                            "cvss_vector": {
                                "type": "string",
                                "description": "Full CVSS vector string (e.g., AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H)"
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "Detailed explanation of severity assessment"
                            },
                            "comparison": {
                                "type": "string",
                                "description": "How this differs from originally reported severity"
                            }
                        },
                        "required": ["severity", "cvss_score", "reasoning"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "submit",
                    "description": "Submit a vulnerability report for processing",
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
                    "name": "exec_bash",
                    "description": "Execute a bash command for vulnerability validation",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Bash command to execute"
                            }
                        },
                        "required": ["command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "create_test_file",
                    "description": "Create a test file for vulnerability validation",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Name of the file to create"
                            },
                            "content": {
                                "type": "string",
                                "description": "Content to write to the file"
                            },
                            "description": {
                                "type": "string",
                                "description": "Description of the file's purpose"
                            }
                        },
                        "required": ["filename", "content", "description"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "log_finding",
                    "description": "Log important findings or observations during triage",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "phase": {
                                "type": "string",
                                "description": "Current triage phase (1, 2, or 3)"
                            },
                            "finding": {
                                "type": "string",
                                "description": "The finding or observation to log"
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Supporting evidence or details"
                            }
                        },
                        "required": ["phase", "finding"]
                    }
                }
            }
        ]
        return tools
    
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a triage tool."""
        try:
            # Instance management tools
            if tool_name == "spawn_codex":
                return await self._spawn_codex(arguments)
            elif tool_name == "wait_for_instance":
                return await self._wait_for_instance(arguments)
            elif tool_name == "read_instance_logs":
                return await self._read_instance_logs(arguments)
            elif tool_name == "send_followup":
                return await self._send_followup(arguments)
            elif tool_name == "terminate_instance":
                return await self._terminate_instance(arguments)
            # Triage phase tools
            elif tool_name == "finished_phase_1":
                return await self._finished_phase_1(arguments)
            elif tool_name == "finished_phase_2":
                return await self._finished_phase_2(arguments)
            elif tool_name == "finished_phase_3":
                return await self._finished_phase_3(arguments)
            elif tool_name == "submit":
                return await self._submit(arguments)
            # Validation tools
            elif tool_name == "exec_bash":
                return await self._exec_bash(arguments)
            elif tool_name == "create_test_file":
                return await self._create_test_file(arguments)
            elif tool_name == "log_finding":
                return await self._log_finding(arguments)
            else:
                return f"❌ Unknown tool: {tool_name}"
        except Exception as e:
            logging.error(f"❌ Tool execution error ({tool_name}): {e}")
            return f"❌ Error executing {tool_name}: {str(e)}"
    
    # Instance management methods
    
    async def _spawn_codex(self, args: Dict[str, Any]) -> str:
        """Spawn a codex instance (limited to 1 for triagers)."""
        if not self.instance_manager:
            return "❌ Instance management not available in this triage session"
        
        # Guard: Check if max instances already spawned
        active_instances = self.instance_manager.get_active_instances()
        if len(active_instances) >= self.max_instances:
            return f"❌ Cannot spawn instance: Maximum of {self.max_instances} instance(s) allowed for triage"
        
        instance_id = args["instance_id"]
        task_description = args["task_description"]
        workspace_dir = instance_id
        duration_minutes = args.get("duration_minutes", 30)
        
        # Instance manager now handles routing/prompt generation internally
        success = await self.instance_manager.spawn_instance(
            instance_id=instance_id,
            task_description=task_description,
            workspace_dir=workspace_dir,
            duration_minutes=duration_minutes
        )
        
        if success:
            return f"✅ Spawned codex instance {instance_id} for vulnerability reproduction"
        else:
            return f"❌ Failed to spawn instance {instance_id}"

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
        
        instance_log_dir = instance["log_dir"]
        status_file = instance_log_dir / "status.json"
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
                        conversation_file = instance_log_dir / "realtime_conversation.json"
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
                            # Read the latest response from final_result.json
                            final_result_file = instance_log_dir / "final_result.json"
                            last_response = "No response available"
                            if final_result_file.exists():
                                try:
                                    async with aiofiles.open(final_result_file, 'r') as f:
                                        final_result = json.loads(await f.read())
                                    
                                    # Get the last assistant message from conversation
                                    conversation = final_result.get("conversation", [])
                                    for msg in reversed(conversation):
                                        if msg.get("role") == "assistant":
                                            last_response = msg.get("content", "")[:200] + ("..." if len(msg.get("content", "")) > 200 else "")
                                            break
                                except Exception as e:
                                    logging.error(f"Error reading final_result for {instance_id}: {e}")
                            
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
    
    async def _read_instance_logs(self, args: Dict[str, Any]) -> str:
        """Read logs from a codex instance."""
        if not self.log_reader:
            return "❌ Log reading not available in this triage session"
        
        instance_id = args["instance_id"]
        tail_lines = args.get("tail_lines", 50)
        
        try:
            logs = await self.log_reader.read_instance_logs(instance_id, tail_lines=tail_lines)
            return f"📋 Logs for instance {instance_id}:\n\n{logs}"
        except Exception as e:
            return f"❌ Error reading logs for {instance_id}: {str(e)}"
    
    async def _send_followup(self, args: Dict[str, Any]) -> str:
        """Send followup message to a waiting instance."""
        if not self.instance_manager:
            return "❌ Instance management not available in this triage session"
        
        instance_id = args["instance_id"]
        message = args["message"]
        
        success = await self.instance_manager.send_followup(instance_id, message)
        
        if success:
            return f"✅ Sent followup to instance {instance_id}"
        else:
            return f"❌ Failed to send followup to {instance_id} (instance may not be waiting)"
    
    async def _terminate_instance(self, args: Dict[str, Any]) -> str:
        """Terminate a specific codex instance."""
        if not self.instance_manager:
            return "❌ Instance management not available in this triage session"
        
        instance_id = args["instance_id"]
        
        success = await self.instance_manager.terminate_instance(instance_id)
        
        if success:
            return f"✅ Terminated instance {instance_id}"
        else:
            return f"❌ Failed to terminate instance {instance_id}"
    
    # Triage phase methods
    
    async def _finished_phase_1(self, args: Dict[str, Any]) -> str:
        """Handle completion of Phase 1 (Initial Review)."""
        decision = args["decision"]
        reasoning = args["reasoning"]
        notes = args.get("notes", "")
        
        # Store phase 1 results
        self.phase_results[1] = {
            "decision": decision,
            "reasoning": reasoning,
            "notes": notes,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Log phase completion
        await self._log_phase_completion(1, decision, reasoning)
        
        if decision == "PROCEED":
            self.current_phase = 2
            return f"✅ Phase 1 completed: {decision}\n\nProceeding to Phase 2: Validation & Reproduction"
        else:
            # REJECT - end triage process
            return f"❌ Phase 1 completed: {decision}\n\nTriage process terminated. Report rejected."
    
    async def _finished_phase_2(self, args: Dict[str, Any]) -> str:
        """Handle completion of Phase 2 (Validation)."""
        decision = args["decision"]
        evidence = args["evidence"]
        additional_findings = args.get("additional_findings", "")
        feedback = args.get("feedback", "")
        
        # Store phase 2 results
        self.phase_results[2] = {
            "decision": decision,
            "evidence": evidence,
            "additional_findings": additional_findings,
            "feedback": feedback,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Log phase completion
        await self._log_phase_completion(2, decision, evidence)
        
        if decision == "REPRODUCED":
            self.current_phase = 3
            return f"✅ Phase 2 completed: {decision}\n\nProceeding to Phase 3: Severity Assessment"
        else:
            # NOT_REPRODUCED - feedback will be handled by TriagerInstance
            return f"❌ Phase 2 completed: {decision}\n\nTriage process terminated. Unable to reproduce vulnerability."
    
    async def _finished_phase_3(self, args: Dict[str, Any]) -> str:
        """Handle completion of Phase 3 (Severity Assessment)."""
        severity = args["severity"]
        cvss_score = args["cvss_score"]
        cvss_vector = args.get("cvss_vector", "")
        reasoning = args["reasoning"]
        comparison = args.get("comparison", "")
        
        # Store phase 3 results
        self.phase_results[3] = {
            "severity": severity,
            "cvss_score": cvss_score,
            "cvss_vector": cvss_vector,
            "reasoning": reasoning,
            "comparison": comparison,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Log phase completion
        await self._log_phase_completion(3, severity, reasoning)
        
        # Triage complete - ready for Slack submission
        return f"✅ Phase 3 completed: {severity} ({cvss_score})\n\nTriage process complete. Vulnerability ready for Slack submission."
    

    async def _submit(self, args: Dict[str, Any]) -> str:
            """Submit vulnerability report for processing (triage or direct to output)."""
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
                       
            await self._send_directly_to_slack(payload)
            return f"✅ Vulnerability report sent to Slack: {args['title']}"

    async def _send_directly_to_slack(self, payload: Dict[str, Any]) -> str:
        """Send vulnerability report directly to Slack (benchmark mode)."""
        webhook_url = self.task_config.get("slack_webhook_url", "https://hooks.slack.com/triggers/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")
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
                logging.info("✅ Slack vulnerability report sent (benchmark mode)")
                
                # Log vulnerability to running file
                await self._log_vulnerability_submission(payload)
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logging.error(f"❌ Failed to send Slack report: {error_msg}")
                
        except Exception as e:
            logging.error(f"❌ Error sending Slack report: {e}")

    async def _log_vulnerability_submission(self, payload: Dict[str, Any]) -> None:
        """Log vulnerability submission to both session-local and global storage."""
        import aiofiles
        from datetime import datetime, timezone
        
        # Log to session-local file (existing behavior)
        vuln_log_file = self.session_dir / "vulnerabilities_found.log"
        
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_entry = f"""
=== VULNERABILITY SUBMITTED TO SLACK ===
Timestamp: {timestamp}
Title: {payload['title']}
Asset: {payload['asset']}
Type: {payload['vuln_type']}
Severity: {payload['severity']}

Description:
{payload['description']}

Reproduction Steps:
{payload['repro_steps']}

Impact:
{payload['impact']}

Cleanup:
{payload['cleanup']}
================================================

"""
        
        try:
            # Log to session-local file
            async with aiofiles.open(vuln_log_file, 'a') as f:
                await f.write(log_entry)
            logging.info(f"📝 Logged vulnerability to session file: {vuln_log_file}")
            
            # Log to session vulnerability storage
            storage = get_session_vulnerability_storage(self.session_dir.parent)  # Get session dir from triager dir
            session_id = self.session_dir.parent.name  # Extract session ID from path
            triager_id = getattr(self, 'triager_id', 'unknown')  # Will be set by TriagerInstance
            
            vuln_hash = await storage.log_vulnerability(payload, session_id, triager_id)
            logging.info(f"📝 Logged vulnerability {vuln_hash} to session storage")
            
        except Exception as e:
            logging.error(f"❌ Failed to log vulnerability: {e}")
    

    async def _exec_bash(self, args: Dict[str, Any]) -> str:
        """Execute a shell command for vulnerability validation."""
        command = args["command"]

        try:
            logging.info(f"🔧 Running bash command: {command}")
            
            # Execute command with timeout
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.session_dir
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
                
                output = []
                output.append(f"Command: {command}")
                
                if stdout:
                    output.append(f"STDOUT:\n{stdout.decode().strip()}")
                if stderr:
                    output.append(f"STDERR:\n{stderr.decode().strip()}")
                
                output.append(f"Exit code: {process.returncode}")
                
                result = "\n".join(output)
                
                # Log command execution
                await self._log_finding(
                    {"phase": str(self.current_phase), "finding": f"Executed bash command: {command}", "evidence": result}
                )
                
                return result
                
            except asyncio.TimeoutError:
                process.kill()
                return f"❌ Command timed out after 60 seconds: {command}"
            
        except Exception as e:
            return f"❌ Error executing command: {str(e)}"
    
    async def _create_test_file(self, args: Dict[str, Any]) -> str:
        """Create a test file for vulnerability validation."""
        filename = args["filename"]
        content = args["content"]
        description = args["description"]
        
        try:
            file_path = self.session_dir / filename
            
            async with aiofiles.open(file_path, 'w') as f:
                await f.write(content)
            
            # Log file creation
            await self._log_finding({
                "phase": str(self.current_phase),
                "finding": f"Created test file: {filename}",
                "evidence": f"Description: {description}\nPath: {file_path}\nSize: {len(content)} bytes"
            })
            
            return f"✅ Created test file: {filename}\nPath: {file_path}\nDescription: {description}"
            
        except Exception as e:
            return f"❌ Error creating file {filename}: {str(e)}"
    
    async def _log_finding(self, args: Dict[str, Any]) -> str:
        """Log important findings during triage."""
        phase = args["phase"]
        finding = args["finding"]
        evidence = args.get("evidence", "")
        
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": phase,
                "finding": finding,
                "evidence": evidence
            }
            
            findings_file = self.session_dir / "triage_findings.log"
            
            # Append to findings log
            log_line = f"[{log_entry['timestamp']}] Phase {phase}: {finding}\n"
            if evidence:
                log_line += f"Evidence: {evidence}\n"
            log_line += "---\n"
            
            async with aiofiles.open(findings_file, 'a') as f:
                await f.write(log_line)
            
            logging.info(f"📝 Logged Phase {phase} finding: {finding}")
            return f"✅ Logged finding for Phase {phase}"
            
        except Exception as e:
            return f"❌ Error logging finding: {str(e)}"
    
    async def _log_phase_completion(self, phase: int, decision: str, details: str):
        """Log completion of a triage phase."""
        try:
            completion_entry = {
                "phase": phase,
                "decision": decision,
                "details": details[:500] + "..." if len(details) > 500 else details,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }
            
            phases_file = self.session_dir / "phase_completions.json"
            
            # Load existing completions
            completions = []
            if phases_file.exists():
                async with aiofiles.open(phases_file, 'r') as f:
                    content = await f.read()
                    if content.strip():
                        completions = json.loads(content)
            
            # Add new completion
            completions.append(completion_entry)
            
            # Save updated completions
            async with aiofiles.open(phases_file, 'w') as f:
                await f.write(json.dumps(completions, indent=2))
            
            logging.info(f"✅ Phase {phase} completed: {decision}")
            
        except Exception as e:
            logging.error(f"❌ Error logging phase completion: {e}")
    
    
    def set_vulnerability_data(self, vulnerability_data: Dict[str, Any]):
        """Set the current vulnerability data being triaged."""
        self.vulnerability_data = vulnerability_data
    
    def get_phase_results(self) -> Dict[int, Dict[str, Any]]:
        """Get results from completed phases."""
        return self.phase_results.copy()
    
    def get_current_phase(self) -> int:
        """Get the current triage phase."""
        return self.current_phase
