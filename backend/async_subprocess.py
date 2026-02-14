"""
Async subprocess utilities — non-blocking wrappers for subprocess.run().

Prevents event loop blocking when pipeline steps call external CLI tools
(xdotool, xprop, xrandr, git, etc.) from within async context.

Two approaches provided:
1. run_async() — full async subprocess via asyncio.create_subprocess_exec
2. run_in_thread() — offload sync subprocess.run to thread pool (safer migration)

Use run_in_thread() for drop-in replacement where sync _run() is called
from many places (ProcessScanner, WindowManager, ShellAgent).
"""
import asyncio
import subprocess
from typing import List, Optional

import nfo
import structlog

logger = structlog.get_logger()


async def run_async(
    cmd: List[str],
    timeout: float = 3.0,
    cwd: Optional[str] = None,
) -> Optional[str]:
    """
    Run a command asynchronously via asyncio subprocess.

    Non-blocking — does not hold the event loop.

    Args:
        cmd: Command and arguments
        timeout: Maximum execution time
        cwd: Working directory

    Returns:
        stdout string or None on error
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        if proc.returncode == 0:
            return stdout.decode().strip()
        return None
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None
    except Exception:
        return None


async def run_async_shell(
    command: str,
    timeout: float = 10.0,
    cwd: Optional[str] = None,
    max_output: int = 2000,
) -> dict:
    """
    Run a shell command asynchronously.

    Args:
        command: Shell command string
        timeout: Maximum execution time
        cwd: Working directory
        max_output: Max output length

    Returns:
        Dict with stdout, stderr, returncode, timed_out
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return {
            "stdout": stdout.decode()[:max_output],
            "stderr": stderr.decode()[:max_output],
            "returncode": proc.returncode,
            "timed_out": False,
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "returncode": -1,
            "timed_out": True,
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "timed_out": False,
        }


def run_in_thread_sync(
    cmd: List[str],
    timeout: float = 3.0,
    cwd: Optional[str] = None,
) -> Optional[str]:
    """
    Synchronous subprocess.run() — existing behavior.
    Used as the target for asyncio.to_thread().
    """
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


async def run_in_thread(
    cmd: List[str],
    timeout: float = 3.0,
    cwd: Optional[str] = None,
) -> Optional[str]:
    """
    Offload subprocess.run() to a thread — non-blocking drop-in replacement.

    Same interface as sync _run() but doesn't block the event loop.
    Use this as a safe migration path before full async subprocess conversion.
    """
    return await asyncio.to_thread(run_in_thread_sync, cmd, timeout, cwd)
