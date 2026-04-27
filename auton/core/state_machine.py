"""Lifecycle state machine for ÆON."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum, auto
from typing import Callable

logger = logging.getLogger(__name__)


class State(Enum):
    """Lifecycle states for the ÆON system."""

    INIT = auto()
    RUNNING = auto()
    HIBERNATE = auto()
    TERMINAL = auto()


TransitionCallback = Callable[[State, State], None]
AsyncTransitionCallback = Callable[[State, State], None]


class StateMachine:
    """Async lifecycle state machine with guarded transitions.

    Valid transitions:
        INIT       -> RUNNING
        INIT       -> TERMINAL
        RUNNING    -> HIBERNATE
        RUNNING    -> TERMINAL
        HIBERNATE  -> RUNNING
        HIBERNATE  -> TERMINAL
    """

    _VALID_TRANSITIONS: dict[State, set[State]] = {
        State.INIT: {State.RUNNING, State.TERMINAL},
        State.RUNNING: {State.HIBERNATE, State.TERMINAL},
        State.HIBERNATE: {State.RUNNING, State.TERMINAL},
        State.TERMINAL: set(),
    }

    def __init__(self) -> None:
        self._state = State.INIT
        self._lock = asyncio.Lock()
        self._transition_callbacks: list[AsyncTransitionCallback | TransitionCallback] = []

    @property
    def current_state(self) -> State:
        """Return the current state."""
        return self._state

    def get_current_state(self) -> State:
        """Return the current state (synchronous accessor)."""
        return self._state

    async def transition_to(self, new_state: State) -> bool:
        """Attempt to transition to a new state.

        Args:
            new_state: The target state.

        Returns:
            True if the transition succeeded, False if it was invalid.
        """
        async with self._lock:
            old_state = self._state
            if new_state not in self._VALID_TRANSITIONS.get(old_state, set()):
                logger.warning(
                    "StateMachine: invalid transition %s -> %s",
                    old_state.name,
                    new_state.name,
                )
                return False

            self._state = new_state
            logger.info(
                "StateMachine: transitioned %s -> %s",
                old_state.name,
                new_state.name,
            )

        for callback in self._transition_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(old_state, new_state)
                else:
                    callback(old_state, new_state)
            except Exception:
                logger.exception(
                    "StateMachine: transition callback error for %s -> %s",
                    old_state.name,
                    new_state.name,
                )

        return True

    def on_transition(self, callback: AsyncTransitionCallback | TransitionCallback) -> None:
        """Register a callback invoked on every state transition.

        Args:
            callback: A sync or async callable receiving (old_state, new_state).
        """
        self._transition_callbacks.append(callback)

    def remove_transition_callback(
        self, callback: AsyncTransitionCallback | TransitionCallback
    ) -> None:
        """Remove a previously registered transition callback."""
        try:
            self._transition_callbacks.remove(callback)
        except ValueError:
            pass
