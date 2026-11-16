from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.e2e.test_live_flow import test_live_canary as run_live_canary


async def test_live_failure_does_not_chain_private_page_error(monkeypatch):
    monkeypatch.setenv("LIVE_LEVEL", "submit")
    runner = SimpleNamespace(
        run=AsyncMock(side_effect=ValueError("PRIVATE_FORM_VALUE"))
    )
    with pytest.raises(pytest.fail.Exception) as caught:
        await run_live_canary(runner, "submit")
    assert caught.value.__context__ is None
    assert "PRIVATE_FORM_VALUE" not in str(caught.value)
