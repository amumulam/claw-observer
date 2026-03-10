"""
Log Parser Module

Combines rules and state machine to parse log lines and emit state changes.
"""

import logging
from typing import Optional, Callable, List
from .rules.base import Event, Rule, RuleSet
from .rules.openclaw_v1 import create_openclaw_rules
from .state_machine import StateMachine, MultiAgentStateMachine, StateChange

logger = logging.getLogger(__name__)


class LogParser:
    """
    Parses log lines using configurable rules and emits state changes.

    Usage:
        parser = LogParser()
        parser.on_event(lambda event: print(event))
        parser.parse_line("[tools] browser executing: navigate")
    """

    def __init__(self, rules: Optional[List[Rule]] = None):
        """
        Initialize the log parser.

        Args:
            rules: Optional list of custom rules. If None, uses default OpenClaw rules.
        """
        if rules:
            self._rule_set = RuleSet(rules)
        else:
            self._rule_set = RuleSet(create_openclaw_rules())

        self._state_machine = StateMachine()
        self._event_listeners: List[Callable[[Event], None]] = []
        self._state_change_listeners: List[Callable[[StateChange], None]] = []

        # Connect state machine to event emission
        self._state_machine.on_state_change(self._on_internal_state_change)

        # Statistics
        self._lines_processed = 0
        self._lines_matched = 0
        self._lines_unmatched = 0

    def _on_internal_state_change(self, change: StateChange) -> None:
        """Handle internal state changes and notify listeners."""
        # Notify state change listeners
        for listener in self._state_change_listeners:
            try:
                listener(change)
            except Exception as e:
                logger.error(f"Error in state change listener: {e}")

        # Create and emit event
        event = Event(
            event_type="state_change",
            state=change.new_state.value,
            previous_state=change.previous_state.value,
            meta=change.meta,
            raw_log=change.raw_log,
        )
        self._notify_event_listeners(event)

    def _notify_event_listeners(self, event: Event) -> None:
        """Notify all event listeners."""
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Error in event listener: {e}")

    def on_event(self, callback: Callable[[Event], None]) -> None:
        """
        Register a callback to be called for each parsed event.

        Args:
            callback: Function to call with Event data
        """
        self._event_listeners.append(callback)

    def on_state_change(self, callback: Callable[[StateChange], None]) -> None:
        """
        Register a callback to be called for each state change.

        Args:
            callback: Function to call with StateChange data
        """
        self._state_change_listeners.append(callback)

    def parse_line(self, line: str) -> Optional[Event]:
        """
        Parse a single log line.

        Args:
            line: Log line to parse

        Returns:
            Event if line matched a rule, None otherwise
        """
        self._lines_processed += 1

        # Try to match against rules
        event = self._rule_set.match(line)

        if event:
            self._lines_matched += 1
            logger.debug(f"Matched rule for line: {line.strip()}")

            # Process through state machine
            self._state_machine.process_event(
                event_state=event.state,
                meta=event.meta,
                raw_log=event.raw_log,
            )

            return event
        else:
            self._lines_unmatched += 1
            logger.debug(f"No match for line: {line.strip()}")
            return None

    def add_rule(self, rule: Rule) -> None:
        """Add a rule to the parser."""
        self._rule_set.add_rule(rule)
        logger.info(f"Added rule: {rule.name}")

    def remove_rule(self, rule_name: str) -> bool:
        """Remove a rule by name."""
        removed = self._rule_set.remove_rule(rule_name)
        if removed:
            logger.info(f"Removed rule: {rule_name}")
        return removed

    def reload_rules(self, rules: Optional[List[Rule]] = None) -> None:
        """
        Reload rules.

        Args:
            rules: New rules to use. If None, reloads default OpenClaw rules.
        """
        if rules:
            self._rule_set = RuleSet(rules)
        else:
            self._rule_set = RuleSet(create_openclaw_rules())
        logger.info("Rules reloaded")

    @property
    def current_state(self) -> str:
        """Get the current state name."""
        return self._state_machine.current_state.value

    @property
    def stats(self) -> dict:
        """Get parsing statistics."""
        return {
            "lines_processed": self._lines_processed,
            "lines_matched": self._lines_matched,
            "lines_unmatched": self._lines_unmatched,
            "match_rate": (
                self._lines_matched / self._lines_processed
                if self._lines_processed > 0
                else 0
            ),
            "current_state": self.current_state,
            "rules_count": len(self._rule_set.rules),
            "rules_version": self._rule_set.version,
        }

    def reset_stats(self) -> None:
        """Reset parsing statistics."""
        self._lines_processed = 0
        self._lines_matched = 0
        self._lines_unmatched = 0


