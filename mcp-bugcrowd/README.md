# MCP Bugcrowd Server

An MCP (Model Context Protocol) server that provides Bugcrowd submission functionality to codex agents.

## Features

- Submit vulnerability reports to Bugcrowd via their API
- Automatically appends "(Codex agent)" to submission titles
- Comprehensive error handling and validation
- Support for all Bugcrowd submission fields

## Setup

1. Install dependencies:
```bash
npm install
```

2. Build the server:
```bash
npm run build
```

3. Set up environment variables:
```bash
# Copy the example file and edit it
cp .env.example .env
# Edit .env and add your actual Bugcrowd API token
```

Alternatively, you can export the environment variable:
```bash
export BUGCROWD_API_TOKEN="your_bugcrowd_api_token"
```

## Usage

The server exposes a single tool: `bugcrowd_submit`

### Parameters

- `title` (required): Title of the vulnerability report
- `description` (required): Detailed description of the vulnerability
- `program_id` (required): Bugcrowd program ID to submit to
- `target_id` (optional): Target ID within the program
- `engagement_id` (optional): Engagement ID within the program
- `severity` (optional): Severity level (1-5, where 5 is highest)
- `bug_url` (optional): URL where the vulnerability was found
- `extra_info` (optional): Additional information about the vulnerability
- `http_request` (optional): HTTP request details demonstrating the vulnerability
- `researcher_email` (optional): Email of the researcher submitting the report
- `state` (optional): State of the submission (default: "new")
- `cvss_string` (optional): CVSS string for the vulnerability
- `vrt_id` (optional): Vulnerability Rating Taxonomy ID
- `custom_fields` (optional): Custom fields as key-value pairs

### Example Response

```json
{
  "success": true,
  "submission_id": "12345",
  "status": "new",
  "title": "SQL Injection in login form (Codex agent)",
  "message": "Vulnerability report submitted successfully to Bugcrowd"
}
```

## Integration with Codex

Add to your codex configuration:

```toml
[mcp_servers.bugcrowd]
command = "node"
args = ["/path/to/mcp-bugcrowd/dist/index.js"]
# No env needed - reads from .env file or system environment

# Optional: Enable network access for API calls
[sandbox]
mode = "workspace-write"
network_access = true
```