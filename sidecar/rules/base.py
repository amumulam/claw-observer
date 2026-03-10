"""
Rule base class for log parsing.

All rule implementations should inherit from this base class.
Rules are versioned to handle log format changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import re


@dataclass
class Event:
    """Represents a parsed log event."""

    event_type: str  # state_change, heartbeat, error
    state: str  # IDLE, THINKING, REPLYING, EXECUTING, ERROR
    previous_state: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    meta: Dict[str, Any] = field(default_factory=dict)
    raw_log: str = ""
    instance_id: str = "openclaw-gateway-1"
    agent_id: Optional[str] = None  # For multi-agent mode

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        result = {
            "type": self.event_type,
            "timestamp": self.timestamp,
            "instance_id": self.instance_id,
            "data": {
                "state": self.state,
                "previous_state": self.previous_state,
                **self.meta,
            },
        }
        # Include agent_id if present
        if self.agent_id:
            result["agent_id"] = self.agent_id
        return result

    def to_json(self) -> str:
        """Convert event to JSON string."""
        import json
        return json.dumps(self.to_dict())


class Rule(ABC):
    """Base class for log parsing rules."""

    # Rule version - should be incremented when log format changes
    VERSION = "1.0"

    # Priority - higher priority rules are checked first
    PRIORITY = 0

    @abstractmethod
    def match(self, line: str) -> Optional[Event]:
        """
        Try to match a log line and return an Event if matched.

        Args:
            line: A single log line to parse

        Returns:
            Event if the line matches this rule, None otherwise
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this rule."""
        pass


class RuleSet:
    """
    A collection of rules that work together to parse logs.

    Rules are checked in priority order (highest first).
    """

    def __init__(self, rules: Optional[List[Rule]] = None):
        self._rules: List[Rule] = []
        if rules:
            self._rules = sorted(rules, key=lambda r: r.PRIORITY, reverse=True)

    def add_rule(self, rule: Rule) -> None:
        """Add a rule to the set."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.PRIORITY, reverse=True)

    def remove_rule(self, rule_name: str) -> bool:
        """Remove a rule by name."""
        for i, rule in enumerate(self._rules):
            if rule.name == rule_name:
                del self._rules[i]
                return True
        return False

    def match(self, line: str) -> Optional[Event]:
        """
        Try to match a log line against all rules.

        Returns the first matching event, or None if no rules match.
        """
        for rule in self._rules:
            event = rule.match(line)
            if event:
                return event
        return None

    @property
    def version(self) -> str:
        """Get the version of this rule set."""
        if not self._rules:
            return "1.0"
        return self._rules[0].VERSION

    @property
    def rules(self) -> List[Rule]:
        """Get all rules in the set."""
        return self._rules.copy()


class RegexRule(Rule):
    """
    A rule that uses a regular expression to match log lines.

    Example:
        rule = RegexRule(
            name="thinking_rule",
            pattern=r"dispatching",
            state="THINKING",
            priority=10
        )
    """

    def __init__(
        self,
        name: str,
        pattern: str,
        state: str,
        priority: int = 0,
        meta_extractor: Optional[Dict[str, int]] = None,
        event_type: str = "state_change",
    ):
        self._name = name
        self._pattern = re.compile(pattern, re.IGNORECASE)
        self._state = state
        self._priority = priority
        self._meta_extractor = meta_extractor or {}
        self._event_type = event_type

    @property
    def name(self) -> str:
        return self._name

    @property
    def PRIORITY(self) -> int:
        return self._priority

    def match(self, line: str) -> Optional[Event]:
        match = self._pattern.search(line)
        if not match:
            return None

        # Extract metadata using named groups
        meta = {}
        for key, group_idx in self._meta_extractor.items():
            try:
                meta[key] = match.group(group_idx)
            except IndexError:
                pass

        return Event(
            event_type=self._event_type,
            state=self._state,
            meta=meta,
            raw_log=line.strip(),
        )
