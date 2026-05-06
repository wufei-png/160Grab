from pathlib import Path
from types import SimpleNamespace

import pytest

from grab.models.schemas import BookingResult, GrabConfig, Slot
from grab.services.booking import BookingService, PageBookingStrategy


class FakeReporter:
    def __init__(self):
        self.events: list[dict] = []
        self.rate_limit_resets = 0
        self.rate_limit_events: list[dict] = []

    async def emit_event(self, event: str, **kwargs):
        self.events.append({"event": event, **kwargs})

    def reset_rate_limit_streak(self) -> None:
        self.rate_limit_resets += 1

    async def record_rate_limit(self, **kwargs):
        self.rate_limit_events.append(kwargs)


class FakeBookingPage:
    def __init__(self, booking_html: str, success_html: str):
        self.booking_html = booking_html
        self.success_html = success_html
        self.current_html = booking_html
        self.evaluated: list[dict] = []
        self.submit_attempts = 0
        self.response_status = 302

    async def goto(self, url: str):
        self.last_url = url
        self.current_html = self.booking_html

    async def content(self) -> str:
        return self.current_html

    async def wait_for_load_state(self, _state: str, timeout: int = 0):
        return None

    async def wait_for_function(self, script: str, arg=None, timeout: int = 0, **kwargs):
        return None

    async def click(self, selector: str):
        assert selector == "#submit_booking"
        self.submit_attempts += 1
        if self.submit_attempts == 3:
            self.current_html = self.success_html

    async def evaluate(self, script: str, arg: dict | None = None):
        if arg is not None:
            self.evaluated.append(arg)
            return {
                "appointmentSelected": bool(arg.get("appointmentValue")),
                "memberSelected": True,
            }
        if "candidateSelectors" in script:
            self.submit_attempts += 1
            if self.submit_attempts == 3:
                self.current_html = self.success_html
            return {"method": "selector", "target": "#suborder #submitbtn"}
        if "paymethod-sure" in script or "const sure =" in script:
            return None
        return {
            "url": "https://www.91160.com/guahao/ystep1/uid-u1/depid-d1/schid-sch-1001.html",
            "title": "预约页",
            "hasBookingForm": self.current_html != self.success_html,
            "hasSubmitButton": self.current_html != self.success_html,
            "selectedTimes": ["09:00-09:30"],
            "checkedMembers": [{"name": "mid", "value": "member-1"}],
            "hiddenFields": [],
            "visibleMessages": [],
        }

    def expect_response(self, _predicate, timeout: int = 0):
        page = self

        class _ResponseContext:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            @property
            def value(self_inner):
                async def _get():
                    return SimpleNamespace(
                        status=page.response_status,
                        ok=200 <= page.response_status < 400,
                        url="https://www.91160.com/guahao/ysubmit.html",
                        request=SimpleNamespace(method="POST"),
                    )

                return _get()

        return _ResponseContext()


@pytest.fixture
def booking_page_html():
    return Path("tests/fixtures/booking_page.html").read_text(encoding="utf-8")


