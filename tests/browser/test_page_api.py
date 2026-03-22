import pytest

from grab.browser.page_api import BrowserPageApi


class FakePage:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def evaluate(self, script: str, arg: dict):
        self.calls.append((script, arg))
        return {"result_code": 1, "path": arg["path"], "params": arg["params"]}


@pytest.fixture
def fake_page():
    return FakePage()


@pytest.mark.asyncio
async def test_page_api_fetch_runs_inside_page_context(fake_page):
    api = BrowserPageApi(fake_page)

    payload = await api.get_json("/guahao/v1/pc/sch/dep", params={"unit_id": "u1"})

    assert payload["result_code"] == 1
    assert fake_page.calls[0][1] == {
        "path": "/guahao/v1/pc/sch/dep",
        "params": {"unit_id": "u1"},
    }
