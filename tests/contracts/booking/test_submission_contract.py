import json
from pathlib import Path

import pytest

from grab.models.schemas import BookingForm, BookingState, DoctorPageTarget
from grab.services.booking import PageBookingStrategy
from grab.transactions.evidence import BookingEvidence
from grab.transactions.store import AttemptStore
from tests.userscripts.test_doctor_page_poller import _run_hook

SCENARIOS = json.loads(
    Path(__file__).with_name("fixtures").joinpath("submission.v1.json").read_text(encoding="utf-8")
)["scenarios"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
async def test_both_paths_use_shared_outcome_and_click_contract(tmp_path, scenario):
    class Control:
        clicks = 0

        async def count(self):
            return 1

        async def is_enabled(self):
            return True

        async def is_visible(self):
            return True

        async def click(self, **kwargs):
            self.clicks += 1
            if scenario["error"]:
                raise TimeoutError()

    control = Control()

    class Page:
        async def content(self):
            return '<input name="schedule_id" value="slot"><input type="hidden" name="mid" value="member"><ul id="delts"><li val="time" class="selected">Synthetic time</li></ul><button id="submitbtn">Submit</button>'

        def locator(self, selector):
            return control

    async def adapter(*args):
        if not scenario["evidence"]:
            return None
        return BookingEvidence(
            BookingState(scenario["evidence"]),
            "u",
            "d",
            "doc",
            "member" if scenario["matched"] else "wrong",
            "slot",
            "time",
        )

    obj = PageBookingStrategy(
        Page(),
        attempt_store=AttemptStore(tmp_path),
        authorization=lambda *_: scenario["authorized"],
        evidence_adapter=adapter,
    )
    obj.prepare(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url=""),
        "member",
    )
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot", appointment_value="time")
    )
    js = _run_hook(
        """(async () => {
        let clicks = 0;
        const outcome = await hooks.submitTransaction({method:'selector', element:{click(){clicks++; if (scenario.error) throw new Error('synthetic');}}}, {scheduleId:'slot', appointmentValue:'time'}, {memberId:'member'}, {unitId:'u',depId:'d',doctorId:'doc'}, scenario.authorized, async () => ({state:scenario.evidence, selection:scenario.matched ? ['u','d','doc','member','slot','time'] : []}));
        return {state:outcome, clicks};
    })()""",
        extra_js=f"const scenario = {json.dumps(scenario)};",
    )
    assert js == {"state": scenario["state"], "clicks": scenario["clicks"]}
    assert result.state == scenario["state"]
    assert control.clicks == scenario["clicks"]
