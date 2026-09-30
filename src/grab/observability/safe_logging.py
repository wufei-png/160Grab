"""Only fixed application messages reach loguru; no exception rendering."""

from loguru import logger as _logger

from grab.observability.privacy import EVENTS, event_message, safe_level

MESSAGES = frozenset(
    [
        "Aggressive session refresh failed while recovering missing _user_key/access_hash.",
        "Booking form selection completed.",
        "Booking page exposed no appointment time options.",
        "Booking page indicates success.",
        "Booking page time options found.",
        "Booking submit completion wait raced with navigation; continuing",
        "Booking submit diagnostics were unavailable because the page kept navigating.",
        "Booking submit did not leave booking form within 5s",
        "Booking submit follow-up action.",
        "Booking submit navigation timed out.",
        "Booking submit network response received.",
        "Booking submit page diagnostics collected.",
        "Booking submit trigger raced with navigation; continuing with page state checks",
        "Booking submit control triggered.",
        "Booking succeeded.",
        "Browser closed",
        "Browser launched",
        "Browser page ready.",
        "Captured doctor target.",
        "Rate limit cooldown started.",
        "Could not read _user_key from page (navigation interrupted JS context); falling back to cookies / cached key.",
        "Deprecated top-level config field 'ocr' is ignored by manual login; remove it from your configuration.",
        "Deprecated top-level config field 'password' is ignored by manual login; remove it from your configuration.",
        "Deprecated top-level config field 'username' is ignored by manual login; remove it from your configuration.",
        "Diagnostic event.",
        "160Grab started.",
        "Doctor schedule response received.",
        "Structured event sink initialization failed; JSONL disabled.",
        "Fetching doctor schedule.",
        "Hour filter mismatch detected.",
        "Invalid configuration; check the configured fields and types.",
        "New page preparation failed.",
        "No appointment time matched filters.",
        "Page.wait_for_load_state failed.",
        "Persistent browser context launched.",
        "Polling attempt.",
        "Polling parse result.",
        "Rate limit detected during booking attempt.",
        "Rate limit detected during schedule polling.",
        "Member selection resolved.",
        "Run failed",
        "Diagnostic snapshot saved locally.",
        "Schedule polling could not resolve _user_key/access_hash. Attempting aggressive session refresh before failing.",
        "Scheduler ready. Starting schedule polling.",
        "Appointment time selected.",
        "Session expired during schedule polling.",
        "Session keepalive completed.",
        "Session keepalive failed but polling will continue.",
        "Session recovered; resuming schedule polling.",
        "Skipping booking retries.",
        "Skipping booking submit follow-up checks because the page kept navigating.",
        "Skipping page preparation because target closed before stealth finished.",
        "Skipping diagnostic snapshot.",
        "Sleeping.",
        "Structured event sink failed; JSONL disabled.",
        "Structured run events are disabled because the JSONL sink could not be initialized",
        "Structured run event output enabled.",
        "Using persistent browser profile.",
        "Using transient browser context (persistent disabled)",
        "请在浏览器中手动完成登录，并导航到目标医生页。程序会在后续确认时读取当前 URL。",
    ]
) | frozenset(event_message(event) for event in EVENTS)


class SafeLogger:
    def __getattr__(self, level):
        if level not in {"debug", "info", "warning", "error", "critical", "exception"}:
            raise AttributeError(level)

        def emit(message, *args, **kwargs):
            safe = (
                message
                if isinstance(message, str) and message in MESSAGES
                else "Diagnostic event."
            )
            _logger.log(
                safe_level("error" if level == "exception" else level).upper(), safe
            )

        return emit

    def remove(self, *args):
        return _logger.remove(*args)

    def add(self, sink, **kwargs):
        kwargs.update(backtrace=False, diagnose=False)
        return _logger.add(sink, **kwargs)


logger = SafeLogger()
