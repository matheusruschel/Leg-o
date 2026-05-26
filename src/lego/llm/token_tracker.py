from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CallType = Literal["analysis", "generation", "fix", "other"]

# USD per 1M tokens. Defaults track Claude Sonnet 4.x list pricing
# at the time of writing; override via TokenTracker(pricing=...) if needed.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "default": (3.0, 15.0),
}


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


class TokenTracker:
    def __init__(
        self,
        model: str = "default",
        pricing: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.model = model
        self.pricing = pricing or DEFAULT_PRICING
        self._by_type: dict[str, _Usage] = {}

    def record(
        self,
        call_type: CallType,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        bucket = self._by_type.setdefault(call_type, _Usage())
        bucket.input_tokens += int(input_tokens or 0)
        bucket.output_tokens += int(output_tokens or 0)
        bucket.calls += 1

    @property
    def total_input_tokens(self) -> int:
        return sum(u.input_tokens for u in self._by_type.values())

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for u in self._by_type.values())

    @property
    def total_calls(self) -> int:
        return sum(u.calls for u in self._by_type.values())

    def estimated_cost(self) -> float:
        in_rate, out_rate = self.pricing.get(self.model, self.pricing["default"])
        return (
            self.total_input_tokens * in_rate / 1_000_000
            + self.total_output_tokens * out_rate / 1_000_000
        )

    def report(self) -> dict:
        by_type = {
            t: {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "calls": u.calls,
            }
            for t, u in self._by_type.items()
        }
        return {
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_calls": self.total_calls,
            "by_type": by_type,
            "estimated_cost_usd": round(self.estimated_cost(), 6),
        }
