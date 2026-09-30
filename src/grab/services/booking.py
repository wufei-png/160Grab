import asyncio
from typing import Protocol

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from grab.booking.form import SUBMIT_SELECTOR, snapshot_html
from grab.booking.page import (
    apply_decision,
    read_decision,
    selected_member_ready,
)
from grab.core.leader import check_leader, exclusive_operation
from grab.models.schemas import (
    BookingForm,
    BookingResult,
    BookingState,
    DoctorPageTarget,
    GrabConfig,
)
from grab.observability.safe_logging import logger
from grab.transactions.store import AttemptStore, StoreBlocked
from grab.utils.rate_limit import RateLimitError, raise_if_rate_limited
from grab.utils.runtime import parse_sleep_time


class BookingStrategy(Protocol):
    async def submit_with_retry(
        self,
        slot_id: str,
        max_attempts: int = 3,
    ) -> BookingResult: ...


class PageBookingStrategy:
    def __init__(
        self,
        page,
        config: GrabConfig | None = None,
        sleep=None,
        debug_snapshot=None,
        reporter=None,
        attempt_store=None,
        authorization=None,
        evidence_adapter=None,
        consent_manager=None,
    ):
        self.page = page
        self.config = config
        self._sleep = sleep
        self.debug_snapshot = debug_snapshot
        self.reporter = reporter
        self.attempt_store = attempt_store or AttemptStore()
        self.authorization = authorization
        self.evidence_adapter = evidence_adapter
        self.consent_manager = consent_manager
        self._run_blocked = False
        self.expected_date: str | None = None
        self.member_id: str | None = None
        self.target: DoctorPageTarget | None = None

    def prepare_target(self, unit_id: str, dept_id: str, member_id: str) -> None:
        self.target = DoctorPageTarget(
            unit_id=unit_id,
            dept_id=dept_id,
            doctor_id="",
            source_url="",
        )
        self.member_id = member_id

    def prepare(self, target: DoctorPageTarget, member_id: str) -> None:
        if self.consent_manager:
            self.consent_manager.new_identity_session()
        self.target = target
        self.member_id = member_id

    def parse_booking_form(self, booking_page_html: str, member_id: str) -> BookingForm:
        snapshot = snapshot_html(booking_page_html)
        schedule_id = (
            snapshot["schedule_ids"][0] if len(snapshot["schedule_ids"]) == 1 else ""
        )
        appointment_options = self._parse_appointment_options(booking_page_html)
        appointment_value, appointment_label = self._select_appointment_option(
            booking_page_html,
            appointment_options,
        )
        invalid_reason = None
        if appointment_options:
            logger.info("Booking page time options found.")
        if self._requires_precise_appointment_selection():
            if appointment_label is not None:
                logger.info("Appointment time selected.")
            elif not appointment_options:
                logger.info("Booking page exposed no appointment time options.")
            else:
                logger.info("No appointment time matched filters.")
        if not schedule_id:
            invalid_reason = "missing_schedule_id"
        elif (
            self._requires_precise_appointment_selection() and appointment_value is None
        ):
            invalid_reason = (
                "hour_filter_mismatch"
                if appointment_options
                else "no_appointment_options"
            )
        return BookingForm(
            member_id=member_id,
            schedule_id=schedule_id,
            appointment_value=appointment_value,
            appointment_label=appointment_label,
            schedule_date=self.expected_date,
            is_valid=invalid_reason is None,
            invalid_reason=invalid_reason,
        )

    def _requires_precise_appointment_selection(self) -> bool:
        return bool(self.config and self.config.hours)

    def _select_appointment_option(
        self,
        booking_page_html: str,
        options: list[tuple[str, str]] | None = None,
    ) -> tuple[str | None, str | None]:
        options = (
            options
            if options is not None
            else self._parse_appointment_options(booking_page_html)
        )
        if not options:
            return None, None

        if not self._requires_precise_appointment_selection():
            return options[0]

        for value, label in options:
            if self._appointment_matches_hours(label):
                return value, label
        return None, None

    def _parse_appointment_options(
        self, booking_page_html: str
    ) -> list[tuple[str, str]]:
        return [
            (t["value"], t["label"])
            for t in snapshot_html(booking_page_html)["times"]
            if t["value"] and t["label"]
        ]

    def _appointment_matches_hours(self, label: str) -> bool:
        if self.config is None or not self.config.hours:
            return True
        slot_range = self._parse_time_range(label)
        if slot_range is None:
            return False
        slot_start, slot_end = slot_range
        return any(
            self._time_ranges_overlap(slot_start, slot_end, filter_range)
            for filter_range in (
                self._parse_time_range(hour_filter) for hour_filter in self.config.hours
            )
            if filter_range is not None
        )

    def _parse_time_range(self, value: str) -> tuple[int, int] | None:
        if not value or "-" not in value:
            return None
        start_text, end_text = value.split("-", maxsplit=1)
        start_minutes = self._parse_time_to_minutes(start_text)
        end_minutes = self._parse_time_to_minutes(end_text)
        if start_minutes is None or end_minutes is None or start_minutes >= end_minutes:
            return None
        return start_minutes, end_minutes

    def _parse_time_to_minutes(self, value: str) -> int | None:
        parts = value.strip().split(":")
        if len(parts) != 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError:
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour * 60 + minute

    def _time_ranges_overlap(
        self,
        slot_start: int,
        slot_end: int,
        filter_range: tuple[int, int],
    ) -> bool:
        filter_start, filter_end = filter_range
        return slot_start < filter_end and slot_end > filter_start

    @exclusive_operation
    async def fetch_booking_form(self, slot_id: str) -> BookingForm:
        await self._sleep_page_action("opening booking form")
        check_leader()
        await self.page.goto(self.build_booking_url(slot_id))
        html = await self.page.content()
        raise_if_rate_limited(html, context="booking form page")
        form = self.parse_booking_form(html, member_id=self.member_id)
        if form.schedule_id != slot_id:
            form.is_valid = False
            form.invalid_reason = "schedule_mismatch"
            form.blockers = ["schedule.mismatch"]
        return form

    def build_booking_url(self, slot_id: str) -> str:
        if self.target is None or self.member_id is None:
            raise RuntimeError("Call prepare() before opening booking forms")
        return (
            "https://www.91160.com/guahao/ystep1/"
            f"uid-{self.target.unit_id}/depid-{self.target.dept_id}/schid-{slot_id}.html"
        )

    @exclusive_operation
    async def fill_booking_form(self, form: BookingForm) -> None:
        # Three bounded DOM preparation passes; deterministic conflicts stop now.
        for attempt in range(3):
            snapshot, decision = await read_decision(self.page, form, self.config)
            if decision["can_prepare"]:
                await apply_decision(self.page, snapshot, decision, guard=check_leader)
                snapshot, decision = await read_decision(self.page, form, self.config)
            form.blockers = decision["blockers"]
            if not form.blockers and selected_member_ready(snapshot, decision):
                return
            deterministic = any(
                b.endswith((".conflict", ".ambiguous", ".mismatch", ".blocked"))
                for b in form.blockers
            )
            if deterministic or attempt == 2:
                break
            await asyncio.sleep(0.25)
        form.is_valid = False
        form.invalid_reason = "required_fields_blocked"

    def blocked_result(self) -> BookingResult | None:
        try:
            if self._run_blocked or self.attempt_store.pending():
                return BookingResult(
                    state=BookingState.OUTCOME_UNKNOWN, failure_class="unknown"
                )
        except StoreBlocked:
            return BookingResult(
                state=BookingState.OUTCOME_UNKNOWN, failure_class="storage"
            )
        return None

    async def _submit_control(self):
        # A unique actionable Locator; no broad text or form.submit fallback.
        control = self.page.locator(SUBMIT_SELECTOR)
        if (
            await control.count() != 1
            or not await control.is_visible()
            or not await control.is_enabled()
        ):
            return None
        return control

    @exclusive_operation
    async def submit_booking_via_page(self, form: BookingForm) -> BookingResult:
        blocked = self.blocked_result()
        if blocked:
            return blocked
        if not form.is_valid or form.member_id != self.member_id or not self.target:
            return BookingResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
        try:
            authorized = self.authorization and self.authorization(
                self.target, form.member_id
            )
        except StoreBlocked:
            return BookingResult(
                state=BookingState.OUTCOME_UNKNOWN, failure_class="storage"
            )
        if not authorized:
            return BookingResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
        await self._sleep_page_action("submitting booking form")
        control = await self._submit_control()
        if control is None:
            return BookingResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
        try:
            snapshot, readiness = await read_decision(self.page, form, self.config)
            if (
                readiness["blockers"]
                or readiness["writes"]
                or not selected_member_ready(snapshot, readiness)
            ):
                return BookingResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
            # Recheck after every preparatory await. Revocation cannot be replaced
            # by another confirmation prompt at this final boundary.
            if self.consent_manager and not self.consent_manager.is_authorized(
                self.target, form.member_id
            ):
                return BookingResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
            if self.config and self.config.booking.submit_mode != "auto":
                return BookingResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
            booking_ref = self.attempt_store.reference(
                self.target.unit_id,
                self.target.dept_id,
                self.target.doctor_id,
                form.member_id,
                form.schedule_id,
                form.appointment_value,
            )
            check_leader()
            attempt_id = self.attempt_store.begin(booking_ref)
        except StoreBlocked:
            self._run_blocked = True
            return BookingResult(
                state=BookingState.OUTCOME_UNKNOWN, failure_class="storage"
            )

        # Everything beyond this boundary can have taken effect. Never retry click,
        # including a timeout raised by Locator.click itself or cancelled navigation.
        outcome = BookingState.OUTCOME_UNKNOWN
        try:
            check_leader()
            await control.click(timeout=5000)
            # No live evidence adapter has been verified yet. Page disappearance,
            # HTTP status, redirects and follow-up/payment controls prove nothing.
            if self.evidence_adapter is not None:
                evidence = await self.evidence_adapter(self.page, self.target, form)
                if evidence and evidence.matches(self.target, form):
                    outcome = evidence.state
        except asyncio.CancelledError:
            self._run_blocked = True
            try:
                self.attempt_store.finish(attempt_id, BookingState.OUTCOME_UNKNOWN)
            except StoreBlocked:
                pass
            raise
        except Exception:
            outcome = BookingState.OUTCOME_UNKNOWN
        if outcome not in {
            BookingState.CONFIRMED_SUCCESS,
            BookingState.CONFIRMED_NO_EFFECT,
        }:
            outcome = BookingState.OUTCOME_UNKNOWN
        try:
            self.attempt_store.finish(attempt_id, outcome)
        except StoreBlocked:
            outcome = BookingState.OUTCOME_UNKNOWN
        self._run_blocked = outcome == BookingState.OUTCOME_UNKNOWN
        result = BookingResult(
            state=outcome, attempts=1, slot_id=form.schedule_id, attempt_id=attempt_id
        )
        if self.reporter is not None:
            try:
                await self.reporter.emit_event(
                    "booking_succeeded" if result.success else "booking_submit_failed",
                    level="info" if result.success else "warning",
                    data={
                        "state": result.state,
                        "attempt_id": attempt_id,
                        "human_action_required": outcome
                        == BookingState.OUTCOME_UNKNOWN,
                    },
                    notify=result.success,
                )
            except Exception:
                pass
        return result

    @exclusive_operation
    async def open_booking_form(self, slot_id: str) -> BookingForm:
        form = await self.fetch_booking_form(slot_id)
        if form.is_valid:
            await self.fill_booking_form(form)
            if self.reporter is not None:
                await self.reporter.emit_event(
                    "booking_form_opened",
                    level="info",
                    message=f"Opened booking form for schedule {slot_id}.",
                    data={
                        "schedule_id": form.schedule_id,
                        "appointment_label": form.appointment_label,
                    },
                )
        return form

    @exclusive_operation
    async def submit_open_form(self, form: BookingForm) -> BookingResult:
        return await self.submit_booking_via_page(form)

    @exclusive_operation
    async def submit_with_retry(
        self, slot_id: str, max_attempts: int = 3
    ) -> BookingResult:
        blocked = self.blocked_result()
        if blocked:
            return blocked
        # The budget belongs exclusively to read-only open/prepare failures.
        budget = min(3, max(1, max_attempts))
        for attempt in range(1, budget + 1):
            try:
                form = await self.open_booking_form(slot_id)
            except (
                PlaywrightTimeoutError,
                TimeoutError,
                ConnectionError,
                RateLimitError,
            ) as exc:
                if attempt < budget:
                    if isinstance(exc, RateLimitError):
                        await self._sleep_rate_limit_gap()
                    else:
                        await self._sleep_retry_gap(attempt)
                    continue
                return BookingResult(
                    state=BookingState.AWAITING_MANUAL_CONFIRMATION,
                    attempts=attempt,
                    failure_class="network",
                )
            if not form.is_valid or form.schedule_id != slot_id:
                if self.reporter is not None:
                    await self.reporter.emit_event(
                        "booking_submit_failed",
                        data={"invalid_reason": form.invalid_reason},
                    )
                return BookingResult(
                    state=BookingState.AWAITING_MANUAL_CONFIRMATION, attempts=attempt
                )
            return await self.submit_open_form(form)
        raise AssertionError("Unreachable preparation budget")

    async def _sleep_page_action(self, action: str) -> None:
        if self.config is None:
            return
        await self._sleep_for(self.config.page_action_sleep_time, f"before {action}")

    async def _sleep_retry_gap(self, attempt: int) -> None:
        if self.config is None:
            return
        await self._sleep_for(
            self.config.booking_retry_sleep_time,
            f"before booking retry attempt {attempt + 1}",
        )

    async def _sleep_rate_limit_gap(self) -> None:
        if self.config is None:
            return
        await self._sleep_for(
            self.config.rate_limit_sleep_time,
            "for booking rate-limit cooldown",
        )

    async def _sleep_for(self, delay_text: str, reason: str) -> None:
        if self._sleep is None:
            return
        delay_ms = parse_sleep_time(delay_text)
        if delay_ms <= 0:
            return
        logger.debug("Sleeping.")
        await self._sleep(delay_ms / 1000)


