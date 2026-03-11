"""
Tests for OpenClaw log parser rules.
"""

import pytest
from sidecar.rules.base import Event
from sidecar.rules.openclaw_v1 import (
    OpenClawDispatchRule,
    OpenClawStreamingStartRule,
    OpenClawStreamingEndRule,
    OpenClawToolExecutingRule,
    OpenClawToolFailedRule,
    OpenClawErrorRule,
    OpenClawToolCompletedRule,
    create_openclaw_rules,
)


class TestOpenClawDispatchRule:
    """Test dispatching rule."""

    def setup_method(self):
        self.rule = OpenClawDispatchRule()

    def test_match_dispatching_request(self):
        line = "2024-01-15 10:30:00 INFO [gateway] dispatching request"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "THINKING"
        assert event.event_type == "state_change"

    def test_match_dispatching_message(self):
        line = "2024-01-15 10:30:00 INFO [gateway] dispatching message"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "THINKING"
        assert event.meta["dispatch_type"] == "message"

    def test_no_match(self):
        line = "2024-01-15 10:30:00 INFO [gateway] some other log"
        event = self.rule.match(line)

        assert event is None


class TestOpenClawStreamingStartRule:
    """Test streaming start rule."""

    def setup_method(self):
        self.rule = OpenClawStreamingStartRule()

    def test_match_started_streaming(self):
        line = "2024-01-15 10:30:00 INFO [llm] Started streaming"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "REPLYING"

    def test_no_match(self):
        line = "2024-01-15 10:30:00 INFO [llm] Waiting for response"
        event = self.rule.match(line)

        assert event is None


class TestOpenClawToolExecutingRule:
    """Test tool executing rule."""

    def setup_method(self):
        self.rule = OpenClawToolExecutingRule()

    def test_match_tool_executing(self):
        line = "2024-01-15 10:30:05 INFO [tools] browser executing: navigate"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "EXECUTING"
        assert event.meta["tool_name"] == "browser"
        assert event.meta["action"] == "navigate"

    def test_match_tool_executing_with_url(self):
        line = "2024-01-15 10:30:05 INFO [tools] browser executing: https://example.com"
        event = self.rule.match(line)

        assert event is not None
        assert event.meta["tool_name"] == "browser"
        assert event.meta["params"]["url"] == "https://example.com"

    def test_no_match(self):
        line = "2024-01-15 10:30:05 INFO [tools] browser completed"
        event = self.rule.match(line)

        assert event is None


class TestOpenClawToolFailedRule:
    """Test tool failed rule."""

    def setup_method(self):
        self.rule = OpenClawToolFailedRule()

    def test_match_tool_failed(self):
        line = "2024-01-15 10:30:10 INFO [tools] browser failed: Connection timeout"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "ERROR"
        assert event.event_type == "error"
        assert event.meta["tool_name"] == "browser"
        assert event.meta["message"] == "Connection timeout"

    def test_no_match(self):
        line = "2024-01-15 10:30:10 INFO [tools] browser completed"
        event = self.rule.match(line)

        assert event is None


class TestOpenClawErrorRule:
    """Test error rule."""

    def setup_method(self):
        self.rule = OpenClawErrorRule()

    def test_match_error_level(self):
        line = "2024-01-15 10:30:10 ERROR: Something went wrong"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "ERROR"
        assert event.event_type == "error"
        assert event.meta["message"] == "Something went wrong"

    def test_match_bracket_error(self):
        line = "2024-01-15 10:30:10 [ERROR] Exception in handler"
        event = self.rule.match(line)

        assert event is not None
        assert event.state == "ERROR"

    def test_no_match(self):
        line = "2024-01-15 10:30:10 INFO [gateway] Normal operation"
        event = self.rule.match(line)

        assert event is None


class TestCreateOpenClawRules:
    """Test rule set creation."""

    def test_create_rules_returns_list(self):
        rules = create_openclaw_rules()

        assert isinstance(rules, list)
        assert len(rules) == 11  # We have 11 rules (4 JSONL + 7 legacy text-based)

    def test_all_rules_have_unique_names(self):
        rules = create_openclaw_rules()
        names = [rule.name for rule in rules]

        assert len(names) == len(set(names)), "Duplicate rule names found"
