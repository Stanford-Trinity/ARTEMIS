# Supervisor Usage Guide

The Supervisor orchestrates multiple Codex instances for comprehensive security testing.

## Prerequisites

### Environment Variables
- `OPENROUTER_API_KEY` - **Required** for supervisor LLM access (or use `OPENAI_API_KEY`)
- `OPENAI_API_KEY` - **Required** for web search functionality via `web_search` tool. Can also be used as primary API key instead of OpenRouter.
- `SUBAGENT_MODEL` - **Required** for spawned Codex instances
- `SUPERVISOR_MODEL` - Optional to override default supervisor model
- `SUMMARIZATION_MODEL` - Optional to override default summarization model
- `ROUTER_MODEL` - Optional to override default router model
- `TODO_GENERATOR_OPENROUTER_MODEL` - Optional to override TODO generator model for OpenRouter
- `TODO_GENERATOR_OPENAI_MODEL` - Optional to override TODO generator model for OpenAI
- `PROMPT_GENERATOR_MODEL` - Optional to override prompt generator model for custom system prompts
- `OPENROUTER_AVAILABLE_MODELS` - Optional comma-separated list of OpenRouter models for switching
- `OPENAI_AVAILABLE_MODELS` - Optional comma-separated list of OpenAI models for switching

### Codex with OpenRouter
To use Codex with Openrouter, create a file called `~/.codex/config.toml` with the following:
```bash
model_provider = "openrouter"

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
```

### Important: Model ID Format Requirements

**The model ID format you use MUST match your API key configuration:**

- **If `OPENROUTER_API_KEY` is set**: Use OpenRouter format for all models
  - Example: `anthropic/claude-sonnet-4`, `openai/gpt-5`, `google/gemini-2.5-pro`
  - This applies to: `SUBAGENT_MODEL`, `SUPERVISOR_MODEL`, `ROUTER_MODEL`, `SUMMARIZATION_MODEL`, `PROMPT_GENERATOR_MODEL`, and all models in `OPENROUTER_AVAILABLE_MODELS`

- **If `OPENROUTER_API_KEY` is NOT set** (using OpenAI only): Use OpenAI format for all models
  - Example: `gpt-5`, `o3`, `o4-mini` (without provider prefix)
  - This applies to all model environment variables
  - All model IDs are passed directly to the OpenAI client

**Mixing formats will cause errors.** The supervisor determines which client to use based on whether `OPENROUTER_API_KEY` is configured, so all model IDs must match that format.

**Performance Notes**:
- Current best results come from a starting combination of `anthropic/claude-sonnet-4` for both supervisor and subinstance models (when using OpenRouter), and using continuation with other strong models.
- `gpt-5` is a reasonable budget option, though significantly worse than Sonnet 4.

### Model Configuration

The supervisor uses different models for different components:

| Component | Environment Variable | OpenRouter Default | OpenAI Default | Description |
|-----------|---------------------|-------------------|---------------|-------------|
| Supervisor | `SUPERVISOR_MODEL` | `openai/o4-mini` | `o4-mini` | Main supervisor orchestration |
| Summarization | `SUMMARIZATION_MODEL` | `openai/o4-mini` | `o4-mini` | Context summarization |
| Router | `ROUTER_MODEL` | `openai/o4-mini` | `o4-mini` | Task routing decisions |
| TODO Generator (OpenRouter) | `TODO_GENERATOR_OPENROUTER_MODEL` | `anthropic/claude-opus-4.1` | N/A | TODO generation via OpenRouter |
| TODO Generator (OpenAI) | `TODO_GENERATOR_OPENAI_MODEL` | N/A | `gpt-5` | TODO generation via OpenAI |

#### Model Switching

The supervisor automatically switches to a different model during **continuation** (when the session calls `finished()` but time remains). This provides resilience against model-specific issues and allows longer sessions.

**How it works:**
1. When continuing, the supervisor randomly selects a different model from the available pool
2. The current model is excluded from selection to ensure a switch occurs
3. Conversation history is summarized and reset with the new model
4. Model switching only occurs during continuation, not mid-session

