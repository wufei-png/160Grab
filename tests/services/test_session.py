from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from grab.models.schemas import GrabConfig
from grab.services.session import SessionCaptureService


class FakeReporter:
    def __init__(self):
        self.events: list[dict] = []

    async def emit_event(self, event: str, **kwargs):
        self.events.append({"event": event, **kwargs})


class FakePage:
    def __init__(
        self,
        url: str,
        member_html: str,
        goto_redirects: dict[str, str] | None = None,
        context=None,
        evaluate_result: dict | None = None,
        evaluate_error: Exception | None = None,
        goto_error: Exception | None = None,
    ):
        self.url = url
        self.member_html = member_html
        self.visited_urls: list[str] = []
        self.goto_calls: list[dict[str, object]] = []
        self.goto_redirects = goto_redirects or {}
        self.context = context if context is not None else SimpleNamespace(pages=[self])
        self.evaluate_result = evaluate_result or {}
        self.evaluate_error = evaluate_error
        self.goto_error = goto_error
        self.evaluate_calls: list[str] = []
        self.closed = False

    async def goto(self, url: str, **kwargs):
        self.visited_urls.append(url)
        self.goto_calls.append({"url": url, "kwargs": kwargs})
        self.url = self.goto_redirects.get(url, url)
        if self.goto_error is not None:
            raise self.goto_error

    async def content(self) -> str:
        return self.member_html

    async def evaluate(self, script: str) -> dict:
        self.evaluate_calls.append(script)
        if self.evaluate_error is not None:
            raise self.evaluate_error
        return self.evaluate_result

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def member_page_html():
    return Path("tests/fixtures/member_page.html").read_text(encoding="utf-8")


def test_parse_doctor_page_url_extracts_required_ids(member_page_html):
    service = SessionCaptureService(
        page=FakePage(
            url="https://www.91160.com/doctors/index/unit_id-21/dep_id-369/docid-14765.html",
            member_html=member_page_html,
        ),
        config=GrabConfig(),
    )

    target = service.parse_doctor_page_url(
        "https://www.91160.com/doctors/index/unit_id-21/dep_id-369/docid-14765.html"
    )

    assert target.unit_id == "21"
    assert target.dept_id == "369"
    assert target.doctor_id == "14765"
    assert target.needs_resolution is False


def test_parse_doctor_page_url_dep_id_zero_requires_resolution(member_page_html):
    service = SessionCaptureService(
        page=FakePage(
            url="https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html",
            member_html=member_page_html,
        ),
        config=GrabConfig(),
    )

    target = service.parse_doctor_page_url(
        "https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html"
    )

    assert target.unit_id == "21"
    assert target.dept_id == "0"
    assert target.doctor_id == "14765"
    assert target.needs_resolution is True


def test_parse_doctor_page_url_docid_only_format(member_page_html):
    """Test that docid-only URL format is accepted with needs_resolution=True."""
    service = SessionCaptureService(
        page=FakePage(
            url="https://www.91160.com/doctors/index/docid-201100706.html",
            member_html=member_page_html,
        ),
        config=GrabConfig(),
    )

    target = service.parse_doctor_page_url(
        "https://www.91160.com/doctors/index/docid-201100706.html"
    )

    assert target.unit_id is None
    assert target.dept_id is None
    assert target.doctor_id == "201100706"
    assert target.needs_resolution is True


def test_parse_doctor_page_url_docid_ignores_query_string(member_page_html):
    service = SessionCaptureService(
        page=FakePage(url="https://www.91160.com/", member_html=member_page_html),
        config=GrabConfig(),
    )
    target = service.parse_doctor_page_url(
        "https://www.91160.com/doctors/index/docid-201100706.html?from=search#top"
    )
    assert target.doctor_id == "201100706"
    assert target.needs_resolution is True


def test_parse_doctor_page_url_error_message_shows_both_formats(member_page_html):
    """Test that error message mentions both supported URL formats."""
    service = SessionCaptureService(
        page=FakePage(url="https://www.91160.com/", member_html=member_page_html),
        config=GrabConfig(),
    )

    with pytest.raises(ValueError) as exc_info:
        service.parse_doctor_page_url("https://www.91160.com/hospital/index.html")

    error_message = str(exc_info.value)
    assert "Full" in error_message or "full" in error_message.lower()
    assert "Docid-only" in error_message or "docid" in error_message


