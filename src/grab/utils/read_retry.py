"""One budget owned by the read-only poller, surviving new generator rounds."""

import random

from grab.errors import ReadRetryExhausted


class ReadRetryBudget:
    def __init__(self, *, max_failures=5, base=1.0, cap=30.0, uniform=None):
        self.max_failures = max_failures
        self.base = base
        self.cap = cap
        self.uniform = uniform or random.uniform
        self.failures = 0
        self.rate_limits = 0

    def valid_response(self):
        self.failures = self.rate_limits = 0

    def failed(
        self, *, rate_limited=False, poll_floor=3.0, cooldown=0.0, retry_after=0.0
    ):
        self.failures += 1
        if rate_limited:
            self.rate_limits += 1
        if self.failures >= self.max_failures or self.rate_limits >= self.max_failures:
            raise ReadRetryExhausted("Read-only consecutive failure budget exhausted.")
        jitter = self.uniform(0, min(self.cap, self.base * 2 ** (self.failures - 1)))
        return max(poll_floor, cooldown if rate_limited else 0, retry_after, jitter)
