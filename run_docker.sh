#!/bin/bash

# Usage: ./run_docker.sh [openrouter|openai]
# Example: ./run_docker.sh openrouter

set -e

PROVIDER=${1:-openrouter}

if [ "$PROVIDER" != "openrouter" ] && [ "$PROVIDER" != "openai" ]; then
    echo "Error: Provider must be 'openrouter' or 'openai'"
    echo "Usage: ./run_docker.sh [openrouter|openai]"
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found. Please create one from .env.example"
    exit 1
fi

# Build the docker run command
DOCKER_CMD="docker run -it --env-file .env"

# Add config volume mount if using openrouter
if [ "$PROVIDER" = "openrouter" ]; then
    echo "Running with OpenRouter configuration..."
    if [ ! -f "$HOME/.codex/config.toml" ]; then
        echo "Error: $HOME/.codex/config.toml not found"
        exit 1
    fi
    DOCKER_CMD="$DOCKER_CMD -v $HOME/.codex/config.toml:/root/.codex/config.toml:ro"
else
    echo "Running with OpenAI configuration..."
fi

# Add volume mount for logs
DOCKER_CMD="$DOCKER_CMD -v $(pwd)/logs:/app/trinity/ARTEMIS/logs"

# Run the container
$DOCKER_CMD artemis \
    python -m supervisor.supervisor \
      --config-file configs/tests/ctf_easy.yaml \
      --benchmark-mode \
      --duration 10 \
      --skip-todos
