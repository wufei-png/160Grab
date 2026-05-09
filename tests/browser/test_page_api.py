import pytest

from grab.browser.page_api import BrowserPageApi


class FakePage:
    def __init__(
        self,
        evaluate_error: Exception | None = None,
        request_payload=None,
        globals_payload=None,
        request_url: str | None = None,
    ):
        self.calls: list[tuple[str, dict]] = []
        self.evaluate_error = evaluate_error
        self.request_calls: list[str] = []
        self.context = type(
            "FakeContext",
            (),
            {
                "request": type(
                    "FakeRequest",
                    (),
                    {
                        "get": self._request_get,
                    },
                )(),
                "cookies": self._cookies,
            },
        )()
        self.request_payload = request_payload or {"result_code": 1}
        self.request_url = request_url
        self.globals_payload = globals_payload or {}
        self.cookie_values = [
            {
                "name": "access_hash",
                "value": "user-key-1",
                "domain": ".user.91160.com",
            }
        ]

    async def evaluate(self, script: str, arg: dict):
        self.calls.append((script, arg))
        if self.evaluate_error is not None:
            raise self.evaluate_error
        if isinstance(arg, dict) and "name" in arg:
            return self.globals_payload.get(arg["name"])
        return {"result_code": 1, "path": arg["path"], "params": arg["params"]}

    async def _request_get(self, url: str):
        self.request_calls.append(url)
        payload = self.request_payload
        response_url = self.request_url or url

        class FakeResponse:
            url = response_url

            async def json(self_inner):
                return payload

        return FakeResponse()

    async def _cookies(self):
        return self.cookie_values


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


@pytest.mark.asyncio
async def test_page_api_falls_back_to_context_request_when_fetch_is_blocked():
    page = FakePage(
        evaluate_error=RuntimeError("Page.evaluate: TypeError: Failed to fetch"),
        request_payload={"result_code": 1, "source": "context.request"},
    )
    api = BrowserPageApi(page)

    payload = await api.get_json("/guahao/v1/pc/sch/dep", params={"unit_id": "u1"})

    assert payload["source"] == "context.request"
    assert page.request_calls == [
        "https://www.91160.com/guahao/v1/pc/sch/dep?unit_id=u1"
    ]


@pytest.mark.asyncio
async def test_page_api_reads_cookie_value_from_browser_context():
    page = FakePage()
    api = BrowserPageApi(page)

    value = await api.get_cookie_value("access_hash", domain_contains="91160.com")

    assert value == "user-key-1"


@pytest.mark.asyncio
async def test_page_api_reads_global_value_from_page_context():
    page = FakePage(globals_payload={"_user_key": "page-user-key"})
    api = BrowserPageApi(page)

    value = await api.get_global_value("_user_key")

    assert value == "page-user-key"


@pytest.mark.asyncio
async def test_page_api_can_touch_authenticated_url_via_shared_request_context():
    page = FakePage(request_url="https://user.91160.com/member.html")
    api = BrowserPageApi(page)

    final_url = await api.touch_url("https://user.91160.com/member.html")

    assert final_url == "https://user.91160.com/member.html"
    assert page.request_calls == ["https://user.91160.com/member.html"]
