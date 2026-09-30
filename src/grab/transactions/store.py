"""Global local submission journal. No raw account/member/booking data is stored.

This is an atomic durable store, not a cross-process lock (S05 owns that gate).
Unresolved records are never expired or removed by diagnostic retention.
"""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from grab.core.leader import check_active_leader
from grab.models.schemas import BookingState
from grab.utils.private_files import private_directory

POLICY_VERSION = "submit-v1"
DEFAULT_STATE_DIR = Path("~/.160grab/transactions")
UNRESOLVED = {"SUBMITTING", "OUTCOME_UNKNOWN"}
TERMINAL = {"CONFIRMED_SUCCESS", "CONFIRMED_NO_EFFECT"}


class StoreBlocked(RuntimeError):
    def __init__(self):
        super().__init__("Submission journal requires manual verification")


def timestamp():
    return datetime.now(UTC).isoformat()


class AttemptStore:
    def __init__(self, root=DEFAULT_STATE_DIR):
        self.root = root

    def read(self):
        check_active_leader()
        try:
            with private_directory(self.root) as directory:
                try:
                    raw = directory.read("journal.json")
                except FileNotFoundError:
                    state = {
                        "version": 1,
                        "salt": secrets.token_hex(32),
                        "attempts": [],
                        "consents": [],
                        "audit": [],
                    }
                    directory.replace_durable(
                        "journal.json", json.dumps(state).encode()
                    )
                    return state
                state = json.loads(raw)
                self._validate(state)
                return state
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StoreBlocked() from exc

    @staticmethod
    def _validate(state):
        if (
            set(state) != {"version", "salt", "attempts", "consents", "audit"}
            or state["version"] != 1
        ):
            raise ValueError
        if (
            not isinstance(state["salt"], str)
            or len(bytes.fromhex(state["salt"])) != 32
        ):
            raise ValueError
        if not all(
            isinstance(state[k], list) for k in ("attempts", "consents", "audit")
        ):
            raise ValueError
        for record in state["attempts"]:
            if set(record) != {
                "attempt_id",
                "booking_ref",
                "state",
                "created_at",
                "updated_at",
                "evidence_type",
                "failure_class",
                "human_action_required",
            }:
                raise ValueError
            if record["state"] not in UNRESOLVED | TERMINAL:
                raise ValueError
            if type(record["human_action_required"]) is not bool or record[
                "human_action_required"
            ] != (record["state"] in UNRESOLVED):
                raise ValueError
            for key in ("attempt_id", "booking_ref"):
                if len(bytes.fromhex(record[key])) != (
                    16 if key == "attempt_id" else 32
                ):
                    raise ValueError
            for key in ("created_at", "updated_at"):
                datetime.fromisoformat(record[key])
            if record["evidence_type"] not in {
                "none",
                "matched_business",
                "human_verified",
            }:
                raise ValueError
            if record["failure_class"] not in {
                None,
                "post_submit",
                "business_rejected",
                "interrupted",
            }:
                raise ValueError
            if (record["state"] in TERMINAL) != (record["evidence_type"] != "none"):
                raise ValueError
        for consent in state["consents"]:
            if set(consent) != {"binding_ref", "policy_version"} or consent[
                "policy_version"
            ] not in {"submit-v0", POLICY_VERSION}:
                raise ValueError
            if len(bytes.fromhex(consent["binding_ref"])) != 32:
                raise ValueError
        for event in state["audit"]:
            if (
                set(event) != {"attempt_id", "state", "at"}
                or event["state"] not in TERMINAL
            ):
                raise ValueError
            if len(bytes.fromhex(event["attempt_id"])) != 16:
                raise ValueError
            datetime.fromisoformat(event["at"])

    def write(self, state):
        check_active_leader()
        try:
            self._validate(state)
            with private_directory(self.root) as directory:
                directory.replace_durable("journal.json", json.dumps(state).encode())
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StoreBlocked() from exc

    def reference(self, *parts):
        state = self.read()
        return hmac.new(
            bytes.fromhex(state["salt"]), json.dumps(parts).encode(), hashlib.sha256
        ).hexdigest()

    def pending(self):
        return [r for r in self.read()["attempts"] if r["state"] in UNRESOLVED]

    def begin(self, booking_ref):
        state = self.read()
        if any(
            r["state"] in UNRESOLVED or r["booking_ref"] == booking_ref
            for r in state["attempts"]
        ):
            raise StoreBlocked()
        record = {
            "attempt_id": secrets.token_hex(16),
            "booking_ref": booking_ref,
            "state": "SUBMITTING",
            "created_at": timestamp(),
            "updated_at": timestamp(),
            "evidence_type": "none",
            "failure_class": None,
            "human_action_required": True,
        }
        state["attempts"].append(record)
        self.write(state)
        return record["attempt_id"]

    def finish(self, attempt_id, outcome, *, human=False):
        outcome = BookingState(outcome).value
        if outcome not in TERMINAL | {"OUTCOME_UNKNOWN"}:
            raise StoreBlocked()
        state = self.read()
        record = next(
            (r for r in state["attempts"] if r["attempt_id"] == attempt_id), None
        )
        if record is None or record["state"] not in UNRESOLVED:
            raise StoreBlocked()
        record.update(
            state=outcome,
            updated_at=timestamp(),
            evidence_type="human_verified"
            if human
            else "matched_business"
            if outcome in TERMINAL
            else "none",
            failure_class="business_rejected"
            if outcome == "CONFIRMED_NO_EFFECT"
            else "post_submit"
            if outcome == "OUTCOME_UNKNOWN"
            else None,
            human_action_required=outcome in UNRESOLVED,
        )
        if human:
            state["audit"].append(
                {"attempt_id": attempt_id, "state": outcome, "at": timestamp()}
            )
        self.write(state)

    def resolve(self, attempt_id, *, booked):
        self.finish(
            attempt_id,
            "CONFIRMED_SUCCESS" if booked else "CONFIRMED_NO_EFFECT",
            human=True,
        )

    def revoke(self):
        state = self.read()
        state["consents"] = []
        self.write(state)
