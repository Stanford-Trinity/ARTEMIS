# Trinity Agent Benchmark Runner

This tool automates running the Trinity Agent with XBOW Validation Benchmarks in CTF mode.

## Overview

The benchmark runner provides a complete automation pipeline that:

1. **Automatically builds Docker containers** from the validation-benchmarks repository
2. **Integrates the Trinity supervisor agent** in CTF mode with full model/API configuration
3. **Provides a custom submit function** for benchmark validation
4. **Grades results** at the end of each run

Example usage:

```bash
# Setup environment (first time)
./setup_benchmark_env.sh

# Check environment
./benchmark_runner.py --check-env

# List available benchmarks
./benchmark_runner.py --list-benchmarks

# Run a basic benchmark
./benchmark_runner.py XBEN-001-24 --duration 30

# Run with specific model
./benchmark_runner.py XBEN-002-24 --supervisor-model "openai/gpt-4" --verbose

# Run with custom configuration
./benchmark_runner.py XBEN-003-24 \
    --supervisor-model "anthropic/claude-3-sonnet" \
    --duration 45 \
    --use-prompt-generation \
    --verbose
```

## Prerequisites

- Python 3.8+
- Docker and Docker Compose
- OpenSSL (for flag generation)
- Trinity Agent supervisor dependencies

## Quick Start

### List Available Benchmarks

```bash
./benchmark_runner.py --list-benchmarks
```

### Run a Benchmark

```bash
./benchmark_runner.py XBEN-001-24 --duration 30
```

### Advanced Usage

```bash
./benchmark_runner.py XBEN-003-24 \
    --supervisor-dir ./supervisor \
    --validation-benchmarks-dir ../validation-benchmarks \
    --duration 45
```

## Command Line Options

- `benchmark_id`: The benchmark to run (e.g., XBEN-001-24)
- `--supervisor-dir`: Path to the Trinity supervisor directory (default: ./supervisor)
- `--validation-benchmarks-dir`: Path to validation-benchmarks directory (default: ../validation-benchmarks)
- `--duration`: Duration to run in minutes (default: 30)
- `--list-benchmarks`: List all available benchmarks

## How It Works

### 1. Docker Container Building

The runner automatically:
- Uses the validation-benchmarks Makefile system
- Generates appropriate flags for each benchmark
- Builds all required Docker images
- Starts containers with proper networking

### 2. Trinity Supervisor Integration

The supervisor is configured for CTF mode with:
- **Benchmark mode enabled**: Skips triage, goes directly to exploitation
- **Finish on submit**: Stops when a valid flag is found
- **Custom configuration**: Tailored to each benchmark's requirements
- **Proper target configuration**: Automatically detects exposed ports

### 3. Submit Function

A custom submit function validates flags:
- Checks flag format (FLAG{64-char-hex})
- Validates against expected benchmark flag
- Provides detailed feedback
- Tracks submission attempts

### 4. Grading System

The grading system:
- Parses all submission attempts
- Analyzes supervisor logs
- Calculates success rates and scores
- Generates comprehensive reports

## Directory Structure

When you run a benchmark, the following structure is created:

```
./logs/benchmark_session_{benchmark_id}_{timestamp}/
├── supervisor_config.yaml          # Generated supervisor configuration
├── benchmark_submit.py             # Custom submit function
├── supervisor.log                  # Trinity supervisor logs
├── grading_results.json           # Final grading results
└── *submission*.json              # Individual submission records
```

## Configuration Details

### Supervisor Configuration

The generated supervisor config includes:

```yaml
name: "CTF Challenge: {benchmark_name}"
description: "{benchmark_description}"
level: "{benchmark_level}"
win_condition: "flag"
mode: "ctf"
target_url: "http://localhost:{port}"
target_ports: [80, 8080, ...]
benchmark_id: "{benchmark_id}"
submit_function: "benchmark_submit"
grading_enabled: true
```

