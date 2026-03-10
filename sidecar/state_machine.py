"""
State Machine for OpenClaw Monitor

Manages the state transitions based on parsed events.
All state changes are tracked and notified to listeners.
"""

from enum import Enum
from typing import Callable, List, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


class State(Enum):
    """Valid states for the OpenClaw monitor."""

    IDLE = "IDLE"
    THINKING = "THINKING"
    REPLYING = "REPLYING"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"


# State transition rules: (from_state, target_state) -> to_state
# All keys use State enum for consistency
TRANSITION_RULES = {
    # From IDLE
    (State.IDLE, State.THINKING): State.THINKING,
    (State.IDLE, State.EXECUTING): State.EXECUTING,  # Direct tool call
    (State.IDLE, State.ERROR): State.ERROR,
    # From THINKING
    (State.THINKING, State.REPLYING): State.REPLYING,
    (State.THINKING, State.EXECUTING): State.EXECUTING,  # Tool call during thinking
    (State.THINKING, State.ERROR): State.ERROR,
    # From REPLYING
    (State.REPLYING, State.IDLE): State.IDLE,
    (State.REPLYING, State.EXECUTING): State.EXECUTING,  # Tool call during reply
    (State.REPLYING, State.ERROR): State.ERROR,
    # From EXECUTING
    (State.EXECUTING, State.REPLYING): State.REPLYING,  # Tool done, back to reply
    (State.EXECUTING, State.IDLE): State.IDLE,  # Tool done, task complete
    (State.EXECUTING, State.ERROR): State.ERROR,
    # From ERROR
    (State.ERROR, State.IDLE): State.IDLE,  # Reset after error
    (State.ERROR, State.THINKING): State.THINKING,  # New request after error
}


@dataclass
class StateChange:
    """Represents a state change event."""

    previous_state: State
    new_state: State
    meta: dict
    raw_log: str


