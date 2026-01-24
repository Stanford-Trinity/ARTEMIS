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

from dotenv import load_dotenv
load_dotenv()

from supervisor.orchestration import SupervisorOrchestrator
from supervisor.todo_generator import TodoGenerator
from supervisor.config import WorkingHoursConfig
from supervisor.llm_client import DEFAULT_BEDROCK_MODEL_ID, get_provider

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
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.INFO)

def load_config(config_file: Path) -> dict:
    """Load task configuration from YAML file."""
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    # Resolve relative file paths to absolute paths relative to config file
    config_dir = config_file.parent.absolute()
    
    if 'filepath' in config and not os.path.isabs(config['filepath']):
        config['filepath'] = str(config_dir / config['filepath'])
    
    return config

async def main():
    parser = argparse.ArgumentParser(description='Codex Supervisor - AI Security Testing Orchestrator')
    parser.add_argument('--config-file', '-f', required=True, type=Path,
                      help='Path to task configuration YAML')
    parser.add_argument('--duration', '-d', type=int, default=60,
                      help='Duration to run (minutes)')
    parser.add_argument('--supervisor-model', '-m', default=None,
                      help='Model for supervisor LLM (overrides environment variable)')
    parser.add_argument('--resume-dir', type=Path,
                      help='Resume from existing session')
    parser.add_argument('--verbose', '-v', action='store_true',
                      help='Verbose logging')
    parser.add_argument('--codex-binary', default='./codex-rs/target/release/codex',
                      help='Path to codex binary')
    parser.add_argument('--benchmark-mode', action='store_true',
                      help='Enable benchmark mode (skip triage, direct to Slack)')
    parser.add_argument('--skip-todos', action='store_true',
                      help='Skip the initial TODO generation step')
    parser.add_argument('--use-prompt-generation', action='store_true',
                      help='Use LLM to generate custom system prompts instead of routing to predefined modes')
    parser.add_argument('--finish-on-submit', action='store_true',
                      help='Finish session when a vulnerability is submitted (instead of continuing until duration expires)')
    
    args = parser.parse_args()
    
    if not args.config_file.exists():
        print(f"❌ Config file not found: {args.config_file}")
        sys.exit(1)
    
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
    
    try:
        config = load_config(args.config_file)
        print(f"✅ Loaded configuration from {args.config_file}")
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)
    
    provider = get_provider()
    if provider == "bedrock":
        if not os.getenv("BEDROCK_MODEL_ID"):
            print("❌ BEDROCK_MODEL_ID environment variable is required for Bedrock")
            print("💡 Create a .env file with: BEDROCK_MODEL_ID=anthropic.claude-opus-4-5-20240620-v1:0")
            sys.exit(1)
    else:
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("❌ Either OPENROUTER_API_KEY or OPENAI_API_KEY environment variable is required")
            print("💡 Create a .env file with: OPENROUTER_API_KEY=your-key-here")
            print("💡 Or use: OPENAI_API_KEY=your-key-here")
            print("💡 Or set LLM_PROVIDER=bedrock with BEDROCK_MODEL_ID for Amazon Bedrock")
            sys.exit(1)
    
    if provider == "openrouter":
        print("✅ OpenRouter API key found")
    elif provider == "bedrock":
        print("✅ Amazon Bedrock configuration found")
    else:
        print("✅ OpenAI API key found")
    
    # Choose supervisor model based on environment or API provider
    if args.supervisor_model:
        supervisor_model = args.supervisor_model
    elif os.getenv("SUPERVISOR_MODEL"):
        supervisor_model = os.getenv("SUPERVISOR_MODEL")
    else:
        # Default based on API provider
        if provider == "openrouter":
            supervisor_model = "openai/o4-mini"  # OpenRouter format
        elif provider == "bedrock":
            supervisor_model = os.getenv("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID)
        else:
            supervisor_model = "o4-mini"  # OpenAI direct format
    print(f"🤖 Using supervisor model: {supervisor_model}")
    
    if args.benchmark_mode:
        print("🏁 BENCHMARK MODE ENABLED - Triage process will be skipped")
    else:
        print("🔍 Normal mode - Vulnerabilities will go through triage process")

    if args.finish_on_submit:
        print("⏹️  FINISH ON SUBMIT MODE ENABLED - Session will end after first submission")

    codex_binary_path = Path(args.codex_binary).resolve()
    if not codex_binary_path.exists():
        print(f"❌ Codex binary not found: {codex_binary_path}")
        sys.exit(1)
    print(f"✅ Codex binary found: {codex_binary_path}")
    
    todo_file = session_dir / "supervisor_todo.json"
    if args.skip_todos:
        print("⏭️  Skipping TODO generation (--skip-todos specified)")
    elif not args.resume_dir and not todo_file.exists():
        print("🎯 Generating initial TODO list from configuration...")
        try:
            config_content = yaml.dump(config, default_flow_style=False)
            
            todo_generator = TodoGenerator(provider=provider)
            initial_todos = await todo_generator.generate_todos_from_config(config_content)
            
            await todo_generator.save_todos_to_file(initial_todos, todo_file)
            
            def count_all_todos(todos):
                total = len(todos)
                for todo in todos:
                    if todo.get("subtasks"):
                        total += count_all_todos(todo["subtasks"])
                return total
            
            total_todo_count = count_all_todos(initial_todos)
            print(f"✅ Generated {len(initial_todos)} top-level TODOs ({total_todo_count} total including subtasks)")
            
        except Exception as e:
            logging.error(f"Failed to generate initial TODOs: {e}")
            print("⚠️  Continuing without pre-generated TODOs")
    elif args.resume_dir:
        print("🔄 Using existing TODO list from resumed session")
    else:
        print("📝 TODO file already exists, skipping generation")
    
    orchestrator = SupervisorOrchestrator(
        config=config,
        session_dir=session_dir,
        supervisor_model=supervisor_model,
        duration_minutes=args.duration,
        verbose=args.verbose,
        codex_binary=str(codex_binary_path),
        benchmark_mode=args.benchmark_mode,
        skip_todos=args.skip_todos,
        use_prompt_generation=args.use_prompt_generation,
        working_hours_config=WorkingHoursConfig.model_validate(config.get('working_hours', {})),
        finish_on_submit=args.finish_on_submit
    )
    
    main_task = None
    
    def signal_handler():
        logging.info("🛑 Signal received, cancelling all tasks...")
        orchestrator.running = False
        if main_task and not main_task.done():
            main_task.cancel()
    
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, lambda s, f: signal_handler())
    
    try:
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
        await orchestrator.shutdown()

def cli_main():
    """CLI entry point."""
    asyncio.run(main())

if __name__ == "__main__":
    cli_main()
