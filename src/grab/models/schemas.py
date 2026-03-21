from dataclasses import dataclass
from datetime import datetime


@dataclass
class Slot:
    hospital: str
    department: str
    doctor: str
    date: str
    time_range: str
    status: str
    unit_id: str = ""
    dep_id: str = ""
    doc_id: str = ""


@dataclass
class Patient:
    name: str
    id_card: str
    phone: str
    patient_id: str = ""


@dataclass
class GrabConfig:
    username: str
    password: str
    hospital_name: str
    department: str
    doctor: str | None
    target_date: str
    patient_name: str
    interval_seconds: int = 5
    max_retries: int = 100
