import os
from dataclasses import asdict

import pytest


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.parametrize("level", ["readonly", "prepare", "submit"])
async def test_live_canary(live_runner, level):
    if os.environ["LIVE_LEVEL"] != level:
        pytest.skip("different explicitly selected canary level")
    try:
        result = await live_runner.run(level)
    except Exception:
        pytest.fail(
            "live canary stopped; inspect original site manually", pytrace=False
        )
    print(asdict(result))  # Only closed status/state, counters and booleans.
    if result.status in {"observed", "prepared", "submit_observed"}:
        if level != "submit":
            assert result.submit_calls == 0
    else:
        pytest.skip("live inconclusive/blocker: " + result.status)
    # submit_observed/UNKNOWN is evidence of one attempt, never claimed success.
