"""
OpenClaw Gateway Log Parsing Rules v1.0

These rules parse OpenClaw Gateway JSONL session logs and convert them to state events.

JSONL Event Types:
- session: New session started
- message: User/Assistant/Tool message
  - role: "user" → THINKING (Agent starts processing)
  - role: "assistant" + stopReason: null → REPLYING (streaming)
  - role: "assistant" + stopReason: "stop" → IDLE (completed)
  - role: "toolResult" → EXECUTING (tool was executed)
- model_change: Model switched
- thinking_level_change: Thinking mode changed
"""

import json
import re
from typing import Optional
from .base import Rule, Event, RegexRule


class OpenClawSessionStartRule(Rule):
    """
    Rule: Detect new session start

    Matches JSONL lines like:
    {"type":"session","id":"...","agentId":"main",...}

    Extracts agent_id from the session data or file path context.
    """

    VERSION = "1.0"
    PRIORITY = 50

    @property
    def name(self) -> str:
        return "openclaw_session_start"

    def match(self, line: str) -> Optional[Event]:
        try:
            data = json.loads(line.strip())
            if data.get("type") == "session":
                agent_id = data.get("agentId", "unknown")
                return Event(
                    event_type="session_start",
                    state="IDLE",
                    meta={
                        "agent_id": agent_id,
                        "session_id": data.get("id"),
                    },
                    raw_log=line.strip(),
                )
        except json.JSONDecodeError:
            pass
        return None


class OpenClawUserMessageRule(Rule):
    """
    Rule: Detect user message (Agent starts thinking)

    Matches JSONL lines like:
    {"type":"message","message":{"role":"user","content":[...]},...}

    State: IDLE → THINKING
    """

    VERSION = "1.0"
    PRIORITY = 100

    @property
    def name(self) -> str:
        return "openclaw_user_message"

    def match(self, line: str) -> Optional[Event]:
        try:
            data = json.loads(line.strip())
            if data.get("type") == "message":
                msg = data.get("message", {})
                if msg.get("role") == "user":
                    # Extract agent_id from parentId pattern or context
                    parent_id = data.get("parentId", "")
                    return Event(
                        event_type="state_change",
                        state="THINKING",
                        meta={
                            "role": "user",
                            "parent_id": parent_id,
                            "message_id": data.get("id"),
                        },
                        raw_log=line.strip(),
                    )
        except json.JSONDecodeError:
            pass
        return None


class OpenClawAssistantResponseRule(Rule):
    """
    Rule: Detect assistant response (REPLYING or IDLE)

    Matches JSONL lines like:
    {"type":"message","message":{"role":"assistant","content":[...],"stopReason":"stop"},...}

    - stopReason: null or missing → REPLYING (streaming in progress)
    - stopReason: "stop" → IDLE (completed)
    """

    VERSION = "1.0"
    PRIORITY = 90

    @property
    def name(self) -> str:
        return "openclaw_assistant_response"

    def match(self, line: str) -> Optional[Event]:
        try:
            data = json.loads(line.strip())
            if data.get("type") == "message":
                msg = data.get("message", {})
                if msg.get("role") == "assistant":
                    stop_reason = msg.get("stopReason")
                    # Extract usage info
                    usage = msg.get("usage", {})

                    if stop_reason == "stop":
                        # Completed
                        return Event(
                            event_type="state_change",
                            state="IDLE",
                            meta={
                                "role": "assistant",
                                "stop_reason": stop_reason,
                                "model": msg.get("model"),
                                "tokens": usage.get("totalTokens", 0),
                            },
                            raw_log=line.strip(),
                        )
                    else:
                        # Still streaming (stopReason is null or not "stop")
                        return Event(
                            event_type="state_change",
                            state="REPLYING",
                            meta={
                                "role": "assistant",
                                "stop_reason": stop_reason,
                                "model": msg.get("model"),
                            },
                            raw_log=line.strip(),
                        )
        except json.JSONDecodeError:
            pass
        return None


class OpenClawToolResultRule(Rule):
    """
    Rule: Detect tool result (EXECUTING)

    Matches JSONL lines like:
    {"type":"message","message":{"role":"toolResult","toolName":"browser",...},...}

    State: → EXECUTING (tool was executed)
    """

    VERSION = "1.0"
    PRIORITY = 95

    @property
    def name(self) -> str:
        return "openclaw_tool_result"

    def match(self, line: str) -> Optional[Event]:
        try:
            data = json.loads(line.strip())
            if data.get("type") == "message":
                msg = data.get("message", {})
                if msg.get("role") == "toolResult":
                    tool_name = msg.get("toolName", "unknown")
                    tool_call_id = msg.get("toolCallId", "")
                    details = msg.get("details", {})

                    # Check if it was an error
                    is_error = details.get("status") == "error" or msg.get("isError", False)
                    error_msg = details.get("error", "")

                    meta = {
                        "role": "toolResult",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "action": "executed",
                    }

                    if is_error:
                        meta["error"] = error_msg
                        return Event(
                            event_type="error",
                            state="ERROR",
                            meta=meta,
                            raw_log=line.strip(),
                        )
                    else:
                        return Event(
                            event_type="state_change",
                            state="EXECUTING",
                            meta=meta,
                            raw_log=line.strip(),
                        )
        except json.JSONDecodeError:
            pass
        return None


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

    Note: This only matches ERROR at the START of a line or after specific
    prefixes to avoid false positives from log content that mentions "error".
    """

    VERSION = "1.0"
    PRIORITY = 100  # Critical priority

    @property
    def name(self) -> str:
        return "openclaw_error"

    def match(self, line: str) -> Optional[Event]:
        # Only match ERROR patterns at line start or with specific prefixes
        # This avoids false positives from log content that contains "error"
        patterns = [
            r"^\s*ERROR\s*[:：]\s*(.*)",           # ERROR: at line start
            r"^\s*\[ERROR\]\s*(.*)",               # [ERROR] at line start
            r"^\s*FATAL\s*[:：]\s*(.*)",           # FATAL: at line start
            r"^\s*\[FATAL\]\s*(.*)",               # [FATAL] at line start
            r"^\s*CRITICAL\s*[:：]\s*(.*)",        # CRITICAL: at line start
            r"^\s*\[CRITICAL\]\s*(.*)",            # [CRITICAL] at line start
            r"\[gateway\].*ERROR",                  # [gateway] ... ERROR
            r"Exception\s*:",                       # Exception: ...
            r"Traceback\s*\(most recent call last\)",  # Python traceback
        ]

        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                # Get error detail if available
                error_detail = ""
                if match.lastindex and match.group(match.lastindex):
                    error_detail = match.group(match.lastindex).strip()
                if not error_detail:
                    error_detail = line.strip()[:200]

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
        # JSONL-based rules (for OpenClaw session logs)
        OpenClawUserMessageRule(),
        OpenClawAssistantResponseRule(),
        OpenClawToolResultRule(),
        OpenClawSessionStartRule(),
        # Legacy text-based rules (for gateway.log)
        OpenClawDispatchRule(),
        OpenClawStreamingStartRule(),
        OpenClawStreamingEndRule(),
        OpenClawToolExecutingRule(),
        OpenClawToolFailedRule(),
        OpenClawErrorRule(),
        OpenClawToolCompletedRule(),
    ]
