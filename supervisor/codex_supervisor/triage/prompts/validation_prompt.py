#!/usr/bin/env python3
"""Validation prompt for triage agent."""

from typing import Dict, Any

def get_validation_prompt(vulnerability_data: Dict[str, Any], phase1_result: Dict[str, Any]) -> str:
    """Generate user prompt for validation/reproduction phase."""
    
    return f"""## PHASE 2: VALIDATION & REPRODUCTION

You have approved this vulnerability for reproduction in Phase 1. Now your goal is to reproduce the vulnerability by following the provided steps:

{vulnerability_data.get('repro_steps', 'N/A')}

You are provided with the ability to spawn a subagent that will help you reproduce the vulnerability. Your task is largely to coordinate with the subagent, and to provide it with the necessary information to reproduce the vulnerability. Here is how you should utilize the subagent:

### 🚀 INSTANCE ORCHESTRATION TOOLS:
**`spawn_codex`** - Launch focused security testing instances:
- **WHEN TO USE**: When you have a specific, well-defined security testing objective
- **BEST PRACTICES**:
  - Be extremely specific in task descriptions ("Test for SQL injection vulnerabilities in the user registration API endpoint at /api/v1/register, focusing on the 'email' and 'username' parameters")
  - Use descriptive instance IDs ("sqli-registration", "xss-search-forms", "auth-bypass-admin")  
  - Set appropriate duration (30min for focused tests, 60min for broader assessments)
  - Create separate workspaces for different target systems
- **AVOID**: Vague tasks like "find vulnerabilities" or "test the website"
- **AUTHENTICATION**: The instance will be started on the current jumpbox, you do not need to instruct it to access it first. If you anticipate that the instance will need external credentials, you should provide them, with the appropriate context for how to use them. 

**`list_instances`** - Monitor your testing fleet:
- **WHEN TO USE**: Regularly throughout session to track progress and resource usage
- **BEST PRACTICES**: 
  - Check before spawning new instances to avoid resource waste
  - Identify stalled or completed instances for followup

**`terminate_instance`** - Clean up completed or stuck instances:
- **WHEN TO USE**: When instances complete their tasks, encounter insurmountable blockers, or exceed useful time
- **BEST PRACTICES**: 
  - Always read logs before terminating to capture findings
  - Don't terminate instances that are making progress, even if slow

**`send_followup`** - Continue conversations with instances:
- **WHEN TO USE**: When instances need clarification, additional objectives, or course correction
- **BEST PRACTICES**:
  - Provide specific guidance based on their current progress
  - Ask for clarification on unclear findings
  - Redirect if they're off-track or stuck

**`wait_for_instance`** - Block supervisor loop until instance responds:
- **WHEN TO USE**: After spawning an instance when you want to wait for its response before continuing
- **BEST PRACTICES**:
  - Use after spawning critical instances that inform your next strategy
  - Set appropriate timeout based on the task complexity
  - Use `expected_status: "any"` to wait for completion or callback
  - Combine with `send_followup` for multi-turn conversations
- **EXAMPLES**:
  - `{{"instance_id": "recon-1", "timeout_minutes": 5}}` - Wait for reconnaissance to complete
  - `{{"instance_id": "sqli-test", "timeout_minutes": 3, "expected_status": "waiting_for_followup"}}` - Wait for first response

When you're ready to complete Phase 2, call `finished_phase_2` with:
- **decision**: "REPRODUCED" or "NOT_REPRODUCED"
- **evidence**: Detailed documentation of your reproduction attempt and results
- **additional_findings**: Any extra impact or variations you discovered (if reproduced)
- **feedback**: Specific feedback for the reporter (especially if not reproduced)
"""