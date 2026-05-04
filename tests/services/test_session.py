from pathlib import Path
from types import SimpleNamespace

import pytest

from grab.models.schemas import GrabConfig
from grab.services.session import SessionCaptureService


class FakePage:
    def __init__(
        self,
        url: str,
        member_html: str,
        goto_redirects: dict[str, str] | None = None,
        context=None,
        evaluate_result: dict | None = None,
        evaluate_error: Exception | None = None,
    ):
        self.url = url
        self.member_html = member_html
        self.visited_urls: list[str] = []
        self.goto_redirects = goto_redirects or {}
        self.context = context if context is not None else SimpleNamespace(pages=[self])
        self.evaluate_result = evaluate_result or {}
        self.evaluate_error = evaluate_error
        self.evaluate_calls: list[str] = []

    async def goto(self, url: str):
        self.visited_urls.append(url)
        self.url = self.goto_redirects.get(url, url)

    async def content(self) -> str:
        return self.member_html

    async def evaluate(self, script: str) -> dict:
        self.evaluate_calls.append(script)
        if self.evaluate_error is not None:
            raise self.evaluate_error
        return self.evaluate_result


@pytest.fixture
def member_page_html():
    return Path("tests/fixtures/member_page.html").read_text()


def test_parse_doctor_page_url_extracts_required_ids(member_page_html):
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
    assert target.needs_resolution is False


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
    service = SessionCaptureService(
        page=login_p,
        config=GrabConfig(),
        prompt_enter=lambda message: None,
    )
    target = await service.capture_target_from_current_page()
    assert target.doctor_id == "200595747"
    assert target.needs_resolution is True
    assert service.page is doctor_p


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
        evaluate_result={"unitId": "21", "deptId": "0"},
    )
    service = SessionCaptureService(page=page, config=GrabConfig())
    target = service.parse_doctor_page_url(page.url)

    resolved_target = await service.resolve_unit_dept_ids(target)

    assert resolved_target.unit_id == "21"
    assert resolved_target.dept_id == "0"
    assert resolved_target.doctor_id == "201100706"
    assert resolved_target.needs_resolution is False
    assert page.evaluate_calls


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
