#!/usr/bin/env python3
import asyncio
import argparse
import json
import yaml
import os
import signal
from pathlib import Path
from datetime import datetime, timezone
import logging
import sys

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, use system environment variables only

from .orchestrator import SupervisorOrchestrator

def setup_logging(session_dir: Path, verbose: bool = False):
    """Setup logging for the supervisor."""
    log_file = session_dir / "supervisor.log"
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    
    # Reduce noise from HTTP libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.INFO)  # Keep some OpenAI logs but reduce verbosity

def load_config(config_file: Path) -> dict:
    """Load task configuration from YAML file."""
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)

async def main():
    parser = argparse.ArgumentParser(description='Codex Supervisor - AI Security Testing Orchestrator')
    parser.add_argument('--config-file', '-f', required=True, type=Path,
                      help='Path to task configuration YAML')
    parser.add_argument('--duration', '-d', type=int, default=60,
                      help='Duration to run (minutes)')
    parser.add_argument('--supervisor-model', '-m', default="openai/o4-mini",
                      help='Model for supervisor LLM (overrides environment variable)')
    parser.add_argument('--work-start-hour', type=int, default=7,
                      help='Work start hour (Pacific)')
    parser.add_argument('--work-end-hour', type=int, default=18,
                      help='Work end hour (Pacific)')
    parser.add_argument('--ignore-work-hours', action='store_true',
                      help='Ignore work hours and run 24/7')
    parser.add_argument('--resume-dir', type=Path,
                      help='Resume from existing session')
    parser.add_argument('--verbose', '-v', action='store_true',
                      help='Verbose logging')
    parser.add_argument('--codex-binary', default='./target/release/codex',
                      help='Path to codex binary')
    
    args = parser.parse_args()
    
    # Validate config file exists
    if not args.config_file.exists():
        print(f"❌ Config file not found: {args.config_file}")
        sys.exit(1)
    
    # Create or resume session directory
    if args.resume_dir:
        session_dir = args.resume_dir
        if not session_dir.exists():
            print(f"❌ Resume directory not found: {session_dir}")
            sys.exit(1)
        print(f"🔄 Resuming supervisor session: {session_dir}")
    else:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        session_dir = Path(f"./logs/supervisor_session_{timestamp}")
        session_dir.mkdir(parents=True, exist_ok=True)
        print(f"🚀 Starting new supervisor session: {session_dir}")
    
    setup_logging(session_dir, args.verbose)
    
    # Load configuration
    try:
        config = load_config(args.config_file)
        print(f"✅ Loaded configuration from {args.config_file}")
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)
    
    # Validate API key
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ OPENROUTER_API_KEY environment variable is required")
        print("💡 Create a .env file with: OPENROUTER_API_KEY=your-key-here")
        sys.exit(1)
    print("✅ OpenRouter API key found")
    
    # Determine supervisor model (CLI arg > env var > default)
    supervisor_model = args.supervisor_model or os.getenv("SUPERVISOR_MODEL", "o3")
    print(f"🤖 Using supervisor model: {supervisor_model}")
    
    # Resolve codex binary path to absolute path
    codex_binary_path = Path(args.codex_binary).resolve()
    if not codex_binary_path.exists():
        print(f"❌ Codex binary not found: {codex_binary_path}")
        sys.exit(1)
    print(f"✅ Codex binary found: {codex_binary_path}")
    
    # Create and run orchestrator
    orchestrator = SupervisorOrchestrator(
        config=config,
        session_dir=session_dir,
        supervisor_model=supervisor_model,
        duration_minutes=args.duration,
        work_hours=(args.work_start_hour, args.work_end_hour),
        ignore_work_hours=args.ignore_work_hours,
        verbose=args.verbose,
        codex_binary=str(codex_binary_path)
    )
    
    # Create a task for the orchestrator
    main_task = None
    
    def signal_handler():
        logging.info("🛑 Signal received, cancelling all tasks...")
        orchestrator.running = False
        if main_task and not main_task.done():
            main_task.cancel()
    
    # Register signal handlers
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, lambda s, f: signal_handler())
    
    try:
        # Run the orchestrator in a task so we can cancel it
        main_task = asyncio.create_task(orchestrator.run_loop())
        await main_task
        print("✅ Supervisor completed successfully")
    except asyncio.CancelledError:
        print("\n⏹️  Supervisor cancelled by user (Ctrl+C)")
        logging.info("🛑 Main task cancelled, initiating shutdown...")
    except KeyboardInterrupt:
        print("\n⏹️  Supervisor interrupted by user (Ctrl+C)")
        logging.info("🛑 KeyboardInterrupt caught, initiating shutdown...")
    except Exception as e:
        logging.error(f"Supervisor error: {e}")
        raise
    finally:
        # Always cleanup
        await orchestrator.shutdown()

def cli_main():
    """CLI entry point."""
    asyncio.run(main())

if __name__ == "__main__":
    cli_main()