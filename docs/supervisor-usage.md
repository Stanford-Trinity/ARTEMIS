docs/supervisor-usage.md # Supervisor Usage Guide

The Supervisor orchestrates multiple Codex instances for comprehensive security testing.

## Prerequisites

### Environment Variables
- `OPENROUTER_API_KEY` - **Required** for supervisor LLM access
- `OPENAI_API_KEY` - **Required** for web search functionality  
- `SUBAGENT_MODEL` - **Required** for spawned Codex instances
- `SUPERVISOR_MODEL` - Optional, defaults to "o3"
- `SUMMARIZATION_MODEL` - Optional, for context summarization

### Setup
```bash
# Create .env file
echo "OPENROUTER_API_KEY=your-openrouter-key" > .env
echo "OPENAI_API_KEY=your-openai-key" >> .env
echo "SUBAGENT_MODEL=openai/gpt-4o" >> .env

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
| `--benchmark-mode` | - | No | False | Skip triage, send directly to Slack |

## Modes

**Normal Mode**: Vulnerabilities go through triage process (validation, classification)
**Benchmark Mode**: Skip triage, send findings directly to Slack webhook

## Example

```bash
python -m codex_supervisor.supervisor \
  --config-file configs/level1.yaml \
  --duration 120 \
  --verbose
```