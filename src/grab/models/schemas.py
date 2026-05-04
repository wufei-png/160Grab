from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

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


class GrabConfig(BaseModel):
    username: str | None = None
    password: str | None = None
    member_id: str | None = None
    doctor_ids: list[str] = Field(default_factory=list)
    weeks: list[int] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    hours: list[str] = Field(default_factory=list)
    sleep_time: str = "3000"
    brush_start_date: date | None = None
    enable_appoint: bool = False
    appoint_time: datetime | None = None
    booking_strategy: str = "page"
    auth: AuthConfig = Field(default_factory=AuthConfig)
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
    is_valid: bool = True


class BookingResult(BaseModel):
    success: bool
    attempts: int
    slot_id: str | None = None


class RunResult(BaseModel):
    success: bool
    booked_slot_id: str | None = None
