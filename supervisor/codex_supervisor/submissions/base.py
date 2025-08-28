"""
Base submission handler interface for benchmark mode.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional
from pathlib import Path


@dataclass
class SubmissionResult:
    """Result of a submission attempt."""
    success: bool
    message: str
    score: Optional[float] = None
    details: Optional[Dict[str, Any]] = None


class BaseSubmissionHandler(ABC):
    """Base class for all submission handlers."""
    
    def __init__(self, session_dir: Path, config: Dict[str, Any]):
        self.session_dir = session_dir
        self.config = config
        self.submission_log = session_dir / f"{self.get_handler_type()}_submissions.log"
    
    @abstractmethod
    def get_handler_type(self) -> str:
        """Return the type identifier for this handler."""
        pass
    
    @abstractmethod
    async def submit(self, submission_data: Dict[str, Any]) -> SubmissionResult:
        """Handle the submission and return result."""
        pass
    
    @abstractmethod
    def get_submission_schema(self) -> Dict[str, Any]:
        """Return the OpenAI function schema for this submission type."""
        pass
    
    async def log_submission(self, submission_data: Dict[str, Any], result: SubmissionResult) -> None:
        """Log the submission attempt."""
        import aiofiles
        import json
        from datetime import datetime, timezone
        
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "submission_data": submission_data,
            "result": {
                "success": result.success,
                "message": result.message,
                "score": result.score,
                "details": result.details
            }
        }
        
        try:
            async with aiofiles.open(self.submission_log, 'a') as f:
                await f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            import logging
            logging.error(f"Failed to log submission: {e}")