**Configuration:**
- `OPENROUTER_AVAILABLE_MODELS` - Comma-separated list (default: `anthropic/claude-sonnet-4,openai/o3,anthropic/claude-opus-4,google/gemini-2.5-pro,openai/o3-pro`)
- `OPENAI_AVAILABLE_MODELS` - Comma-separated list (default: `o3,gpt-5`)

**Example:**
```bash
export OPENROUTER_AVAILABLE_MODELS="anthropic/claude-sonnet-4,openai/o3,google/gemini-2.5-pro"
export TODO_GENERATOR_OPENROUTER_MODEL="anthropic/claude-sonnet-4"
```

### System Prompt Modes

The supervisor supports two modes for determining system prompts for codex instances:

#### A) Router Mode (Default)
Uses an LLM router to select from predefined specialist system prompts:
- `generalist` - General-purpose cybersecurity testing
- `web` - Web application vulnerability testing  
- `enumeration` - Network and service enumeration
- `linux-privesc` - Linux privilege escalation
- `windows-privesc` - Windows privilege escalation
- `active-directory` - Active Directory testing
- `web-enumeration` - Web service enumeration
- `client-side-web` - Client-side web vulnerabilities
- `shelling` - Shell access and exploitation

#### B) Custom Prompt Generation Mode
Uses an LLM to generate task-specific system prompts tailored to each individual task. Enable with `--use-prompt-generation`.

**How it works:**
1. When spawning a codex instance, the supervisor sends the task description to an LLM
2. The LLM generates a detailed, task-specific system prompt
3. This custom prompt is written to a `.md` file in the workspace  
4. The codex binary loads this custom prompt instead of built-in specialists
5. If generation fails, automatically falls back to router mode

**Configuration:**
- `PROMPT_GENERATOR_MODEL` - Model for generating custom prompts (default: `anthropic/claude-opus-4.1`)

**Example usage:**
```bash
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --use-prompt-generation \
  --duration 120
```

### Setup
```bash
# Create .env file
echo "OPENROUTER_API_KEY=your-openrouter-key" > .env
echo "OPENAI_API_KEY=your-openai-key" >> .env
echo "SUBAGENT_MODEL=anthropic/claude-sonnet-4" >> .env

# Build codex binary (if needed)
cargo build --release --manifest-path codex-rs/Cargo.toml
```

## Usage

```bash
python -m supervisor.supervisor [OPTIONS]
```

## Arguments

| Argument | Short | Required | Default | Description |
|----------|-------|----------|---------|-------------|
| `--config-file` | `-f` | Yes | - | Path to task configuration YAML |
| `--duration` | `-d` | No | 60 | Duration to run (minutes, pauses during non-working hours) |
| `--supervisor-model` | `-m` | No | `openai/o4-mini` | Model for supervisor LLM |
| `--resume-dir` | - | No | - | Resume from existing session directory |
| `--verbose` | `-v` | No | False | Enable verbose logging (DEBUG level) |
| `--codex-binary` | - | No | `./codex-rs/target/release/codex` | Path to codex binary |
| `--benchmark-mode` | - | No | False | Enable benchmark mode (modular submissions) |
| `--skip-todos` | - | No | False | Skip initial TODO generation step |
| `--use-prompt-generation` | - | No | False | Use LLM to generate custom system prompts instead of routing |
| `--finish-on-submit` | - | No | False | Finish session when a vulnerability is submitted (instead of continuing until duration expires) |
| `--working-hours-start` | - | No | 9 | Working hours start time (24-hour format, e.g., 9 for 9 AM) |
| `--working-hours-end` | - | No | 17 | Working hours end time (24-hour format, e.g., 17 for 5 PM) |
| `--working-hours-timezone` | - | No | `US/Pacific` | Timezone for working hours (e.g., `US/Pacific`, `UTC`, `US/Eastern`) |

## Modes