def test_parse_doctor_page_url_rejects_unsupported_pages(member_page_html):
    service = SessionCaptureService(
        page=FakePage(url="https://www.91160.com/", member_html=member_page_html),
        config=GrabConfig(),
    )

    with pytest.raises(ValueError):
        service.parse_doctor_page_url("https://www.91160.com/hospital/index.html")


@pytest.mark.asyncio
async def test_capture_target_from_current_page_reads_url_once_after_enter(
    member_page_html,
):
    page = FakePage(
        url="https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html",
        member_html=member_page_html,
    )
    prompts: list[str] = []
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_enter=lambda message: prompts.append(message),
    )

    target = await service.capture_target_from_current_page()

    assert target.doctor_id == "14765"
    assert "目标医生页" in prompts[0]


@pytest.mark.asyncio
async def test_capture_target_from_second_tab_when_primary_is_login(member_page_html):
    """Doctor listing often opens in a new tab; Playwright's page may still be login."""
    ctx = SimpleNamespace(pages=[])
    login_p = FakePage("https://user.91160.com/login.html", member_page_html, context=ctx)
    doctor_p = FakePage(
        "https://www.91160.com/doctors/index/docid-200595747.html",
        member_page_html,
        context=ctx,
    )
    ctx.pages = [login_p, doctor_p]
    selected_pages = []
    service = SessionCaptureService(
        page=login_p,
        config=GrabConfig(),
        prompt_enter=lambda message: None,
        on_page_change=lambda page: selected_pages.append(page),
    )
    target = await service.capture_target_from_current_page()
    assert target.doctor_id == "200595747"
    assert target.needs_resolution is True
    assert service.page is doctor_p
    assert selected_pages == [doctor_p]


@pytest.mark.asyncio
async def test_capture_target_from_current_page_waits_for_redirect_after_enter(
    member_page_html,
):
    doctor_url = (
        "https://www.91160.com/doctors/index/"
        "unit_id-131/dep_id-369/docid-200226241.html"
    )
    page = FakePage(
        url="https://user.91160.com/login.html",
        member_html=member_page_html,
    )
    sleep_calls = 0

    async def fake_sleep(_seconds: float):
        nonlocal sleep_calls
        sleep_calls += 1
        page.url = doctor_url

    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_enter=lambda message: None,
        sleep=fake_sleep,
    )

    target = await service.capture_target_from_current_page()

    assert target.doctor_id == "200226241"
    assert target.unit_id == "131"
    assert target.dept_id == "369"
    assert sleep_calls == 1


@pytest.mark.asyncio
async def test_capture_target_from_current_page_triggers_debug_snapshot_on_unexpected_page(
    member_page_html,
):
    class StopAfterRetry(RuntimeError):
        pass

    page = FakePage(
        url="https://user.91160.com/login.html",
        member_html=member_page_html,
        goto_redirects={
            "https://user.91160.com/member.html": "https://user.91160.com/login.html"
        },
    )
    prompts: list[str] = []
    snapshots: list[str] = []
    calls = 0

    async def debug_snapshot(label: str):
        snapshots.append(label)

    def prompt_enter(message: str):
        nonlocal calls
        prompts.append(message)
        calls += 1
        if calls > 1:
            raise StopAfterRetry()

    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_enter=prompt_enter,
        debug_snapshot=debug_snapshot,
    )

    with pytest.raises(StopAfterRetry):
        await service.capture_target_from_current_page()

    assert snapshots == [
        "login-page-before-member-probe",
        "unexpected-page-after-manual-login",
    ]
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_print_login_page_diagnostics_reports_missing_post_and_ticket(capsys):
    async def debug_state_provider():
        return {
            "events": [
                {
                    "kind": "requestfailed",
                    "url": "https://turing.captcha.qcloud.com/TCaptcha.js",
                }
            ],
            "login_form": {
                "ticket_present": False,
                "randstr_present": False,
                "target_value": "https://user.91160.com/login.html",
                "error_num": "1",
                "visible_messages": [],
            },
        }

    service = SessionCaptureService(
        page=FakePage(
            url="https://user.91160.com/login.html",
            member_html="",
        ),
        config=GrabConfig(),
        debug_state_provider=debug_state_provider,
    )

    await service.print_login_page_diagnostics()

    output = capsys.readouterr().out
    assert "没有看到 POST /login.html 响应" in output
    assert "验证码票据: ticket=no, randstr=no" in output
    assert "更像是验证码没有完成" in output