class MultiAgentLogParser:
    """
    Parses log lines for multiple OpenClaw agents.

    Each agent has an independent state machine.
    Log lines are expected to have agent_id prefix (tab-separated):
        "{agent_id}\t{log_line}"

    Usage:
        parser = MultiAgentLogParser()
        parser.on_event(lambda agent_id, event: print(f"{agent_id}: {event}"))
        parser.parse_line("main\t{\"type\":\"message\",\"message\":{\"role\":\"user\"}}")
    """

    def __init__(self, rules: Optional[List[Rule]] = None):
        """
        Initialize the multi-agent log parser.

        Args:
            rules: Optional list of custom rules. If None, uses default OpenClaw rules.
        """
        if rules:
            self._rule_set = RuleSet(rules)
        else:
            self._rule_set = RuleSet(create_openclaw_rules())

        self._state_machine = MultiAgentStateMachine()
        self._event_listeners: List[Callable[[str, Event], None]] = []
        self._state_change_listeners: List[Callable[[str, StateChange], None]] = []

        # Connect state machine to event emission
        self._state_machine.on_state_change(self._on_internal_state_change)

        # Statistics per agent
        self._agent_stats: dict[str, dict] = {}

    def _on_internal_state_change(self, agent_id: str, change: StateChange) -> None:
        """Handle internal state changes and notify listeners."""
        # Notify state change listeners
        for listener in self._state_change_listeners:
            try:
                listener(agent_id, change)
            except Exception as e:
                logger.error(f"Error in state change listener: {e}")

        # Create and emit event
        event = Event(
            event_type="state_change",
            state=change.new_state.value,
            previous_state=change.previous_state.value,
            meta={**change.meta, "agent_id": agent_id},
            raw_log=change.raw_log,
        )
        self._notify_event_listeners(agent_id, event)

    def _notify_event_listeners(self, agent_id: str, event: Event) -> None:
        """Notify all event listeners."""
        for listener in self._event_listeners:
            try:
                listener(agent_id, event)
            except Exception as e:
                logger.error(f"Error in event listener: {e}")

    def on_event(self, callback: Callable[[str, Event], None]) -> None:
        """
        Register a callback to be called for each parsed event.

        Args:
            callback: Function to call with (agent_id, Event) data
        """
        self._event_listeners.append(callback)

    def on_state_change(self, callback: Callable[[str, StateChange], None]) -> None:
        """
        Register a callback to be called for each state change.

        Args:
            callback: Function to call with (agent_id, StateChange) data
        """
        self._state_change_listeners.append(callback)

    def parse_line(self, line: str) -> Optional[tuple[str, Event]]:
        """
        Parse a single log line with agent_id prefix.

        Args:
            line: Log line with format "{agent_id}\t{log_line}"

        Returns:
            (agent_id, Event) if line matched a rule, None otherwise
        """
        # Extract agent_id from the line
        if "\t" in line:
            agent_id, log_line = line.split("\t", 1)
        else:
            # Fallback: no agent_id prefix, use "default"
            logger.debug("Line without agent_id prefix, using 'default'")
            agent_id = "default"
            log_line = line

        # Update agent stats
        if agent_id not in self._agent_stats:
            self._agent_stats[agent_id] = {"processed": 0, "matched": 0}
        self._agent_stats[agent_id]["processed"] += 1

        # Try to match against rules
        event = self._rule_set.match(log_line)

        if event:
            self._agent_stats[agent_id]["matched"] += 1
            logger.debug(f"Matched rule for agent {agent_id}: {log_line.strip()}")

            # Process through state machine
            self._state_machine.process_event(
                agent_id=agent_id,
                event_state=event.state,
                meta=event.meta,
                raw_log=log_line.strip(),
            )

            return (agent_id, event)
        else:
            logger.debug(f"No match for agent {agent_id}: {log_line.strip()}")
            return None

    @property
    def _lines_processed(self) -> int:
        """Total lines processed across all agents."""
        return sum(stats.get("processed", 0) for stats in self._agent_stats.values())

    @_lines_processed.setter
    def _lines_processed(self, value: int) -> None:
        pass  # Setter ignored, tracking via agent_stats

    @property
    def _lines_matched(self) -> int:
        """Total lines matched across all agents."""
        return sum(stats.get("matched", 0) for stats in self._agent_stats.values())

    @property
    def _lines_unmatched(self) -> int:
        """Total lines unmatched across all agents."""
        return self._lines_processed - self._lines_matched

    def get_agent_state(self, agent_id: str) -> Optional[str]:
        """Get the current state of an agent."""
        state = self._state_machine.get_agent_state(agent_id)
        return state.value if state else None

    def get_all_states(self) -> dict[str, str]:
        """Get states of all agents."""
        return self._state_machine.get_all_states()

    def get_agent_ids(self) -> list[str]:
        """Get list of all tracked agent IDs."""
        return self._state_machine.get_agent_ids()

    @property
    def stats(self) -> dict:
        """Get parsing statistics for all agents."""
        return {
            "agents": self._agent_stats.copy(),
            "agent_count": len(self._agent_stats),
            "lines_processed": self._lines_processed,
            "lines_matched": self._lines_matched,
        }

    def reset_stats(self) -> None:
        """Reset parsing statistics."""
        self._agent_stats = {}
