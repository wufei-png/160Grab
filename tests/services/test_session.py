from pathlib import Path

import pytest

from grab.models.schemas import GrabConfig
from grab.services.session import SessionCaptureService


class FakePage:
    def __init__(self, url: str, member_html: str):
        self.url = url
        self.member_html = member_html
        self.visited_urls: list[str] = []

    async def goto(self, url: str):
        self.visited_urls.append(url)
        self.url = url

    async def content(self) -> str:
        return self.member_html


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
