from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal


@dataclass
class CanaryPolicy:
    level: Literal["readonly", "prepare", "submit"]
    live_e2e: bool = False
    live_booking: bool = False
    prepare_approved: bool = False
    # Exact in-memory scenario, never persisted or logged.
    approved_submission: tuple | None = None

    def allows_prepare(self):
        return self.level != "readonly" and self.prepare_approved

    def allows_submit(self, target, form):
        return (
            target is not None
            and self.level == "submit"
            and self.live_e2e
            and self.live_booking
            and self.approved_submission == scenario(target, form)
        )


def scenario(target, form):
    return (
        target.unit_id,
        target.dept_id,
        target.doctor_id,
        form.member_id,
        form.schedule_id,
        form.schedule_date,
        form.appointment_value,
    )


_current = ContextVar("canary_policy", default=None)


def current_policy():
    return _current.get()


@contextmanager
def canary_scope(policy):
    token = _current.set(policy)
    try:
        yield policy
    finally:
        _current.reset(token)
