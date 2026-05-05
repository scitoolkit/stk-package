"""
Tool execution logger for SciToolkit.

Provides thread-safe logging of tool executions with:
- Daily log files
- Structured JSONL tool call log
- Auto-rotation (30 day retention)
- Performance metrics
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from collections import deque

from ..config import LOGS_DIR


@dataclass
class LogEntry:
    """Single log entry."""
    timestamp: str
    toolkit: str
    tool: str
    message: str
    level: str  # info, warning, error, success

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class ToolCallRecord:
    """Record of a tool call for structured logging."""
    timestamp: str
    toolkit: str
    tool: str
    args: Dict[str, Any]  # Sanitized arguments
    duration: Optional[float] = None
    success: Optional[bool] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class ToolLogger:
    """
    Thread-safe logger for tool execution.

    Features:
    - Daily log files (~/.scitoolkit/logs/YYYY-MM-DD.log)
    - Structured JSONL tool call log (tool_calls.jsonl)
    - In-memory buffer for TUI (last 1000 entries)
    - Auto-rotation (keeps last 30 days)
    """

    def __init__(self, max_memory_logs: int = 1000):
        """
        Initialize logger.

        Args:
            max_memory_logs: Maximum log entries to keep in memory
        """
        self._lock = threading.Lock()
        self._memory_logs = deque(maxlen=max_memory_logs)
        self._active_calls: Dict[str, ToolCallRecord] = {}  # tool_id -> record

        # Ensure logs directory exists
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Rotate old logs on startup
        self._rotate_logs()

    def log_tool_start(self, toolkit: str, tool: str, args: Dict[str, Any]) -> str:
        """
        Log the start of a tool execution.

        Args:
            toolkit: Toolkit name
            tool: Tool name
            args: Tool arguments (will be sanitized)

        Returns:
            tool_id: Unique ID for this tool call
        """
        timestamp = datetime.now().isoformat()
        tool_id = f"{toolkit}::{tool}::{timestamp}"

        # Sanitize arguments (remove potential secrets)
        sanitized_args = self._sanitize_args(args)

        # Create record
        record = ToolCallRecord(
            timestamp=timestamp,
            toolkit=toolkit,
            tool=tool,
            args=sanitized_args
        )

        with self._lock:
            self._active_calls[tool_id] = record

            # Log to daily file
            self._write_daily_log(
                f"[{timestamp}] {toolkit}::{tool} - Starting",
                level="info"
            )

            # Add to memory
            entry = LogEntry(
                timestamp=timestamp,
                toolkit=toolkit,
                tool=tool,
                message="Starting",
                level="info"
            )
            self._memory_logs.append(entry)

        return tool_id

    def log_tool_output(
        self,
        toolkit: str,
        tool: str,
        message: str,
        level: str = "info"
    ):
        """
        Log output from a tool during execution.

        Args:
            toolkit: Toolkit name
            tool: Tool name
            message: Output message
            level: Log level (info, warning, error, success)
        """
        timestamp = datetime.now().isoformat()

        with self._lock:
            # Log to daily file
            self._write_daily_log(
                f"[{timestamp}] {toolkit}::{tool} - {message}",
                level=level
            )

            # Add to memory
            entry = LogEntry(
                timestamp=timestamp,
                toolkit=toolkit,
                tool=tool,
                message=message,
                level=level
            )
            self._memory_logs.append(entry)

    def log_tool_complete(
        self,
        tool_id: str,
        duration: float,
        success: bool,
        error: Optional[str] = None
    ):
        """
        Log the completion of a tool execution.

        Args:
            tool_id: Tool ID from log_tool_start()
            duration: Execution duration in seconds
            success: Whether execution succeeded
            error: Error message if failed
        """
        timestamp = datetime.now().isoformat()

        with self._lock:
            # Get the active call record
            if tool_id not in self._active_calls:
                # Tool wasn't started properly, create minimal record
                parts = tool_id.split("::")
                if len(parts) >= 2:
                    record = ToolCallRecord(
                        timestamp=timestamp,
                        toolkit=parts[0],
                        tool=parts[1],
                        args={},
                        duration=duration,
                        success=success,
                        error=error
                    )
                else:
                    return  # Invalid tool_id
            else:
                record = self._active_calls[tool_id]
                record.duration = duration
                record.success = success
                record.error = error
                del self._active_calls[tool_id]

            # Write to structured log
            self._write_tool_call_log(record)

            # Log to daily file
            status = "✓ Completed" if success else "✗ Failed"
            msg = f"[{timestamp}] {record.toolkit}::{record.tool} - {status} in {duration:.2f}s"
            if error:
                msg += f" - {error}"

            self._write_daily_log(msg, level="success" if success else "error")

            # Add to memory
            entry = LogEntry(
                timestamp=timestamp,
                toolkit=record.toolkit,
                tool=record.tool,
                message=f"{status} in {duration:.2f}s" + (f" - {error}" if error else ""),
                level="success" if success else "error"
            )
            self._memory_logs.append(entry)

    def get_recent_logs(self, limit: Optional[int] = None) -> List[LogEntry]:
        """
        Get recent log entries from memory.

        Args:
            limit: Maximum number of entries (None = all)

        Returns:
            List of log entries (most recent last)
        """
        with self._lock:
            logs = list(self._memory_logs)
            if limit:
                logs = logs[-limit:]
            return logs

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize arguments to remove potential secrets.

        Args:
            args: Original arguments

        Returns:
            Sanitized arguments
        """
        sanitized = {}

        # List of keys that might contain secrets
        secret_keys = {
            'password', 'passwd', 'pwd',
            'token', 'api_key', 'apikey', 'key',
            'secret', 'auth', 'credential'
        }

        for key, value in args.items():
            key_lower = key.lower()

            # Check if key name suggests a secret
            if any(secret in key_lower for secret in secret_keys):
                sanitized[key] = "***REDACTED***"
            else:
                # Keep the value but limit size for large objects
                if isinstance(value, (str, int, float, bool, type(None))):
                    sanitized[key] = value
                elif isinstance(value, (list, tuple)):
                    # Truncate long lists
                    if len(value) > 10:
                        sanitized[key] = f"<{type(value).__name__} of {len(value)} items>"
                    else:
                        sanitized[key] = value
                elif isinstance(value, dict):
                    # Recursively sanitize nested dicts
                    sanitized[key] = self._sanitize_args(value)
                else:
                    # Other types: just show type
                    sanitized[key] = f"<{type(value).__name__}>"

        return sanitized

    def _write_daily_log(self, message: str, level: str = "info"):
        """
        Write to daily log file.

        Args:
            message: Log message
            level: Log level
        """
        # Get today's log file
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = LOGS_DIR / f"{today}.log"

        # Write line
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                level_prefix = {
                    'info': '[INFO]',
                    'warning': '[WARN]',
                    'error': '[ERROR]',
                    'success': '[SUCCESS]'
                }.get(level, '[INFO]')

                f.write(f"{level_prefix} {message}\n")
        except Exception:
            # Fail silently - don't break execution over logging
            pass

    def _write_tool_call_log(self, record: ToolCallRecord):
        """
        Write tool call record to structured JSONL log.

        Args:
            record: Tool call record
        """
        log_file = LOGS_DIR / "tool_calls.jsonl"

        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record.to_dict()) + '\n')
        except Exception:
            # Fail silently
            pass

    def _rotate_logs(self):
        """Remove log files older than 30 days."""
        try:
            cutoff_date = datetime.now() - timedelta(days=30)

            for log_file in LOGS_DIR.glob("*.log"):
                # Parse date from filename (YYYY-MM-DD.log)
                try:
                    date_str = log_file.stem  # Get filename without extension
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")

                    if file_date < cutoff_date:
                        log_file.unlink()
                except (ValueError, OSError):
                    # Skip files that don't match pattern or can't be deleted
                    continue
        except Exception:
            # Fail silently - rotation is not critical
            pass


# Global logger instance
_logger: Optional[ToolLogger] = None


def get_logger() -> ToolLogger:
    """
    Get the global logger instance.

    Returns:
        ToolLogger instance
    """
    global _logger
    if _logger is None:
        _logger = ToolLogger()
    return _logger
