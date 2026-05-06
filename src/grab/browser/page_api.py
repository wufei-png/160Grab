class BrowserPageApi:
    def __init__(self, page, base_url: str = "https://www.91160.com"):
        self.page = page
        self.base_url = base_url

    async def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict:
        request_params = {"path": path, "params": params or {}}
        try:
            return await self.page.evaluate(
                """async ({path, params}) => {
                    const url = new URL(path, "https://www.91160.com");
                    Object.entries(params ?? {}).forEach(([key, value]) => {
                        url.searchParams.set(key, value);
                    });
                    const response = await fetch(url.toString(), {
                        credentials: "include",
                    });
                    return await response.json();
                }""",
                request_params,
            )
        except Exception as exc:
            if not self._should_fallback_to_context_request(exc):
                raise
            context = getattr(self.page, "context", None)
            request = getattr(context, "request", None)
            if request is None:
                raise

            url = self._build_url(path, params or {})
            response = await request.get(url)
            return await self._parse_response_json(response, url)

    async def get_json_via_page_ajax(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict:
        request_params = {"path": path, "params": params or {}}
        return await self.page.evaluate(
            """async ({path, params}) => {
                const url = new URL(path, "https://www.91160.com");
                return await new Promise((resolve, reject) => {
                    if (window.jQuery?.ajax) {
                        window.jQuery.ajax({
                            url: url.toString(),
                            type: 'GET',
                            data: params ?? {},
                            dataType: 'json',
                            timeout: 15000,
                            success: resolve,
                            error: (xhr, textStatus, errorThrown) => {
                                const body = (xhr?.responseText || '').slice(0, 160);
                                reject(
                                    new Error(
                                        `ajax error status=${xhr?.status || ''} `
                                        + `textStatus=${textStatus || ''} `
                                        + `error=${errorThrown || ''} `
                                        + `body=${body}`
                                    )
                                );
                            }
                        });
                        return;
                    }

                    Object.entries(params ?? {}).forEach(([key, value]) => {
                        url.searchParams.set(key, value);
                    });
                    fetch(url.toString(), { credentials: 'include' })
                        .then(async (response) => resolve(await response.json()))
                        .catch((error) => reject(error));
                });
            }""",
            request_params,
        )

    async def get_cookie_value(
        self,
        name: str,
        domain_contains: str | None = None,
    ) -> str | None:
        context = getattr(self.page, "context", None)
        if context is None or not hasattr(context, "cookies"):
            return None

        cookies = await context.cookies()
        for cookie in reversed(cookies):
            if cookie.get("name") != name:
                continue
            domain = cookie.get("domain") or ""
            if domain_contains is not None and domain_contains not in domain:
                continue
            value = cookie.get("value")
            if value:
                return value
        return None

    async def get_global_value(self, name: str):
        return await self.page.evaluate(
            """({ name }) => {
                if (name in globalThis) {
                    return globalThis[name];
                }
                return null;
            }""",
            {"name": name},
        )

    def _build_url(self, path: str, params: dict[str, str]) -> str:
        from urllib.parse import urlencode

        if path.startswith("http://") or path.startswith("https://"):
            base = path
        else:
            base = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if not params:
            return base
        return f"{base}?{urlencode(params)}"

    def _should_fallback_to_context_request(self, exc: Exception) -> bool:
        message = str(exc)
        return any(
            token in message
            for token in (
                "Failed to fetch",
                "Unexpected token",
                "not valid JSON",
            )
        )

    async def _parse_response_json(self, response, url: str) -> dict:
        try:
            return await response.json()
        except Exception as exc:
            text = await response.text()
            snippet = text[:160].replace("\n", " ").replace("\r", " ")
            raise ValueError(
                f"Expected JSON from {url}, got non-JSON response: {snippet}"
            ) from exc
