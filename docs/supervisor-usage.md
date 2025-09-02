# Supervisor Usage Guide

The Supervisor orchestrates multiple Codex instances for comprehensive security testing.

## Prerequisites

### Environment Variables
- `OPENROUTER_API_KEY` - **Required** for supervisor LLM access
- `OPENAI_API_KEY` - **Required** for web search functionality  
- `SUBAGENT_MODEL` - **Required** for spawned Codex instances
- `SUPERVISOR_MODEL` - Optional to override default supervisor model
- `SUMMARIZATION_MODEL` - Optional to override default summarization model
- `ROUTER_MODEL` - Optional to override default router model
- `TODO_GENERATOR_OPENROUTER_MODEL` - Optional to override TODO generator model for OpenRouter
- `TODO_GENERATOR_OPENAI_MODEL` - Optional to override TODO generator model for OpenAI
- `PROMPT_GENERATOR_MODEL` - Optional to override prompt generator model for custom system prompts
- `OPENROUTER_AVAILABLE_MODELS` - Optional comma-separated list of OpenRouter models for switching
- `OPENAI_AVAILABLE_MODELS` - Optional comma-separated list of OpenAI models for switching

**Notes**: 
- If you do not specify an `OPENROUTER_API_KEY` all models will default to OpenAI models, and will be passed into the OpenAI client. This means that you should pass in models in a way the OpenAI client expects (e.g. `openai/gpt-5` if you're using OpenRouter becomes `gpt-5` when using only OpenAI models).
- Current best results come from a starting combination of `anthropic/claude-sonnet-4` for both supervisor and subinstance models. `gpt-5` is also likely to perform well, though we haven't done any large scale runs yet.

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

The supervisor can switch between different models during execution for resilience. You can customize the available models:

- `OPENROUTER_AVAILABLE_MODELS` - Comma-separated list (default: `anthropic/claude-sonnet-4,openai/o3,anthropic/claude-opus-4,google/gemini-2.5-pro,openai/o3-pro`)
- `OPENAI_AVAILABLE_MODELS` - Comma-separated list (default: `o3,gpt-5`)

Example:
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
python -m codex_supervisor.supervisor \
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
cd codex-rs && cargo build --release
```

## Usage

```bash
python -m codex_supervisor.supervisor [OPTIONS]
```

## Arguments

| Argument | Short | Required | Default | Description |
|----------|-------|----------|---------|-------------|
| `--config-file` | `-f` | Yes | - | Path to task configuration YAML |
| `--duration` | `-d` | No | 60 | Duration to run (minutes) |
| `--supervisor-model` | `-m` | No | `openai/o4-mini` | Model for supervisor LLM |
| `--resume-dir` | - | No | - | Resume from existing session directory |
| `--verbose` | `-v` | No | False | Enable verbose logging |
| `--codex-binary` | - | No | `./target/release/codex` | Path to codex binary |
| `--benchmark-mode` | - | No | False | Enable benchmark mode (modular submissions) |
| `--skip-todos` | - | No | False | Skip initial TODO generation step |
| `--use-prompt-generation` | - | No | False | Use LLM to generate custom system prompts instead of routing |

## Modes

**Normal Mode**: Vulnerabilities go through triage process (validation, classification)
**Benchmark Mode**: Uses modular submission system for specialized testing (e.g., CTF challenges, direct submissions)

## Benchmark Mode Configuration

When using `--benchmark-mode`, you must specify submission handlers in your config file:

```yaml
# Example config with CTF submission handler
submissions:
  - type: "ctf"
    config:
      output_file: "ctf_results.json"

# Your other task configuration...
targets:
  - name: "example-target"
    # ... target config
```

Available submission handlers:
- **`ctf`**: For CTF flag submissions, saves to local JSON file
- **`vulnerability`**: For standard vulnerability reports (similar to normal mode)

## Examples

### Normal Mode
```bash
python -m codex_supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --duration 120 \
  --verbose
```

### Benchmark Mode (CTF)
```bash
python -m codex_supervisor.supervisor \
  --config-file ../configs/tests/ctf_easy.yaml \
  --benchmark-mode \
  --duration 60
```

### Custom System Prompt Generation
```bash
python -m codex_supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --use-prompt-generation \
  --duration 120 \
  --verbose
```

### Custom Prompts with Different Model
```bash
export PROMPT_GENERATOR_MODEL="anthropic/claude-sonnet-4"
python -m codex_supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --use-prompt-generation \
  --duration 90
```

### Skip Initial TODO Generation
```bash
python -m codex_supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --skip-todos \
  --duration 90
```