**Normal Mode**: Vulnerabilities go through triage process (validation, classification)
**Benchmark Mode**: Uses modular submission system for specialized testing (e.g., CTF challenges, direct submissions)
**Finish-on-Submit Mode**: Session ends early when a vulnerability is submitted (use with `--finish-on-submit`)

## Benchmark Mode Configuration

When using `--benchmark-mode`, you must specify submission handlers in your config file. The submission system uses a **modular registry pattern** that allows custom handlers to be added.

```yaml
# Example config with CTF submission handler
submission_config:
  type: "ctf"
  config:
    output_file: "ctf_results.json"

# Your other task configuration...
targets:
  - name: "example-target"
    # ... target config
```

**Available submission handlers:**
- **`ctf`**: For CTF flag submissions, saves to local JSON file
- **`vulnerability`**: For standard vulnerability reports (similar to normal mode)

**Extending the submission system:**
The submission system is designed to be extensible. New handlers can be created by:
1. Subclassing `BaseSubmissionHandler` from `supervisor/submissions/base.py`
2. Implementing required methods: `submit()` and `get_submission_schema()`
3. Registering the handler in `supervisor/tools.py` via the registry

See existing handlers in `supervisor/submissions/` for examples.

## Examples

### Normal Mode
```bash
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --duration 120 \
  --verbose
```

### Benchmark Mode (CTF)
```bash
python -m supervisor.supervisor \
  --config-file ../configs/tests/ctf_easy.yaml \
  --benchmark-mode \
  --duration 60
```

### Custom System Prompt Generation
```bash
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --use-prompt-generation \
  --duration 120 \
  --verbose
```

### Custom Prompts with Different Model
```bash
export PROMPT_GENERATOR_MODEL="anthropic/claude-sonnet-4"
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --use-prompt-generation \
  --duration 90
```

### Skip Initial TODO Generation
```bash
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --skip-todos \
  --duration 90
```

### Finish on Submit Mode
```bash
# Run for up to 120 minutes, but end early if a vulnerability is submitted
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --finish-on-submit \
  --duration 120
```

### Finish on Submit with Benchmark Mode
```bash
# End session immediately after CTF flag submission
python -m supervisor.supervisor \
  --config-file ../configs/tests/ctf_easy.yaml \
  --benchmark-mode \
  --finish-on-submit \
  --duration 60
```

### Working Hours Configuration
```bash
# Run supervisor only during business hours in Eastern timezone
python -m supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --duration 480 \
  --working-hours-start 9 \
  --working-hours-end 17 \
  --working-hours-timezone "US/Eastern"
```

## Advanced Features

### Working Hours

The supervisor supports **automatic sleep during non-working hours**, ensuring operations only occur during specified business hours. The session duration automatically adjusts to account for sleep time.

**Features:**
- Automatically pauses supervisor when outside working hours
- Session duration extended by sleep time (e.g., 60 minute duration = 60 minutes of actual work)
- Timezone-aware scheduling
- Working hours status shown in heartbeat file and logs
- Configurable via command-line arguments

**Configuration:**
- `--working-hours-start` - Start hour in 24-hour format (default: 9 for 9 AM)
- `--working-hours-end` - End hour in 24-hour format (default: 17 for 5 PM)
- `--working-hours-timezone` - Timezone string (default: `US/Pacific`)

**Example behavior:**
- Start session at 4:00 PM with 120 minute duration
- Working hours: 9 AM - 5 PM
- Supervisor runs for 60 minutes (4:00 PM - 5:00 PM)
- Sleeps from 5:00 PM until 9:00 AM next day
- Resumes at 9:00 AM and runs remaining 60 minutes

**Implementation:** `supervisor/working_hours.py`

### Context Management & Auto-Summarization

The supervisor automatically manages conversation context to handle long sessions that exceed model context limits.

**Features:**
- **Automatic summarization** when approaching 200,000 token limit (triggers at 185,000)
- **Token counting** using `tiktoken` (o200k_base encoding)
- **Smart preservation**: Keeps system message, initial user message, and most recent 20 messages
- **Orphaned tool message validation**: Automatically removes tool messages without corresponding tool calls
- **Transparent operation**: Summarization happens automatically without user intervention

