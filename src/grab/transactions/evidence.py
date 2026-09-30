"""A seam for verified read-only business evidence adapters (live work: S06).

No DOM marker or server message is trusted by default.
"""

from dataclasses import dataclass

from grab.models.schemas import BookingForm, BookingState, DoctorPageTarget


@dataclass(frozen=True)
class BookingEvidence:
    state: BookingState
    unit_id: str
    dept_id: str
    doctor_id: str
    member_id: str
    schedule_id: str
    appointment_value: str | None

    def matches(self, target: DoctorPageTarget, form: BookingForm) -> bool:
        return self.state in {
            BookingState.CONFIRMED_SUCCESS,
            BookingState.CONFIRMED_NO_EFFECT,
        } and (
            self.unit_id,
            self.dept_id,
            self.doctor_id,
            self.member_id,
            self.schedule_id,
            self.appointment_value,
        ) == (
            target.unit_id,
            target.dept_id,
            target.doctor_id,
            form.member_id,
            form.schedule_id,
            form.appointment_value,
        )
