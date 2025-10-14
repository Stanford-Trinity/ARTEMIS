#!/usr/bin/env python3
"""Initial review prompt for triage agent."""

from typing import Dict, Any, Optional

def get_initial_review_prompt(vulnerability_data: Dict[str, Any], task_config: Dict[str, Any], previous_vulns_context: Optional[str] = None) -> str:
    """Generate user prompt for initial review phase."""
    
    return f"""## PHASE 1: INITIAL REVIEW

Please conduct an initial review of this vulnerability report:

**Vulnerability Report:**
- **Title**: {vulnerability_data.get('title', 'N/A')}
- **Asset**: {vulnerability_data.get('asset', 'N/A')}
- **Reported Severity**: {vulnerability_data.get('severity', 'N/A')}
- **Vulnerability Type**: {vulnerability_data.get('vuln_type', 'N/A')}
- **Description**: {vulnerability_data.get('description', 'N/A')}

**Reproduction Steps:**
```
{vulnerability_data.get('repro_steps', 'N/A')}
```

**Claimed Impact:**
{vulnerability_data.get('impact', 'N/A')}

**Testing Scope:**
{task_config}

**Previous Vulnerabilities Already Found:**
{previous_vulns_context or ""}

## Review Checklist:
1. **Scope Verification**: Is the reported asset within the defined testing boundaries?
2. **Completeness Check**: Are the reproduction steps clear, complete, and actionable?
3. **Legitimacy Assessment**: Does this appear to be a genuine security vulnerability?
4. **Impact Assessment**: Does the claimed impact seem realistic and significant?
5. **Resource Evaluation**: Is this worth investing time and resources to reproduce?

## Your Task:
Review this vulnerability report against the checklist above. Based on your assessment, decide whether to **PROCEED** to validation or **REJECT** this report.

When you're ready to complete Phase 1, call `finished_phase_1` with:
- **decision**: "PROCEED" or "REJECT" 
- **reasoning**: Detailed explanation of your decision
- **notes**: Any additional observations or concerns

If you decide to PROCEED, briefly outline what you plan to focus on during the validation phase."""