**How it works:**
1. Supervisor monitors token count after each turn
2. When threshold reached (185k tokens), creates summary of middle conversation
3. Preserves system prompt, initial context, and recent 20 messages
4. Inserts summary as user message between initial context and recent messages
5. Continues with reduced token count

**Token limits:**
- Max context: 200,000 tokens
- Trigger threshold: 185,000 tokens (15,000 token buffer)
- Summarization model: Configurable via `SUMMARIZATION_MODEL` (default: `o4-mini`)

**Implementation:** `supervisor/context_manager.py`

### Continuation System

The continuation system allows supervisor sessions to run beyond a single model's context or decision limit by automatically restarting with fresh context.

**Features:**
- **Automatic triggering**: Activates when supervisor calls `finished()` but 5+ minutes remain
- **Model switching**: Randomly selects different model for fresh perspective
- **Context reset**: Conversation summarized and reset with continuation prompt
- **Vulnerability tracking**: Loads all submitted vulnerabilities for context
- **Time tracking**: Continuation inherits remaining time from original session

**How it works:**
1. Supervisor calls `finished()` tool
2. System checks remaining time
3. If ≥5 minutes remain, initiation continuation:
   - Summarize conversation history (up to 185k tokens)
   - Switch to random different model from available pool
   - Reset conversation with:
     - System prompt
     - Original task context
     - Conversation summary
     - Submitted vulnerabilities log
     - Remaining time
4. Supervisor continues with fresh context

**Continuation prompt includes:**
- Original task configuration
- Summary of work completed
- All vulnerabilities found and submitted
- Time remaining in session

**Implementation:** `supervisor/orchestration/orchestrator.py:214-358`

### Supervisor Tools

The supervisor has access to a comprehensive toolkit for managing instances, tracking progress, and maintaining context.

#### Instance Management Tools

**`spawn_codex`** - Spawn a new codex instance
```json
{
  "instance_id": "web-enum-001",
  "task_description": "Enumerate web services on 192.168.1.100",
  "duration_minutes": 60
}
```

**`terminate_instance`** - Terminate a running instance
```json
{
  "instance_id": "web-enum-001"
}
```

**`send_followup`** - Send followup message to instance
```json
{
  "instance_id": "web-enum-001",
  "message": "Focus on the /admin endpoint you discovered"
}
```

**`list_instances`** - Get status of all active instances
```json
{}
```

**`read_instance_logs`** - Read conversation logs from instance
```json
{
  "instance_id": "web-enum-001",
  "format": "readable",  // or "openai_json"
  "tail_lines": 50,  // optional
  "max_tokens": 10000  // optional, smart truncation
}
```

**`wait_for_instance`** - Block until instance needs input or completes
```json
{
  "instance_id": "web-enum-001",
  "timeout_minutes": 5
}
```

#### Progress Tracking Tools

**`write_supervisor_note`** - Write persistent note
```json
{
  "content": "Found interesting SQLi vector in login form"
}
```

**`read_supervisor_notes`** - Read all notes from session
```json
{}
```

**`update_supervisor_todo`** - Manage hierarchical TODOs
```json
{
  "action": "add",  // or "update", "remove", "complete", "add_subtask"
  "item_id": "recon-001",  // required for update/remove/complete
  "parent_id": "recon-001",  // required for add_subtask
  "description": "Port scan 192.168.1.0/24",
  "priority": "high",  // high, medium, low
  "notes": "Focus on web services"
}
```

**`read_supervisor_todo`** - Read TODO list with filtering
```json
{
  "filter_status": "pending",  // pending, completed, all
  "filter_priority": "high",  // optional
  "item_id": "recon-001",  // optional, shows subtasks
  "depth": 2  // how many subtask levels to show
}
```

#### History & Search Tools

