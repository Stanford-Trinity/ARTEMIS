"""
Registry for managing different submission handlers.
"""

from typing import Dict, Type, Optional
from .base import BaseSubmissionHandler


class SubmissionRegistry:
    """Registry for submission handlers."""
    
    def __init__(self):
        self._handlers: Dict[str, Type[BaseSubmissionHandler]] = {}
    
    def register(self, handler_type: str, handler_class: Type[BaseSubmissionHandler]):
        """Register a submission handler."""
        self._handlers[handler_type] = handler_class
    
    def get_handler_class(self, handler_type: str) -> Optional[Type[BaseSubmissionHandler]]:
        """Get a handler class by type."""
        return self._handlers.get(handler_type)
    
    def get_available_types(self) -> list[str]:
        """Get list of available handler types."""
        return list(self._handlers.keys())
    
    def create_handler(self, handler_type: str, session_dir, config) -> Optional[BaseSubmissionHandler]:
        """Create and return a handler instance."""
        handler_class = self.get_handler_class(handler_type)
        if handler_class:
            return handler_class(session_dir, config)
        return None


# Global registry instance
registry = SubmissionRegistry()