@pytest.mark.asyncio
async def test_print_login_page_diagnostics_emits_structured_event():
    async def debug_state_provider():
        return {
            "events": [],
            "login_form": {
                "ticket_present": False,
                "randstr_present": False,
                "target_value": "https://user.91160.com/login.html",
                "error_num": "1",
                "visible_messages": ["验证码错误"],
            },
        }

    reporter = FakeReporter()
    service = SessionCaptureService(
        page=FakePage(
            url="https://user.91160.com/login.html",
            member_html="",
        ),
        config=GrabConfig(),
        debug_state_provider=debug_state_provider,
        reporter=reporter,
    )

    await service.print_login_page_diagnostics()

    assert reporter.events[-1]["event"] == "login_page_diagnostics"
    assert reporter.events[-1]["data"]["visible_messages"] == ["验证码错误"]


@pytest.mark.asyncio
async def test_probe_logged_in_state_from_login_page_detects_existing_session(
    member_page_html,
):
    page = FakePage(
        url="https://user.91160.com/login.html",
        member_html=member_page_html,
        goto_redirects={
            "https://user.91160.com/member.html": "https://user.91160.com/member.html"
        },
    )
    service = SessionCaptureService(page=page, config=GrabConfig())

    result = await service.probe_logged_in_state_from_login_page()

    assert result is True
    assert page.visited_urls == ["https://user.91160.com/member.html"]
    assert page.url == "https://user.91160.com/member.html"


@pytest.mark.asyncio
async def test_probe_logged_in_state_from_login_page_detects_failed_login(
    member_page_html,
):
    page = FakePage(
        url="https://user.91160.com/login.html",
        member_html=member_page_html,
        goto_redirects={
            "https://user.91160.com/member.html": "https://user.91160.com/login.html"
        },
    )
    service = SessionCaptureService(page=page, config=GrabConfig())

    result = await service.probe_logged_in_state_from_login_page()

    assert result is False
    assert page.visited_urls == ["https://user.91160.com/member.html"]
    assert page.url == "https://user.91160.com/login.html"


@pytest.mark.asyncio
async def test_resolve_unit_dept_ids_from_page_context(member_page_html):
    page = FakePage(
        url="https://www.91160.com/doctors/index/docid-201100706.html",
        member_html=member_page_html,
        evaluate_result={"unitId": "21", "deptId": "369"},
    )
    service = SessionCaptureService(page=page, config=GrabConfig())
    target = service.parse_doctor_page_url(page.url)

    resolved_target = await service.resolve_unit_dept_ids(target)

    assert resolved_target.unit_id == "21"
    assert resolved_target.dept_id == "369"
    assert resolved_target.doctor_id == "201100706"
    assert resolved_target.needs_resolution is False
    assert page.evaluate_calls


@pytest.mark.asyncio
async def test_resolve_unit_dept_ids_from_doctor_page_html_patterns():
    doctor_html = """
    <html>
      <body>
        <a id="addMark" doctor_id="200254692" dep_id="372" unit_id="131">+关注</a>
        <a class="orderLogin" href="https://www.91160.com/guahao/ystep1/uid-131/depid-372/schid-abc.html">预约</a>
      </body>
    </html>
    """
    page = FakePage(
        url="https://www.91160.com/doctors/index/unit_id-131/dep_id-0/docid-200254692.html",
        member_html=doctor_html,
        evaluate_result={},
    )
    service = SessionCaptureService(page=page, config=GrabConfig())
    target = service.parse_doctor_page_url(page.url)

    resolved_target = await service.resolve_unit_dept_ids(target)

    assert resolved_target.unit_id == "131"
    assert resolved_target.dept_id == "372"
    assert resolved_target.doctor_id == "200254692"
    assert resolved_target.needs_resolution is False


@pytest.mark.asyncio
async def test_resolve_member_id_uses_configured_member_id_when_valid(member_page_html):
    page = FakePage(url="https://www.91160.com/", member_html=member_page_html)
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(member_id="m2"),
    )

    member_id = await service.resolve_member_id()

    assert member_id == "m2"
    assert page.visited_urls == ["https://user.91160.com/member.html"]
    assert page.goto_calls == [
        {
            "url": "https://user.91160.com/member.html",
            "kwargs": {"wait_until": "domcontentloaded"},
        }
    ]


