#!/usr/bin/env python3
"""
Triage Manager for spawning and managing individual triager instances.
Each vulnerability report gets its own triager instance with dedicated workspace.
"""

import asyncio
import json
import logging
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
import aiofiles
from openai import AsyncOpenAI

from .prompts.system_prompt import get_triage_system_prompt
from .prompts.initial_review_prompt import get_initial_review_prompt
from .prompts.validation_prompt import get_validation_prompt
from .prompts.severity_prompt import get_severity_prompt
from .triage_tools import TriageTools
from ..orchestration.instance_manager import InstanceManager
from ..orchestration.log_reader import LogReader


class TriagerInstance:
    """Individual triager instance - runs the triage process for one vulnerability report."""
    
    def __init__(
        self,
        triager_id: str,
        session_dir: Path,
        task_config: Dict[str, Any],
        vulnerability_data: Dict[str, Any],
        supervisor_model: str = "o3",
        api_key: str = None,
        codex_binary: str = "./target/release/codex"
    ):
        self.triager_id = triager_id
        self.session_dir = session_dir
        self.task_config = task_config
        self.vulnerability_data = vulnerability_data
        self.supervisor_model = supervisor_model
        self.api_key = api_key
        self.codex_binary = codex_binary
        
        # Triager state
        self.running = False
        self.conversation_history = []
        self.current_phase = 1
        
        # Files
        self.conversation_log_file = session_dir / "triage_conversation.log"
        self.conversation_history_file = session_dir / "conversation_history.json"
        self.feedback_file = session_dir / "supervisor_feedback.txt"
        
        # Initialize instance management (limited to 1 instance)
        self.instance_manager = InstanceManager(session_dir, codex_binary)
        self.log_reader = LogReader(session_dir, self.instance_manager)
        self.max_instances = 1
        self.spawned_instances = 0
        
        # Initialize OpenAI client with fallback support
        # Auto-detect provider based on API key
        use_openrouter = api_key and (api_key.startswith('sk-or-') or 'openrouter' in api_key.lower())
        base_url = "https://openrouter.ai/api/v1" if use_openrouter else "https://api.openai.com/v1"
        
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
        
        # Initialize triage tools with instance management
        self.triage_tools = TriageTools(
            session_dir=session_dir,
            task_config=task_config,
            instance_manager=self.instance_manager,
            log_reader=self.log_reader,
            max_instances=self.max_instances
        )
        
        logging.info(f"🔍 Initialized TriagerInstance {triager_id} for: {vulnerability_data.get('title', 'Unknown')}")
    
    async def run_triage(self) -> Dict[str, Any]:
        """Run the complete 3-phase triage process."""
        self.running = True
        
        try:
            logging.info(f"🔍 Starting triage process for: {self.vulnerability_data.get('title', 'Unknown')}")
            
            # Initialize conversation with system prompt
            self.conversation_history = [
                {"role": "system", "content": get_triage_system_prompt()}
            ]
            
            # Set vulnerability data in tools
            self.triage_tools.set_vulnerability_data(self.vulnerability_data)
            
            # Start Phase 1: Initial Review
            phase1_prompt = get_initial_review_prompt(self.vulnerability_data, self.task_config)
            self.conversation_history.append({"role": "user", "content": phase1_prompt})
            
            # Run conversation until completion
            result = await self._run_triage_conversation()
            
            return result
            
        except Exception as e:
            logging.error(f"❌ Triage process failed: {e}")
            return {"final_result": "ERROR", "error": str(e)}
        
        finally:
            self.running = False
    
    async def _run_triage_conversation(self) -> Dict[str, Any]:
        """Run the triage conversation through all phases."""
        
        phase_results = {}
        iteration = 0
        
        while self.running:
            iteration += 1
            
            # Call LLM with tools
            success = await self._call_triage_llm_with_tools()
            if not success:
                break
            
            # Check phase completion
            phase_results = self.triage_tools.get_phase_results()
            
            # Handle phase transitions
            if self.current_phase == 1 and 1 in phase_results:
                if phase_results[1]["decision"] == "REJECT":
                    # Phase 1 rejected - write feedback and end process
                    await self._write_rejection_feedback(phase_results[1])
                    return {"final_result": "REJECTED", "phase": 1, "reason": phase_results[1]["reasoning"]}
                else:
                    # Phase 1 approved - move to Phase 2
                    self.current_phase = 2
                    phase2_prompt = get_validation_prompt(self.vulnerability_data, phase_results[1])
                    self.conversation_history.append({"role": "user", "content": phase2_prompt})
            
            elif self.current_phase == 2 and 2 in phase_results:
                if phase_results[2]["decision"] == "NOT_REPRODUCED":
                    # Phase 2 failed - write feedback and end process
                    await self._write_reproduction_failure_feedback(phase_results[2])
                    return {"final_result": "NOT_REPRODUCED", "phase": 2, "feedback": phase_results[2]["feedback"]}
                else:
                    # Phase 2 succeeded - move to Phase 3
                    self.current_phase = 3
                    phase3_prompt = get_severity_prompt(self.vulnerability_data, phase_results[2])
                    self.conversation_history.append({"role": "user", "content": phase3_prompt})
            
            elif self.current_phase == 3 and 3 in phase_results:
                # Phase 3 completed - send to Slack and write success feedback
                await self._send_to_slack_and_notify(phase_results)
                return {
                    "final_result": "COMPLETED",
                    "severity": phase_results[3]["severity"],
                    "cvss_score": phase_results[3]["cvss_score"],
                    "phase_results": phase_results
                }
        
        # If we get here, something went wrong
        return {"final_result": "ERROR", "reason": "Triage process did not complete properly"}
    
    async def _call_triage_llm_with_tools(self) -> bool:
        """Make LLM call with tool support."""
        try:
            tools = self.triage_tools.get_tool_definitions()
            
            # Use correct parameters based on API provider
            completion_params = {
                "model": self.supervisor_model,
                "messages": self.conversation_history,
                "tools": tools,
                "tool_choice": "auto",
            }
            
            # Only set max_tokens for OpenRouter
            if os.getenv("OPENROUTER_API_KEY"):
                completion_params["max_tokens"] = 10000
            else:
                completion_params["max_completion_tokens"] = 10000
                
            response = await self.client.chat.completions.create(**completion_params)
            
            message = response.choices[0].message
            response_content = message.content or ""
            
            # Add assistant response to conversation
            self.conversation_history.append({
                "role": "assistant",
                "content": response_content,
                "tool_calls": message.tool_calls
            })
            
            # Handle tool calls
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)
                    
                    logging.info(f"🔧 Executing triage tool: {tool_name}")
                    
                    # Execute the tool
                    tool_result = await self.triage_tools.execute_tool(tool_name, arguments)
                    
                    # Add tool result to conversation
                    self.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result
                    })
            
            # Log and save conversation
            await self._log_conversation_entry(response_content, message.tool_calls if message.tool_calls else [])
            await self._save_conversation_history()
            
            return True
            
        except Exception as e:
            logging.error(f"❌ Triage LLM call failed: {e}")
            return False
    
    async def _write_reproduction_failure_feedback(self, phase2_result: Dict[str, Any]):
        """Write feedback when unable to reproduce vulnerability."""
        vulnerability_title = self.vulnerability_data.get('title', 'Unknown')
        feedback_message = phase2_result.get('feedback', 'Unable to reproduce - no specific feedback provided')
        
        feedback_content = f"""🔍 **Triage Update: Unable to Reproduce**

**Report**: {vulnerability_title}
**Triage ID**: {self.triager_id}
**Phase 2 Result**: NOT_REPRODUCED

**Triage Feedback**: {feedback_message}

The triage team was unable to reproduce the reported vulnerability after thorough testing. 

**Original Report**:
{self.vulnerability_data}

Please revisit this particular report. You may resubmit a new report after you have resolved the issues.
"""
        
        # Write feedback file
        async with aiofiles.open(self.feedback_file, 'w') as f:
            await f.write(feedback_content)
        
        logging.info(f"📤 Wrote reproduction failure feedback for {self.triager_id}")
    
    async def _write_rejection_feedback(self, phase1_result: Dict[str, Any]):
        """Write feedback for rejected reports."""
        reasoning = phase1_result.get("reasoning", "No specific reason provided")
        
        feedback_content = f"""❌ **Triage Update: Report Rejected**

**Report**: {self.vulnerability_data.get('title', 'Unknown')}
**Triage ID**: {self.triager_id}
**Phase 1 Result**: REJECTED

**Rejection Reason**: {reasoning}

The vulnerability report was rejected during initial review. 

Please review and address the issues before resubmitting."""
        
        async with aiofiles.open(self.feedback_file, 'w') as f:
            await f.write(feedback_content)
        
        logging.info(f"📤 Wrote rejection feedback for {self.triager_id}")
    
    async def _send_to_slack_and_notify(self, phase_results: Dict[str, Any]):
        """Send completed vulnerability to Slack and notify supervisor."""
        
        # TODO: Implement actual Slack submission
        severity = phase_results[3]["severity"]
        cvss_score = phase_results[3]["cvss_score"]
        
        logging.info(f"📤 [PLACEHOLDER] Would send to Slack: {self.vulnerability_data.get('title', 'Unknown')} - {severity} ({cvss_score})")
        
        # Notify supervisor of successful triage
        success_feedback = f"""✅ **Triage Complete: Vulnerability Confirmed**

**Report**: {self.vulnerability_data.get('title', 'Unknown')}
**Triage ID**: {self.triager_id}
**Final Classification**: {severity} (CVSS: {cvss_score})

The vulnerability has been successfully reproduced and classified. It has been submitted to the appropriate team for further action.

No further action needed from your side."""
        
        # Write success feedback file
        async with aiofiles.open(self.feedback_file, 'w') as f:
            await f.write(success_feedback)
        
        logging.info(f"📤 Wrote success feedback for {self.triager_id}")
    
    async def _log_conversation_entry(self, response_content: str, tool_calls):
        """Log conversation entry to human-readable file."""
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            log_entry = f"[{timestamp}] ASSISTANT: {response_content}\n"
            
            if tool_calls:
                for tool_call in tool_calls:
                    log_entry += f"[{timestamp}] TOOL_CALL: {tool_call.function.name}({tool_call.function.arguments})\n"
            
            log_entry += "---\n"
            
            async with aiofiles.open(self.conversation_log_file, 'a') as f:
                await f.write(log_entry)
                
        except Exception as e:
            logging.error(f"❌ Error logging conversation: {e}")
    
    async def _save_conversation_history(self):
        """Save structured conversation history to JSON."""
        try:
            # Create serializable version
            serializable_history = []
            for message in self.conversation_history:
                serialized_message = {
                    "role": message["role"],
                    "content": message.get("content", "")
                }
                
                if "tool_calls" in message and message["tool_calls"]:
                    serialized_message["tool_calls"] = []
                    for tool_call in message["tool_calls"]:
                        serialized_message["tool_calls"].append({
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments
                            }
                        })
                
                if "tool_call_id" in message:
                    serialized_message["tool_call_id"] = message["tool_call_id"]
                
                serializable_history.append(serialized_message)
            
            # Save conversation data
            conversation_data = {
                "triager_id": self.triager_id,
                "vulnerability_title": self.vulnerability_data.get('title', 'Unknown'),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "messages": serializable_history
            }
            
            async with aiofiles.open(self.conversation_history_file, 'w') as f:
                await f.write(json.dumps(conversation_data, indent=2))
                
        except Exception as e:
            logging.error(f"❌ Error saving conversation history: {e}")


