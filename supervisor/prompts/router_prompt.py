#!/usr/bin/env python3

def get_router_prompt(task_description: str, specialists: list) -> str:
    """Generate routing prompt for selecting specialist instance."""
    
    # Detailed specialist descriptions for routing decisions

    specialist_descriptions = {
        "active-directory": "Active Directory enumeration, LDAP queries, Kerberos attacks, domain mapping, group memberships, SPNs, unconstrained delegation, signing policies",
        "client-side-web": "Client-side web vulnerabilities (XSS, CSRF, XS-Leak, CSS injection), browser-based attacks, DOM manipulation, credential theft via client-side vectors",
        "enumeration": "General network and service enumeration, port scanning, service discovery, OS detection, network mapping, vulnerability scanning with nmap/masscan",
        "linux-privesc": "Linux privilege escalation, sudo misconfigurations, SUID/SGID binaries, kernel exploits, container escapes, cronjobs, file permissions",
        "shelling": "Payload generation, reverse shells, shell stabilization, TTY upgrades, payload delivery methods, shell pivoting, connection reliability",
        "web-enumeration": "Web application enumeration, directory/file brute-forcing, API endpoint discovery, web crawling, sensitive endpoint identification",
        "web": "Server-side web vulnerabilities (SQL injection, NoSQL injection, IDOR, file upload, LFI/RFI, command injection, server-side template injection)",
        "windows-privesc": "Windows privilege escalation, Potato attacks, service misconfigurations, scheduled tasks, LSASS dumping, DLL hijacking, Windows exploits"
    }
    
    specialist_list = []
    for spec in specialists:
        description = specialist_descriptions.get(spec, "General specialist")
        specialist_list.append(f"- **{spec}**: {description}")
    
    specialist_text = "\n".join(specialist_list)
    
    return f"""You are a task router that determines which specialist should handle a given task.

Available specialists:
{specialist_text}

Task to route: {task_description}

Analyze the task description and select the most appropriate specialist based on:

**Selection Guidelines:**
- **active-directory**: Tasks involving AD domains, LDAP, Kerberos, domain controllers, domain users/groups
- **client-side-web**: Tasks focused on XSS, CSRF, DOM-based attacks, browser exploitation, client-side vulnerabilities
- **enumeration**: General reconnaissance, port scanning, service discovery, initial network mapping
- **linux-privesc**: Tasks requiring privilege escalation on Linux systems, already having basic access
- **shelling**: Tasks focused on gaining shell access, payload delivery, reverse shells, shell stabilization
- **web-enumeration**: Discovering web endpoints, directories, APIs, web application reconnaissance
- **web**: Server-side web application testing, SQL injection, file uploads, server-side vulnerabilities
- **windows-privesc**: Tasks requiring privilege escalation on Windows systems, already having basic access

**Key Decision Factors:**
1. **Primary technology/platform** mentioned (Windows, Linux, AD, web apps)
2. **Phase of attack** (reconnaissance, exploitation, post-exploitation)
3. **Specific vulnerability types** mentioned
4. **Access level assumed** (external, user-level, admin-level)

Return a JSON response with exactly this format:
{{"specialist": "specialist-name"}}

The specialist name must be exactly one of: {', '.join(specialists)}"""