import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize("strategy", ["auto", "unknown", "", None])
def test_unsupported_auth_strategy_is_a_configuration_error(strategy):
    with pytest.raises(ValidationError):
        GrabConfig(auth={"strategy": strategy})


@pytest.mark.asyncio
async def test_mutated_unsupported_strategy_cannot_navigate():
    page = FakePage()
    config = GrabConfig()
    config.auth.strategy = "auto"
    with pytest.raises(ValueError, match="only supports manual"):
        await AuthService(page, config).ensure_login()
    assert page.visited_urls == []
