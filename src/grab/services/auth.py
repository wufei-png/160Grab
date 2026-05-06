from grab.models.schemas import GrabConfig, LoginResult


class AuthService:
    def __init__(
        self,
        page,
        config: GrabConfig,
        ocr_client=None,
        notify=None,
        reporter=None,
    ):
        self.page = page
        self.config = config
        self.ocr_client = ocr_client
        self.notify = notify or (lambda message: None)
        self.reporter = reporter

    async def manual_login(self) -> LoginResult:
        await self.page.goto("https://user.91160.com/login.html")
        message = (
            "请在浏览器中手动完成登录，并导航到目标医生页。程序会在后续确认时读取当前 URL。"
        )
        self.notify(message)
        if self.reporter is not None:
            await self.reporter.emit_event(
                "manual_login_waiting",
                level="info",
                message=message,
                data={"login_url": "https://user.91160.com/login.html"},
            )
        return LoginResult(success=True, attempts=1)

    async def auto_login(self, max_attempts: int = 3) -> LoginResult:
        raise NotImplementedError(
            "TODO: implement click-word verification flow for auto login"
        )

    async def ensure_login(self, max_attempts: int = 3) -> LoginResult:
        if self.config.auth.strategy == "manual":
            return await self.manual_login()
        if self.config.auth.strategy == "auto":
            return await self.auto_login(max_attempts=max_attempts)
        raise ValueError(f"Unsupported auth strategy: {self.config.auth.strategy}")
