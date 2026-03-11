"""
Log Reader Module

Reads logs from various sources:
- File (tail -F with rotation support)
- Docker container (docker logs -f)
- Systemd journal (journalctl -f)
- Standard input
- Multi-agent JSONL sessions
"""

import asyncio
import subprocess
import sys
import os
from typing import Optional, Callable
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class LogReader(ABC):
    """Abstract base class for log readers."""

    @abstractmethod
    async def read_lines(self) -> AsyncIterator[str]:
        """Read log lines asynchronously."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the log reader and cleanup resources."""
        pass


class FileLogReader(LogReader):
    """
    Reads logs from a file using tail -F.

    Handles log rotation automatically via tail -F.
    """

    def __init__(self, file_path: str, buffer_size: int = 1024):
        self.file_path = file_path
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False

    async def read_lines(self) -> AsyncIterator[str]:
        """Start tail -F and read lines."""
        self._running = True
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
                while self._running:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        # Check if process exited
                        if self._process.returncode is not None:
                            break
                        await asyncio.sleep(0.1)
                        continue

                    buffer += chunk.decode("utf-8", errors="replace")

                    # Process complete lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            yield line

        except FileNotFoundError:
            logger.error(f"tail command not found. File path: {self.file_path}")
            raise
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            raise

    async def close(self) -> None:
        """Terminate the tail process."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error terminating tail process: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass


class SessionFileReader(LogReader):
    """
    Reads a single JSONL session file with tail -F.

    Prepends session_id to each line.
    """

    def __init__(self, session_path: str, session_id: str, agent_id: str, buffer_size: int = 1024):
        self.session_path = session_path
        self.session_id = session_id
        self.agent_id = agent_id
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False
        self._callback: Optional[callable] = None

    def on_file_complete(self, callback: callable):
        """Register callback for when file stops changing."""
        self._callback = callback

    async def read_lines(self) -> AsyncIterator[str]:
        """Start tail -F and read lines with session info."""
        self._running = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                "tail",
                "-F",
                self.session_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout:
                buffer = ""
                while self._running:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        if self._process.returncode is not None:
                            break
                        await asyncio.sleep(0.1)
                        continue

                    buffer += chunk.decode("utf-8", errors="replace")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            # Format: agent_id\tsession_id\tline
                            yield f"{self.agent_id}\t{self.session_id}\t{line}"

        except Exception as e:
            logger.error(f"Error reading session file {self.session_path}: {e}")
        finally:
            if self._callback:
                self._callback(self.session_path)

    async def close(self) -> None:
        """Terminate the tail process."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass


class MultiAgentLogReader(LogReader):
    """
    Reads logs from multiple OpenClaw agent directories.

    Monitors JSONL session files in /root/.openclaw/agents/{agent-id}/sessions/

    Features:
    - Auto-discover agents at startup
    - Auto-discover new session files every 5 seconds
    - Automatically tail new sessions
    - Handles file rotation via tail -F
    """

    def __init__(
        self,
        base_path: str = "/root/.openclaw/agents",
        agent_ids: Optional[list[str]] = None,
        buffer_size: int = 1024,
        scan_interval: float = 5.0,
        on_agent_discovered: Optional[Callable[[str], None]] = None,
    ):
        self.base_path = base_path
        self.agent_ids = agent_ids  # If None, auto-discover
        self.buffer_size = buffer_size
        self.scan_interval = scan_interval
        self.on_agent_discovered = on_agent_discovered

        self._readers: dict[str, dict[str, SessionFileReader]] = {}  # {agent_id: {session_id: reader}}
        self._tasks: list[asyncio.Task] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._known_sessions: set[str] = set()  # Set of "agent_id:session_id"

    async def _discover_agents(self) -> list[str]:
        """Discover agent directories."""
        if self.agent_ids:
            return self.agent_ids

        discovered = []
        try:
            if os.path.isdir(self.base_path):
                for name in os.listdir(self.base_path):
                    agent_path = os.path.join(self.base_path, name)
                    if os.path.isdir(agent_path):
                        sessions_dir = os.path.join(agent_path, "sessions")
                        if os.path.isdir(sessions_dir):
                            discovered.append(name)
                            logger.info(f"Discovered agent: {name}")
                            # Notify callback when agent is discovered
                            if self.on_agent_discovered:
                                self.on_agent_discovered(name)
        except Exception as e:
            logger.error(f"Error discovering agents: {e}")

        return discovered

    async def _discover_sessions(self, agent_id: str) -> list[tuple[str, str]]:
        """
        Discover session files for an agent.

        Returns list of (session_id, session_path) tuples.
        """
        sessions = []
        sessions_dir = os.path.join(self.base_path, agent_id, "sessions")

        try:
            if os.path.isdir(sessions_dir):
                for f in os.listdir(sessions_dir):
                    if f.endswith(".jsonl"):
                        session_id = f[:-6]  # Remove .jsonl
                        full_path = os.path.join(sessions_dir, f)
                        sessions.append((session_id, full_path))
        except Exception as e:
            logger.error(f"Error discovering sessions for {agent_id}: {e}")

        return sessions

    def _on_session_file_complete(self, session_path: str):
        """Called when a session file stops changing."""
        # Could implement logic to detect stale sessions here
        pass

    async def _start_session_reader(self, agent_id: str, session_id: str, session_path: str) -> None:
        """Start reading a session file."""
        key = f"{agent_id}:{session_id}"
        if key in self._known_sessions:
            return  # Already reading this session

        logger.debug(f"Starting reader for session: {key} ({session_path})")

        reader = SessionFileReader(
            session_path=session_path,
            session_id=session_id,
            agent_id=agent_id,
            buffer_size=self.buffer_size,
        )
        reader.on_file_complete(self._on_session_file_complete)

        # Store reader
        if agent_id not in self._readers:
            self._readers[agent_id] = {}
        self._readers[agent_id][session_id] = reader
        self._known_sessions.add(key)

        # Start reading and queueing
        async for line in reader.read_lines():
            if not self._running:
                break
            await self._queue.put(line)

    async def _scan_for_new_sessions(self) -> None:
        """Periodically scan for new session files."""
        while self._running:
            try:
                agent_ids = await self._discover_agents()

                for agent_id in agent_ids:
                    sessions = await self._discover_sessions(agent_id)
                    for session_id, session_path in sessions:
                        key = f"{agent_id}:{session_id}"
                        if key not in self._known_sessions:
                            # Start a new task to read this session
                            task = asyncio.create_task(
                                self._start_session_reader(agent_id, session_id, session_path)
                            )
                            self._tasks.append(task)

            except Exception as e:
                logger.error(f"Error scanning for sessions: {e}")

            # Wait before next scan
            await asyncio.sleep(self.scan_interval)

    async def read_lines(self) -> AsyncIterator[str]:
        """Start reading from all agents and yield lines."""
        self._running = True

        # Initial agent discovery
        agent_ids = await self._discover_agents()
        logger.info(f"Monitoring {len(agent_ids)} agents: {agent_ids}")

        # Start scan task
        self._scan_task = asyncio.create_task(self._scan_for_new_sessions())

        # Yield from queue
        while self._running:
            try:
                line = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield line
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error reading from queue: {e}")
                break

    async def close(self) -> None:
        """Close all readers."""
        self._running = False

        # Close all readers
        for agent_id, sessions in self._readers.items():
            for session_id, reader in sessions.items():
                try:
                    await reader.close()
                except Exception as e:
                    logger.warning(f"Error closing reader for {agent_id}:{session_id}: {e}")

        # Cancel scan task
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        # Cancel all reader tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("MultiAgentLogReader closed")


