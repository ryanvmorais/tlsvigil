"""
What a channel did with its plan in one run (spec 005, RF-12).

Each channel (:mod:`tlsvigil.github`, and the Telegram and e-mail channels to come) gives one
:class:`Delivery`; the orchestration reports them and turns a failed one into the exit code ``4``.
The types live apart from the orchestration so that it can import the channels and the channels
can import these without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tlsvigil.config import Channel

__all__ = ["Delivery", "DeliveryOutcome"]


class DeliveryOutcome(StrEnum):
    """
    How a delivery ended.

    Attributes:
        SENT (str): The channel was sent a message: an issue, a comment, a Telegram message or
            an e-mail.
        UNCHANGED (str): There was nothing to tell; the issue may have been brought up to date.
        FAILED (str): A request failed or was refused; ``message`` says why.
    """

    SENT = "sent"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Delivery:
    """
    The delivery to one channel.

    Attributes:
        channel (Channel): The channel.
        outcome (DeliveryOutcome): How it ended.
        message (str | None): Why it failed, as plain text in the language of the run;
            ``None`` otherwise.
        issue (int | None): ``github`` only: the number of the issue written to, when there is
            one.
    """

    channel: Channel
    outcome: DeliveryOutcome
    message: str | None = None
    issue: int | None = None
