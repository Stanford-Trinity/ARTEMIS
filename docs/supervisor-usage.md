# Supervisor Usage Guide

The Supervisor orchestrates multiple Codex instances for comprehensive security testing.

## Prerequisites

### Environment Variables
- `OPENROUTER_API_KEY` - **Required** for supervisor LLM access
- `OPENAI_API_KEY` - **Required** for web search functionality  
- `SUBAGENT_MODEL` - **Required** for spawned Codex instances
- `SUMMARIZATION_MODEL` - Optional to override default

**Notes**: 
- If you do not specify an `OPENROUTER_API_KEY` all models will default to OpenAI models, and will be passed into the OpenAI client. This means that you should pass in models in a way the OpenAI client expects (e.g. `openai/gpt-5` if you're using OpenRouter becomes `gpt-5` when using only OpenAI models).
- Current best results come from a starting combination of `anthropic/claude-sonnet-4` for both supervisor and subinstance models. `gpt-5` is also likely to perform well, though we haven't done any large scale runs yet.

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

### Skip Initial TODO Generation
```bash
python -m codex_supervisor.supervisor \
  --config-file ../configs/stanford/level1.yaml \
  --skip-todos \
  --duration 90
```