# ============ Single-agent log readers ============

class DockerLogReader(LogReader):
    """Reads logs from a Docker container."""

    def __init__(self, container_name: str, tail_lines: int = 0, buffer_size: int = 1024):
        self.container_name = container_name
        self.tail_lines = tail_lines
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False

    async def read_lines(self) -> AsyncIterator[str]:
        """Start docker logs -f and read lines."""
        self._running = True
        cmd = ["docker", "logs", "-f"]
        if self.tail_lines > 0:
            cmd.extend(["--tail", str(self.tail_lines)])
        cmd.append(self.container_name)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            if self._process.stdout:
                buffer = ""
                while self._running:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        if self._process.returncode is not None:
                            break
                        await asyncio.sleep(0.1)
                        continue

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
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error terminating docker logs process: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass


class JournalctlLogReader(LogReader):
    """Reads logs from systemd journal."""

    def __init__(self, unit: str, buffer_size: int = 1024):
        self.unit = unit
        self.buffer_size = buffer_size
        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False

    async def read_lines(self) -> AsyncIterator[str]:
        """Start journalctl -f and read lines."""
        self._running = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                "journalctl",
                "-f",
                "-u",
                self.unit,
                "--output=cat",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if self._process.stdout:
                buffer = ""
                while self._running:
                    chunk = await self._process.stdout.read(self.buffer_size)
                    if not chunk:
                        if self._process.returncode is not None:
                            break
                        await asyncio.sleep(0.1)
                        continue

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
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error terminating journalctl process: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass


class StdinLogReader(LogReader):
    """Reads logs from standard input."""

    def __init__(self, buffer_size: int = 1024):
        self.buffer_size = buffer_size
        self._running = True

    async def read_lines(self) -> AsyncIterator[str]:
        """Read lines from stdin."""
        loop = asyncio.get_event_loop()
        buffer = ""

        while self._running:
            try:
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
    """Automatically detects the best log reader."""

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
            if await self._check_docker():
                logger.info(f"Using Docker log reader for container: {self.docker_container}")
                return DockerLogReader(self.docker_container)

            if await self._check_journalctl():
                logger.info(f"Using journalctl log reader for unit: {self.systemd_unit}")
                return JournalctlLogReader(self.systemd_unit)

            if await self._check_file():
                logger.info(f"Using file log reader: {self.log_path}")
                return FileLogReader(self.log_path)

            logger.warning("No log source detected, falling back to stdin")
            return StdinLogReader()

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
    """Factory function to create a log reader."""
    return AutoLogReader(
        log_source=source,
        log_path=log_path,
        docker_container=docker_container,
        systemd_unit=systemd_unit,
    )
