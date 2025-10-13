#!/usr/bin/env python3
"""Working hours utility module for supervisor."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Tuple, Optional
import pytz


class WorkingHoursManager:
    """Manages working hours logic and sleep calculations."""
    
    def __init__(self, start_hour: int = 9, end_hour: int = 17, timezone_str: str = "US/Pacific"):
        """
        Initialize working hours manager.
        
        Args:
            start_hour: Start of working hours (24-hour format, e.g., 9 for 9 AM)
            end_hour: End of working hours (24-hour format, e.g., 17 for 5 PM)
            timezone_str: Timezone string (e.g., "US/Pacific", "UTC", "US/Eastern")
        """
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.timezone_str = timezone_str
        
        try:
            self.timezone = pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            logging.warning(f"Unknown timezone '{timezone_str}', falling back to UTC")
            self.timezone = pytz.UTC
            self.timezone_str = "UTC"
        
        # Validate hours
        if not (0 <= start_hour <= 23):
            raise ValueError(f"start_hour must be between 0-23, got {start_hour}")
        if not (0 <= end_hour <= 23):
            raise ValueError(f"end_hour must be between 0-23, got {end_hour}")
        if start_hour >= end_hour:
            raise ValueError(f"start_hour ({start_hour}) must be less than end_hour ({end_hour})")
        
        logging.info(f"🕐 Working hours: {self._format_time(start_hour)} - {self._format_time(end_hour)} {timezone_str}")
    
    def _format_time(self, hour: int) -> str:
        """Format hour in 12-hour format."""
        if hour == 0:
            return "12:00 AM"
        elif hour < 12:
            return f"{hour}:00 AM"
        elif hour == 12:
            return "12:00 PM"
        else:
            return f"{hour - 12}:00 PM"
    
    def is_within_working_hours(self, dt: Optional[datetime] = None) -> bool:
        """
        Check if current time (or provided datetime) is within working hours.
        
        Args:
            dt: Datetime to check, defaults to current time
            
        Returns:
            True if within working hours, False otherwise
        """
        if dt is None:
            dt = datetime.now(self.timezone)
        elif dt.tzinfo is None:
            # Assume UTC if no timezone info
            dt = pytz.UTC.localize(dt)
        
        # Convert to working hours timezone
        local_dt = dt.astimezone(self.timezone)
        current_hour = local_dt.hour
        
        return self.start_hour <= current_hour < self.end_hour
    
    def get_next_working_time(self, dt: Optional[datetime] = None) -> datetime:
        """
        Get the next time working hours start.
        
        Args:
            dt: Reference datetime, defaults to current time
            
        Returns:
            Datetime when working hours next start
        """
        if dt is None:
            dt = datetime.now(self.timezone)
        elif dt.tzinfo is None:
            # Assume UTC if no timezone info
            dt = pytz.UTC.localize(dt)
        
        # Convert to working hours timezone
        local_dt = dt.astimezone(self.timezone)
        
        # If we're already in working hours, return current time
        if self.is_within_working_hours(dt):
            return dt
        
        # Calculate next working time
        today_start = local_dt.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
        
        if local_dt.hour < self.start_hour:
            # Before working hours today - start today
            next_working = today_start
        else:
            # After working hours today - start tomorrow
            next_working = today_start + timedelta(days=1)
        
        return next_working
    
    def calculate_sleep_duration(self, dt: Optional[datetime] = None) -> Tuple[timedelta, datetime]:
        """
        Calculate how long to sleep until working hours start.
        
        Args:
            dt: Reference datetime, defaults to current time
            
        Returns:
            Tuple of (sleep_duration, wake_time)
        """
        if dt is None:
            dt = datetime.now(self.timezone)
        elif dt.tzinfo is None:
            # Assume UTC if no timezone info
            dt = pytz.UTC.localize(dt)
        
        if self.is_within_working_hours(dt):
            return timedelta(0), dt
        
        next_working = self.get_next_working_time(dt)
        sleep_duration = next_working - dt
        
        return sleep_duration, next_working
    
    async def wait_for_working_hours(self, dt: Optional[datetime] = None) -> Tuple[timedelta, datetime]:
        """
        Sleep until working hours start if currently outside working hours.
        
        Args:
            dt: Reference datetime, defaults to current time
            
        Returns:
            Tuple of (actual_sleep_duration, wake_time)
        """
        if self.is_within_working_hours(dt):
            return timedelta(0), dt or datetime.now(self.timezone)
        
        sleep_duration, wake_time = self.calculate_sleep_duration(dt)
        sleep_seconds = sleep_duration.total_seconds()
        
        if sleep_seconds > 0:
            local_wake_time = wake_time.astimezone(self.timezone)
            logging.info(f"😴 Outside working hours, sleeping until {local_wake_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logging.info(f"💤 Sleep duration: {self._format_duration(sleep_duration)}")
            
            await asyncio.sleep(sleep_seconds)
            
            actual_wake_time = datetime.now(self.timezone)
            logging.info(f"⏰ Woke up at {actual_wake_time.strftime('%Y-%m-%d %H:%M:%S %Z')} - resuming supervisor")
            
            return sleep_duration, actual_wake_time
        
        return timedelta(0), dt or datetime.now(self.timezone)
    
    def _format_duration(self, duration: timedelta) -> str:
        """Format duration in human-readable format."""
        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 and hours == 0:  # Only show seconds if less than an hour
            parts.append(f"{seconds}s")
        
        return " ".join(parts) if parts else "0s"
    
    def get_status_info(self) -> dict:
        """Get current working hours status information."""
        now = datetime.now(self.timezone)
        is_working = self.is_within_working_hours(now)
        
        if is_working:
            # Calculate time until end of working hours
            today_end = now.replace(hour=self.end_hour, minute=0, second=0, microsecond=0)
            time_until_end = today_end - now
            
            return {
                "status": "working_hours",
                "in_working_hours": True,
                "current_time": now.strftime('%Y-%m-%d %H:%M:%S %Z'),
                "working_hours": f"{self._format_time(self.start_hour)} - {self._format_time(self.end_hour)} {self.timezone_str}",
                "time_until_end": self._format_duration(time_until_end)
            }
        else:
            sleep_duration, wake_time = self.calculate_sleep_duration(now)
            
            return {
                "status": "outside_working_hours",
                "in_working_hours": False,
                "current_time": now.strftime('%Y-%m-%d %H:%M:%S %Z'),
                "working_hours": f"{self._format_time(self.start_hour)} - {self._format_time(self.end_hour)} {self.timezone_str}",
                "next_working_time": wake_time.astimezone(self.timezone).strftime('%Y-%m-%d %H:%M:%S %Z'),
                "sleep_duration": self._format_duration(sleep_duration)
            }
