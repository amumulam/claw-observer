"""
Log Reader Module

Reads log streams from various sources:
- File (tail -F)
- Docker container (docker logs -f)
- Systemd journal (journalctl -f)
- Standard input
"""

import asyncio
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Callable, Awaitable
import logging

logger = logging.getLogger(__name__)


class LogReader(ABC):
    """Abstract base class for log readers."""

    @abstractmethod
    async def read_lines(self) -> AsyncIterator[str]:
        """
        Read log lines asynchronously.

        Yields:
            Log lines as strings
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the log reader and cleanup resources."""
        pass


class FileLogReader(LogReader):
    """
    Reads logs from a file using tail -F.

    Handles log rotation automatically.
    """

    def __init__(self, file_path: str, buffer_size: int = 1024):
        self.file_path = file_path
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None

    async def read_lines(self) -> AsyncIterator[str]:
        """Start tail -F and read lines."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                "tail",
                "-F",
                self.file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout:
                buffer = ""
                while True:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        break

                    buffer += chunk.decode("utf-8", errors="replace")

                    # Process complete lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():  # Skip empty lines
                            yield line

        except FileNotFoundError:
            logger.error(f"tail command not found. File path: {self.file_path}")
            raise
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            raise

    async def close(self) -> None:
        """Terminate the tail process."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error terminating tail process: {e}")
                self._process.kill()


