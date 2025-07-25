#!/bin/bash

# Codex with Health Monitoring Startup Script
# This script launches both the codex autonomous mode and the health monitor

set -e

# Configuration
CODEX_DIR="./codex-rs"
HEALTH_MONITOR_SCRIPT="./codex-health-monitor.py"
HEALTH_CONFIG="./health-monitor-config.json"
LOG_DIR="./logs"

# Default values - can be overridden via environment variables
AUTONOMOUS_CONFIG="${AUTONOMOUS_CONFIG:-../config.yaml}"
AUTONOMOUS_DURATION="${AUTONOMOUS_DURATION:-2}" # 24 hours in minutes
DRIVER_MODEL="${DRIVER_MODEL:-o3}"
FULL_AUTO="${FULL_AUTO:-true}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
  echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"
}

error() {
  echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $1" >&2
}

success() {
  echo -e "${GREEN}[$(date '+%H:%M:%S')] SUCCESS:${NC} $1"
}

warn() {
  echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARNING:${NC} $1"
}

# Function to cleanup background processes
cleanup() {
  log "Shutting down..."

  if [[ -n $CODEX_PID ]] && kill -0 $CODEX_PID 2>/dev/null; then
    log "Stopping codex (PID: $CODEX_PID)"
    kill $CODEX_PID
    wait $CODEX_PID 2>/dev/null || true
  fi

  if [[ -n $MONITOR_PID ]] && kill -0 $MONITOR_PID 2>/dev/null; then
    log "Stopping health monitor (PID: $MONITOR_PID)"
    kill $MONITOR_PID
    wait $MONITOR_PID 2>/dev/null || true
  fi

  success "Shutdown complete"
  exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM EXIT

# Print startup banner
echo "================================================================"
echo "🚀 Codex Autonomous Mode with Health Monitoring"
echo "================================================================"
log "Autonomous config: $AUTONOMOUS_CONFIG"
log "Duration: $AUTONOMOUS_DURATION minutes"
log "Driver model: $DRIVER_MODEL"
log "Full auto mode: $FULL_AUTO"
log "Health monitor config: $HEALTH_CONFIG"
echo "================================================================"

# Validate prerequisites
log "Checking prerequisites..."

# Check if we're in the right directory
if [[ ! -d "$CODEX_DIR" ]]; then
  error "Codex directory not found: $CODEX_DIR"
  error "Please run this script from the parent directory of codex-rs"
  exit 1
fi

# Check if health monitor script exists
if [[ ! -f "$HEALTH_MONITOR_SCRIPT" ]]; then
  error "Health monitor script not found: $HEALTH_MONITOR_SCRIPT"
  exit 1
fi

# Check Python dependencies
if ! python3 -c "import psutil, requests" 2>/dev/null; then
  warn "Python dependencies missing. Installing..."
  python3 -m pip install psutil requests || {
    error "Failed to install Python dependencies"
    error "Please run: python3 -m pip install psutil requests"
    exit 1
  }
fi

# Create logs directory
mkdir -p "$LOG_DIR"

success "Prerequisites validated"

# Start health monitor in background
log "Starting health monitor..."
python3 -u "$HEALTH_MONITOR_SCRIPT" --config "$HEALTH_CONFIG" --verbose >"$LOG_DIR/health-monitor.log" 2>&1 &
MONITOR_PID=$!

if kill -0 $MONITOR_PID 2>/dev/null; then
  success "Health monitor started (PID: $MONITOR_PID)"
else
  error "Failed to start health monitor"
  exit 1
fi

# Give health monitor a moment to start
sleep 2

# Start codex autonomous mode
log "Starting codex autonomous mode..."
cd "$CODEX_DIR"

# Build codex command using cargo run
CODEX_CMD="cargo run --bin codex -- autonomous -f $AUTONOMOUS_CONFIG -d $AUTONOMOUS_DURATION -m $DRIVER_MODEL"

if [[ "$FULL_AUTO" == "true" ]]; then
  CODEX_CMD="$CODEX_CMD --full-auto"
fi

log "Executing: $CODEX_CMD"

# Start codex and capture PID
$CODEX_CMD >"../$LOG_DIR/codex-autonomous.log" 2>&1 &
CODEX_PID=$!

if kill -0 $CODEX_PID 2>/dev/null; then
  success "Codex autonomous mode started (PID: $CODEX_PID)"
else
  error "Failed to start codex autonomous mode"
  cleanup
  exit 1
fi

cd ..

# Both services started successfully
log "Both services started successfully"
log "Codex PID: $CODEX_PID"
log "Health Monitor PID: $MONITOR_PID"
log "Logs: $LOG_DIR/"
log "Health monitor will track codex status and send Slack updates"
log "Press Ctrl+C to stop both services, or wait for codex to complete naturally"

# Wait for codex to complete naturally
wait $CODEX_PID
CODEX_EXIT_CODE=$?

if [[ $CODEX_EXIT_CODE -eq 0 ]]; then
  success "Codex completed successfully"
else
  error "Codex exited with code: $CODEX_EXIT_CODE"
fi

# Cleanup
cleanup
