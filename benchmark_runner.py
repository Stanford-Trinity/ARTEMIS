#!/usr/bin/env python3
"""
Benchmark Runner for Trinity Agent with XBOW Validation Benchmarks
Automates docker container building, supervisor integration, and grading.
"""

import asyncio
import argparse
import json
import subprocess
import os
import sys
import tempfile
import yaml
from pathlib import Path
from datetime import datetime, timezone
import logging
import signal
import time

class BenchmarkRunner:
    def __init__(self, benchmark_id: str, supervisor_dir: Path, validation_benchmarks_dir: Path,
                 supervisor_args: dict = None):
        self.benchmark_id = benchmark_id
        self.supervisor_dir = supervisor_dir
        self.validation_benchmarks_dir = validation_benchmarks_dir
        self.benchmark_path = validation_benchmarks_dir / "benchmarks" / benchmark_id
        self.session_dir = None
        self.container_running = False
        self.supervisor_args = supervisor_args or {}

        # Validate paths
        if not self.supervisor_dir.exists():
            raise ValueError(f"Supervisor directory not found: {supervisor_dir}")
        if not self.validation_benchmarks_dir.exists():
            raise ValueError(f"Validation benchmarks directory not found: {validation_benchmarks_dir}")
        if not self.benchmark_path.exists():
            raise ValueError(f"Benchmark not found: {benchmark_id}")

        self.setup_logging()
        self.validate_api_keys()

    def setup_logging(self):
        """Setup logging configuration."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger(__name__)

    def validate_api_keys(self):
        """Validate that required API keys are available."""
        # Check for API keys
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        if not openrouter_key and not openai_key:
            raise ValueError(
                "Either OPENROUTER_API_KEY or OPENAI_API_KEY environment variable is required.\n"
                "Create a .env file with: OPENROUTER_API_KEY=your-key-here\n"
                "Or use: OPENAI_API_KEY=your-key-here"
            )

        if openrouter_key:
            self.logger.info("✅ OpenRouter API key found")
            self.api_provider = "openrouter"
        else:
            self.logger.info("✅ OpenAI API key found")
            self.api_provider = "openai"

    def get_default_model(self) -> str:
        """Get default model based on API provider."""
        if self.supervisor_args.get('supervisor_model'):
            return self.supervisor_args['supervisor_model']
        elif os.getenv("SUPERVISOR_MODEL"):
            return os.getenv("SUPERVISOR_MODEL")
        else:
            # Default based on API provider
            if self.api_provider == "openrouter":
                return "openai/o4-mini"  # OpenRouter format
            else:
                return "o4-mini"  # OpenAI direct format

    def build_docker_containers(self) -> bool:
        """Build docker containers for the benchmark."""
        self.logger.info(f"Building docker containers for {self.benchmark_id}...")

        # Store original directory
        original_dir = os.getcwd()

        try:
            # Change to validation-benchmarks directory
            os.chdir(self.validation_benchmarks_dir)

            # Build the benchmark using the Makefile
            result = subprocess.run(
                ["make", "build", f"BENCHMARK={self.benchmark_id}"],
                capture_output=True,
                text=True,
                check=True
            )

            self.logger.info("Docker containers built successfully")
            self.logger.debug(f"Build output: {result.stdout}")
            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to build docker containers: {e}")
            self.logger.error(f"Error output: {e.stderr}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during build: {e}")
            return False
        finally:
            # Always return to original directory
            os.chdir(original_dir)

    def start_benchmark_containers(self) -> bool:
        """Start the benchmark containers."""
        self.logger.info(f"Starting benchmark containers for {self.benchmark_id}...")

        # Store original directory
        original_dir = os.getcwd()

        try:
            # Change to benchmark directory
            os.chdir(self.benchmark_path)

            # Start containers using docker-compose
            result = subprocess.run(
                ["docker", "compose", "up", "-d", "--wait"],
                capture_output=True,
                text=True,
                check=True
            )

            self.container_running = True
            self.logger.info("Benchmark containers started successfully")
            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to start containers: {e}")
            self.logger.error(f"Error output: {e.stderr}")
            return False
        finally:
            # Always return to original directory
            os.chdir(original_dir)

    def stop_benchmark_containers(self):
        """Stop the benchmark containers."""
        if not self.container_running:
            return

        self.logger.info(f"Stopping benchmark containers for {self.benchmark_id}...")

        # Store original directory
        original_dir = os.getcwd()

        try:
            os.chdir(self.benchmark_path)
            subprocess.run(
                ["docker", "compose", "down"],
                capture_output=True,
                text=True,
                check=True
            )
            self.container_running = False
            self.logger.info("Benchmark containers stopped")
        except Exception as e:
            self.logger.error(f"Error stopping containers: {e}")
        finally:
            # Always return to original directory
            os.chdir(original_dir)

    def create_supervisor_config(self, duration_minutes: int = 30) -> Path:
        """Create supervisor configuration for CTF mode."""
        self.logger.info("Creating supervisor configuration for CTF mode...")

        # Read benchmark metadata
        benchmark_json_path = self.benchmark_path / "benchmark.json"
        with open(benchmark_json_path, 'r') as f:
            benchmark_metadata = json.load(f)

        # Create session directory with absolute path
        timestamp = int(datetime.now(timezone.utc).timestamp())
        self.session_dir = Path.cwd() / f"logs/benchmark_session_{self.benchmark_id}_{timestamp}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Create supervisor configuration
        config = {
            "description": benchmark_metadata["description"],
            "win_condition": benchmark_metadata["win_condition"],
            "target_url": "http://localhost",  # Will be updated based on docker-compose ports
            # Configure XBOW validation submission handler
            "submission_config": {
                "type": "xbow_validation",
                "benchmark_id": self.benchmark_id
            }
        }

        # Determine actual mapped ports from running containers
        config["target_url"] = "http://localhost"  # Default fallback

        try:
            # Get actual port mappings from running containers
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd=self.benchmark_path,
                capture_output=True,
                text=True,
                check=True
            )

            containers = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    containers.append(json.loads(line))

            # Find containers with HTTP ports exposed
            for container in containers:
                publishers = container.get('Publishers', [])
                for pub in publishers:
                    if pub.get('TargetPort') == 80:  # HTTP port
                        published_port = pub.get('PublishedPort')
                        if published_port:
                            config["target_url"] = f"http://localhost:{published_port}"
                            config["target_port"] = published_port
                            self.logger.info(f"Found HTTP service on port {published_port}")
                            break
                if 'target_port' in config:
                    break

        except Exception as e:
            self.logger.warning(f"Could not detect container ports: {e}, using fallback")
            # Fallback: try to get port from docker compose port command
            try:
                result = subprocess.run(
                    ["docker", "compose", "port", "idor_broken_authz_trading_platform", "80"],
                    cwd=self.benchmark_path,
                    capture_output=True,
                    text=True,
                    check=False  # Don't fail if service name doesn't exist
                )
                if result.returncode == 0 and result.stdout.strip():
                    port_info = result.stdout.strip()  # e.g., "0.0.0.0:32774"
                    if ':' in port_info:
                        port = port_info.split(':')[-1]
                        config["target_url"] = f"http://localhost:{port}"
                        config["target_port"] = int(port)
                        self.logger.info(f"Found HTTP service on port {port} via fallback")
            except Exception as fallback_error:
                self.logger.warning(f"Fallback port detection failed: {fallback_error}")

        # Verify the target URL is reachable
        if 'target_port' in config:
            try:
                import requests
                response = requests.get(config["target_url"], timeout=5)
                if response.status_code == 200:
                    self.logger.info(f"✅ Target URL {config['target_url']} is reachable")
                else:
                    self.logger.warning(f"⚠️ Target URL {config['target_url']} returned status {response.status_code}")
            except Exception as e:
                self.logger.warning(f"⚠️ Could not verify target URL {config['target_url']}: {e}")

        # Save configuration
        config_path = self.session_dir / "supervisor_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

        self.logger.info(f"Supervisor configuration created: {config_path}")
        return config_path


    async def run_supervisor(self, config_path: Path, duration_minutes: int = 30) -> bool:
        """Run the trinity supervisor in CTF mode."""
        self.logger.info("Starting trinity supervisor in CTF mode...")

        # Store original directory
        original_dir = os.getcwd()

        try:
            # Change to supervisor directory
            os.chdir(self.supervisor_dir)

            # Use absolute path for config file
            config_path_abs = config_path.resolve()

            # Prepare supervisor command
            cmd = [
                "python", "-m", "codex_supervisor.supervisor",
                "--config-file", str(config_path_abs),
                "--duration", str(duration_minutes),
                "--benchmark-mode",  # Enable CTF/benchmark mode
                "--finish-on-submit",  # Stop after successful submission
            ]

            # Add model specification
            model = self.get_default_model()
            cmd.extend(["--supervisor-model", model])

            # Add optional supervisor arguments
            if self.supervisor_args.get('verbose'):
                cmd.append("--verbose")
            if self.supervisor_args.get('skip_todos'):
                cmd.append("--skip-todos")
            if self.supervisor_args.get('use_prompt_generation'):
                cmd.append("--use-prompt-generation")
            if self.supervisor_args.get('codex_binary'):
                cmd.extend(["--codex-binary", self.supervisor_args['codex_binary']])

            # Working hours configuration - always ignore working hours in benchmark mode
            cmd.extend(["--working-hours-start", "0"])
            cmd.extend(["--working-hours-end", "23"])

            # Override any user-specified working hours in benchmark mode
            if self.supervisor_args.get('working_hours_timezone'):
                cmd.extend(["--working-hours-timezone", self.supervisor_args['working_hours_timezone']])

            self.logger.info(f"Using supervisor model: {model}")
            self.logger.info(f"Running supervisor command: {' '.join(cmd)}")

            # Run supervisor
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            # Stream output in real-time
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                self.logger.info(f"SUPERVISOR: {line.decode().strip()}")

            # Wait for completion
            return_code = await process.wait()

            if return_code == 0:
                self.logger.info("Supervisor completed successfully")
                return True
            else:
                self.logger.error(f"Supervisor failed with return code: {return_code}")
                return False

        except Exception as e:
            self.logger.error(f"Error running supervisor: {e}")
            return False
        finally:
            # Always return to original directory
            os.chdir(original_dir)

    def grade_results(self) -> dict:
        """Grade the benchmark results."""
        self.logger.info("Grading benchmark results...")

        if not self.session_dir:
            return {"error": "No session directory found"}

        # Look for XBOW validation submission results
        xbow_submissions_file = self.session_dir / "xbow_validation_submissions.json"
        supervisor_log = self.session_dir / "supervisor.log"

        results = {
            "benchmark_id": self.benchmark_id,
            "session_dir": str(self.session_dir),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "submissions": [],
            "success": False,
            "score": 0,
            "total_attempts": 0,
            "correct_attempts": 0
        }

        # Parse XBOW validation submissions
        if xbow_submissions_file.exists():
            try:
                with open(xbow_submissions_file, 'r') as f:
                    submissions_data = json.load(f)

                results["submissions"] = submissions_data
                results["total_attempts"] = len(submissions_data)

                # Find the best result
                for submission in submissions_data:
                    if submission.get("is_correct"):
                        results["correct_attempts"] += 1
                        results["success"] = True
                        results["score"] = max(results["score"], submission.get("score", 0))

                # If we have any submissions, get the latest one
                if submissions_data:
                    latest_submission = submissions_data[-1]
                    results["latest_submission"] = latest_submission
                    results["latest_flag"] = latest_submission.get("flag")
                    results["expected_flag"] = latest_submission.get("expected_flag")

            except Exception as e:
                self.logger.error(f"Error reading XBOW validation submissions: {e}")
                results["error"] = f"Failed to read submissions: {e}"

        # If no XBOW validation submissions file exists, that's an error
        if not xbow_submissions_file.exists():
            results["error"] = f"XBOW validation submissions file not found: {xbow_submissions_file}"
            self.logger.error(f"XBOW validation submissions file not found: {xbow_submissions_file}")

        # Parse supervisor log for additional insights
        if supervisor_log.exists():
            try:
                with open(supervisor_log, 'r') as f:
                    log_content = f.read()

                # Extract key metrics
                results["log_analysis"] = {
                    "total_lines": len(log_content.splitlines()),
                    "error_count": log_content.count("ERROR"),
                    "warning_count": log_content.count("WARNING"),
                    "submission_attempts": log_content.count("SUBMIT")
                }

            except Exception as e:
                self.logger.error(f"Error analyzing supervisor log: {e}")

        # Save results
        results_file = self.session_dir / "grading_results.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)

        self.logger.info(f"Grading complete. Results saved to: {results_file}")
        return results

    async def run_benchmark(self, duration_minutes: int = 30) -> dict:
        """Run the complete benchmark process."""
        self.logger.info(f"Starting benchmark run for {self.benchmark_id}")
        self.logger.info(f"API Provider: {self.api_provider}")
        self.logger.info(f"Supervisor Model: {self.get_default_model()}")

        # Log supervisor arguments
        if self.supervisor_args:
            self.logger.info(f"Supervisor arguments: {self.supervisor_args}")

        try:
            # Step 1: Build docker containers
            if not self.build_docker_containers():
                return {"error": "Failed to build docker containers"}

            # Step 2: Start benchmark containers
            if not self.start_benchmark_containers():
                return {"error": "Failed to start benchmark containers"}

            # Step 3: Create supervisor configuration
            config_path = self.create_supervisor_config(duration_minutes)

            # Step 4: Run supervisor
            supervisor_success = await self.run_supervisor(config_path, duration_minutes)

            # Step 5: Grade results
            results = self.grade_results()

            if supervisor_success:
                results["supervisor_status"] = "completed"
            else:
                results["supervisor_status"] = "failed"

            # Add configuration info to results
            results["configuration"] = {
                "api_provider": self.api_provider,
                "supervisor_model": self.get_default_model(),
                "supervisor_args": self.supervisor_args,
                "duration_minutes": duration_minutes
            }

            return results

        except Exception as e:
            self.logger.error(f"Benchmark run failed: {e}")
            return {"error": str(e)}

        finally:
            # Cleanup: Stop containers
            self.stop_benchmark_containers()


async def main():
    parser = argparse.ArgumentParser(
        description='Run Trinity Agent with XBOW Validation Benchmarks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s XBEN-001-24 --duration 30
  %(prog)s XBEN-003-24 --supervisor-model "openai/gpt-4" --verbose
  %(prog)s XBEN-005-24 --codex-binary ./target/debug/codex --skip-todos
  %(prog)s --list-benchmarks

Environment Variables:
  OPENROUTER_API_KEY or OPENAI_API_KEY: Required for supervisor
  SUPERVISOR_MODEL: Default model to use (can be overridden with --supervisor-model)
  NO_CACHE: Set to 1 to disable Docker build cache"""
    )

    # Basic arguments
    parser.add_argument('benchmark_id', nargs='?', help='Benchmark ID (e.g., XBEN-001-24)')
    parser.add_argument('--supervisor-dir', type=Path, default='./supervisor',
                       help='Path to supervisor directory')
    parser.add_argument('--validation-benchmarks-dir', type=Path, default='../validation-benchmarks',
                       help='Path to validation-benchmarks directory')
    parser.add_argument('--duration', '-d', type=int, default=30,
                       help='Duration to run (minutes)')
    parser.add_argument('--list-benchmarks', action='store_true',
                       help='List available benchmarks')
    parser.add_argument('--run-all', action='store_true',
                       help='Run all available benchmarks sequentially')

    # Supervisor model and API configuration
    parser.add_argument('--supervisor-model', '-m', default=None,
                       help='Model for supervisor LLM (e.g., "openai/gpt-4", "o4-mini")')

    # Supervisor behavior arguments
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    parser.add_argument('--skip-todos', action='store_true',
                       help='Skip the initial TODO generation step')
    parser.add_argument('--use-prompt-generation', action='store_true',
                       help='Use LLM to generate custom system prompts')
    parser.add_argument('--codex-binary', default=None,
                       help='Path to codex binary (default: ./target/release/codex)')

    # Working hours configuration
    parser.add_argument('--working-hours-start', type=int, default=None,
                       help='Working hours start time (24-hour format, default: 9)')
    parser.add_argument('--working-hours-end', type=int, default=None,
                       help='Working hours end time (24-hour format, default: 17)')
    parser.add_argument('--working-hours-timezone', default=None,
                       help='Timezone for working hours (default: US/Pacific)')

    # Docker build options
    parser.add_argument('--no-cache', action='store_true',
                       help='Disable Docker build cache')
    parser.add_argument('--check-env', action='store_true',
                       help='Check environment and API keys without running benchmark')

    args = parser.parse_args()

    # Set environment variables from args
    if args.no_cache:
        os.environ['NO_CACHE'] = '1'

    # Handle environment check
    if args.check_env:
        print("🔍 Checking environment...")
        try:
            # Check API keys
            openrouter_key = os.getenv("OPENROUTER_API_KEY")
            openai_key = os.getenv("OPENAI_API_KEY")

            if openrouter_key:
                print("✅ OpenRouter API key found")
                api_provider = "openrouter"
            elif openai_key:
                print("✅ OpenAI API key found")
                api_provider = "openai"
            else:
                print("❌ No API key found. Set OPENROUTER_API_KEY or OPENAI_API_KEY")
                return

            # Check model
            if args.supervisor_model:
                model = args.supervisor_model
            elif os.getenv("SUPERVISOR_MODEL"):
                model = os.getenv("SUPERVISOR_MODEL")
            else:
                model = "openai/o4-mini" if api_provider == "openrouter" else "o4-mini"
            print(f"🤖 Supervisor model: {model}")

            # Check paths
            if args.supervisor_dir.exists():
                print(f"✅ Supervisor directory: {args.supervisor_dir}")
            else:
                print(f"❌ Supervisor directory not found: {args.supervisor_dir}")

            if args.validation_benchmarks_dir.exists():
                print(f"✅ Validation benchmarks directory: {args.validation_benchmarks_dir}")
            else:
                print(f"❌ Validation benchmarks directory not found: {args.validation_benchmarks_dir}")

            # Check codex binary
            codex_binary = args.codex_binary or "./target/release/codex"
            codex_path = Path(codex_binary).resolve()
            if codex_path.exists():
                print(f"✅ Codex binary: {codex_path}")
            else:
                print(f"⚠️  Codex binary not found: {codex_path}")

            print("\n🎯 Environment check complete!")
            return

        except Exception as e:
            print(f"❌ Environment check failed: {e}")
            return

    # Handle benchmark listing
    if args.list_benchmarks:
        benchmarks_dir = args.validation_benchmarks_dir / "benchmarks"
        if benchmarks_dir.exists():
            print("Available benchmarks:")
            for benchmark_dir in sorted(benchmarks_dir.iterdir()):
                if benchmark_dir.is_dir() and benchmark_dir.name.startswith('XBEN-'):
                    benchmark_json = benchmark_dir / "benchmark.json"
                    if benchmark_json.exists():
                        try:
                            with open(benchmark_json, 'r') as f:
                                metadata = json.load(f)
                            print(f"  {benchmark_dir.name}: {metadata.get('name', 'Unknown')}")
                        except:
                            print(f"  {benchmark_dir.name}: (metadata error)")
        return

    # Handle running all benchmarks
    if args.run_all:
        benchmarks_dir = args.validation_benchmarks_dir / "benchmarks"
        if not benchmarks_dir.exists():
            print(f"❌ Benchmarks directory not found: {benchmarks_dir}")
            return

        # Get all available benchmarks
        benchmark_ids = []
        for benchmark_dir in sorted(benchmarks_dir.iterdir()):
            if benchmark_dir.is_dir() and benchmark_dir.name.startswith('XBEN-'):
                benchmark_json = benchmark_dir / "benchmark.json"
                if benchmark_json.exists():
                    benchmark_ids.append(benchmark_dir.name)

        print(f"🚀 Running {len(benchmark_ids)} benchmarks sequentially...")

        # Prepare supervisor arguments
        supervisor_args = {
            'supervisor_model': args.supervisor_model,
            'verbose': args.verbose,
            'skip_todos': args.skip_todos,
            'use_prompt_generation': args.use_prompt_generation,
            'codex_binary': args.codex_binary,
            'working_hours_start': args.working_hours_start,
            'working_hours_end': args.working_hours_end,
            'working_hours_timezone': args.working_hours_timezone,
        }
        supervisor_args = {k: v for k, v in supervisor_args.items() if v is not None}

        all_results = []
        successful_count = 0

        for i, benchmark_id in enumerate(benchmark_ids, 1):
            print(f"\n{'='*60}")
            print(f"📊 BENCHMARK {i}/{len(benchmark_ids)}: {benchmark_id}")
            print(f"{'='*60}")

            try:
                runner = BenchmarkRunner(
                    benchmark_id=benchmark_id,
                    supervisor_dir=args.supervisor_dir,
                    validation_benchmarks_dir=args.validation_benchmarks_dir,
                    supervisor_args=supervisor_args
                )

                result = await runner.run_benchmark(args.duration)
                result["benchmark_number"] = i
                result["total_benchmarks"] = len(benchmark_ids)
                all_results.append(result)

                if result.get("success"):
                    successful_count += 1
                    print(f"✅ {benchmark_id} COMPLETED (Score: {result.get('score', 0)}/100)")
                else:
                    print(f"❌ {benchmark_id} FAILED")

            except Exception as e:
                print(f"💥 {benchmark_id} ERROR: {e}")
                all_results.append({
                    "benchmark_id": benchmark_id,
                    "benchmark_number": i,
                    "total_benchmarks": len(benchmark_ids),
                    "error": str(e),
                    "success": False,
                    "score": 0
                })

        # Generate summary report
        print(f"\n{'='*60}")
        print(f"📊 FINAL RESULTS: {successful_count}/{len(benchmark_ids)} SUCCESSFUL")
        print(f"{'='*60}")

        total_score = sum(r.get("score", 0) for r in all_results)
        max_possible_score = len(benchmark_ids) * 100
        overall_percentage = (total_score / max_possible_score * 100) if max_possible_score > 0 else 0

        print(f"Overall Score: {total_score}/{max_possible_score} ({overall_percentage:.1f}%)")
        print(f"Success Rate: {successful_count}/{len(benchmark_ids)} ({successful_count/len(benchmark_ids)*100:.1f}%)")

        # Save combined results
        timestamp = int(datetime.now(timezone.utc).timestamp())
        results_file = Path(f"./logs/all_benchmarks_results_{timestamp}.json")
        results_file.parent.mkdir(exist_ok=True)

        combined_results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_benchmarks": len(benchmark_ids),
            "successful_benchmarks": successful_count,
            "total_score": total_score,
            "max_possible_score": max_possible_score,
            "overall_percentage": overall_percentage,
            "individual_results": all_results
        }

        with open(results_file, 'w') as f:
            json.dump(combined_results, f, indent=2)

        print(f"📄 Detailed results saved to: {results_file}")
        return

    # Validate benchmark_id is provided
    if not args.benchmark_id:
        parser.error("benchmark_id is required unless using --list-benchmarks, --check-env, or --run-all")

    # Prepare supervisor arguments
    supervisor_args = {
        'supervisor_model': args.supervisor_model,
        'verbose': args.verbose,
        'skip_todos': args.skip_todos,
        'use_prompt_generation': args.use_prompt_generation,
        'codex_binary': args.codex_binary,
        'working_hours_start': args.working_hours_start,
        'working_hours_end': args.working_hours_end,
        'working_hours_timezone': args.working_hours_timezone,
    }

    # Remove None values
    supervisor_args = {k: v for k, v in supervisor_args.items() if v is not None}

    # Run benchmark
    try:
        runner = BenchmarkRunner(
            benchmark_id=args.benchmark_id,
            supervisor_dir=args.supervisor_dir,
            validation_benchmarks_dir=args.validation_benchmarks_dir,
            supervisor_args=supervisor_args
        )

        # Handle graceful shutdown
        def signal_handler():
            print("\n🛑 Shutting down...")
            runner.stop_benchmark_containers()
            sys.exit(0)

        signal.signal(signal.SIGINT, lambda s, f: signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler())

        # Run the benchmark
        results = await runner.run_benchmark(args.duration)

        # Print results
        print("\n" + "="*50)
        print("BENCHMARK RESULTS")
        print("="*50)
        print(json.dumps(results, indent=2))

        if results.get("success"):
            print("\n✅ Benchmark completed successfully!")
            print(f"Score: {results.get('score', 0)}/100")
        else:
            print("\n❌ Benchmark failed or incomplete")

    except KeyboardInterrupt:
        print("\n⏹️  Benchmark interrupted by user")
    except Exception as e:
        print(f"\n❌ Benchmark error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())