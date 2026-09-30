from typing import Any


def _is_destroyed_context_error(exc: BaseException) -> bool:
    return "Execution context was destroyed" in str(exc)


class BrowserPageApi:
    def __init__(self, page, base_url: str = "https://www.91160.com"):
        self.page = page
        self.base_url = base_url

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> dict:
        return await self.get_json_via_page_ajax(path, params)

    async def get_json_via_page_ajax(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict:
        # One transport per call. The polling owner alone decides whether to retry.
        from grab.errors import TransientSessionRefreshError

        try:
            result = await self.page.evaluate(
                r"""async ({path, params}) => {
                    const url = new URL(path, "https://www.91160.com");
                    const pack = (status, body, retryAfter, finalUrl = '') => ({
                        __grab_read_response__: true, status, body, retryAfter,
                        loginRedirect: /https:\/\/(www|user)\.91160\.com\/login\.html(?:[?#]|$)/.test(finalUrl),
                        redirected: !!finalUrl && new URL(finalUrl).pathname !== url.pathname,
                    });
                    if (window.jQuery?.ajax) {
                        return await new Promise(resolve => window.jQuery.ajax({
                            url: url.toString(), type: 'GET', data: params ?? {},
                            dataType: 'json', timeout: 15000,
                            success: (data, _, xhr) => resolve(pack(xhr?.status || 200, data, xhr?.getResponseHeader?.('Retry-After'))),
                            error: (xhr) => resolve(pack(xhr?.status || 0, null, xhr?.getResponseHeader?.('Retry-After'), xhr?.responseURL || '')),
                        }));
                    }
                    Object.entries(params ?? {}).forEach(([key, value]) => url.searchParams.set(key, value));
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), 15000);
                    try {
                        const response = await fetch(url.toString(), { credentials: 'include', signal: controller.signal });
                        let body = null;
                        try { body = await response.json(); } catch (_) { }
                        return pack(response.status, body, response.headers.get('Retry-After'), response.url);
                    } finally { clearTimeout(timer); }
                }""",
                {"path": path, "params": params or {}},
            )
        except Exception as exc:
            from playwright.async_api import Error as PlaywrightError

            if isinstance(exc, (PlaywrightError, TimeoutError)) or any(
                marker in str(exc)
                for marker in ("Failed to fetch", "Execution context was destroyed")
            ):
                raise TransientSessionRefreshError(
                    "Read-only transport failed."
                ) from None
            raise
        return self._unwrap_read_response(result)

    def _unwrap_read_response(self, result):
        from grab.errors import (
            SessionExpiredError,
            TransientSessionRefreshError,
            UnknownSessionError,
        )
        from grab.utils.rate_limit import RateLimitError, parse_retry_after

        if (
            not isinstance(result, dict)
            or result.get("__grab_read_response__") is not True
        ):
            return result
        status = result.get("status", 0)
        retry_after = parse_retry_after(result.get("retryAfter"))
        if status == 429:
            raise RateLimitError(
                "Rate limited.", "schedule_polling", retry_after=retry_after
            )
        if status == 0 or 500 <= status < 600:
            exc = TransientSessionRefreshError("Read-only transport failed.")
            exc.retry_after = retry_after
            raise exc
        if result.get("loginRedirect"):
            raise SessionExpiredError("Authenticated session expired.")
        if result.get("redirected") or not 200 <= status < 300:
            raise UnknownSessionError()
        if result.get("body") is None:
            raise UnknownSessionError("schema_drift")
        return result["body"]

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
        return await self.page.evaluate(
            "({name}) => name in globalThis ? globalThis[name] : null",
            {"name": name},
        )
