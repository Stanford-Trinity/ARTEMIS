#!/bin/bash
# Setup script for Trinity Agent Benchmark Runner
# This script helps configure the environment for running benchmarks

set -e

echo "🔧 Trinity Agent Benchmark Environment Setup"
echo "=============================================="

# Check if .env file exists
if [ ! -f "./supervisor/.env" ]; then
    echo "📝 Creating .env file in supervisor directory..."

    # Create .env file from example if it exists
    if [ -f "./supervisor/.env.example" ]; then
        cp "./supervisor/.env.example" "./supervisor/.env"
        echo "✅ Copied .env.example to .env"
    else
        # Create basic .env template
        cat > "./supervisor/.env" << EOF
# API Configuration for Trinity Agent Supervisor
# Choose ONE of the following API providers:

# Option 1: OpenRouter (recommended for access to multiple models)
# OPENROUTER_API_KEY=your-openrouter-key-here

# Option 2: OpenAI Direct
# OPENAI_API_KEY=your-openai-key-here

# Supervisor Model Configuration
# For OpenRouter, use format: openai/gpt-4, anthropic/claude-3-sonnet, etc.
# For OpenAI direct, use: gpt-4, gpt-3.5-turbo, etc.
# SUPERVISOR_MODEL=openai/o4-mini

# Docker Configuration
# NO_CACHE=0

# Working Hours (optional)
# WORKING_HOURS_START=9
# WORKING_HOURS_END=17
# WORKING_HOURS_TIMEZONE=US/Pacific
EOF
        echo "✅ Created basic .env template"
    fi

    echo "⚠️  Please edit ./supervisor/.env and add your API key"
    echo "   Use: OPENROUTER_API_KEY=your-key-here"
    echo "   Or:  OPENAI_API_KEY=your-key-here"
    echo ""
fi

# Check if API keys are configured
echo "🔍 Checking API configuration..."

source "./supervisor/.env" 2>/dev/null || true

if [ -n "$OPENROUTER_API_KEY" ]; then
    echo "✅ OpenRouter API key found"
    export OPENROUTER_API_KEY
elif [ -n "$OPENAI_API_KEY" ]; then
    echo "✅ OpenAI API key found"
    export OPENAI_API_KEY
else
    echo "❌ No API key found in .env file"
    echo "   Please edit ./supervisor/.env and add:"
    echo "   OPENROUTER_API_KEY=your-key-here"
    echo "   OR"
    echo "   OPENAI_API_KEY=your-key-here"
    exit 1
fi

# Check Docker
echo ""
echo "🐳 Checking Docker..."
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        echo "✅ Docker is running"
    else
        echo "❌ Docker is installed but not running"
        echo "   Please start Docker and try again"
        exit 1
    fi
else
    echo "❌ Docker not found"
    echo "   Please install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check Docker Compose
if docker compose version >/dev/null 2>&1; then
    echo "✅ Docker Compose is available"
else
    echo "❌ Docker Compose not found"
    echo "   Please install Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

# Check paths
echo ""
echo "📁 Checking directories..."

if [ -d "./supervisor" ]; then
    echo "✅ Supervisor directory found"
else
    echo "❌ Supervisor directory not found"
    echo "   Expected: ./supervisor"
    exit 1
fi

if [ -d "../validation-benchmarks" ]; then
    echo "✅ Validation benchmarks directory found"
    BENCHMARK_COUNT=$(find "../validation-benchmarks/benchmarks" -name "XBEN-*" -type d 2>/dev/null | wc -l)
    echo "   Found $BENCHMARK_COUNT benchmarks"
else
    echo "❌ Validation benchmarks directory not found"
    echo "   Expected: ../validation-benchmarks"
    echo "   Please clone the validation-benchmarks repository"
    exit 1
fi

# Check codex binary
echo ""
echo "🚀 Checking codex binary..."

CODEX_PATHS=(
    "./target/release/codex"
    "./target/debug/codex"
    "./supervisor/codex"
    "$(which codex 2>/dev/null || true)"
)

CODEX_FOUND=""
for path in "${CODEX_PATHS[@]}"; do
    if [ -n "$path" ] && [ -f "$path" ]; then
        CODEX_FOUND="$path"
        break
    fi
done

if [ -n "$CODEX_FOUND" ]; then
    echo "✅ Codex binary found: $CODEX_FOUND"
else
    echo "⚠️  Codex binary not found in standard locations"
    echo "   Searched: ${CODEX_PATHS[*]}"
    echo "   You may need to specify --codex-binary when running benchmarks"
fi

# Check Python dependencies
echo ""
echo "🐍 Checking Python environment..."

if cd "./supervisor" && python -c "import codex_supervisor" 2>/dev/null; then
    echo "✅ Trinity supervisor Python package is available"
    cd - >/dev/null
else
    echo "❌ Trinity supervisor Python package not found"
    echo "   Please install dependencies in the supervisor directory:"
    echo "   cd ./supervisor && pip install -e ."
    exit 1
fi

# Run environment check
echo ""
echo "🎯 Running benchmark runner environment check..."
if python "./benchmark_runner.py" --check-env; then
    echo ""
    echo "🎉 Environment setup complete!"
    echo ""
    echo "Example usage:"
    echo "  ./benchmark_runner.py --list-benchmarks"
    echo "  ./benchmark_runner.py XBEN-001-24 --duration 30"
    echo "  ./benchmark_runner.py XBEN-003-24 --supervisor-model 'openai/gpt-4' --verbose"
    echo ""
else
    echo ""
    echo "❌ Environment check failed"
    echo "   Please review the errors above and fix any issues"
    exit 1
fi