class TriageManager:
    """Manager that spawns and tracks individual triager instances."""
    
    def __init__(
        self, 
        session_dir: Path, 
        task_config: Dict[str, Any],
        supervisor_model: str = "o3", 
        api_key: str = None,
        codex_binary: str = "./target/release/codex"
    ):
        self.session_dir = session_dir
        self.task_config = task_config
        self.supervisor_model = supervisor_model
        self.api_key = api_key
        self.codex_binary = codex_binary
        
        # Create triage instances directory
        self.triage_instances_dir = session_dir / "triage_instances"
        self.triage_instances_dir.mkdir(exist_ok=True)
        
        # Track active triager instances
        self.active_triagers: Dict[str, Dict[str, Any]] = {}
        
        logging.info(f"🔍 Initialized TriageManager with instances dir: {self.triage_instances_dir}")
    
    async def submit_vulnerability_report(self, vulnerability_data: Dict[str, Any]) -> str:
        """Submit a vulnerability report by spawning a new triager instance."""
        
        # Generate unique triager ID
        triager_id = str(uuid.uuid4())[:8]
        
        # Create triager workspace
        triager_dir = self.triage_instances_dir / f"triager_{triager_id}"
        triager_dir.mkdir(exist_ok=True)
        
        try:
            # Create triager instance
            triager = TriagerInstance(
                triager_id=triager_id,
                session_dir=triager_dir,
                task_config=self.task_config,
                vulnerability_data=vulnerability_data,
                supervisor_model=self.supervisor_model,
                api_key=self.api_key,
                codex_binary=self.codex_binary
            )
            
            # Store instance info
            self.active_triagers[triager_id] = {
                "triager_id": triager_id,
                "status": "starting",
                "start_time": datetime.now(timezone.utc).isoformat(),
                "workspace_dir": str(triager_dir),
                "vulnerability_data": vulnerability_data,
                "instance": triager
            }
            
            # Start triager in background
            asyncio.create_task(self._run_triager(triager_id))
            
            logging.info(f"🔍 Spawned triager {triager_id} for vulnerability: {vulnerability_data.get('title', 'Unknown')}")
            return f"✅ Vulnerability submitted to triage with ID: {triager_id}"
            
        except Exception as e:
            logging.error(f"❌ Failed to spawn triager {triager_id}: {e}")
            # Clean up on failure
            if triager_id in self.active_triagers:
                del self.active_triagers[triager_id]
            return f"❌ Failed to submit vulnerability for triage: {str(e)}"
    
    async def _run_triager(self, triager_id: str):
        """Run a triager instance in the background."""
        try:
            instance_info = self.active_triagers[triager_id]
            triager = instance_info["instance"]
            
            # Update status
            instance_info["status"] = "running"
            
            # Run the triage process
            result = await triager.run_triage()
            
            # Update final status based on result
            if result.get("final_result") == "COMPLETED":
                instance_info["status"] = "completed"
                instance_info["result"] = "reproduced_and_classified"
            elif result.get("final_result") == "NOT_REPRODUCED":
                instance_info["status"] = "completed" 
                instance_info["result"] = "unable_to_reproduce"
            elif result.get("final_result") == "REJECTED":
                instance_info["status"] = "completed"
                instance_info["result"] = "rejected"
            else:
                instance_info["status"] = "failed"
                instance_info["result"] = "error"
            
            instance_info["end_time"] = datetime.now(timezone.utc).isoformat()
            instance_info["triage_result"] = result
            
            logging.info(f"🔍 Triager {triager_id} completed with result: {instance_info['result']}")
            
        except Exception as e:
            logging.error(f"❌ Triager {triager_id} failed: {e}")
            if triager_id in self.active_triagers:
                self.active_triagers[triager_id]["status"] = "failed"
                self.active_triagers[triager_id]["error"] = str(e)
                self.active_triagers[triager_id]["end_time"] = datetime.now(timezone.utc).isoformat()
    
    def get_triager_feedback_dirs(self) -> List[Path]:
        """Get directories of all active triagers to check for feedback files."""
        feedback_dirs = []
        for triager_id, instance_info in self.active_triagers.items():
            if instance_info.get("status") in ["running", "completed"]:
                triager_dir = Path(instance_info["workspace_dir"])
                if triager_dir.exists():
                    feedback_dirs.append(triager_dir)
        return feedback_dirs
    
    async def get_triage_status(self) -> Dict[str, Any]:
        """Get current triage status."""
        try:
            running_count = sum(1 for info in self.active_triagers.values() if info.get("status") == "running")
            completed_count = sum(1 for info in self.active_triagers.values() if info.get("status") == "completed")
            
            return {
                "running_count": running_count,
                "completed_count": completed_count,
                "total_triagers": len(self.active_triagers)
            }
        except Exception as e:
            logging.error(f"❌ Error getting triage status: {e}")
            return {"error": str(e)}