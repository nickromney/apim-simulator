"""Selectable local rate-limit algorithms for classic and v2 gateway labs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel


class RateLimitSettings(BaseModel):
    algorithm: Literal["sliding-window", "token-bucket"] = "sliding-window"


@dataclass
class TokenBucket:
    capacity: int
    renewal_period: int
    updated_at: float
    tokens: float

    def refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.capacity / self.renewal_period)
        self.updated_at = max(self.updated_at, now)

    def __len__(self) -> int:
        # Existing counters expose spent calls; only complete tokens are usable.
        return max(0, math.ceil(self.capacity - self.tokens - 1e-9))

    def append(self, now: float) -> None:
        self.extend([now])

    def extend(self, calls: list[float]) -> None:
        self.tokens = max(0.0, self.tokens - len(calls))

    def retry_after(self) -> int:
        return max(1, math.ceil((1.0 - self.tokens) * self.renewal_period / self.capacity))


def token_bucket(store: dict, key: str, *, now: float, calls: int, renewal_period: int) -> TokenBucket:
    bucket = store.get(key)
    if not isinstance(bucket, TokenBucket) or (bucket.capacity, bucket.renewal_period) != (calls, renewal_period):
        bucket = TokenBucket(calls, renewal_period, now, float(calls))
        store[key] = bucket
    bucket.refill(now)
    return bucket
