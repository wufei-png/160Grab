import os

import pytest


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_flow_reaches_booking_confirmation_page(live_runner):
    result = await live_runner.run(until="booking_confirmation")

    assert result.logged_in is True
    assert result.schedule_checked is True
    assert result.booking_form_opened is True


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_flow_submits_only_when_live_booking_enabled(live_runner):
    result = await live_runner.run(until="final_submit")

    assert result.submitted is bool(int(os.environ.get("LIVE_BOOKING", "0")))
