📧 Vulnerability Disclosure Email Template (Fill-In for Agent)

Instruction:
Given the vulnerability context that follows this prompt, draft a professional, coordinated disclosure email to the vendor’s security contact. Ensure clarity, technical accuracy, and responsible disclosure tone. Redact sensitive internal details (e.g., IPs, client identifiers).

⸻

Email Fields to Fill

1. Subject Line
Security Vulnerability Disclosure: [Product] [Short Summary of Issue]

⸻

2. Greeting
	•	Preferred: “Dear [Vendor Security Team / Product Security Team / [Contact Name]],”

⸻

3. Introduction
State who is reporting and under what context:
	•	Reporter: Project Trinity (Agent)
	•	Contact: trinity-research@lists.stanford.edu
	•	Purpose: Reporting under responsible disclosure guidelines.

⸻

4. Vulnerability Summary
	•	Product: [fill from context]
	•	Version(s): [fill from context]
	•	Vulnerability Type: [e.g., Incorrect Access Control, Insecure Permissions]
	•	Attack Type: [Remote / Local / Physical / Context-dependent]
	•	Impact: [Code Execution, Information Disclosure, etc.]
	•	Severity Estimate: [Low / Medium / High / Critical; optional CVSS if known]

⸻

5. Technical Details
Provide a concise description with enough info for vendor reproduction:
	•	Affected Components: [e.g., SMB share, JSON-RPC API]
	•	Attack Vector: [step-by-step exploitation path]
	•	Root Cause: [default creds, insecure configuration, etc.]
	•	Result: [RCE as root, data disclosure, etc.]

⸻

6. Proof of Concept (Optional)
[Summarize reproduction steps; include commands or HTTP requests if safe to share.]

⸻

7. Suggested Mitigations
[Practical fixes the vendor could implement, e.g., disable guest SMB write, enforce credential change, restrict API access.]

⸻

8. Coordination & Disclosure Timeline
“We are following responsible disclosure practices. We would like to coordinate with you on remediation. Our proposed timeline is:
	•	Initial report: [Date]
	•	Vendor acknowledgement requested: [~7 days]
	•	Patch development window: [e.g., 30–90 days]
	•	Public disclosure: [Date or ‘upon vendor patch release’]

If you have a preferred timeline, please let us know.”

⸻

9. Closing
Polite, cooperative close. Example:

“We look forward to working with you on addressing this issue. Please confirm receipt of this report and let us know if you require additional information.”

Signature:
Project Trinity (Agent)
trinity-research@lists.stanford.edu
