"""Fence a Locator's awaited click with a synchronous browser-side check."""

import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from grab.booking.form import SUBMIT_SELECTOR
from grab.booking.page import SNAPSHOT_SCRIPT


def booking_page_matches(page, expected_url):
    current = getattr(page, "url", None)
    if current is None:
        return True  # Non-browser service test doubles have no navigation adapter.
    actual, expected = urlsplit(current), urlsplit(expected_url)
    return (actual.scheme, actual.netloc, actual.path) == (
        expected.scheme,
        expected.netloc,
        expected.path,
    )


INSTALL_GUARD = """({key, selector, expectedUrl}) => {
    const snapshot = SNAPSHOT_FUNCTION;
    const controls = [...document.querySelectorAll(selector)];
    if (controls.length !== 1 || location.origin + location.pathname !== expectedUrl) return false;
    const control = controls[0];
    const fingerprint = () => {
        const copy = new DOMParser().parseFromString(snapshot(), 'text/html');
        return JSON.stringify({
            url: location.origin + location.pathname,
            controls: [...copy.querySelectorAll('input,select,textarea,#delts li[val]')].map(n => n.outerHTML),
            dates: [...copy.querySelectorAll('#jzdate')].map(n => n.parentElement.textContent),
            submit: [...copy.querySelectorAll(selector)].map(n => n.outerHTML),
            form: control.form ? [control.form.action, control.form.method, control.form.noValidate] : null,
        });
    };
    const expected = fingerprint();
    const state = {blocked:false};
    const capture = event => {
        const candidate = event.target instanceof Element ? event.target.closest(selector) : null;
        if (!candidate) return;
        let valid = false;
        try { valid = candidate === control && fingerprint() === expected; } catch (_) {}
        if (!valid) {
            state.blocked = true;
            event.preventDefault(); event.stopImmediatePropagation();
        }
    };
    const events = ['pointerdown','mousedown','click'];
    events.forEach(name => window.addEventListener(name, capture, true));
    state.dispose = () => events.forEach(name => window.removeEventListener(name, capture, true));
    globalThis[key] = state;
    return true;
}""".replace("SNAPSHOT_FUNCTION", SNAPSHOT_SCRIPT)


@asynccontextmanager
async def submit_boundary_guard(page, expected_url):
    if not hasattr(page, "evaluate"):

        async def intact():
            return True

        yield intact if booking_page_matches(page, expected_url) else None
        return
    key = "__grab160_submit_guard_" + secrets.token_hex(16)

    async def intact():
        try:
            return (
                await page.evaluate("key => globalThis[key]?.blocked === false", key)
                is True
            )
        except Exception:
            # A navigation or lost execution context makes the guard's result
            # unavailable; it must not turn an uncertain attempt into a result.
            return False

    try:
        allowed = await page.evaluate(
            INSTALL_GUARD,
            {"key": key, "selector": SUBMIT_SELECTOR, "expectedUrl": expected_url},
        )
        yield intact if allowed is True else None
    finally:
        try:
            await page.evaluate(
                "key => {globalThis[key]?.dispose(); delete globalThis[key];}", key
            )
        except Exception:
            pass  # Navigation cannot erase the durable submission outcome.