@pytest.fixture
def booking_submit_success_html():
    return Path("tests/fixtures/booking_submit_success.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def page_booking_strategy(booking_page_html, booking_submit_success_html):
    page = FakeBookingPage(booking_page_html, booking_submit_success_html)
    strategy = PageBookingStrategy(page=page)
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")
    return strategy


@pytest.fixture
def booking_service(page_booking_strategy):
    return BookingService(page_strategy=page_booking_strategy)


def test_page_booking_strategy_builds_form_from_booking_page(
    page_booking_strategy, booking_page_html
):
    form = page_booking_strategy.parse_booking_form(
        booking_page_html,
        member_id="member-1",
    )

    assert form.member_id == "member-1"
    assert form.schedule_id == "sch-1001"
    assert form.appointment_value == "detl-1"
    assert form.appointment_label == "09:00-09:30"


@pytest.mark.asyncio
async def test_page_booking_strategy_retries_same_slot_three_times(
    page_booking_strategy,
):
    result = await page_booking_strategy.submit_with_retry(
        slot_id="sch-1001",
        max_attempts=3,
    )

    assert result.success is True
    assert result.attempts == 3


def test_booking_service_exposes_page_strategy_only_by_default(booking_service):
    assert booking_service.strategy_name == "page"


@pytest.mark.asyncio
async def test_open_booking_form_prefills_member_id(page_booking_strategy):
    form = await page_booking_strategy.open_booking_form("sch-1001")

    assert form.member_id == "member-1"
    assert page_booking_strategy.page.evaluated[0]["memberId"] == "member-1"
    assert page_booking_strategy.page.evaluated[0]["appointmentValue"] == "detl-1"


@pytest.mark.asyncio
async def test_booking_service_books_first_matching_slot_only(booking_page_html):
    class FakeStrategy:
        def __init__(self):
            self.received_slot_ids: list[str] = []

        async def submit_with_retry(self, slot_id: str, max_attempts: int = 3):
            self.received_slot_ids.append(slot_id)
            return BookingResult(success=True, attempts=1, slot_id=slot_id)

    strategy = FakeStrategy()
    service = BookingService(page_strategy=strategy)

    result = await service.try_book_first_available(
        [
            Slot(schedule_id="sch-1", doctor_id="doc-1", time_range="09:00-09:30"),
            Slot(schedule_id="sch-2", doctor_id="doc-1", time_range="09:30-10:00"),
        ]
    )

    assert result.slot_id == "sch-1"
    assert strategy.received_slot_ids == ["sch-1"]


@pytest.mark.asyncio
async def test_booking_service_tries_later_slots_when_earlier_slot_fails():
    class FakeStrategy:
        def __init__(self):
            self.received_slot_ids: list[str] = []

        async def submit_with_retry(self, slot_id: str, max_attempts: int = 3):
            self.received_slot_ids.append(slot_id)
            return BookingResult(
                success=slot_id == "sch-2",
                attempts=1,
                slot_id=slot_id if slot_id == "sch-2" else None,
            )

    strategy = FakeStrategy()
    service = BookingService(page_strategy=strategy)

    result = await service.try_book_first_available(
        [
            Slot(schedule_id="sch-1", doctor_id="doc-1", time_range=""),
            Slot(schedule_id="sch-2", doctor_id="doc-1", time_range=""),
        ]
    )

    assert result.success is True
    assert result.slot_id == "sch-2"
    assert strategy.received_slot_ids == ["sch-1", "sch-2"]


def test_page_booking_strategy_selects_first_matching_appointment_by_hour_filter(
    booking_page_html, booking_submit_success_html
):
    strategy = PageBookingStrategy(
        page=FakeBookingPage(booking_page_html, booking_submit_success_html),
        config=GrabConfig(hours=["09:30-10:00"]),
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    form = strategy.parse_booking_form(booking_page_html, member_id="member-1")

    assert form.is_valid is True
    assert form.appointment_value == "detl-2"
    assert form.appointment_label == "09:30-10:00"


def test_page_booking_strategy_marks_form_invalid_when_no_appointment_matches_hours(
    booking_page_html, booking_submit_success_html
):
    strategy = PageBookingStrategy(
        page=FakeBookingPage(booking_page_html, booking_submit_success_html),
        config=GrabConfig(hours=["13:00-13:30"]),
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    form = strategy.parse_booking_form(booking_page_html, member_id="member-1")

    assert form.is_valid is False
    assert form.appointment_value is None
    assert form.invalid_reason == "hour_filter_mismatch"


def test_page_booking_strategy_marks_form_invalid_when_booking_page_has_no_time_options(
    booking_submit_success_html,
):
    booking_page_without_time_options = """
    <html>
      <body>
        <form id="booking-form">
          <input type="hidden" name="schedule_id" value="sch-1001" />
          <button id="submit_booking" type="button">提交预约</button>
        </form>
      </body>
    </html>
    """
    strategy = PageBookingStrategy(
        page=FakeBookingPage(
            booking_page_without_time_options,
            booking_submit_success_html,
        ),
        config=GrabConfig(hours=["09:00-19:00"]),
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    form = strategy.parse_booking_form(
        booking_page_without_time_options,
        member_id="member-1",
    )

    assert form.is_valid is False
    assert form.invalid_reason == "no_appointment_options"


@pytest.mark.asyncio
async def test_open_booking_form_uses_91160_booking_url_shape(page_booking_strategy):
    await page_booking_strategy.open_booking_form("sch-1001")

    assert (
        page_booking_strategy.page.last_url
        == "https://www.91160.com/guahao/ystep1/uid-u1/depid-d1/schid-sch-1001.html"
    )


def test_prepare_target_must_run_before_opening_form(
    booking_page_html, booking_submit_success_html
):
    strategy = PageBookingStrategy(
        page=FakeBookingPage(booking_page_html, booking_submit_success_html)
    )

    with pytest.raises(RuntimeError):
        strategy.build_booking_url("sch-1001")


@pytest.mark.asyncio
async def test_page_booking_strategy_waits_between_failed_attempts(
    booking_page_html,
    booking_submit_success_html,
):
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    strategy = PageBookingStrategy(
        page=FakeBookingPage(booking_page_html, booking_submit_success_html),
        config=GrabConfig(
            page_action_sleep_time="0",
            booking_retry_sleep_time="2000",
            rate_limit_sleep_time="10000",
        ),
        sleep=fake_sleep,
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    result = await strategy.submit_with_retry(
        slot_id="sch-1001",
        max_attempts=3,
    )

    assert result.success is True
    assert sleep_calls == [2.0, 2.0]


class RateLimitedBookingPage(FakeBookingPage):
    def __init__(self, booking_html: str, success_html: str):
        super().__init__(booking_html, success_html)
        self.goto_calls = 0

    async def goto(self, url: str):
        self.last_url = url
        self.goto_calls += 1
        if self.goto_calls == 1:
            self.current_html = "<html><body>您单位时间内访问次数过多！</body></html>"
            return
        self.current_html = self.booking_html


@pytest.mark.asyncio
async def test_page_booking_strategy_uses_longer_cooldown_after_rate_limit(
    booking_page_html,
    booking_submit_success_html,
):
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    strategy = PageBookingStrategy(
        page=RateLimitedBookingPage(booking_page_html, booking_submit_success_html),
        config=GrabConfig(
            page_action_sleep_time="0",
            booking_retry_sleep_time="2000",
            rate_limit_sleep_time="10000",
        ),
        sleep=fake_sleep,
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    await strategy.submit_with_retry(
        slot_id="sch-1001",
        max_attempts=2,
    )

    assert sleep_calls[0] == 10.0


@pytest.mark.asyncio
async def test_page_booking_strategy_does_not_retry_deterministic_invalid_form(
    booking_submit_success_html,
):
    booking_page_without_time_options = """
    <html>
      <body>
        <form id="booking-form">
          <input type="hidden" name="schedule_id" value="sch-1001" />
          <button id="submit_booking" type="button">提交预约</button>
        </form>
      </body>
    </html>
    """
    sleep_calls: list[float] = []
    reporter = FakeReporter()

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    page = FakeBookingPage(
        booking_page_without_time_options,
        booking_submit_success_html,
    )
    strategy = PageBookingStrategy(
        page=page,
        config=GrabConfig(
            hours=["09:00-19:00"],
            page_action_sleep_time="0",
            booking_retry_sleep_time="2000",
        ),
        sleep=fake_sleep,
        reporter=reporter,
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    result = await strategy.submit_with_retry(
        slot_id="sch-1001",
        max_attempts=3,
    )

    assert result.success is False
    assert result.attempts == 1
    assert sleep_calls == []
    assert reporter.events[-1]["data"]["invalid_reason"] == "no_appointment_options"


class FirstTrySuccessBookingPage(FakeBookingPage):
    async def evaluate(self, script: str, arg: dict | None = None):
        if arg is not None:
            self.evaluated.append(arg)
            return {
                "appointmentSelected": bool(arg.get("appointmentValue")),
                "memberSelected": True,
            }
        if "candidateSelectors" in script:
            self.submit_attempts += 1
            self.current_html = self.success_html
            return {"method": "selector", "target": "#suborder #submitbtn"}
        if "paymethod-sure" in script or "const sure =" in script:
            return None
        return await super().evaluate(script, arg)


@pytest.mark.asyncio
async def test_submit_booking_via_page_emits_success_event_and_notification(
    booking_page_html,
    booking_submit_success_html,
):
    reporter = FakeReporter()
    strategy = PageBookingStrategy(
        page=FirstTrySuccessBookingPage(
            booking_page_html,
            booking_submit_success_html,
        ),
        reporter=reporter,
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    form = await strategy.open_booking_form("sch-1001")
    success = await strategy.submit_booking_via_page(form)

    assert success is True
    assert reporter.events[-1]["event"] == "booking_succeeded"
    assert reporter.events[-1]["notify"] is True


@pytest.mark.asyncio
async def test_submit_booking_via_page_emits_failure_event_with_diagnostics(
    booking_page_html,
    booking_submit_success_html,
):
    reporter = FakeReporter()
    strategy = PageBookingStrategy(
        page=FakeBookingPage(booking_page_html, booking_submit_success_html),
        reporter=reporter,
    )
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")

    form = await strategy.open_booking_form("sch-1001")
    success = await strategy.submit_booking_via_page(form)

    assert success is False
    assert reporter.events[-1]["event"] == "booking_submit_failed"
    assert "diagnostics" in reporter.events[-1]["data"]