**`read_supervisor_conversation`** - Read conversation history
```json
{
  "tail_lines": 100,  // optional
  "from_iteration": 5,  // optional
  "to_iteration": 10  // optional
}
```

**`search_supervisor_history`** - Regex search through history
```json
{
  "query": "SQL.*injection",  // regex pattern
  "context_lines": 3,  // lines before/after match
  "case_sensitive": false
}
```

**`web_search`** - Search the web for information
```json
{
  "query": "CVE-2024-1234 exploit"
}
```
*Note: Requires `OPENAI_API_KEY` and uses OpenAI's Responses API*

#### Session Control Tools

**`submit`** - Submit vulnerability report (normal mode only)
```json
{
  "title": "SQL Injection in login form",
  "asset": "192.168.1.100:443",
  "vuln_type": "SQL Injection",
  "severity": "High",
  "description": "Detailed description...",
  "repro_steps": "1. Navigate to...",
  "impact": "Database access possible",
  "cleanup": "No cleanup needed"
}
```

**`finished`** - Mark session as complete
```json
{
  "finish_reason": "All targets tested, 3 vulnerabilities found"
}
```

### Hierarchical TODO System

The supervisor maintains a powerful hierarchical TODO system for tracking progress across complex penetration tests.

**Features:**
- **Recursive subtasks**: Unlimited nesting depth
- **Priority levels**: High, medium, low
- **Status tracking**: Pending, completed (with timestamps)
- **Filtering**: By status, priority, parent item, and depth
- **Progress tracking**: Automatic subtask count and completion percentage
- **Persistence**: Stored in `supervisor_todo.json`

**TODO Structure:**
```json
{
  "id": "recon-001",
  "description": "Initial network reconnaissance",
  "priority": "high",
  "status": "pending",
  "notes": "Focus on web services first",
  "created_at": "2025-01-15T10:00:00Z",
  "updated_at": "2025-01-15T10:00:00Z",
  "completed_at": "2025-01-15T12:00:00Z",  // when status is completed
  "subtasks": [
    {
      "id": "recon-001-a",
      "description": "Port scan 192.168.1.0/24",
      "priority": "high",
      "status": "completed",
      "subtasks": []
    }
  ]
}
```

**Initial TODO Generation:**
On first run (unless `--skip-todos` specified), the supervisor uses an LLM to generate initial TODOs from the configuration file:
- Analyzes task scope and targets
- Creates hierarchical breakdown of testing approach
- Generates actionable items with priorities
- Stored in `logs/supervisor_session_*/supervisor_todo.json`

**TODO Operations:**
- `add` - Create new top-level TODO
- `add_subtask` - Create subtask under parent
- `update` - Modify description, priority, or notes
- `complete` - Mark as completed with timestamp
- `remove` - Delete TODO item

**Implementation:** `supervisor/tools.py:625-916`

### Session Persistence & Resume

The supervisor maintains comprehensive session state that can be resumed after interruption.

**Session Files:**
- `supervisor_session_<timestamp>/` - Session directory
  - `supervisor.log` - Full supervisor logs
  - `supervisor_todo.json` - Hierarchical TODO list
  - `supervisor_heartbeat.json` - Current status (PID, iteration, active instances)
  - `session_metadata.json` - Configuration and statistics
  - `supervisor_iteration_NNN.json` - Conversation snapshot per iteration
  - `supervisor_notes/` - Persistent notes directory
  - `triage_instances/` - Individual triage workspaces
  - `<instance_id>/` - Codex instance workspaces

**Resuming Sessions:**
```bash
python -m supervisor.supervisor \
  --resume-dir ./logs/supervisor_session_1736950800 \
  --duration 60
```

**Resume behavior:**
- Loads existing conversation history
- Restores TODO list
- Continues from last iteration number
- Reconnects to any still-running instances
- Maintains original configuration

**Heartbeat File:**
Updated every iteration with:
- Supervisor PID
- Current iteration number
- Active instance count
- Working hours status
- Last update timestamp

**Implementation:** `supervisor/orchestration/orchestrator.py:442-580`

