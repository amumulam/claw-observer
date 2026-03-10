"""
OpenClaw Gateway Log Parsing Rules v1.0

These rules parse OpenClaw Gateway logs and convert them to state events.
"""

import re
from typing import Optional
from .base import Rule, Event, RegexRule


class OpenClawDispatchRule(Rule):
    """
    Rule: Detect dispatching state (THINKING)

    Matches logs like:
    - "dispatching request"
    - "dispatching message"
    """

    VERSION = "1.0"
    PRIORITY = 100  # High priority - first state in chain

    @property
    def name(self) -> str:
        return "openclaw_dispatch"

    def match(self, line: str) -> Optional[Event]:
        pattern = r"dispatching\s+(request|message)"
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return Event(
                event_type="state_change",
                state="THINKING",
                meta={"dispatch_type": match.group(1)},
                raw_log=line.strip(),
            )
        return None


class OpenClawStreamingStartRule(Rule):
    """
    Rule: Detect streaming start (REPLYING)

    Matches logs like:
    - "Started streaming"
    - "started streaming response"
    """

    VERSION = "1.0"
    PRIORITY = 90

    @property
    def name(self) -> str:
        return "openclaw_streaming_start"

    def match(self, line: str) -> Optional[Event]:
        pattern = r"Started\s+streaming"
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return Event(
                event_type="state_change",
                state="REPLYING",
                previous_state="THINKING",
                raw_log=line.strip(),
            )
        return None


class OpenClawStreamingEndRule(Rule):
    """
    Rule: Detect streaming end + dispatch complete (IDLE)

    Matches logs like:
    - "Closed streaming" + "dispatch complete"

    This rule needs to track both events to transition to IDLE.
    The state machine will handle the two-step transition.
    """

    VERSION = "1.0"
    PRIORITY = 85

    @property
    def name(self) -> str:
        return "openclaw_streaming_end"

    def match(self, line: str) -> Optional[Event]:
        # Check for "Closed streaming"
        if re.search(r"Closed\s+streaming", line, re.IGNORECASE):
            return Event(
                event_type="state_change",
                state="IDLE",
                previous_state="REPLYING",
                meta={"reason": "streaming_closed"},
                raw_log=line.strip(),
            )

        # Check for "dispatch complete"
        if re.search(r"dispatch\s+complete", line, re.IGNORECASE):
            return Event(
                event_type="state_change",
                state="IDLE",
                previous_state="REPLYING",
                meta={"reason": "dispatch_complete"},
                raw_log=line.strip(),
            )

        return None


class OpenClawToolExecutingRule(Rule):
    """
    Rule: Detect tool execution (EXECUTING)

    Matches logs like:
    - "[tools] browser executing: navigate"
    - "[tools] exec executing: ls -la"
    """

    VERSION = "1.0"
    PRIORITY = 95

    @property
    def name(self) -> str:
        return "openclaw_tool_executing"

    def match(self, line: str) -> Optional[Event]:
        pattern = r"\[tools\]\s+(\w+)\s+executing:\s*(.*)"
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            tool_name = match.group(1)
            action = match.group(2).strip() if match.group(2) else ""

            # Try to parse params from action
            params = {}
            if action.startswith("http"):
                params["url"] = action
            elif "=" in action:
                for part in action.split():
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[k] = v

            return Event(
                event_type="state_change",
                state="EXECUTING",
                meta={
                    "tool_name": tool_name,
                    "action": action,
                    "params": params,
                },
                raw_log=line.strip(),
            )
        return None


class OpenClawToolFailedRule(Rule):
    """
    Rule: Detect tool failure (ERROR)

    Matches logs like:
    - "[tools] browser failed: Connection timeout"
    - "[tools] exec failed: Command aborted"
    """

    VERSION = "1.0"
    PRIORITY = 100  # Same as dispatch - errors are critical

    @property
    def name(self) -> str:
        return "openclaw_tool_failed"

    def match(self, line: str) -> Optional[Event]:
        pattern = r"\[tools\]\s+(\w+)\s+failed:\s*(.*)"
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            tool_name = match.group(1)
            error_msg = match.group(2).strip() if match.group(2) else "Unknown error"

            return Event(
                event_type="error",
                state="ERROR",
                meta={
                    "error_code": "TOOL_EXECUTION_FAILED",
                    "tool_name": tool_name,
                    "message": error_msg,
                },
                raw_log=line.strip(),
            )
        return None


class OpenClawErrorRule(Rule):
    """
    Rule: Detect ERROR level logs

    Matches logs like:
    - "ERROR: Something went wrong"
    - "[ERROR] Exception in handler"
    """

    VERSION = "1.0"
    PRIORITY = 100  # Critical priority

    @property
    def name(self) -> str:
        return "openclaw_error"

    def match(self, line: str) -> Optional[Event]:
        # Match various ERROR patterns
        patterns = [
            r"\bERROR\b[:\s]+(.*)",
            r"\[ERROR\]\s*(.*)",
            r"\bFATAL\b[:\s]+(.*)",
            r"\bCRITICAL\b[:\s]+(.*)",
        ]

        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                error_detail = match.group(1).strip() if match.group(1) else "Unknown error"
                return Event(
                    event_type="error",
                    state="ERROR",
                    meta={
                        "error_code": "INTERNAL_ERROR",
                        "message": error_detail,
                    },
                    raw_log=line.strip(),
                )

        return None


class OpenClawToolCompletedRule(Rule):
    """
    Rule: Detect tool completion

    Matches logs like:
    - "[tools] browser completed"
    - "[tools] exec completed successfully"

    This doesn't change state directly but can be used for metrics.
    """

    VERSION = "1.0"
    PRIORITY = 50

    @property
    def name(self) -> str:
        return "openclaw_tool_completed"

    def match(self, line: str) -> Optional[Event]:
        pattern = r"\[tools\]\s+(\w+)\s+completed"
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            tool_name = match.group(1)
            return Event(
                event_type="state_change",
                state="REPLYING",  # Return to replying after tool execution
                meta={
                    "tool_name": tool_name,
                    "action": "completed",
                },
                raw_log=line.strip(),
            )
        return None


def create_openclaw_rules() -> list:
    """
    Create and return a list of all OpenClaw rules.

    Returns:
        List of Rule instances
    """
    return [
        OpenClawDispatchRule(),
        OpenClawStreamingStartRule(),
        OpenClawStreamingEndRule(),
        OpenClawToolExecutingRule(),
        OpenClawToolFailedRule(),
        OpenClawErrorRule(),
        OpenClawToolCompletedRule(),
    ]