@pytest.mark.asyncio
async def test_resolve_member_id_continues_after_member_page_timeout(member_page_html):
    page = FakePage(
        url="https://www.91160.com/",
        member_html=member_page_html,
        goto_error=PlaywrightTimeoutError("Timeout 30000ms exceeded."),
    )
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(member_id="m2"),
    )

    member_id = await service.resolve_member_id()

    assert member_id == "m2"
    assert page.url == "https://user.91160.com/member.html"


@pytest.mark.asyncio
async def test_resolve_member_id_auto_selects_only_member():
    member_html = """
    <table>
      <tbody id="mem_list">
        <tr id="mem147750901">
          <td>吴非</td>
          <td>未认证</td>
        </tr>
      </tbody>
    </table>
    """
    page = FakePage(url="https://www.91160.com/", member_html=member_html)
    prompts: list[str] = []
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_text=lambda message: prompts.append(message) or "",
    )

    member_id = await service.resolve_member_id()

    assert member_id == "147750901"
    assert prompts == []


@pytest.mark.asyncio
async def test_fetch_member_profiles_uses_temporary_page_when_available(member_page_html):
    original_page = FakePage(
        url="https://www.91160.com/doctors/index/docid-201100706.html",
        member_html=member_page_html,
    )
    temp_page = FakePage(url="about:blank", member_html=member_page_html)

    async def new_page():
        return temp_page

    ctx = SimpleNamespace(pages=[original_page], new_page=new_page)
    original_page.context = ctx
    temp_page.context = ctx
    service = SessionCaptureService(page=original_page, config=GrabConfig())

    members = await service.fetch_member_profiles()

    assert [member.member_id for member in members] == ["m1", "m2"]
    assert original_page.visited_urls == []
    assert temp_page.visited_urls == ["https://user.91160.com/member.html"]
    assert temp_page.closed is True


@pytest.mark.asyncio
async def test_fetch_member_profiles_prefers_context_request(member_page_html):
    original_page = FakePage(
        url="https://www.91160.com/doctors/index/docid-201100706.html",
        member_html=member_page_html,
    )

    class FakeResponse:
        async def text(self_inner):
            return member_page_html

    class FakeRequest:
        def __init__(self):
            self.calls: list[str] = []

        async def get(self_inner, url: str):
            self_inner.calls.append(url)
            return FakeResponse()

    fake_request = FakeRequest()
    original_page.context = SimpleNamespace(pages=[original_page], request=fake_request)
    service = SessionCaptureService(page=original_page, config=GrabConfig())

    members = await service.fetch_member_profiles()

    assert [member.member_id for member in members] == ["m1", "m2"]
    assert fake_request.calls == ["https://user.91160.com/member.html"]
    assert original_page.visited_urls == []


@pytest.mark.asyncio
async def test_resolve_member_id_prompts_when_config_missing(member_page_html):
    page = FakePage(url="https://www.91160.com/", member_html=member_page_html)
    prompts: list[str] = []
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_text=lambda message: prompts.append(message) or "m1",
    )

    member_id = await service.resolve_member_id()

    assert member_id == "m1"
    assert "就诊人编号" in prompts[0]


@pytest.mark.asyncio
async def test_resolve_member_id_accepts_visible_label_text(member_page_html):
    page = FakePage(url="https://www.91160.com/", member_html=member_page_html)
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_text=lambda _message: "m2: 李四（未认证）",
    )

    member_id = await service.resolve_member_id()

    assert member_id == "m2"


@pytest.mark.asyncio
async def test_resolve_member_id_reprompts_until_valid(member_page_html, capsys):
    page = FakePage(url="https://www.91160.com/", member_html=member_page_html)
    responses = iter(["", "m2"])
    prompts: list[str] = []
    service = SessionCaptureService(
        page=page,
        config=GrabConfig(),
        prompt_text=lambda message: prompts.append(message) or next(responses),
    )

    member_id = await service.resolve_member_id()

    output = capsys.readouterr().out
    assert member_id == "m2"
    assert len(prompts) == 2
    assert "输入无效" in output
