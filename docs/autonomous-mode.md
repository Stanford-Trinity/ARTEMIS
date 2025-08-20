# Autonomous Mode Usage Guide

Autonomous mode runs a single Codex instance with an external LLM driver for extended security testing sessions.

## Prerequisites

### Environment Variables
- API key for your chosen model provider (OpenRouter, OpenAI, etc.)
- Set via environment variables or `~/.codex/config.toml`

### Configuration File
Create a YAML file defining your testing objectives:

```yaml
task_description: "Perform comprehensive security testing on the target"
target_info: "Web application security assessment"
scope:
  - "Target: https://example.com/"
objectives:
  - "Enumerate endpoints and functionality"
  - "Test for common vulnerabilities"
constraints:
  - "No destructive testing"
  - "Respect rate limits"
  - "Follow responsible disclosure"
```

## Usage

```bash
codex autonomous [OPTIONS] --config-file <FILE>
```

## Arguments

| Argument | Short | Default | Description |
|----------|-------|---------|-------------|
| `--config-file` | `-f` | Required | Path to task configuration YAML |
| `--duration` | `-d` | 30 | Duration in minutes |
| `--driver-model` | `-m` | `o3` | Model for external LLM driver |
| `--full-auto` | - | false | Skip approvals, use workspace-write sandbox |
| `--resume-dir` | - | - | Resume from existing session directory |
| `--work-start-hour` | - | 0 | Start hour (0-23, Pacific time) |
| `--work-end-hour` | - | 23 | End hour (0-23, Pacific time) |
| `--ignore-work-hours` | - | false | Run continuously, ignore work hours |
| `--logs-dir` | - | - | Custom logs directory |
| `--mode` | - | - | Specialist mode (web, linux-privesc, etc.) |

## Examples

### Basic Usage
```bash
codex autonomous \
  --config-file configs/webapp-test.yaml \
  --duration 60 \
  --driver-model "openai/gpt-4o"
```

### Full Auto Mode
```bash
codex autonomous \
  --config-file configs/pentest.yaml \
  --full-auto \
  --duration 120 \
  --ignore-work-hours
```

### Resume Session
```bash
codex autonomous \
  --resume-dir logs/autonomous_session_1234567890 \
  --duration 30
```

## Session Management

### Logs
Sessions create timestamped directories in `logs/autonomous_session_*` containing:
- `iteration_*.json` - Conversation history
- `context_log.txt` - Context and decisions
- `session_info.json` - Session metadata
- `heartbeat.json` - Current status

### Work Hours
By default, autonomous mode pauses outside work hours (0-23 Pacific). Use `--ignore-work-hours` for continuous operation.

## Configuration Override

Use `-c` to override config values:
```bash
codex autonomous \
  --config-file test.yaml \
  -c model="claude-3-5-sonnet-20241022" \
  -c 'sandbox_permissions=["network-access"]'
```