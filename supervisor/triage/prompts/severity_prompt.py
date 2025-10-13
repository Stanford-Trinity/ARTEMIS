#!/usr/bin/env python3
"""Severity assessment prompt for triage agent."""

from typing import Dict, Any

def get_severity_prompt(vulnerability_data: Dict[str, Any], phase2_result: Dict[str, Any]) -> str:
    """Generate user prompt for severity assessment phase."""
    
    return f"""## PHASE 3: SEVERITY ASSESSMENT

You have successfully reproduced this vulnerability in Phase 2. Now classify its severity using industry-standard CVSS criteria.

**Vulnerability Summary:**
- **Title**: {vulnerability_data.get('title', 'N/A')}
- **Asset**: {vulnerability_data.get('asset', 'N/A')}
- **Type**: {vulnerability_data.get('vuln_type', 'N/A')}
- **Originally Reported As**: {vulnerability_data.get('severity', 'N/A')}

## CVSS-Based Severity Classification:

### CRITICAL (9.0-10.0)
- Complete system compromise possible
- Administrative/root access obtained
- Large-scale data exfiltration capability
- Network-wide impact potential

### HIGH (7.0-8.9)
- Significant privilege escalation
- Access to sensitive data or critical systems
- User account compromise with broader implications
- Strong potential for lateral movement

### MEDIUM (4.0-6.9)
- Limited unauthorized access gained
- Information disclosure of moderate sensitivity
- Denial of service against important services
- Requires some user interaction but still exploitable

### LOW (0.1-3.9)
- Minimal security impact
- Difficult to exploit or requires extensive prerequisites
- Theoretical vulnerabilities with limited real-world impact
- Information disclosure of non-sensitive data

## CVSS v3.1 Assessment Factors:

**Attack Vector (AV)**:
- Network (N): Exploitable remotely over network
- Adjacent (A): Requires local network access
- Local (L): Requires local system access
- Physical (P): Requires physical access

**Attack Complexity (AC)**:
- Low (L): Straightforward to exploit
- High (H): Requires special conditions or extensive preparation

**Privileges Required (PR)**:
- None (N): No authentication needed
- Low (L): Basic user privileges
- High (H): Administrative privileges

**User Interaction (UI)**:
- None (N): No user interaction required
- Required (R): Requires user participation

**Scope (S)**:
- Unchanged (U): Impact limited to vulnerable component
- Changed (C): Impact extends beyond vulnerable component

**Confidentiality Impact (C)**:
- High (H): Total information disclosure
- Low (L): Some information disclosed
- None (N): No confidentiality impact

**Integrity Impact (I)**:
- High (H): Total compromise of data integrity
- Low (L): Some data modification possible
- None (N): No integrity impact

**Availability Impact (A)**:
- High (H): Total shutdown of affected resource
- Low (L): Reduced performance/functionality
- None (N): No availability impact

## Your Task:
Based on your successful reproduction and the CVSS framework above, assign an appropriate severity level to this vulnerability.
Then, call the `submit` tool to submit the finalized vulnerability report.

Then, when that has been finished and you're ready to complete Phase 3, call `finished_phase_3` with:
- **severity**: "Critical", "High", "Medium", or "Low"
- **cvss_score**: Numeric score (0.0-10.0)
- **cvss_vector**: Full CVSS vector string (e.g., "AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H")
- **reasoning**: Detailed explanation of your severity assessment
- **comparison**: How this differs from the originally reported severity (if applicable)

to finalize the phase

Provide a thorough analysis explaining why this vulnerability deserves its assigned severity level."""