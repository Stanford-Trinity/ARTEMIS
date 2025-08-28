"""
CTF submission handler for flag submissions.
"""

import json
import logging
from typing import Dict, Any
from datetime import datetime, timezone
from .base import BaseSubmissionHandler, SubmissionResult


class CTFSubmissionHandler(BaseSubmissionHandler):
    """Handler for CTF flag submissions."""
    
    def get_handler_type(self) -> str:
        return "ctf"
    
    def get_submission_schema(self) -> Dict[str, Any]:
        """Return the OpenAI function schema for CTF submissions."""
        return {
            "type": "function",
            "function": {
                "name": "submit_ctf_flag",
                "description": "Submit a CTF flag for a specific challenge",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "challenge_name": {
                            "type": "string",
                            "description": "Name of the CTF challenge"
                        },
                        "flag": {
                            "type": "string", 
                            "description": "The CTF flag (e.g., flag{...} or CTF{...})"
                        }
                    },
                    "required": ["challenge_name", "flag"]
                }
            }
        }
    
    async def submit(self, submission_data: Dict[str, Any]) -> SubmissionResult:
        """Submit CTF flag and save to tracking file."""
        challenge_name = submission_data.get("challenge_name", "")
        flag = submission_data.get("flag", "")
        
        if not challenge_name or not flag:
            result = SubmissionResult(
                success=False,
                message="Both challenge_name and flag are required"
            )
            await self.log_submission(submission_data, result)
            return result
        
        # Save to CTF submissions file
        await self._save_flag_submission(challenge_name, flag)
        
        result = SubmissionResult(
            success=True,
            message=f"✅ Flag submitted for challenge '{challenge_name}': {flag}",
            details={
                "challenge": challenge_name,
                "flag": flag
            }
        )
        
        await self.log_submission(submission_data, result)
        logging.info(f"📝 CTF flag submitted: {challenge_name} = {flag}")
        
        return result
    
    async def _save_flag_submission(self, challenge_name: str, flag: str):
        """Save flag-challenge pair to submissions file."""
        import aiofiles
        
        submissions_file = self.session_dir / "ctf_submissions.json"
        
        # Load existing submissions
        submissions = {}
        if submissions_file.exists():
            try:
                async with aiofiles.open(submissions_file, 'r') as f:
                    content = await f.read()
                    if content.strip():
                        submissions = json.loads(content)
            except Exception as e:
                logging.error(f"Error reading existing CTF submissions: {e}")
        
        # Add new submission with timestamp
        submissions[challenge_name] = {
            "flag": flag,
            "submitted_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Save back to file
        try:
            async with aiofiles.open(submissions_file, 'w') as f:
                await f.write(json.dumps(submissions, indent=2))
            logging.info(f"📄 Saved CTF submission to {submissions_file}")
        except Exception as e:
            logging.error(f"❌ Failed to save CTF submission: {e}")