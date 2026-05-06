from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from grab.utils.profile_name import validate_profile_name
from grab.utils.runtime import normalize_hour_value


class Slot(BaseModel):
    schedule_id: str = ""
    doctor_id: str = ""
    weekday: int = 0
    day_period: str = ""
    hospital: str = ""
    department: str = ""
    doctor: str = ""
    date: str = ""
    time_range: str = ""
    status: str = ""
    unit_id: str = ""
    dep_id: str = ""
    doc_id: str = ""


class Patient(BaseModel):
    name: str
    id_card: str
    phone: str
    patient_id: str = ""


class OcrConfig(BaseModel):
    base_url: str


class AuthConfig(BaseModel):
    strategy: str = "manual"


class BrowserConfig(BaseModel):
    stealth: bool = True
    launch_persistent_context: bool = True
    profile_name: str | None = None
    profiles_root_dir: str = "~/.160grab/browser-profiles"

    @field_validator("profile_name", mode="before")
    @classmethod
    def normalize_profile_name(cls, value):
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            return None
        return validate_profile_name(candidate)

    @field_validator("profiles_root_dir", mode="before")
    @classmethod
    def normalize_profiles_root_dir(cls, value):
        if value is None:
            return "~/.160grab/browser-profiles"
        candidate = str(value).strip()
        if not candidate:
            raise ValueError("browser.profiles_root_dir cannot be empty")
        return candidate


class LoggingConfig(BaseModel):
    jsonl_dir: str = "~/.160grab/logs"
    heartbeat_interval_seconds: int = 300

    @field_validator("jsonl_dir", mode="before")
    @classmethod
    def normalize_jsonl_dir(cls, value):
        if value is None:
            return "~/.160grab/logs"
        candidate = str(value).strip()
        if not candidate:
            raise ValueError("logging.jsonl_dir cannot be empty")
        return candidate

    @field_validator("heartbeat_interval_seconds")
    @classmethod
    def validate_heartbeat_interval_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("logging.heartbeat_interval_seconds must be positive")
        return value


class WebhookNotificationConfig(BaseModel):
    url: str | None = None
    timeout_seconds: int = 5
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, value):
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            return None
        return candidate

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("notifications.webhook.timeout_seconds must be positive")
        return value


class NotificationsConfig(BaseModel):
    desktop: bool = True
    rate_limit_threshold: int = 3
    webhook: WebhookNotificationConfig = Field(
        default_factory=WebhookNotificationConfig
    )

    @field_validator("rate_limit_threshold")
    @classmethod
    def validate_rate_limit_threshold(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("notifications.rate_limit_threshold must be positive")
        return value


class GrabConfig(BaseModel):
    username: str | None = None
    password: str | None = None
    member_id: str | None = None
    doctor_ids: list[str] = Field(default_factory=list)
    weeks: list[int] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    hours: list[str] = Field(default_factory=list)
    sleep_time: str = "3000"
    page_action_sleep_time: str = "400-900"
    booking_retry_sleep_time: str = "2000-4000"
    rate_limit_sleep_time: str = "15000-25000"
    brush_start_date: date | None = None
    enable_appoint: bool = False
    appoint_time: datetime | None = None
    booking_strategy: str = "page"
    auth: AuthConfig = Field(default_factory=AuthConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    ocr: OcrConfig | None = None

    @field_validator("hours", mode="before")
    @classmethod
    def normalize_hours(cls, value):
        if value is None:
            return []
        return [normalize_hour_value(item) for item in value]


class LoginResult(BaseModel):
    success: bool
    attempts: int


class DoctorPageTarget(BaseModel):
    """
    Represents a parsed doctor page URL target.

    Supports two URL formats:
    1. Full format: https://www.91160.com/doctors/index/unit_id-{unit_id}/dep_id-{dept_id}/docid-{doctor_id}.html
    2. Docid-only format: https://www.91160.com/doctors/index/docid-{doctor_id}.html

    When using docid-only format, unit_id and dept_id will be None and needs_resolution=True,
    indicating that further resolution is required to obtain the full scheduling information.
    """

    unit_id: str | None = None
    dept_id: str | None = None
    doctor_id: str
    source_url: str
    needs_resolution: bool = False


class MemberProfile(BaseModel):
    member_id: str
    name: str
    certified: bool


class BookingForm(BaseModel):
    member_id: str
    schedule_id: str
    appointment_value: str | None = None
    appointment_label: str | None = None
    is_valid: bool = True
    invalid_reason: str | None = None


class BookingResult(BaseModel):
    success: bool
    attempts: int
    slot_id: str | None = None


class RunResult(BaseModel):
    success: bool
    booked_slot_id: str | None = None
