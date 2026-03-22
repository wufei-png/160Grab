import pytest

from grab.models.schemas import GrabConfig, LoginResult
from grab.services.auth import AuthService


class FakePage:
    def __init__(self):
        self.url = "https://www.91160.com/"
        self.visited_urls: list[str] = []

    async def goto(self, url: str):
        self.visited_urls.append(url)
        self.url = url


@pytest.mark.asyncio
async def test_manual_auth_strategy_opens_login_page_and_prints_guidance():
    page = FakePage()
    messages: list[str] = []
    service = AuthService(
        page=page,
        config=GrabConfig(auth={"strategy": "manual"}),
        notify=lambda message: messages.append(message),
    )

    result = await service.ensure_login()

    assert result == LoginResult(success=True, attempts=1)
    assert page.visited_urls == ["https://user.91160.com/login.html"]
    assert "手动完成登录" in messages[0]


@pytest.mark.asyncio
async def test_auto_auth_strategy_is_todo_for_click_word_verification():
    page = FakePage()
    service = AuthService(
        page=page,
        config=GrabConfig(
            auth={"strategy": "auto"},
            username="13800138000",
            password="secret",
        ),
    )

    with pytest.raises(NotImplementedError):
        await service.ensure_login()
