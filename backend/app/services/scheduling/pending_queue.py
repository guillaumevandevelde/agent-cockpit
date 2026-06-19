"""Pending queue for kanban cards rejected due to memory limits.

When a card cannot be dispatched because of hardware-aware session limits,
it's placed in this queue. A periodic retry mechanism attempts to dispatch
queued cards when memory becomes available.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PendingCard:
    """A card waiting to be dispatched when resources are available."""
    card_id: str
    project_key: str
    project_path: str
    agent_override: Optional[str] = None
    impediment_question: Optional[str] = None
    queued_at: float = field(default_factory=time.monotonic)
    retry_count: int = 0
    last_retry_at: Optional[float] = None


class PendingQueue:
    """In-memory queue for cards awaiting dispatch."""

    def __init__(self, max_retries: int = 10, retry_interval_s: float = 30.0):
        self._queue: dict[str, PendingCard] = {}  # card_id -> PendingCard
        self._max_retries = max_retries
        self._retry_interval_s = retry_interval_s
        self._last_retry_check: float = 0.0

    def enqueue(
        self,
        card_id: str,
        project_key: str,
        project_path: str,
        agent_override: Optional[str] = None,
        impediment_question: Optional[str] = None,
    ) -> bool:
        """Add a card to the pending queue.
        
        Returns True if enqueued, False if already queued or max retries exceeded.
        """
        if card_id in self._queue:
            return False  # Already queued

        card = PendingCard(
            card_id=card_id,
            project_key=project_key,
            project_path=project_path,
            agent_override=agent_override,
            impediment_question=impediment_question,
        )
        self._queue[card_id] = card
        logger.info(
            f"Card {card_id} queued for retry (project={project_key}, "
            f"queued_at={card.queued_at})"
        )
        return True

    def dequeue(self, card_id: str) -> Optional[PendingCard]:
        """Remove and return a card from the queue."""
        return self._queue.pop(card_id, None)

    def get_retryable_cards(self) -> list[PendingCard]:
        """Get cards that are due for retry."""
        now = time.monotonic()
        if now - self._last_retry_check < self._retry_interval_s:
            return []
        self._last_retry_check = now

        retryable = []
        for card in list(self._queue.values()):
            if card.retry_count >= self._max_retries:
                logger.warning(
                    f"Card {card.card_id} exceeded max retries ({self._max_retries}), "
                    f"removing from queue"
                )
                self._queue.pop(card.card_id, None)
                continue
            
            # Exponential backoff: 30s, 60s, 120s, ...
            backoff = self._retry_interval_s * (2 ** card.retry_count)
            if card.last_retry_at is None or (now - card.last_retry_at) >= backoff:
                retryable.append(card)

        return retryable

    def mark_retry(self, card_id: str) -> None:
        """Mark a card as retried."""
        if card_id in self._queue:
            card = self._queue[card_id]
            card.retry_count += 1
            card.last_retry_at = time.monotonic()

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def get_status(self) -> dict:
        """Get queue status for monitoring."""
        now = time.monotonic()
        return {
            "size": self.size,
            "cards": [
                {
                    "card_id": c.card_id,
                    "project_key": c.project_key,
                    "retry_count": c.retry_count,
                    "queued_seconds_ago": int(now - c.queued_at),
                }
                for c in self._queue.values()
            ],
        }


# Module-level singleton
pending_queue = PendingQueue()