class BookingService:
    def __init__(self, page_strategy: PageBookingStrategy, strategy_name: str = "page"):
        if strategy_name != "page":
            raise NotImplementedError("Only page booking strategy is implemented")
        self.strategy_name = strategy_name
        self.page_strategy = page_strategy

    def blocked_result(self):
        return self.page_strategy.blocked_result()

    def prepare(self, target: DoctorPageTarget, member_id: str) -> None:
        self.page_strategy.prepare(target, member_id)

    async def try_book_first_available(self, slots) -> BookingResult:
        if not slots:
            return BookingResult(
                state=BookingState.DISCOVERED, attempts=0, slot_id=None
            )
        total_attempts = 0
        for slot in slots:
            self.page_strategy.expected_date = slot.date or None
            result = await self.page_strategy.submit_with_retry(slot.schedule_id)
            total_attempts += result.attempts
            if result.state != BookingState.CONFIRMED_NO_EFFECT:
                return result
        return BookingResult(
            state=BookingState.CONFIRMED_NO_EFFECT,
            attempts=total_attempts,
            slot_id=None,
        )

    @exclusive_operation
    async def open_booking_form(self, slot) -> BookingForm:
        self.page_strategy.expected_date = slot.date or None
        return await self.page_strategy.open_booking_form(slot.schedule_id)

    @exclusive_operation
    async def submit_open_form(self, form: BookingForm) -> BookingResult:
        return await self.page_strategy.submit_open_form(form)