class DockerLogReader(LogReader):
    """
    Reads logs from a Docker container.

    Uses `docker logs -f` to stream logs.
    """

    def __init__(
        self,
        container_name: str,
        tail_lines: int = 0,  # 0 = all existing lines
        buffer_size: int = 1024,
    ):
        self.container_name = container_name
        self.tail_lines = tail_lines
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None

    async def read_lines(self) -> AsyncIterator[str]:
        """Start docker logs -f and read lines."""
        cmd = ["docker", "logs", "-f"]
        if self.tail_lines > 0:
            cmd.extend(["--tail", str(self.tail_lines)])
        cmd.append(self.container_name)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
            )

            if self._process.stdout:
                buffer = ""
                while True:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        break

                    buffer += chunk.decode("utf-8", errors="replace")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            yield line

        except FileNotFoundError:
            logger.error("docker command not found. Is Docker installed?")
            raise
        except Exception as e:
            logger.error(f"Error reading Docker logs: {e}")
            raise

    async def close(self) -> None:
        """Terminate the docker logs process."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error terminating docker logs process: {e}")
                self._process.kill()


class JournalctlLogReader(LogReader):
    """
    Reads logs from systemd journal.

    Uses `journalctl -f` to stream logs.
    """

    def __init__(
        self,
        unit: str,
        buffer_size: int = 1024,
    ):
        self.unit = unit
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None

    async def read_lines(self) -> AsyncIterator[str]:
        """Start journalctl -f and read lines."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                "journalctl",
                "-f",
                "-u",
                self.unit,
                "--output=cat",  # No metadata, just the log message
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout:
                buffer = ""
                while True:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        break

                    buffer += chunk.decode("utf-8", errors="replace")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            yield line

        except FileNotFoundError:
            logger.error("journalctl command not found. Is systemd installed?")
            raise
        except Exception as e:
            logger.error(f"Error reading journalctl logs: {e}")
            raise

    async def close(self) -> None:
        """Terminate the journalctl process."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error terminating journalctl process: {e}")
                self._process.kill()


class StdinLogReader(LogReader):
    """
    Reads logs from standard input.

    Useful for piping logs directly to the sidecar.
    """

    def __init__(self, buffer_size: int = 1024):
        self.buffer_size = buffer_size
        self._running = True

    async def read_lines(self) -> AsyncIterator[str]:
        """Read lines from stdin."""
        loop = asyncio.get_event_loop()
        buffer = ""

        while self._running:
            try:
                # Read from stdin in a non-blocking way
                chunk = await loop.run_in_executor(
                    None,
                    lambda: sys.stdin.read(self.buffer_size),
                )
                if not chunk:
                    break

                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        yield line

            except Exception as e:
                logger.error(f"Error reading stdin: {e}")
                break

    async def close(self) -> None:
        """Stop reading."""
        self._running = False


class AutoLogReader(LogReader):
    """
    Automatically detects the best log reader based on environment.

    Detection order:
    1. Docker container (if docker command available and container exists)
    2. File (if log file exists)
    3. Journalctl (if systemd unit exists)
    4. Fallback to file reader
    """

    def __init__(
        self,
        log_source: str,
        log_path: str = "/var/log/openclaw/gateway.log",
        docker_container: str = "openclaw-gateway",
        systemd_unit: str = "openclaw-gateway",
    ):
        self.log_source = log_source
        self.log_path = log_path
        self.docker_container = docker_container
        self.systemd_unit = systemd_unit
        self._reader: Optional[LogReader] = None

    async def _detect_reader(self) -> LogReader:
        """Detect and create the appropriate log reader."""
        source = self.log_source.lower()

        # Explicit source specification
        if source.startswith("file:"):
            return FileLogReader(source[5:])
        elif source.startswith("docker:"):
            return DockerLogReader(source[7:])
        elif source.startswith("journalctl:"):
            return JournalctlLogReader(source[11:])
        elif source == "stdin":
            return StdinLogReader()

        # Auto detection
        if source == "auto":
            # Try Docker first
            if await self._check_docker():
                logger.info(f"Using Docker log reader for container: {self.docker_container}")
                return DockerLogReader(self.docker_container)

            # Try journalctl
            if await self._check_journalctl():
                logger.info(f"Using journalctl log reader for unit: {self.systemd_unit}")
                return JournalctlLogReader(self.systemd_unit)

            # Try file
            if await self._check_file():
                logger.info(f"Using file log reader: {self.log_path}")
                return FileLogReader(self.log_path)

            # Fallback to stdin
            logger.warning("No log source detected, falling back to stdin")
            return StdinLogReader()

        # Default: file reader
        logger.info(f"Using default file log reader: {self.log_path}")
        return FileLogReader(self.log_path)

    async def _check_docker(self) -> bool:
        """Check if Docker is available and container exists."""
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "ps",
                "--format",
                "{{.Names}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
            containers = stdout.decode().strip().split("\n")
            return self.docker_container in containers
        except Exception:
            return False

    async def _check_journalctl(self) -> bool:
        """Check if journalctl is available and unit exists."""
        try:
            process = await asyncio.create_subprocess_exec(
                "journalctl",
                "-u",
                self.systemd_unit,
                "-n",
                "1",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=5.0)
            return process.returncode == 0
        except Exception:
            return False

    async def _check_file(self) -> bool:
        """Check if log file exists."""
        import os
        return os.path.exists(self.log_path)

    async def read_lines(self) -> AsyncIterator[str]:
        """Initialize reader and start reading."""
        if self._reader is None:
            self._reader = await self._detect_reader()

        async for line in self._reader.read_lines():
            yield line

    async def close(self) -> None:
        """Close the underlying reader."""
        if self._reader:
            await self._reader.close()


def create_log_reader(
    source: str,
    log_path: str = "/var/log/openclaw/gateway.log",
    docker_container: str = "openclaw-gateway",
    systemd_unit: str = "openclaw-gateway",
) -> LogReader:
    """
    Factory function to create a log reader.

    Args:
        source: Log source specification (auto, file:path, docker:container, etc.)
        log_path: Path to log file (for file source)
        docker_container: Docker container name (for docker source)
        systemd_unit: Systemd unit name (for journalctl source)

    Returns:
        LogReader instance
    """
    return AutoLogReader(
        log_source=source,
        log_path=log_path,
        docker_container=docker_container,
        systemd_unit=systemd_unit,
    )
