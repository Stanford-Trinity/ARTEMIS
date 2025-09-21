"""
Modular submission system for CTF benchmarks.

This module provides a flexible framework for handling CTF flag submissions
while maintaining existing Slack functionality for vulnerability reports.
"""

from .base import BaseSubmissionHandler, SubmissionResult
from .registry import SubmissionRegistry
from .ctf import CTFSubmissionHandler
from .vulnerability import VulnerabilitySubmissionHandler
from .xbow_validation import XBOWValidationSubmissionHandler

__all__ = [
    'BaseSubmissionHandler',
    'SubmissionResult',
    'SubmissionRegistry',
    'CTFSubmissionHandler',
    'VulnerabilitySubmissionHandler',
    'XBOWValidationSubmissionHandler'
]