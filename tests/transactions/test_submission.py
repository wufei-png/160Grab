import asyncio

import pytest

from grab.models.schemas import (
    BookingForm,
    BookingState,
    DoctorPageTarget,
    GrabConfig,
    Slot,
)
from grab.services.booking import BookingService, PageBookingStrategy
from grab.transactions.evidence import BookingEvidence
from grab.transactions.store import AttemptStore


class Control:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def click(self, **kwargs):
        assert self.page.store.pending()[0]["state"] == "SUBMITTING"
        self.page.clicks += 1
        if self.page.error:
            raise self.page.error


class Page:
    def __init__(self, store, error=None):
        self.store, self.error, self.clicks = store, error, 0

    async def content(self):
        return '<input name="schedule_id" value="slot"><input type="radio" name="mid" value="member" checked><ul id="delts"><li val="time" class="selected">Synthetic time</li></ul><button id="submitbtn">Submit</button>'

    def locator(self, selector):
        return Control(self)


def strategy(tmp_path, *, error=None, authorized=True, adapter=None):
    store = AttemptStore(tmp_path)
    page = Page(store, error)
    obj = PageBookingStrategy(
        page,
        attempt_store=store,
        authorization=lambda *_: authorized,
        evidence_adapter=adapter,
    )
    obj.prepare(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doctor", source_url=""),
        "member",
    )
    return obj


def form():
    return BookingForm(member_id="member", schedule_id="slot", appointment_value="time")


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("synthetic timeout"),
        RuntimeError("Execution context was destroyed"),
        ConnectionError("lost response"),
        RuntimeError("429"),
        None,
    ],
)
async def test_one_click_and_restart_block_all_targets(tmp_path, error):
    obj = strategy(tmp_path, error=error)
    result = await obj.submit_open_form(form())
    assert result.state == BookingState.OUTCOME_UNKNOWN
    assert obj.page.clicks == 1
    result = await obj.submit_with_retry("other-slot")
    assert result.state == BookingState.OUTCOME_UNKNOWN
    restarted = strategy(tmp_path)
    assert (
        await restarted.submit_open_form(form())
    ).state == BookingState.OUTCOME_UNKNOWN
    assert restarted.page.clicks == 0


async def test_no_authorization_no_click(tmp_path):
    obj = strategy(tmp_path, authorized=False)
    assert (
        await obj.submit_open_form(form())
    ).state == BookingState.AWAITING_MANUAL_CONFIRMATION
    assert obj.page.clicks == 0
    assert obj.attempt_store.pending() == []


async def test_journal_write_failure_never_clicks(tmp_path, monkeypatch):
    obj = strategy(tmp_path)
    obj.attempt_store.read()
    monkeypatch.setattr(
        "grab.utils.private_files.os.replace",
        lambda *_args, **_kw: (_ for _ in ()).throw(OSError()),
    )
    assert (await obj.submit_open_form(form())).state == BookingState.OUTCOME_UNKNOWN
    assert obj.page.clicks == 0


@pytest.mark.parametrize(
    "state", [BookingState.CONFIRMED_SUCCESS, BookingState.CONFIRMED_NO_EFFECT]
)
@pytest.mark.parametrize("matched", [True, False])
async def test_only_matched_business_evidence_can_resolve(tmp_path, state, matched):
    async def adapter(page, target, selected):
        return BookingEvidence(
            state, "u", "d", "doctor", "member" if matched else "wrong", "slot", "time"
        )

    obj = strategy(tmp_path, adapter=adapter)
    result = await obj.submit_open_form(form())
    assert result.state == (state if matched else BookingState.OUTCOME_UNKNOWN)
    assert obj.page.clicks == 1


async def test_cancel_leaves_persistent_blocker(tmp_path):
    obj = strategy(tmp_path, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await obj.submit_open_form(form())
    assert obj.page.clicks == 1
    assert obj.attempt_store.pending()


async def test_preparation_retries_capped_at_three_without_click(tmp_path):
    obj = strategy(tmp_path)
    calls = []

    async def unavailable(_slot):
        calls.append(_slot)
        raise TimeoutError()

    obj.open_booking_form = unavailable
    assert (
        await obj.submit_with_retry("slot", 99)
    ).state == BookingState.AWAITING_MANUAL_CONFIRMATION
    assert len(calls) == 3
    assert obj.page.clicks == 0


async def test_service_never_moves_slot_after_unknown(tmp_path):
    obj = strategy(tmp_path)

    async def open_form(slot):
        return form()

    obj.open_booking_form = open_form
    result = await BookingService(obj).try_book_first_available(
        [Slot(schedule_id="slot"), Slot(schedule_id="other")]
    )
    assert result.state == BookingState.OUTCOME_UNKNOWN
    assert obj.page.clicks == 1


async def test_revocation_during_prepare_wait_prevents_click_without_regrant(tmp_path):
    from grab.transactions.consent import ConsentManager

    obj = strategy(tmp_path)
    prompts = []
    consent = ConsentManager(
        obj.attempt_store,
        prompt=lambda text: prompts.append(text) or "AUTHORIZE",
        interactive=True,
    )
    obj.consent_manager = consent
    obj.authorization = consent.ensure
    obj.config = GrabConfig(page_action_sleep_time="1")

    async def revoke_during_wait(_seconds):
        obj.attempt_store.revoke()

    obj._sleep = revoke_during_wait
    result = await obj.submit_open_form(form())
    assert result.state == BookingState.AWAITING_MANUAL_CONFIRMATION
    assert obj.page.clicks == 0
    assert len(prompts) == 1
    assert not obj.attempt_store.pending()


def real_reporter(tmp_path):
    from grab.observability.notifications import (
        NotificationManager,
        NullDesktopNotifier,
    )
    from grab.observability.reporter import JsonlEventSink, RunReporter

    return RunReporter(
        sink=JsonlEventSink(tmp_path / "logs", "a" * 12),
        notification_manager=NotificationManager(
            desktop_notifier=NullDesktopNotifier()
        ),
        rate_limit_threshold=3,
    )


async def test_invalid_form_with_real_reporter_remains_manual(tmp_path):
    obj = strategy(tmp_path)
    obj.reporter = real_reporter(tmp_path)

    async def invalid_form(_slot):
        return BookingForm(member_id="member", schedule_id="slot", is_valid=False)

    obj.open_booking_form = invalid_form
    result = await obj.submit_with_retry("slot")
    assert result.state == BookingState.AWAITING_MANUAL_CONFIRMATION
    assert result.exit_code == 2
    assert obj.page.clicks == 0
    assert '"event": "booking_submit_failed"' in obj.reporter.jsonl_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "outcome",
    [
        BookingState.CONFIRMED_SUCCESS,
        BookingState.CONFIRMED_NO_EFFECT,
        BookingState.OUTCOME_UNKNOWN,
    ],
)
async def test_submission_records_terminal_event_with_real_reporter(tmp_path, outcome):
    import json

    async def adapter(_page, _target, _selected):
        return BookingEvidence(outcome, "u", "d", "doctor", "member", "slot", "time")

    obj = strategy(tmp_path, adapter=adapter)
    obj.reporter = real_reporter(tmp_path)
    result = await obj.submit_open_form(form())
    assert result.state == outcome
    events = [
        json.loads(line) for line in obj.reporter.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == (
        "booking_succeeded" if result.success else "booking_submit_failed"
    )
    assert events[-1]["data"]["state"] == outcome
    assert events[-1]["data"]["attempt_id"] == result.attempt_id
