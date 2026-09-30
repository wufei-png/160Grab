import json
import os

import pytest

from grab.models.schemas import BookingResult, BookingState, RunResult
from grab.transactions.store import AttemptStore, StoreBlocked


def test_result_false_never_implies_retry():
    assert (
        BookingResult(success=False).state == BookingState.AWAITING_MANUAL_CONFIRMATION
    )
    for state, code in [
        (BookingState.CONFIRMED_SUCCESS, 0),
        (BookingState.CONFIRMED_NO_EFFECT, 1),
        (BookingState.AWAITING_MANUAL_CONFIRMATION, 2),
        (BookingState.OUTCOME_UNKNOWN, 3),
    ]:
        result = RunResult(state=state)
        assert result.success == (code == 0)
        assert result.exit_code == code


def test_restart_and_revoke_preserve_pending(tmp_path):
    store = AttemptStore(tmp_path / "transactions")
    ref = store.reference("synthetic-member", "synthetic-doctor", "synthetic-slot")
    attempt = store.begin(ref)
    store = AttemptStore(store.root)
    assert store.pending()[0]["state"] == "SUBMITTING"
    store.revoke()
    with pytest.raises(StoreBlocked):
        store.begin(store.reference("other"))
    store.resolve(attempt, booked=False)
    assert not store.pending()
    assert store.read()["audit"][0]["state"] == "CONFIRMED_NO_EFFECT"
    with pytest.raises(StoreBlocked):
        store.begin(ref)
    raw = (store.root / "journal.json").read_text()
    assert "synthetic-member" not in raw
    assert "synthetic-doctor" not in raw
    if os.name == "posix":
        assert (store.root / "journal.json").stat().st_mode & 0o777 == 0o600
        assert store.root.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        '{"version":99}',
        json.dumps(
            {"version": 1, "salt": "bad", "attempts": [], "consents": [], "audit": []}
        ),
    ],
)
def test_corrupt_fail_closed(tmp_path, raw):
    (tmp_path / "journal.json").write_text(raw)
    with pytest.raises(StoreBlocked):
        AttemptStore(tmp_path).pending()
    assert (tmp_path / "journal.json").read_text() == raw


def test_failed_write_has_no_begin(tmp_path, monkeypatch):
    store = AttemptStore(tmp_path)
    ref = store.reference("synthetic")
    monkeypatch.setattr(
        "grab.utils.private_files.os.fsync", lambda _: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(StoreBlocked):
        store.begin(ref)
    assert not store.pending()
