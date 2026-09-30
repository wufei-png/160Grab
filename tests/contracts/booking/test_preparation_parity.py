import json

import pytest

from grab.booking.form import snapshot_html
from tests.contracts.booking.test_form_decisions import PREPARATION
from tests.userscripts.test_doctor_page_poller import _run_hook


@pytest.mark.parametrize("case", PREPARATION, ids=lambda c: c["id"])
def test_node_uses_shared_strict_decision_contract(case):
    snapshot = snapshot_html(case["html"])
    expression = (
        "hooks.decideBookingPreparation("
        + ",".join(json.dumps(v) for v in (snapshot, case["selection"], case["values"]))
        + ")"
    )
    result = _run_hook(expression)
    expected = {k: v for k, v in case["expected"].items() if k != "submit_clicks"}
    assert {k: result[k] for k in expected} == expected
