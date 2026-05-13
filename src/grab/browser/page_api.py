import asyncio
from typing import Any
from loguru import logger

def _is_destroyed_context_error(exc: BaseException) -> bool:
    return "Execution context was destroyed" in str(exc)


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
        cookie = await self.get_cookie(name, domain_contains=domain_contains)
        value = cookie.get("value") if cookie is not None else None
        return value or None

    async def get_cookie(
        self,
        name: str,
        domain_contains: str | None = None,
    ) -> dict[str, Any] | None:
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
            return cookie
        return None

    async def touch_url(self, url: str) -> str:
        context = getattr(self.page, "context", None)
        request = getattr(context, "request", None)
        if request is not None:
            response = await request.get(url)
            return getattr(response, "url", url)

        probe_page = self.page
        owns_temp_page = False
        new_page = getattr(context, "new_page", None)
        if callable(new_page):
            probe_page = await new_page()
            owns_temp_page = True

        try:
            await probe_page.goto(url, wait_until="domcontentloaded")
            return probe_page.url
        finally:
            if owns_temp_page and hasattr(probe_page, "close"):
                await probe_page.close()

    async def get_global_value(self, name: str):
        """Read a JS global from the page.

        Doctor pages may still be navigating or re-rendering shortly after a
        paste/goto; evaluate in that window raises "Execution context was
        destroyed". Wait for DOM readiness and retry a few times before surfacing
        the error.
        """
        wait = getattr(self.page, "wait_for_load_state", None)
        expression = """({ name }) => {
                if (name in globalThis) {
                    return globalThis[name];
                }
                return null;
            }"""
        arg = {"name": name}
        last_error: BaseException | None = None
        for attempt in range(3):
            if callable(wait):
                try:
                    await wait("domcontentloaded", timeout=8_000)
                except Exception as exc:
                    logger.warning(
                        "Page.wait_for_load_state failed: {}",
                        exc,
                    )
            try:
                return await self.page.evaluate(expression, arg)
            except Exception as exc:
                last_error = exc
                if _is_destroyed_context_error(exc) and attempt < 2:
                    await asyncio.sleep(0.15 * (attempt + 1))
                    continue
                raise
        assert last_error is not None
        raise last_error

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
