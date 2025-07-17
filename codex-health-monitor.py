#!/usr/bin/env python3
"""
Codex Health Monitor - Standalone service to monitor codex agent health
Monitors both process existence and heartbeat file activity
"""

import json
import time
import os
import sys
import psutil
import requests
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any

class CodexHealthMonitor:
    def __init__(self, config_file: str):
        self.config = self.load_config(config_file)
        self.start_time = datetime.now()
        self.last_slack_update = datetime.min
        self.consecutive_failures = 0
        
    def load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        default_config = {
            "check_interval_seconds": 30,
            "slack_update_interval_minutes": 10,
            "slack_webhook_url": None,
            "slack_channel": "#codex-health",
            "max_idle_minutes": 5,
            "heartbeat_file": "./codex-rs/logs/heartbeat.json",
            "codex_process_name": "codex",
            "verbose": True
        }
        
        if os.path.exists(config_file):
            with open(config_file) as f:
                user_config = json.load(f)
                default_config.update(user_config)
        else:
            print(f"⚠️  Config file {config_file} not found, using defaults")
            
        return default_config
    
    def check_process_health(self, pid: Optional[int] = None) -> tuple[bool, str]:
        """Check if codex process is running"""
        try:
            if pid:
                # Check specific PID
                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                        return True, f"Process {pid} is running"
                    else:
                        return False, f"Process {pid} exists but is zombie/dead"
                else:
                    return False, f"Process {pid} not found"
            else:
                # Search by process name
                process_name = self.config["codex_process_name"]
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        # Check if process name contains codex or command line contains codex
                        if (process_name.lower() in proc.info['name'].lower() or 
                            any(process_name in arg for arg in proc.info['cmdline'] or [])):
                            return True, f"Found codex process: PID {proc.info['pid']}"
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                        
                return False, f"No process found matching '{process_name}'"
                
        except Exception as e:
            return False, f"Error checking process: {e}"
    
    def check_heartbeat_health(self) -> tuple[bool, str, Dict[str, Any]]:
        """Check heartbeat file for recent activity"""
        heartbeat_file = self.config["heartbeat_file"]
        
        # Check primary location first
        if not os.path.exists(heartbeat_file):
            # Fall back to backup location if primary doesn't exist
            backup_heartbeat_file = os.path.expanduser("~/codex-logs-backup/latest_session_heartbeat.json")
            if os.path.exists(backup_heartbeat_file):
                heartbeat_file = backup_heartbeat_file
                if self.config["verbose"]:
                    print(f"⚠️  Using backup heartbeat: {backup_heartbeat_file}")
            else:
                return False, f"Heartbeat file not found in primary ({self.config['heartbeat_file']}) or backup ({backup_heartbeat_file}) locations", {}
        
        try:
            with open(heartbeat_file) as f:
                heartbeat_data = json.load(f)
            
            # Check timestamp - handle both timezone-aware and naive timestamps
            timestamp_str = heartbeat_data["timestamp"]
            if timestamp_str.endswith('Z'):
                # UTC timestamp with Z suffix
                last_heartbeat = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            elif '+' in timestamp_str or timestamp_str.count('-') > 2:
                # Timezone-aware timestamp
                last_heartbeat = datetime.fromisoformat(timestamp_str)
            else:
                # Assume local naive timestamp
                last_heartbeat = datetime.fromisoformat(timestamp_str)
            
            # Convert to UTC for comparison if timezone-aware
            if last_heartbeat.tzinfo is not None:
                last_heartbeat_utc = last_heartbeat.astimezone(timezone.utc).replace(tzinfo=None)
                time_since_heartbeat = datetime.utcnow() - last_heartbeat_utc
            else:
                time_since_heartbeat = datetime.now() - last_heartbeat
                
            max_idle = timedelta(minutes=self.config["max_idle_minutes"])
            
            if time_since_heartbeat > max_idle:
                return False, f"No heartbeat for {time_since_heartbeat.total_seconds():.0f}s (max: {max_idle.total_seconds():.0f}s)", heartbeat_data
            else:
                return True, f"Healthy heartbeat {time_since_heartbeat.total_seconds():.0f}s ago", heartbeat_data
                
        except Exception as e:
            return False, f"Error reading heartbeat: {e}", {}
    
    def get_overall_health(self) -> Dict[str, Any]:
        """Get overall health status combining process and heartbeat checks"""
        process_healthy, process_msg = self.check_process_health()
        heartbeat_healthy, heartbeat_msg, heartbeat_data = self.check_heartbeat_health()
        
        overall_healthy = process_healthy and heartbeat_healthy
        uptime = datetime.now() - self.start_time
        
        status = {
            "overall_healthy": overall_healthy,
            "process_healthy": process_healthy,
            "heartbeat_healthy": heartbeat_healthy,
            "process_message": process_msg,
            "heartbeat_message": heartbeat_msg,
            "uptime_seconds": int(uptime.total_seconds()),
            "monitor_start_time": self.start_time.isoformat(),
            "last_check": datetime.now().isoformat(),
            "consecutive_failures": self.consecutive_failures,
            "heartbeat_data": heartbeat_data
        }
        
        return status
    
    def send_slack_notification(self, status: Dict[str, Any]) -> bool:
        """Send status update to Slack"""
        webhook_url = self.config.get("slack_webhook_url")
        if not webhook_url:
            if self.config["verbose"]:
                print("📢 Slack webhook not configured, skipping notification")
            return True
        
        try:
            uptime_hours = status["uptime_seconds"] / 3600
            
            # Build message in the new format
            # Status is HEALTHY if process is found, regardless of heartbeat
            status_text = "HEALTHY" if status['process_healthy'] else "UNHEALTHY"
            status_emoji = ":white_check_mark:" if status['process_healthy'] else ":x:"
            
            message_parts = [
                f"{status_emoji} Codex Agent Status: {status_text}",
                f"• Monitor uptime: {uptime_hours:.1f} hours",
                f"• Process: {':white_check_mark:' if status['process_healthy'] else ':x:'} {status['process_message']}",
                f"• Heartbeat: {':white_check_mark:' if status['heartbeat_data'] else ':x:'} Last save time {status['heartbeat_message'].split()[-2] if 'ago' in status['heartbeat_message'] else status['heartbeat_message'].split()[3] if 'No heartbeat for' in status['heartbeat_message'] else 'unknown'} ago"
            ]
            
            # Add heartbeat details if available
            if status["heartbeat_data"]:
                hb = status["heartbeat_data"]
                if "iteration" in hb:
                    message_parts.append(f"• Iterations completed: {hb['iteration']}")
                if "session_timestamp" in hb:
                    message_parts.append(f"• Session: {hb['session_timestamp']}")
            
            if not status['process_healthy']:
                message_parts.append(f"• Consecutive failures: {status['consecutive_failures']}")
            
            message_text = "\n".join(message_parts)
            
            payload = {
                "username": "🤖 Codex Health Monitor",
                "text": message_text
            }
            
            response = requests.post(webhook_url, json=payload, timeout=10)
            
            if response.status_code == 200:
                if self.config["verbose"]:
                    print("📢 Slack notification sent successfully")
                return True
            else:
                print(f"❌ Slack notification failed: {response.status_code} {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error sending Slack notification: {e}")
            return False
    
    def should_send_slack_update(self) -> bool:
        """Check if it's time to send a Slack update"""
        interval = timedelta(minutes=self.config["slack_update_interval_minutes"])
        return datetime.now() - self.last_slack_update > interval
    
    def run_health_check(self) -> None:
        """Run a single health check cycle"""
        status = self.get_overall_health()
        
        if status["overall_healthy"]:
            self.consecutive_failures = 0
            health_emoji = "✅"
        else:
            self.consecutive_failures += 1
            health_emoji = "❌"
        
        # Always log health status
        if self.config["verbose"] or not status["overall_healthy"]:
            print(f"{health_emoji} Health Check ({datetime.now().strftime('%H:%M:%S')})")
            print(f"  Process: {status['process_message']}")
            print(f"  Heartbeat: {status['heartbeat_message']}")
            if status["heartbeat_data"] and "iteration" in status["heartbeat_data"]:
                print(f"  Iteration: {status['heartbeat_data']['iteration']}")
            if not status["overall_healthy"]:
                print(f"  Failures: {status['consecutive_failures']}")
        
        # Send Slack update if it's time or if unhealthy
        if self.should_send_slack_update() or not status["overall_healthy"]:
            if self.send_slack_notification(status):
                self.last_slack_update = datetime.now()
    
    def run(self) -> None:
        """Main monitoring loop"""
        print("🏥 Codex Health Monitor starting...")
        print(f"📋 Config: {json.dumps(self.config, indent=2)}")
        
        try:
            while True:
                self.run_health_check()
                time.sleep(self.config["check_interval_seconds"])
                
        except KeyboardInterrupt:
            print("\n🏥 Health monitor stopping...")
        except Exception as e:
            print(f"❌ Health monitor error: {e}")
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Codex Health Monitor")
    parser.add_argument("--config", "-c", default="health-monitor-config.json",
                       help="Path to configuration JSON file")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    monitor = CodexHealthMonitor(args.config)
    if args.verbose:
        monitor.config["verbose"] = True
        
    monitor.run()

if __name__ == "__main__":
    main()