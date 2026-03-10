"""
Tests for the state machine.
"""

import pytest
from sidecar.state_machine import StateMachine, State, StateChange


class TestStateMachine:
    """Test state machine transitions."""

    def setup_method(self):
        """Reset state machine before each test."""
        self.sm = StateMachine()

    def test_initial_state_is_idle(self):
        assert self.sm.current_state == State.IDLE

    def test_idle_to_thinking(self):
        change = self.sm.process_event("THINKING")

        assert change is not None
        assert change.previous_state == State.IDLE
        assert change.new_state == State.THINKING
        assert self.sm.current_state == State.THINKING

    def test_thinking_to_replying(self):
        self.sm.process_event("THINKING")
        change = self.sm.process_event("REPLYING")

        assert change is not None
        assert change.previous_state == State.THINKING
        assert change.new_state == State.REPLYING
        assert self.sm.current_state == State.REPLYING

    def test_thinking_to_executing(self):
        self.sm.process_event("THINKING")
        change = self.sm.process_event("EXECUTING")

        assert change is not None
        assert change.new_state == State.EXECUTING
        assert self.sm.current_state == State.EXECUTING

    def test_executing_to_replying(self):
        self.sm.process_event("THINKING")
        self.sm.process_event("EXECUTING")
        change = self.sm.process_event("REPLYING")

        assert change is not None
        assert change.new_state == State.REPLYING

    def test_replying_to_idle(self):
        self.sm.process_event("THINKING")
        self.sm.process_event("REPLYING")
        change = self.sm.process_event("IDLE")

        assert change is not None
        assert change.new_state == State.IDLE
        assert self.sm.current_state == State.IDLE

    def test_any_state_to_error(self):
        # From IDLE
        change = self.sm.process_event("ERROR")
        assert change is not None
        assert change.new_state == State.ERROR

        # Reset
        self.sm.reset()

        # From THINKING
        self.sm.process_event("THINKING")
        change = self.sm.process_event("ERROR")
        assert change is not None
        assert change.new_state == State.ERROR

    def test_error_to_idle(self):
        self.sm.process_event("ERROR")
        change = self.sm.process_event("IDLE")

        assert change is not None
        assert change.new_state == State.IDLE

    def test_callback_is_called(self):
        callback_called = False
        captured_change = None

        def callback(change):
            nonlocal callback_called, captured_change
            callback_called = True
            captured_change = change

        self.sm.on_state_change(callback)
        self.sm.process_event("THINKING")

        assert callback_called
        assert captured_change.new_state == State.THINKING

    def test_history_is_recorded(self):
        self.sm.process_event("THINKING")
        self.sm.process_event("REPLYING")
        self.sm.process_event("IDLE")

        assert len(self.sm.history) == 3
        assert self.sm.history[0].new_state == State.THINKING
        assert self.sm.history[1].new_state == State.REPLYING
        assert self.sm.history[2].new_state == State.IDLE

    def test_reset(self):
        self.sm.process_event("THINKING")
        self.sm.process_event("REPLYING")

        assert self.sm.current_state == State.REPLYING

        self.sm.reset()

        assert self.sm.current_state == State.IDLE

    def test_to_dict(self):
        self.sm.process_event("THINKING")

        result = self.sm.to_dict()

        assert result["state"] == "THINKING"
        assert result["history_count"] == 1