### Configuration File Structure

The supervisor configuration file uses YAML format with the following structure:

```yaml
# Task identification
name: "Web Application Security Assessment"
description: "Comprehensive security testing of web application"

# Target specifications
targets:
  - name: "main-web-app"
    url: "https://example.com"
    scope:
      - "*.example.com"
      - "192.168.1.0/24"

# Files referenced in configuration (relative paths resolved from config file directory)
filepath: "./additional_context.txt"  # Optional

# Submission configuration (for benchmark mode)
submission_config:
  type: "ctf"  # or "vulnerability"
  config:
    output_file: "results.json"

# Additional context
scope: "All subdomains and IP ranges listed"
rules_of_engagement: "No DoS, no social engineering"
testing_credentials:
  username: "test_user"
  password: "test_password"
```

**Path Resolution:**
- Relative paths (e.g., `./file.txt`) are resolved relative to the config file location
- Absolute paths are used as-is
- Applies to `filepath` and any other file references

**Required Fields:**
- `name` - Task identifier
- At minimum one of: `targets`, `description`, or custom task-specific fields

**Optional Fields:**
- `submission_config` - Required for benchmark mode
- `filepath` - Additional context file
- Any custom fields needed for your specific use case

**Example Configurations:**
See `configs/stanford/` directory for real-world examples.

### Instance Status Types

Codex instances tracked by the supervisor can have the following statuses:

| Status | Description | Next Actions |
|--------|-------------|--------------|
| `running` | Instance is actively working | Wait or read logs to monitor progress |
| `waiting_for_followup` | Instance needs supervisor input | Use `send_followup` to continue or `terminate_instance` to end |
| `completed` | Instance finished successfully | Use `read_instance_logs` to review, spawn new instances if needed |
| `failed` | Instance encountered error | Use `read_instance_logs` to diagnose, potentially respawn with fixes |
| `timeout` | Instance exceeded duration limit | Review logs, decide if task needs more time or different approach |
| `error` | Instance terminated abnormally | Check logs for errors, may need configuration fixes |

**Status Transitions:**
```
[spawn] → running → {completed, failed, timeout, waiting_for_followup}
                 ↓
waiting_for_followup → [send_followup] → running
                    ↓
                    [terminate] → completed
```

**Checking Status:**
Use `list_instances` tool to see current status of all instances.

### Logging & Debugging

The supervisor provides comprehensive logging for debugging and monitoring.

**Log Levels:**
- **INFO** (default): High-level events, tool calls, instance status changes
- **DEBUG** (`--verbose` flag): Detailed execution, API calls, token counts

**Log Files:**
- `logs/supervisor_session_*/supervisor.log` - Main supervisor log
- `logs/supervisor_session_*/triage_instances/triager_*/triage_conversation.log` - Triage logs
- `logs/supervisor_session_*/<instance_id>/codex.log` - Individual instance logs

**Log Configuration:**
```python
# Set in supervisor.py:19-35
logging.basicConfig(
    level=logging.DEBUG if verbose else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(session_dir / "supervisor.log"),
        logging.StreamHandler()  # Also prints to console
    ]
)
```

**Useful Log Patterns:**
- `🔧 Supervisor calling tool:` - Tool execution
- `🚨 CRITICAL ERROR:` - Fatal errors requiring attention
- `🔄 Supervisor iteration` - Start of each iteration
- `😴 Outside working hours` - Working hours sleep
- `🔍 Starting triage process` - Triage initiated
- `✅ Spawned instance` - New instance created

**Debugging Tips:**
1. Use `--verbose` for detailed API interaction logs
2. Check `supervisor_heartbeat.json` for current state
3. Review iteration JSON files for conversation history
4. Use `search_supervisor_history` tool within session to find specific events
5. Instance logs contain full codex execution details

**Critical Errors:**
The supervisor exits immediately on:
- Tool calls returning `None` (indicates internal error)
- Tool calls throwing uncaught exceptions
- API client initialization failures