### Submit Function Features

- **Format validation**: Ensures flags match `FLAG{[a-f0-9]{64}}`
- **Benchmark validation**: Compares against expected flag for the benchmark
- **Detailed responses**: Provides specific feedback for debugging
- **Scoring**: Awards points based on correctness

## Example Output

```bash
$ ./benchmark_runner.py XBEN-001-24 --duration 30

2024-09-20 14:30:00 - INFO - Building docker containers for XBEN-001-24...
2024-09-20 14:30:15 - INFO - Docker containers built successfully
2024-09-20 14:30:16 - INFO - Starting benchmark containers for XBEN-001-24...
2024-09-20 14:30:20 - INFO - Benchmark containers started successfully
2024-09-20 14:30:21 - INFO - Creating supervisor configuration for CTF mode...
2024-09-20 14:30:21 - INFO - Supervisor configuration created: ./logs/benchmark_session_XBEN-001-24_1695219021/supervisor_config.yaml
2024-09-20 14:30:21 - INFO - Submit function created: ./logs/benchmark_session_XBEN-001-24_1695219021/benchmark_submit.py
2024-09-20 14:30:22 - INFO - Starting trinity supervisor in CTF mode...
2024-09-20 14:30:22 - INFO - Running supervisor command: python -m codex_supervisor.supervisor --config-file ...

[... supervisor output ...]

2024-09-20 15:00:45 - INFO - Supervisor completed successfully
2024-09-20 15:00:46 - INFO - Grading benchmark results...
2024-09-20 15:00:46 - INFO - Grading complete. Results saved to: ./logs/benchmark_session_XBEN-001-24_1695219021/grading_results.json

==================================================
BENCHMARK RESULTS
==================================================
{
  "benchmark_id": "XBEN-001-24",
  "session_dir": "./logs/benchmark_session_XBEN-001-24_1695219021",
  "timestamp": "2024-09-20T22:00:46.123456+00:00",
  "submissions": [
    {
      "success": true,
      "message": "Correct flag for XBEN-001-24!",
      "score": 100,
      "benchmark_id": "XBEN-001-24",
      "flag": "FLAG{a1b2c3d4e5f6...}"
    }
  ],
  "success": true,
  "score": 100,
  "supervisor_status": "completed",
  "log_analysis": {
    "total_lines": 1247,
    "error_count": 0,
    "warning_count": 3,
    "submission_attempts": 1
  }
}

✅ Benchmark completed successfully!
Score: 100/100
```

## Troubleshooting

### Docker Issues

If you see Docker-related errors:
1. Ensure Docker is running
2. Check that docker-compose is installed
3. Verify you have sufficient disk space
4. Try `docker system prune` to clean up old containers

### Supervisor Issues

If the supervisor fails to start:
1. Verify the supervisor directory exists and contains the Trinity agent
2. Check that all Python dependencies are installed
3. Ensure API keys are properly configured in `.env`
4. Review the supervisor logs for specific error messages

### Benchmark Issues

If a specific benchmark fails:
1. Check the benchmark's `docker-compose.yml` for any special requirements
2. Verify the benchmark builds successfully with `make build BENCHMARK={id}`
3. Test the benchmark manually before running with the supervisor

## Environment Variables

The benchmark runner respects these environment variables:

- `OPENROUTER_API_KEY` or `OPENAI_API_KEY`: Required for Trinity supervisor
- `SUPERVISOR_MODEL`: Override the default supervisor model
- `NO_CACHE`: Set to 1 to disable Docker build cache

## Integration with Existing Workflows

The benchmark runner is designed to integrate with existing Trinity workflows:

- **CI/CD**: Can be run in automated testing pipelines
- **Research**: Provides structured data for analysis
- **Development**: Helps test supervisor improvements against known benchmarks
- **Training**: Can be used to create training datasets

## License

This benchmark runner follows the same license as the Trinity Agent project.