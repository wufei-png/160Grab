import re
from typing import Protocol

from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from grab.models.schemas import BookingForm, BookingResult, DoctorPageTarget, GrabConfig
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
    ):
        self.page = page
        self.config = config
        self._sleep = sleep
        self.debug_snapshot = debug_snapshot
        self.reporter = reporter
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
        self.target = target
        self.member_id = member_id

    def parse_booking_form(self, booking_page_html: str, member_id: str) -> BookingForm:
        schedule_match = re.search(
            r'name="schedule_id"\s+value="([^"]+)"',
            booking_page_html,
        )
        schedule_id = schedule_match.group(1) if schedule_match else ""
        appointment_options = self._parse_appointment_options(booking_page_html)
        appointment_value, appointment_label = self._select_appointment_option(
            booking_page_html,
            appointment_options,
        )
        if appointment_options:
            logger.info(
                "Booking page time options for schedule {}: {}",
                schedule_id or "<missing>",
                [label for _, label in appointment_options],
            )
        if self._requires_precise_appointment_selection():
            if appointment_label is not None:
                logger.info(
                    "Selected appointment time for schedule {}: {}",
                    schedule_id or "<missing>",
                    appointment_label,
                )
            else:
                logger.info(
                    "No appointment time matched filters {} for schedule {}",
                    self.config.hours if self.config is not None else [],
                    schedule_id or "<missing>",
                )
        return BookingForm(
            member_id=member_id,
            schedule_id=schedule_id,
            appointment_value=appointment_value,
            appointment_label=appointment_label,
            is_valid=bool(schedule_id)
            and (
                not self._requires_precise_appointment_selection()
                or appointment_value is not None
            ),
        )

    def _requires_precise_appointment_selection(self) -> bool:
        return bool(self.config and self.config.hours)

    def _select_appointment_option(
        self,
        booking_page_html: str,
        options: list[tuple[str, str]] | None = None,
    ) -> tuple[str | None, str | None]:
        options = options if options is not None else self._parse_appointment_options(
            booking_page_html
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
        delts_match = re.search(
            r'<(?:ul|div)[^>]*id="delts"[^>]*>(?P<body>.*?)</(?:ul|div)>',
            booking_page_html,
            re.S,
        )
        body = delts_match.group("body") if delts_match else booking_page_html
        raw_options = re.findall(
            r'<li[^>]*\bval="(?P<value>[^"]+)"[^>]*>(?P<label>.*?)</li>',
            body,
            re.S,
        )
        options: list[tuple[str, str]] = []
        for value, raw_label in raw_options:
            label = re.sub(r"<[^>]+>", "", raw_label).strip()
            if value.strip() and label:
                options.append((value.strip(), label))
        return options

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

    async def fetch_booking_form(self, slot_id: str) -> BookingForm:
        await self._sleep_page_action("opening booking form")
        await self.page.goto(self.build_booking_url(slot_id))
        html = await self.page.content()
        raise_if_rate_limited(html, context="booking form page")
        return self.parse_booking_form(html, member_id=self.member_id)

    def build_booking_url(self, slot_id: str) -> str:
        if self.target is None or self.member_id is None:
            raise RuntimeError("Call prepare() before opening booking forms")
        return (
            "https://www.91160.com/guahao/ystep1/"
            f"uid-{self.target.unit_id}/depid-{self.target.dept_id}/schid-{slot_id}.html"
        )

    async def fill_booking_form(self, form: BookingForm) -> None:
        if not hasattr(self.page, "evaluate"):
            return

        selection_result = await self.page.evaluate(
            """({ memberId, appointmentValue }) => {
                const clickElement = (element) => {
                    if (!element) return false;
                    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    if ('checked' in element) {
                        element.checked = true;
                        element.setAttribute('checked', 'checked');
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    return true;
                };

                let appointmentSelected = false;
                if (appointmentValue) {
                    const options = Array.from(document.querySelectorAll('#delts li[val]'));
                    const target = options.find(
                        (item) => (item.getAttribute('val') || '').trim() === appointmentValue
                    );
                    if (target) {
                        appointmentSelected = clickElement(target);
                    }
                }

                const hiddenSelectors = [
                    'input[name="member_id"]',
                    '#member_id',
                    'input[name="memberId"]',
                    '#memberId',
                    'input[name="mid"]',
                    '#mid',
                    'input[name="his_mem_id"]',
                    '#his_mem_id',
                ];
                hiddenSelectors.forEach((selector) => {
                    const input = document.querySelector(selector);
                    if (input) {
                        input.value = memberId;
                    }
                });

                const radioSelectors = [
                    `input[type="radio"][value="${memberId}"]`,
                    `input[name="mid"][value="${memberId}"]`,
                    `input[name="member_id"][value="${memberId}"]`,
                    `input[data-member-id="${memberId}"]`,
                    `input[data-mid="${memberId}"]`,
                ];
                let memberSelected = false;
                for (const selector of radioSelectors) {
                    const input = document.querySelector(selector);
                    if (input) {
                        memberSelected = clickElement(input);
                        if (memberSelected) break;
                    }
                }

                if (!memberSelected) {
                    const allRadios = Array.from(document.querySelectorAll('input[type="radio"]'));
                    if (allRadios.length === 1) {
                        memberSelected = clickElement(allRadios[0]);
                    }
                }

                const selectors = [
                    'input[name="disease_input"]',
                    '#disease_input',
                    'textarea[name="disease_content"]',
                    '#disease_content',
                    'input[name="accept"][value="1"]',
                    '#check_yuyue_rule',
                ];
                selectors.forEach((selector) => {
                    const input = document.querySelector(selector);
                    if (input) {
                        if (input.type === 'radio' || input.type === 'checkbox') {
                            input.checked = true;
                            input.setAttribute('checked', 'checked');
                        } else if (!input.value) {
                            input.value = '11111111111111';
                        }
                    }
                });
                return { appointmentSelected, memberSelected };
            }""",
            {
                "memberId": form.member_id,
                "appointmentValue": form.appointment_value,
            },
        )
        logger.info(
            "Booking form selection result: appointment_selected={}, member_selected={}, appointment_label={}",
            selection_result.get("appointmentSelected"),
            selection_result.get("memberSelected"),
            form.appointment_label,
        )

    async def _trigger_submit_control(self) -> dict:
        return await self.page.evaluate(
            """() => {
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clickElement = (element) => {
                    if (!element) return false;
                    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    return true;
                };

                const candidateSelectors = [
                    '#suborder #submitbtn',
                    '#submitbtn',
                    '#submit_booking',
                    '#submitBooking',
                    '#sub',
                    '#submit',
                    '#suborder button[type="submit"]',
                    '#suborder input[type="submit"]',
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'button.btn_submit',
                    'button.sub-btn',
                    'input.sub-btn',
                ];
                for (const selector of candidateSelectors) {
                    const element = document.querySelector(selector);
                    if (isVisible(element)) {
                        clickElement(element);
                        return { method: 'selector', target: selector };
                    }
                }

                const textCandidates = Array.from(
                    document.querySelectorAll('button, input[type="button"], input[type="submit"], a')
                ).filter((element) => {
                    const text = (element.textContent || element.value || '').trim();
                    return isVisible(element) && /确认预约|提交预约|提交|预约|下一步/.test(text);
                });
                if (textCandidates.length > 0) {
                    const element = textCandidates[0];
                    clickElement(element);
                    return {
                        method: 'text-match',
                        target: (element.textContent || element.value || '').trim(),
                    };
                }

                const form = document.querySelector('form');
                if (form) {
                    if (typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                        return { method: 'requestSubmit', target: 'form' };
                    }
                    form.submit();
                    return { method: 'submit', target: 'form' };
                }

                return { method: 'not-found', target: null };
            }"""
        )

    async def _collect_booking_page_diagnostics(self) -> dict:
        return await self.page.evaluate(
            """() => {
                const textOf = (node) => (node?.textContent || node?.value || '').trim();
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                };
                const selectedTimes = Array.from(document.querySelectorAll('#delts li'))
                    .filter((item) =>
                        item.classList.contains('cur')
                        || item.classList.contains('active')
                        || item.classList.contains('selected')
                        || item.querySelector('input:checked')
                    )
                    .map((item) => textOf(item));
                const checkedMembers = Array.from(document.querySelectorAll('input[type="radio"]:checked'))
                    .map((item) => ({
                        name: item.getAttribute('name') || '',
                        value: item.value || '',
                    }));
                const hiddenFieldSelectors = [
                    'input[name="mid"]',
                    'input[name="member_id"]',
                    'input[name="his_mem_id"]',
                    'input[name="detlid"]',
                    '#detlid_realtime',
                    '#level_code',
                    'input[name="sch_data"]',
                ];
                const hiddenFields = hiddenFieldSelectors.map((selector) => {
                    const node = document.querySelector(selector);
                    return {
                        selector,
                        value: node ? (node.value || '') : '',
                    };
                });
                const visibleMessages = Array.from(document.querySelectorAll(
                    '.wrong,.warning,.import,.fine,.tips,.msg,.message,.error,.err,.layui-layer-content,.select-member-close,.select-vertifycode-close,.tip,.order-tit'
                ))
                    .filter((node) => isVisible(node))
                    .map((node) => textOf(node))
                    .filter(Boolean);
                return {
                    url: window.location.href,
                    title: document.title,
                    hasBookingForm: !!document.querySelector('#suborder'),
                    hasSubmitButton: !!document.querySelector('#suborder #submitbtn, #suborder input[type="submit"], #suborder button[type="submit"]'),
                    selectedTimes,
                    checkedMembers,
                    hiddenFields,
                    visibleMessages,
                };
            }"""
        )

    def _is_booking_success(
        self,
        before_url: str,
        diagnostics: dict,
    ) -> bool:
        current_url = diagnostics.get("url") or before_url
        navigated_away = (
            bool(before_url)
            and current_url != before_url
            and "/guahao/ystep1/" not in current_url
        )
        form_disappeared = diagnostics.get("hasBookingForm") is False
        submit_button_gone = diagnostics.get("hasSubmitButton") is False
        return navigated_away or (form_disappeared and submit_button_gone)

    async def submit_booking_via_page(self, form: BookingForm) -> bool:
        await self._sleep_page_action("submitting booking form")
        if self.debug_snapshot is not None:
            await self.debug_snapshot("booking-form-before-submit")

        before_url = getattr(self.page, "url", "")

        submit_result = None
        submit_response = None
        if hasattr(self.page, "expect_response"):
            try:
                async with self.page.expect_response(
                    lambda response: "ysubmit.html" in response.url
                    and getattr(response.request, "method", "") == "POST",
                    timeout=8000,
                ) as response_info:
                    submit_result = await self._trigger_submit_control()
                submit_response = await response_info.value
            except PlaywrightTimeoutError:
                if submit_result is None:
                    submit_result = await self._trigger_submit_control()
        else:
            submit_result = await self._trigger_submit_control()

        logger.info(
            "Booking submit trigger result: method={}, target={}",
            submit_result.get("method"),
            submit_result.get("target"),
        )
        followup_action = await self.page.evaluate(
            """() => {
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                };
                const click = (element) => {
                    if (!element) return false;
                    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    return true;
                };

                const sure = document.querySelector('#sure');
                if (isVisible(sure)) {
                    click(sure);
                    return 'paymethod-sure';
                }

                const okBtn = document.querySelector('#ok_btn');
                if (isVisible(okBtn)) {
                    click(okBtn);
                    return 'disease-ok';
                }

                return null;
            }"""
        )
        if followup_action:
            logger.info("Booking submit follow-up action: {}", followup_action)
        if submit_result.get("method") == "not-found":
            raise RuntimeError("Could not find a submit control on booking page")
        if submit_response is not None:
            logger.info(
                "Booking submit network response: status={}, ok={}, url={}",
                getattr(submit_response, "status", None),
                getattr(submit_response, "ok", None),
                getattr(submit_response, "url", ""),
            )
        if hasattr(self.page, "wait_for_function"):
            try:
                await self.page.wait_for_function(
                    """(beforeUrl) => {
                        const leftBookingPage = window.location.href !== beforeUrl
                            && !window.location.href.includes('/guahao/ystep1/');
                        const bookingFormGone = !document.querySelector('#suborder');
                        return leftBookingPage || bookingFormGone;
                    }""",
                    arg=before_url,
                    timeout=5000,
                )
            except PlaywrightTimeoutError:
                logger.info("Booking submit did not leave booking form within 5s")
        html = await self.page.content()
        raise_if_rate_limited(html, context="booking submit page")
        if self.reporter is not None:
            self.reporter.reset_rate_limit_streak()
        diagnostics = await self._collect_booking_page_diagnostics()
        success = self._is_booking_success(before_url, diagnostics)
        if success:
            logger.info(
                "Booking page indicates success: schedule_id={}, appointment_label={}, url={}",
                form.schedule_id,
                form.appointment_label,
                diagnostics.get("url"),
            )
            if self.reporter is not None:
                await self.reporter.emit_event(
                    "booking_succeeded",
                    level="info",
                    message=(
                        f"Booking succeeded for schedule {form.schedule_id}"
                        + (
                            f" at {form.appointment_label}"
                            if form.appointment_label
                            else ""
                        )
                        + "."
                    ),
                    data={
                        "schedule_id": form.schedule_id,
                        "appointment_label": form.appointment_label,
                        "url": diagnostics.get("url"),
                    },
                    notify=True,
                    notification_title="160Grab 挂号成功",
                    notification_severity="info",
                )
        else:
            logger.info(
                "Booking submit page diagnostics: url={}, title={}, has_booking_form={}, has_submit_button={}, selected_times={}, checked_members={}, hidden_fields={}, visible_messages={}",
                diagnostics.get("url"),
                diagnostics.get("title"),
                diagnostics.get("hasBookingForm"),
                diagnostics.get("hasSubmitButton"),
                diagnostics.get("selectedTimes"),
                diagnostics.get("checkedMembers"),
                diagnostics.get("hiddenFields"),
                diagnostics.get("visibleMessages"),
            )
            if self.debug_snapshot is not None:
                await self.debug_snapshot("booking-submit-not-success")
            if self.reporter is not None:
                await self.reporter.emit_event(
                    "booking_submit_failed",
                    level="warning",
                    message=(
                        f"Booking submit did not complete successfully for "
                        f"schedule {form.schedule_id}."
                    ),
                    data={
                        "schedule_id": form.schedule_id,
                        "appointment_label": form.appointment_label,
                        "diagnostics": diagnostics,
                    },
                )
        return success

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

    async def submit_open_form(self, form: BookingForm) -> BookingResult:
        success = await self.submit_booking_via_page(form)
        return BookingResult(success=success, attempts=1, slot_id=form.schedule_id)

    async def submit_with_retry(
        self,
        slot_id: str,
        max_attempts: int = 3,
    ) -> BookingResult:
        for attempt in range(1, max_attempts + 1):
            try:
                form = await self.open_booking_form(slot_id)
                if self.reporter is not None:
                    self.reporter.reset_rate_limit_streak()
                if not form.is_valid:
                    if self.reporter is not None:
                        await self.reporter.emit_event(
                            "booking_submit_failed",
                            level="warning",
                            message=(
                                f"Booking form is invalid for schedule {slot_id} "
                                f"on attempt {attempt}."
                            ),
                            data={
                                "schedule_id": slot_id,
                                "attempt": attempt,
                            },
                        )
                    if attempt < max_attempts:
                        await self._sleep_retry_gap(attempt)
                    continue
                if await self.submit_booking_via_page(form):
                    return BookingResult(
                        success=True, attempts=attempt, slot_id=slot_id
                    )
            except RateLimitError as exc:
                logger.warning(
                    "Rate limit detected during booking attempt {} for slot {}: {}",
                    attempt,
                    slot_id,
                    exc.message,
                )
                if self.reporter is not None:
                    await self.reporter.record_rate_limit(
                        context="booking",
                        message=exc.message,
                        data={"attempt": attempt, "slot_id": slot_id},
                    )
                if attempt < max_attempts:
                    await self._sleep_rate_limit_gap()
                continue

            if attempt < max_attempts:
                await self._sleep_retry_gap(attempt)

        return BookingResult(success=False, attempts=max_attempts, slot_id=slot_id)

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
        logger.debug("Sleeping {} ms {}", delay_ms, reason)
        await self._sleep(delay_ms / 1000)


class BookingService:
    def __init__(self, page_strategy: PageBookingStrategy, strategy_name: str = "page"):
        if strategy_name != "page":
            raise NotImplementedError("Only page booking strategy is implemented")
        self.strategy_name = strategy_name
        self.page_strategy = page_strategy

    def prepare(self, target: DoctorPageTarget, member_id: str) -> None:
        self.page_strategy.prepare(target, member_id)

    async def try_book_first_available(self, slots) -> BookingResult:
        if not slots:
            return BookingResult(success=False, attempts=0, slot_id=None)
        total_attempts = 0
        for slot in slots:
            result = await self.page_strategy.submit_with_retry(slot.schedule_id)
            total_attempts += result.attempts
            if result.success:
                return result
        return BookingResult(success=False, attempts=total_attempts, slot_id=None)

    async def open_booking_form(self, slot) -> BookingForm:
        return await self.page_strategy.open_booking_form(slot.schedule_id)

    async def submit_open_form(self, form: BookingForm) -> BookingResult:
        return await self.page_strategy.submit_open_form(form)
