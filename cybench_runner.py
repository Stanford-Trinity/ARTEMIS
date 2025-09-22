#!/usr/bin/env python3
"""
Cybench Benchmark Runner

This tool automates running the Trinity Agent with Cybench CTF challenges.

Example usage:
    ./cybench_runner.py benchmark/project-sekai-ctf/sekaictf-2023/crypto/noisier-crc --duration 30
    ./cybench_runner.py --list-tasks
    ./cybench_runner.py --run-all --duration 45
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List


class CybenchRunner:
    """Runner for cybench CTF challenges with Trinity supervisor integration."""

    def __init__(self, task_id: str, supervisor_dir: Path = None, cybench_dir: Path = None,
                 supervisor_args: Dict[str, Any] = None):
        self.task_id = task_id
        self.supervisor_dir = supervisor_dir or Path("./supervisor")
        self.cybench_dir = cybench_dir or Path("../cybench")
        self.supervisor_args = supervisor_args or {}
        self.session_dir = None
        self.logger = self._setup_logging()

    def _setup_logging(self) -> logging.Logger:
        """Setup logging for the benchmark runner."""
        logger = logging.getLogger(f"cybench_runner_{self.task_id}")
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    async def run_benchmark(self, duration_minutes: int = 30) -> Dict[str, Any]:
        """Run a complete cybench benchmark."""
        start_time = time.time()

        self.logger.info(f"Starting cybench benchmark for {self.task_id}")

        try:
            # Create session directory
            timestamp = int(time.time())
            self.session_dir = Path(f"logs/cybench_session_{self.task_id.replace('/', '_')}_{timestamp}")
            self.session_dir.mkdir(parents=True, exist_ok=True)

            # Setup Docker network
            await self._ensure_docker_network()

            # Start task containers
            await self._start_task_containers()

            # Create supervisor configuration
            await self._create_supervisor_config()

            # Run supervisor
            supervisor_result = await self._run_supervisor(duration_minutes)

            # Grade results
            results = self._grade_results()
            results["supervisor_result"] = supervisor_result
            results["duration_minutes"] = duration_minutes
            results["actual_duration"] = (time.time() - start_time) / 60

            return results

        except Exception as e:
            self.logger.error(f"Benchmark failed: {e}")
            return {
                "task_id": self.task_id,
                "session_dir": str(self.session_dir) if self.session_dir else None,
                "success": False,
                "error": str(e),
                "score": 0
            }
        finally:
            # Cleanup
            try:
                await self._stop_task_containers()
            except Exception as e:
                self.logger.warning(f"Error during cleanup: {e}")

    async def _ensure_docker_network(self):
        """Ensure the shared_net network exists for cybench tasks."""
        self.logger.info("Ensuring Docker network 'shared_net' exists")

        try:
            # Check if network exists
            result = subprocess.run(
                ["docker", "network", "inspect", "shared_net"],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                # Create network
                subprocess.run(
                    ["docker", "network", "create", "shared_net"],
                    check=True,
                    capture_output=True
                )
                self.logger.info("Created Docker network 'shared_net'")
            else:
                self.logger.info("Docker network 'shared_net' already exists")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to setup Docker network: {e}")

    async def _start_task_containers(self):
        """Start the Docker containers for the cybench task or run init script for file-based challenges."""
        task_path = self.cybench_dir / self.task_id

        if not task_path.exists():
            raise FileNotFoundError(f"Task directory not found: {task_path}")

        start_docker_script = task_path / "start_docker.sh"
        init_script = task_path / "init_script.sh"

        # Store original working directory before changing to task directory
        original_cwd = os.getcwd()

        # Check if both scripts exist
        if start_docker_script.exists() and init_script.exists():
            # Service-based challenge: run start_docker.sh first, then init_script.sh
            self.logger.info(f"Starting containers and setting up challenge for {self.task_id}")

            try:
                os.chdir(task_path)

                # Run start_docker.sh first
                subprocess.run(["chmod", "+x", "start_docker.sh"], check=True)
                subprocess.run(["./start_docker.sh"], check=True)

                # Wait for containers to be ready
                await asyncio.sleep(5)
                self.logger.info("Containers started successfully")

                # Create challenge files directory for init script
                if self.supervisor_dir.is_absolute():
                    supervisor_abs_dir = self.supervisor_dir
                else:
                    supervisor_abs_dir = Path(original_cwd) / self.supervisor_dir

                self.challenge_files_dir = supervisor_abs_dir / "challenge_files" / f"session_{int(time.time())}"
                self.challenge_files_dir.mkdir(parents=True, exist_ok=True)

                # Run init_script.sh
                subprocess.run(["chmod", "+x", "init_script.sh"], check=True)
                subprocess.run(
                    ["./init_script.sh", str(self.challenge_files_dir.absolute()), "false"],
                    check=True
                )

                self.logger.info(f"Challenge files extracted to: {self.challenge_files_dir}")

            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to start containers or run init script: {e}")
            finally:
                os.chdir(original_cwd)

        elif init_script.exists():
            # File-based challenge with init script
            self.logger.info(f"Setting up file-based challenge for {self.task_id}")

            # Store original working directory before changing to task directory
            original_cwd = os.getcwd()

            # Create challenge files directory relative to supervisor working directory
            # Supervisor runs from ./supervisor/, so we put files in supervisor/challenge_files/
            # Calculate absolute path from original working directory
            if self.supervisor_dir.is_absolute():
                supervisor_abs_dir = self.supervisor_dir
            else:
                supervisor_abs_dir = Path(original_cwd) / self.supervisor_dir

            self.challenge_files_dir = supervisor_abs_dir / "challenge_files" / f"session_{int(time.time())}"
            self.challenge_files_dir.mkdir(parents=True, exist_ok=True)

            # Change to task directory and run init script
            try:
                os.chdir(task_path)

                # Make init script executable and run it
                subprocess.run(["chmod", "+x", "init_script.sh"], check=True)
                subprocess.run(
                    ["./init_script.sh", str(self.challenge_files_dir.absolute()), "false"],
                    check=True
                )

                self.logger.info(f"Challenge files extracted to: {self.challenge_files_dir}")

            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to run init script: {e}")
            finally:
                os.chdir(original_cwd)

        else:
            raise FileNotFoundError(f"No init_script.sh found in: {task_path}")

    async def _stop_task_containers(self):
        """Stop the Docker containers for the cybench task."""
        task_path = self.cybench_dir / self.task_id

        if not task_path.exists():
            return

        stop_docker_script = task_path / "stop_docker.sh"

        if stop_docker_script.exists():
            self.logger.info(f"Stopping containers for {self.task_id}")

            original_cwd = os.getcwd()
            try:
                os.chdir(task_path)

                subprocess.run(["chmod", "+x", "stop_docker.sh"], check=True)
                subprocess.run(["./stop_docker.sh"], check=True)

                self.logger.info("Containers stopped successfully")

            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Error stopping containers: {e}")
            finally:
                os.chdir(original_cwd)
        else:
            # File-based challenge, no containers to stop
            self.logger.info(f"File-based challenge, no containers to stop for {self.task_id}")

    async def _create_supervisor_config(self):
        """Create supervisor configuration for the cybench task."""
        # Load task metadata
        metadata_path = self.cybench_dir / self.task_id / "metadata" / "metadata.json"

        if not metadata_path.exists():
            raise FileNotFoundError(f"Task metadata not found: {metadata_path}")

        with open(metadata_path, 'r') as f:
            metadata = json.load(f)

        # Check if this is a service-based or file-based challenge
        task_path = self.cybench_dir / self.task_id
        compose_file = task_path / "docker-compose.yml"
        is_service_based = compose_file.exists()

        if is_service_based:
            # Service-based challenge - get target host/port info
            target_host = metadata.get("target_host", "localhost:9999")
            if ":" in target_host:
                host, port = target_host.split(":", 1)
                target_url = f"http://{target_host}"
                target_ports = [int(port)]
            else:
                target_url = f"http://{target_host}"
                target_ports = [80, 8080, 9999]  # Common ports
        else:
            # File-based challenge - no target URL
            target_url = None
            target_ports = []

        # Create supervisor config
        config = {
            "name": f"Cybench Challenge: {self.task_id}",
            "description": metadata.get("easy_prompt", f"CTF challenge from {self.task_id}"),
            "level": f"Difficulty {metadata.get('difficulty', 'Unknown')}",
            "win_condition": "flag",
            "mode": "ctf",
            "task_id": self.task_id,
            "submit_function": "cybench_submit",
            "grading_enabled": True,
            "submission_config": {
                "type": "cybench",
                "task_id": self.task_id,
                "cybench_dir": str(self.cybench_dir)
            },
            "working_hours": {
                "start": 0,
                "end": 23,
                "timezone": "UTC"
            },
            **self.supervisor_args
        }

        # Add target info only for service-based challenges
        if is_service_based:
            config["target_url"] = target_url
            config["target_ports"] = target_ports
        else:
            # For file-based challenges, specify the challenge files directory relative to supervisor
            if hasattr(self, 'challenge_files_dir'):
                # Make path relative to supervisor directory
                try:
                    relative_path = self.challenge_files_dir.relative_to(self.supervisor_dir.resolve())
                except ValueError:
                    # If paths aren't in the same hierarchy, use just the directory name
                    relative_path = f"challenge_files/{self.challenge_files_dir.name}"
                config["challenge_files_dir"] = str(relative_path)
                config["description"] += f" Challenge files are located at: {relative_path}"

        # Save config
        config_file = self.session_dir / "supervisor_config.yaml"
        import yaml
        with open(config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

        self.logger.info(f"Supervisor configuration created: {config_file}")

        # Create custom submit function
        await self._create_submit_function()

    async def _create_submit_function(self):
        """Create the cybench submit function."""
        submit_function = '''#!/usr/bin/env python3
"""
Cybench submission function for Trinity supervisor.
"""

def cybench_submit(flag, description=""):
    """Submit a flag for cybench validation."""
    return {
        "flag": flag,
        "description": description
    }
'''

        submit_file = self.session_dir / "cybench_submit.py"
        with open(submit_file, 'w') as f:
            f.write(submit_function)

        self.logger.info(f"Submit function created: {submit_file}")

    async def _run_supervisor(self, duration_minutes: int) -> Dict[str, Any]:
        """Run the Trinity supervisor."""
        config_file = self.session_dir / "supervisor_config.yaml"
        log_file = self.session_dir / "supervisor.log"

        # Build supervisor command with absolute path to config
        cmd = [
            sys.executable, "-m", "codex_supervisor.supervisor",
            "--config-file", str(config_file.absolute()),
            "--duration", str(duration_minutes),
            "--working-hours-start", "0",
            "--working-hours-end", "23",
            "--working-hours-timezone", "UTC"
        ]

        # Add optional supervisor arguments
        if self.supervisor_args.get('supervisor_model'):
            cmd.extend(["--supervisor-model", self.supervisor_args['supervisor_model']])

        if self.supervisor_args.get('use_prompt_generation'):
            cmd.append("--use-prompt-generation")

        if self.supervisor_args.get('codex_binary'):
            cmd.extend(["--codex-binary", self.supervisor_args['codex_binary']])

        self.logger.info(f"Starting supervisor with command: {' '.join(cmd)}")

        # Change to supervisor directory
        original_cwd = os.getcwd()
        try:
            os.chdir(self.supervisor_dir)

            # Run supervisor with real-time output
            process = await asyncio.create_subprocess_exec(*cmd)
            await process.wait()

            return {
                "return_code": process.returncode,
                "stdout": "Output streamed to console",
                "stderr": "Errors streamed to console",
                "success": process.returncode == 0
            }

        finally:
            os.chdir(original_cwd)

    def _grade_results(self) -> Dict[str, Any]:
        """Grade the cybench results."""
        self.logger.info("Grading cybench results...")

        if not self.session_dir:
            return {"error": "No session directory found"}

        # Look for cybench submission results
        supervisor_logs_dir = self.supervisor_dir / "logs"
        supervisor_session_dirs = list(supervisor_logs_dir.glob("supervisor_session_*")) if supervisor_logs_dir.exists() else []
        cybench_submissions_file = None

        if supervisor_session_dirs:
            # Find the most recent supervisor session directory
            latest_supervisor_dir = max(supervisor_session_dirs, key=lambda p: p.stat().st_mtime)
            cybench_submissions_file = latest_supervisor_dir / "cybench_submissions.json"
            self.logger.info(f"Looking for cybench submissions in: {cybench_submissions_file}")
        else:
            self.logger.warning(f"No supervisor session directories found in {supervisor_logs_dir.absolute()}")

        results = {
            "task_id": self.task_id,
            "session_dir": str(self.session_dir),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "submissions": [],
            "success": False,
            "score": 0,
            "total_attempts": 0,
            "correct_attempts": 0
        }

        # Parse cybench submissions
        if cybench_submissions_file and cybench_submissions_file.exists():
            try:
                with open(cybench_submissions_file, 'r') as f:
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
                self.logger.error(f"Error reading cybench submissions: {e}")
                results["error"] = f"Failed to read submissions: {e}"

        # If no submissions file exists, that's an error
        if not cybench_submissions_file or not cybench_submissions_file.exists():
            results["error"] = f"Cybench submissions file not found: {cybench_submissions_file}"
            self.logger.error(f"Cybench submissions file not found: {cybench_submissions_file}")

        # Save grading results
        grading_file = self.session_dir / "grading_results.json"
        try:
            with open(grading_file, 'w') as f:
                json.dump(results, f, indent=2)
            self.logger.info(f"Grading complete. Results saved to: {grading_file}")
        except Exception as e:
            self.logger.error(f"Failed to save grading results: {e}")

        return results


def list_cybench_tasks(cybench_dir: Path) -> List[str]:
    """List all available cybench tasks."""
    task_list_file = cybench_dir / "task_list.txt"

    if not task_list_file.exists():
        print(f"❌ Task list not found: {task_list_file}")
        return []

    with open(task_list_file, 'r') as f:
        tasks = [line.strip() for line in f if line.strip()]

    return tasks


async def run_all_cybench_tasks(args) -> List[Dict[str, Any]]:
    """Run all cybench tasks sequentially."""
    tasks = list_cybench_tasks(Path(args.cybench_dir))

    if not tasks:
        print("❌ No cybench tasks found")
        return []

    print(f"🚀 Running {len(tasks)} cybench tasks...")

    # Prepare supervisor arguments
    supervisor_args = {
        'supervisor_model': args.supervisor_model,
        'prompt_generation_enabled': args.use_prompt_generation,
        'codex_binary': args.codex_binary,
        'working_hours_start': args.working_hours_start,
        'working_hours_end': args.working_hours_end,
        'working_hours_timezone': args.working_hours_timezone,
    }
    supervisor_args = {k: v for k, v in supervisor_args.items() if v is not None}

    all_results = []
    successful_count = 0

    for i, task_id in enumerate(tasks, 1):
        print(f"\n{'='*60}")
        print(f"📊 TASK {i}/{len(tasks)}: {task_id}")
        print(f"{'='*60}")

        try:
            runner = CybenchRunner(
                task_id=task_id,
                supervisor_dir=Path(args.supervisor_dir),
                cybench_dir=Path(args.cybench_dir),
                supervisor_args=supervisor_args
            )

            result = await runner.run_benchmark(args.duration)
            result["task_number"] = i
            result["total_tasks"] = len(tasks)
            all_results.append(result)

            if result.get("success"):
                successful_count += 1
                print(f"✅ {task_id} COMPLETED (Score: {result.get('score', 0)}/100)")
            else:
                print(f"❌ {task_id} FAILED")

        except Exception as e:
            print(f"💥 {task_id} ERROR: {e}")
            all_results.append({
                "task_id": task_id,
                "task_number": i,
                "total_tasks": len(tasks),
                "error": str(e),
                "success": False,
                "score": 0
            })

    # Generate summary report
    print(f"\n{'='*60}")
    print(f"📊 FINAL RESULTS: {successful_count}/{len(tasks)} SUCCESSFUL")
    print(f"{'='*60}")

    for result in all_results:
        status = "✅" if result.get("success") else "❌"
        score = result.get("score", 0)
        print(f"{status} {result['task_id']}: {score}/100")

    # Save summary
    timestamp = int(time.time())
    summary_file = Path(f"logs/cybench_summary_{timestamp}.json")
    summary_file.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_tasks": len(tasks),
        "successful_tasks": successful_count,
        "success_rate": successful_count / len(tasks) if tasks else 0,
        "results": all_results
    }

    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n📄 Summary saved to: {summary_file}")

    return all_results


def main():
    parser = argparse.ArgumentParser(
        description="Run cybench CTF challenges with Trinity supervisor",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("task_id", nargs="?", help="Cybench task ID (e.g., benchmark/project-sekai-ctf/sekaictf-2023/crypto/noisier-crc)")
    parser.add_argument("--supervisor-dir", default="./supervisor", help="Path to supervisor directory")
    parser.add_argument("--cybench-dir", default="../cybench", help="Path to cybench directory")
    parser.add_argument("--duration", type=int, default=30, help="Duration to run in minutes")
    parser.add_argument("--list-tasks", action="store_true", help="List all available cybench tasks")
    parser.add_argument("--run-all", action="store_true", help="Run all cybench tasks")
    parser.add_argument("--supervisor-model", help="Model to use for supervisor")
    parser.add_argument("--use-prompt-generation", action="store_true", help="Enable prompt generation")
    parser.add_argument("--codex-binary", help="Path to codex binary")
    parser.add_argument("--working-hours-start", type=int, default=0, help="Working hours start")
    parser.add_argument("--working-hours-end", type=int, default=23, help="Working hours end")
    parser.add_argument("--working-hours-timezone", default="UTC", help="Working hours timezone")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.list_tasks:
        tasks = list_cybench_tasks(Path(args.cybench_dir))
        print(f"📋 Available Cybench Tasks ({len(tasks)} total):")
        for task in tasks:
            print(f"  {task}")
        return

    if args.run_all:
        asyncio.run(run_all_cybench_tasks(args))
        return

    if not args.task_id:
        parser.error("task_id is required unless using --list-tasks or --run-all")

    async def run_single_task():
        runner = CybenchRunner(
            task_id=args.task_id,
            supervisor_dir=Path(args.supervisor_dir),
            cybench_dir=Path(args.cybench_dir)
        )

        result = await runner.run_benchmark(args.duration)

        print("\n" + "="*50)
        print("CYBENCH RESULTS")
        print("="*50)
        print(json.dumps(result, indent=2))

        if result.get("success"):
            print(f"\n✅ Task completed successfully!")
            print(f"Score: {result.get('score', 0)}/100")
        else:
            print(f"\n❌ Task failed")
            if "error" in result:
                print(f"Error: {result['error']}")

    asyncio.run(run_single_task())


if __name__ == "__main__":
    main()