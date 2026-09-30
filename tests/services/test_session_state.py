from datetime import UTC, datetime

import pytest

from grab.browser.page_api import BrowserPageApi
from grab.errors import (
    SessionExpiredError,
    TransientSessionRefreshError,
    UnknownSessionError,
)
from grab.services.session_state import SessionState, classify_schedule, is_login_url
from grab.utils.rate_limit import RateLimitError, parse_retry_after


@pytest.mark.parametrize(
    ("payload", "state", "failure"),
    [
        ({"result_code": 1, "data": {"schedules": []}}, "VALID", None),
        ({"code": 1, "sch": {}, "dates": {}}, "VALID", None),
        ({"error_code": 10021}, "EXPIRED", "session_expired"),
        ({"msg": "访问过于频繁"}, "RATE_LIMITED", "rate_limited"),
        ({"code": 1}, "UNKNOWN", "schema_drift"),
        ({"code": 1, "sch": {"new": "unknown"}}, "UNKNOWN", "schema_drift"),
        ({"code": 1, "data": {"schedules": [{}]}}, "UNKNOWN", "schema_drift"),
        ({"code": 12345}, "UNKNOWN", "unknown"),
        (None, "UNKNOWN", "schema_drift"),
    ],
)
def test_schedule_classification(payload, state, failure):
    result = classify_schedule(payload)
    assert result.state == SessionState(state)
    assert result.failure_class == failure


@pytest.mark.parametrize(
    ("meta", "error"),
    [
        ({"status": 503}, TransientSessionRefreshError),
        ({"status": 0}, TransientSessionRefreshError),
        ({"status": 429, "retryAfter": "45"}, RateLimitError),
        ({"status": 200, "loginRedirect": True}, SessionExpiredError),
        ({"status": 200, "redirected": True}, UnknownSessionError),
        ({"status": 200, "body": None}, UnknownSessionError),
        ({"status": 403}, UnknownSessionError),
    ],
)
def test_transport_classification(meta, error):
    with pytest.raises(error) as captured:
        BrowserPageApi(None)._unwrap_read_response(
            {"__grab_read_response__": True, **meta}
        )
    assert "SYN_SECRET" not in str(captured.value)
    if error is RateLimitError:
        assert captured.value.retry_after == 45


def test_login_redirect_exact_host_and_path():
    assert is_login_url("https://user.91160.com/login.html?ignored=1")
    assert not is_login_url("https://unknown.invalid/login.html")
    assert not is_login_url("https://user.91160.com/member.html")


def test_retry_after():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert parse_retry_after("Thu, 01 Oct 2026 00:01:00 GMT", now) == 60
    assert parse_retry_after("33") == 33
    for value in [None, "bad", "nan", "inf", "-1"]:
        assert parse_retry_after(value, now) == 0


def test_mixed_representations_do_not_authorize_unvalidated_fallback():
    from grab.models.schemas import DoctorPageTarget
    from grab.services.schedule import ScheduleService

    reader = ScheduleService(None)
    reader.set_target(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url="")
    )
    payload = {
        "result_code": 1,
        "data": {"schedules": []},
        "sch": {
            "changed": {"schedule_id": "slot", "y_state": True, "to_date": "2030-01-01"}
        },
    }
    with pytest.raises(UnknownSessionError, match="schema_drift"):
        reader.parse_doctor_schedule(payload)