class StateMachine:
    """
    Finite state machine for tracking OpenClaw Gateway state.

    Usage:
        sm = StateMachine()
        sm.on_state_change(lambda change: print(f"{change.previous_state} -> {change.new_state}"))

        # Process events
        sm.process_event(Event(state="THINKING"))
    """

    def __init__(self, initial_state: State = State.IDLE):
        self._state = initial_state
        self._listeners: List[Callable[[StateChange], None]] = []
        self._history: List[StateChange] = []
        self._history_max = 100  # Keep last 100 state changes

    @property
    def current_state(self) -> State:
        """Get the current state."""
        return self._state

    @property
    def state_name(self) -> str:
        """Get the current state name as string."""
        return self._state.value

    @property
    def history(self) -> List[StateChange]:
        """Get state change history."""
        return self._history.copy()

    def on_state_change(self, callback: Callable[[StateChange], None]) -> None:
        """
        Register a callback to be called on state changes.

        Args:
            callback: Function to call with StateChange data
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[StateChange], None]) -> bool:
        """Remove a state change listener."""
        try:
            self._listeners.remove(callback)
            return True
        except ValueError:
            return False

    def process_event(self, event_state: str, meta: Optional[dict] = None, raw_log: str = "") -> Optional[StateChange]:
        """
        Process an event and transition state if valid.

        Args:
            event_state: The target state from the event
            meta: Additional metadata from the event
            raw_log: The raw log line that triggered this event

        Returns:
            StateChange if transition occurred, None if state didn't change
        """
        target_state_str = event_state.upper()

        # Validate target state
        try:
            target_state = State[target_state_str]
        except KeyError:
            logger.warning(f"Unknown state: {target_state_str}")
            return None

        # Check if transition is valid (use State enum for lookup)
        new_state = self._get_next_state(self._state, target_state)

        if new_state is None:
            logger.debug(f"Invalid transition: {self._state} -> {target_state}")
            return None

        if new_state == self._state:
            # No state change
            return None

        # Perform transition
        previous_state = self._state
        self._state = new_state

        state_change = StateChange(
            previous_state=previous_state,
            new_state=new_state,
            meta=meta or {},
            raw_log=raw_log,
        )

        # Record history
        self._history.append(state_change)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(state_change)
            except Exception as e:
                logger.error(f"Error in state change listener: {e}")

        logger.info(f"State changed: {previous_state.value} -> {new_state.value}")
        return state_change

    def _get_next_state(self, from_state: State, target_state: State) -> Optional[State]:
        """
        Determine the next state based on transition rules.

        Args:
            from_state: Current state
            target_state: Target state from event

        Returns:
            Next state if valid transition, None otherwise
        """
        # Check specific transition rule first
        key = (from_state, target_state)
        if key in TRANSITION_RULES:
            return TRANSITION_RULES[key]

        # If target is ERROR, always allow (from any state)
        if target_state == State.ERROR:
            return State.ERROR

        # Default: no transition allowed
        return None

    def reset(self, to_state: State = State.IDLE) -> None:
        """Reset state machine to initial state."""
        if self._state != to_state:
            previous_state = self._state
            self._state = to_state
            logger.info(f"State machine reset: {previous_state.value} -> {to_state.value}")

    def to_dict(self) -> dict:
        """Convert state machine state to dictionary."""
        return {
            "state": self._state.value,
            "history_count": len(self._history),
            "last_state": self._history[-1].previous_state.value if self._history else None,
        }


class MultiAgentStateMachine:
    """
    Multi-agent state machine for tracking multiple OpenClaw agents.

    Each agent has its own independent state machine.

    Usage:
        sm = MultiAgentStateMachine()
        sm.on_state_change(lambda agent_id, change: print(f"{agent_id}: {change.previous_state} -> {change.new_state}"))
        sm.process_event("main", Event(state="THINKING"))
        sm.process_event("baba", Event(state="EXECUTING"))
    """

    def __init__(self, initial_state: State = State.IDLE):
        self._initial_state = initial_state
        self._agents: dict[str, StateMachine] = {}
        self._listeners: List[Callable[[str, StateChange], None]] = []

    def _get_or_create_agent(self, agent_id: str) -> StateMachine:
        """Get or create a state machine for an agent."""
        if agent_id not in self._agents:
            logger.info(f"Creating new agent: {agent_id}")
            sm = StateMachine(initial_state=self._initial_state)
            sm.on_state_change(lambda change, aid=agent_id: self._notify_listeners(aid, change))
            self._agents[agent_id] = sm
        return self._agents[agent_id]

    def _notify_listeners(self, agent_id: str, change: StateChange) -> None:
        """Notify all listeners of a state change."""
        for listener in self._listeners:
            try:
                listener(agent_id, change)
            except Exception as e:
                logger.error(f"Error in multi-agent state change listener: {e}")

    def on_state_change(self, callback: Callable[[str, StateChange], None]) -> None:
        """
        Register a callback for state changes.

        Callback receives: (agent_id, StateChange)
        """
        self._listeners.append(callback)

    def process_event(self, agent_id: str, event_state: str, meta: Optional[dict] = None, raw_log: str = "") -> Optional[StateChange]:
        """
        Process an event for a specific agent.

        Args:
            agent_id: The agent ID
            event_state: The target state from the event
            meta: Additional metadata
            raw_log: The raw log line

        Returns:
            StateChange if transition occurred, None otherwise
        """
        sm = self._get_or_create_agent(agent_id)
        return sm.process_event(event_state, meta, raw_log)

    def get_agent_state(self, agent_id: str) -> Optional[State]:
        """Get the current state of an agent."""
        if agent_id in self._agents:
            return self._agents[agent_id].current_state
        return self._initial_state

    def get_all_states(self) -> dict[str, str]:
        """Get states of all agents."""
        return {
            agent_id: sm.current_state.value
            for agent_id, sm in self._agents.items()
        }

    def get_agent_ids(self) -> list[str]:
        """Get list of all tracked agent IDs."""
        return list(self._agents.keys())

    def to_dict(self) -> dict:
        """Convert all agent states to dictionary."""
        return {
            "agents": {
                agent_id: sm.to_dict()
                for agent_id, sm in self._agents.items()
            },
            "agent_count": len(self._agents),
        }
