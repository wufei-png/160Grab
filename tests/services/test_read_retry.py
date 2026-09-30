import asyncio

import pytest

from grab.errors import (
    ReadRetryExhausted,
    SessionExpiredError,
    TransientSessionRefreshError,
    UnknownSessionError,
)
from grab.models.schemas import DoctorPageTarget, GrabConfig
from grab.services.schedule import ScheduleService
from grab.utils.rate_limit import RateLimitError
from grab.utils.read_retry import ReadRetryBudget
from tests.services.test_schedule import SequencedPageApi

VALID = {"result_code": 1, "data": {"schedules": []}}
TARGET = DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url="")


class Responses(SequencedPageApi):
    async def get_json_via_page_ajax(self, path, params):
        self.ajax_calls.append((path, params))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def service(responses, sleep, **kwargs):
    result = ScheduleService(
        Responses(responses),
        GrabConfig(sleep_time="0", rate_limit_sleep_time="12000"),
        sleep=sleep,
        retry_budget=ReadRetryBudget(uniform=lambda a, b: b),
        **kwargs,
    )
    result.set_target(TARGET)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), TransientSessionRefreshError(), RateLimitError("limited", "test")],
)
async def test_five_failed_reads_stop_and_new_round_does_not_reset(failure):
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    reader = service([failure] * 6, sleep)
    with pytest.raises(ReadRetryExhausted):
        await anext(reader.poll())
    assert len(reader.page_api.ajax_calls) == 5
    assert len(delays) == 4
    assert all(d >= (12 if isinstance(failure, RateLimitError) else 3) for d in delays)
    # Restarting the generator does not grant another request after exhaustion.
    with pytest.raises(ReadRetryExhausted):
        await anext(reader.poll())
    assert len(reader.page_api.ajax_calls) == 5


@pytest.mark.asyncio
async def test_only_valid_business_response_resets_budget():
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    reader = service(
        [TimeoutError()] * 4 + [VALID] + [TimeoutError()] * 4 + [VALID], sleep
    )
    gen = reader.poll()
    assert await anext(gen) == []
    assert reader.retry_budget.failures == 0
    assert await anext(gen) == []
    assert len(reader.page_api.ajax_calls) == 10
    await gen.aclose()


@pytest.mark.asyncio
async def test_retry_after_and_cancel_during_sleep_no_extra_request():
    async def sleep(seconds):
        assert seconds == 60
        raise asyncio.CancelledError

    failure = RateLimitError("limited", "test", retry_after=60)
    reader = service([failure, VALID], sleep)
    with pytest.raises(asyncio.CancelledError):
        await anext(reader.poll())
    assert len(reader.page_api.ajax_calls) == 1


@pytest.mark.asyncio
async def test_cancel_during_request_is_not_retried():
    async def sleep(seconds):
        pytest.fail("cancel must not sleep")

    reader = service([asyncio.CancelledError(), VALID], sleep)
    with pytest.raises(asyncio.CancelledError):
        await anext(reader.poll())
    assert len(reader.page_api.ajax_calls) == 1


@pytest.mark.asyncio
async def test_expired_clears_cache_unknown_schema_stops_without_empty_slots():
    async def sleep(seconds):
        pytest.fail("must stop")

    reader = service(
        [{"error_code": 10021}, {"code": 1, "sch": {"new": "shape"}}], sleep
    )
    with pytest.raises(SessionExpiredError):
        await anext(reader.poll())
    assert reader._last_schedule_user_key is None
    with pytest.raises(UnknownSessionError, match="schema_drift"):
        await anext(reader.poll())
    assert len(reader.page_api.ajax_calls) == 2


def test_full_jitter_cap_and_floor():
    seen = []
    budget = ReadRetryBudget(
        max_failures=10, uniform=lambda a, b: seen.append((a, b)) or b
    )
    delays = [budget.failed(poll_floor=3) for _ in range(7)]
    assert delays == [3, 3, 4, 8, 16, 30, 30]
    assert seen == [(0, b) for b in [1, 2, 4, 8, 16, 30, 30]]


@pytest.mark.asyncio
async def test_missing_key_diagnostic_is_low_frequency_without_manual_login():
    from tests.services.test_schedule import MissingKeyPageApi

    calls = []

    async def probe(*args, **kwargs):
        calls.append(kwargs)
        raise TransientSessionRefreshError()

    reader = ScheduleService(
        MissingKeyPageApi(), GrabConfig(), session_refresh=probe, monotonic=lambda: 0
    )
    reader.set_target(TARGET)
    with pytest.raises(TransientSessionRefreshError):
        await reader.fetch_doctor_schedule("2026-10-01")
    with pytest.raises(UnknownSessionError, match="missing_key"):
        await reader.fetch_doctor_schedule("2026-10-01")
    assert len(calls) == 1
    assert reader.page_api.ajax_calls == []
