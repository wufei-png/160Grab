import asyncio
import math
import re
from urllib.parse import urlparse, urlunparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from grab.models.schemas import DoctorPageTarget, GrabConfig, MemberProfile


def _strip_url_query_and_fragment(url: str) -> str:
    """Normalize listing URLs: Playwright may report ?query or #fragment from the site."""
    p = urlparse(url.strip())
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


class SessionCaptureService:
    def __init__(
        self,
        page,
        config: GrabConfig,
        prompt_enter=None,
        prompt_text=None,
        debug_snapshot=None,
        debug_state_provider=None,
        sleep=None,
        on_page_change=None,
    ):
        self.page = page
        self.config = config
        self.prompt_enter = prompt_enter or (lambda message: input(message))
        self.prompt_text = prompt_text or (lambda message: input(message))
        self.debug_snapshot = debug_snapshot
        self.debug_state_provider = debug_state_provider
        self.sleep = sleep or asyncio.sleep
        self.on_page_change = on_page_change

    def _set_page(self, page) -> None:
        self.page = page
        if self.on_page_change is not None:
            self.on_page_change(page)

    def _prefer_meaningful_id(self, *values: str | None) -> str | None:
        for value in values:
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized and normalized != "0":
                return normalized
        return None

    def _extract_unit_dept_ids_from_html(
        self, html: str
    ) -> tuple[str | None, str | None]:
        attr_match = re.search(
            r'id="addMark"[^>]*\bdep_id="(?P<dept_id>[^"]+)"[^>]*\bunit_id="(?P<unit_id>[^"]+)"',
            html,
        )
        if attr_match is not None:
            return (
                self._prefer_meaningful_id(attr_match.group("unit_id")),
                self._prefer_meaningful_id(attr_match.group("dept_id")),
            )

        booking_match = re.search(
            r'/guahao/ystep1/uid-(?P<unit_id>[^/]+)/depid-(?P<dept_id>[^/]+)/schid-',
            html,
        )
        if booking_match is not None:
            return (
                self._prefer_meaningful_id(booking_match.group("unit_id")),
                self._prefer_meaningful_id(booking_match.group("dept_id")),
            )

        dept_page_match = re.search(r'/dep/show/depid-(?P<dept_id>[^/.]+)\.html', html)
        schedule_row_match = re.search(
            r'<li[^>]+class="[^"]*liClassData[^"]*"[^>]+id="(?P<dept_id>[^"_]+)_[^"]+"',
            html,
        )
        return (
            None,
            self._prefer_meaningful_id(
                dept_page_match.group("dept_id") if dept_page_match else None,
                schedule_row_match.group("dept_id") if schedule_row_match else None,
            ),
        )

    async def _goto_member_page(self, page=None) -> None:
        page = page or self.page
        member_url = "https://user.91160.com/member.html"
        try:
            await page.goto(member_url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            if page.url.startswith(member_url):
                print(
                    "⚠️ member.html 已打开，但等待 DOMContentLoaded 超时；继续读取当前页面。"
                )
                return
            raise

    async def probe_logged_in_state_from_login_page(self) -> bool | None:
        current_url = self.page.url
        if not current_url.endswith("login.html"):
            return None

        try:
            await self._goto_member_page()
        except Exception as exc:
            print(f"⚠️ Could not probe member.html for login state: {exc}")
            return None

        if self.page.url.endswith("member.html"):
            print(
                "ℹ️ 登录态已经建立，但站点把你留在了登录页或又跳回了登录页。"
            )
            print("   现在页面已切到 member.html，请继续手动导航到医生详情页。")
            return True

        if self.page.url.endswith("login.html"):
            print("   探测 member.html 后仍回到登录页，更像是本次登录并未成功。")
            return False

        print(f"   探测 member.html 后进入了意外页面: {self.page.url}")
        return None

    async def print_login_page_diagnostics(self) -> None:
        if self.debug_state_provider is None:
            return

        try:
            state = await self.debug_state_provider()
        except Exception as exc:
            print(f"⚠️ Could not inspect login page diagnostics: {exc}")
            return

        login_form = state.get("login_form") or {}
        events = state.get("events") or []
        login_related_events = [
            event
            for event in events
            if any(
                token in (event.get("url") or "")
                for token in (
                    "login.html",
                    "member.html",
                    "TCaptcha",
                    "captcha",
                    "turing.captcha",
                    "qcloud.com",
                )
            )
        ]
        login_post_events = [
            event
            for event in login_related_events
            if event.get("kind") == "response"
            and event.get("method") == "POST"
            and "login.html" in (event.get("url") or "")
        ]
        captcha_failures = [
            event
            for event in login_related_events
            if event.get("kind") == "requestfailed"
            and "captcha" in (event.get("url") or "").lower()
        ]
        page_errors = [
            event.get("text")
            for event in events
            if event.get("kind") == "pageerror" and event.get("text")
        ]
        visible_messages = login_form.get("visible_messages") or []

        print("   登录页诊断:")
        if visible_messages:
            print("   - 页面提示: " + " | ".join(visible_messages))
        if page_errors:
            print("   - 页面脚本错误: " + " | ".join(page_errors[-2:]))
        if login_post_events:
            last_post = login_post_events[-1]
            print(
                "   - 最近登录提交: "
                f"{last_post.get('method')} {last_post.get('url')} -> "
                f"{last_post.get('status')}"
            )
        else:
            print("   - 最近登录提交: 没有看到 POST /login.html 响应")
        print(
            "   - 验证码票据: "
            f"ticket={'yes' if login_form.get('ticket_present') else 'no'}, "
            f"randstr={'yes' if login_form.get('randstr_present') else 'no'}"
        )
        print(
            "   - 表单目标: "
            f"target={login_form.get('target_value') or '<empty>'}, "
            f"error_num={login_form.get('error_num') or '<empty>'}"
        )
        if captcha_failures:
            print(
                "   - 验证码请求失败: "
                + " | ".join(event.get("url") or "" for event in captcha_failures[-2:])
            )

        if not login_post_events and not login_form.get("ticket_present"):
            print("   - 推断: 更像是验证码没有完成，或登录表单根本没有真正提交。")
        elif login_post_events and visible_messages:
            print("   - 推断: 表单提交过，但页面返回了前端可见的错误提示。")
        elif login_post_events:
            print("   - 推断: 表单提交过，但还需要结合快照或网络响应继续判断。")

    def parse_doctor_page_url(self, url: str) -> DoctorPageTarget:
        """
        Parse a doctor page URL and extract identifying parameters.

        Supports two URL formats:
        1. Full format: https://www.91160.com/doctors/index/unit_id-{unit_id}/dep_id-{dept_id}/docid-{doctor_id}.html
        2. Docid-only format: https://www.91160.com/doctors/index/docid-{doctor_id}.html

        For full format URLs, returns a target with unit_id, dept_id, and doctor_id populated.
        For docid-only URLs, returns a target with doctor_id populated, but unit_id and dept_id as None,
        and needs_resolution=True to indicate further resolution is required.
        """
        url = _strip_url_query_and_fragment(url)
        full_format_match = re.fullmatch(
            r"https://www\.91160\.com/doctors/index/unit_id-(?P<unit_id>[^/]+)/dep_id-(?P<dept_id>[^/]+)/docid-(?P<doctor_id>[^/.]+)\.html",
            url,
        )
        if full_format_match is not None:
            dept_id = full_format_match.group("dept_id")
            return DoctorPageTarget(
                unit_id=full_format_match.group("unit_id"),
                dept_id=dept_id,
                doctor_id=full_format_match.group("doctor_id"),
                source_url=url,
                needs_resolution=dept_id == "0",
            )

        docid_only_match = re.fullmatch(
            r"https://www\.91160\.com/doctors/index/docid-(?P<doctor_id>[^/.]+)\.html",
            url,
        )
        if docid_only_match is not None:
            return DoctorPageTarget(
                unit_id=None,
                dept_id=None,
                doctor_id=docid_only_match.group("doctor_id"),
                source_url=url,
                needs_resolution=True,
            )

        raise ValueError(
            "Unsupported URL format. Supported formats:\n"
            "  1. Full: https://www.91160.com/doctors/index/unit_id-xx/dep_id-xx/docid-xx.html\n"
            "  2. Docid-only: https://www.91160.com/doctors/index/docid-xxxxx.html"
        )

    def _parse_doctor_target_from_any_tab(self) -> DoctorPageTarget:
        """
        Try the primary tab first, then every other tab (newest last in Playwright -> reverse).

        Many 91160 links use target=_blank; the doctor page may not be on the tab Playwright
        attached to first.
        """
        pages = list(self.page.context.pages)
        last_error: ValueError | None = None
        for p in reversed(pages):
            try:
                target = self.parse_doctor_page_url(p.url)
            except ValueError as exc:
                last_error = exc
                continue
            if p is not self.page:
                print(
                    "   检测到医生详情页在另一标签页（常见于搜索/列表点击后新开标签），"
                    "已把自动化切换到该标签。"
                )
                print(f"   该页 URL: {p.url}")
            self._set_page(p)
            return target
        if last_error is not None:
            raise last_error
        raise ValueError("No browser tabs available.")

    async def _wait_for_doctor_target(
        self,
        timeout_seconds: float = 1.5,
        poll_interval_seconds: float = 0.2,
    ) -> DoctorPageTarget:
        attempts = max(1, math.ceil(timeout_seconds / poll_interval_seconds))
        last_error: ValueError | None = None
        for attempt in range(attempts):
            try:
                return self._parse_doctor_target_from_any_tab()
            except ValueError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                await self.sleep(poll_interval_seconds)

        if last_error is not None:
            raise last_error
        raise ValueError("No browser tabs available.")

    async def capture_target_from_current_page(self) -> DoctorPageTarget:
        while True:
            self.prompt_enter("请先完成登录并停留在目标医生页，准备好后按 Enter 继续: ")
            try:
                return await self._wait_for_doctor_target()
            except ValueError as e:
                print(f"❌ {e}")
                print(f"   程序当前读到的地址栏 URL: {self.page.url}")
                if len(self.page.context.pages) > 1:
                    print("   当前所有标签页 URL（检查医生页是否新开在别的标签）:")
                    for i, p in enumerate(self.page.context.pages):
                        print(f"     [{i + 1}] {p.url}")
                if "www.91160.com/doctors/index/" not in self.page.url:
                    print(
                        "   提示：必须在「本程序自动打开的浏览器窗口」里进入医生页；"
                        "系统默认浏览器或其它窗口里的地址不会被读取。"
                    )
                if self.page.url.endswith("login.html"):
                    await self.print_login_page_diagnostics()
                    if self.debug_snapshot is not None:
                        await self.debug_snapshot("login-page-before-member-probe")
                    login_state = await self.probe_logged_in_state_from_login_page()
                    if login_state is False:
                        print("   这更像是验证码未过、密码错误，或站点直接拒绝了本次登录。")
                    elif login_state is None:
                        print(
                            "   当前还停留在登录页，但仅凭 URL 不能断定登录一定失败。"
                        )
                elif "/guahao/ystep1/" in self.page.url:
                    print("   当前已经到了预约页，不是医生详情页，请返回医生详情页后重试。")
                elif "/member.html" in self.page.url:
                    print("   当前停留在就诊人页面，先回到医生详情页再按 Enter。")
                if self.debug_snapshot is not None:
                    await self.debug_snapshot("unexpected-page-after-manual-login")
                print(
                    "支持的URL格式:\n"
                    "  1. Full: https://www.91160.com/doctors/index/unit_id-xx/dep_id-xx/docid-xx.html\n"
                    "  2. Docid-only: https://www.91160.com/doctors/index/docid-xxxxx.html"
                )
                print("请重新在浏览器中导航到医生详情页，然后按 Enter 继续。\n")

    async def resolve_unit_dept_ids(self, target: DoctorPageTarget) -> DoctorPageTarget:
        """
        Resolve missing unit_id and dept_id from the current doctor page.

        When a docid-only URL is detected, this method extracts the unit_id and dept_id
        from the page's JavaScript context or DOM elements.

        Common patterns on 91160 doctor pages:
        - window.unit_id, window.dept_id
        - data-unit-id, data-dept-id attributes
        - Meta tags with unit/dept information

        Returns:
            DoctorPageTarget with resolved unit_id and dept_id if found,
            otherwise returns the original target with needs_resolution=True
            and logs a warning.

        Raises:
            RuntimeError: If page is not on a doctor detail page.
        """
        # Verify we're on a doctor detail page
        if "/doctors/index" not in self.page.url:
            raise RuntimeError(
                f"resolve_unit_dept_ids requires a doctor detail page, current URL: {self.page.url}"
            )

        try:
            result = await self.page.evaluate(
                r"""() => {
                    const normalize = (value) => {
                        if (value === undefined || value === null) return '';
                        return String(value).trim();
                    };
                    const keepMeaningful = (value) => {
                        const normalized = normalize(value);
                        return normalized && normalized !== '0' ? normalized : '';
                    };
                    const firstAttr = (selectors, attrs) => {
                        for (const selector of selectors) {
                            const node = document.querySelector(selector);
                            if (!node) continue;
                            for (const attr of attrs) {
                                const value = keepMeaningful(node.getAttribute(attr));
                                if (value) return value;
                            }
                        }
                        return '';
                    };
                    const firstHrefMatch = (selectors, pattern, groupIndex) => {
                        for (const selector of selectors) {
                            const node = document.querySelector(selector);
                            const href = normalize(node?.getAttribute('href'));
                            if (!href) continue;
                            const match = href.match(pattern);
                            if (match?.[groupIndex]) {
                                const value = keepMeaningful(match[groupIndex]);
                                if (value) return value;
                            }
                        }
                        return '';
                    };

                    // Try to extract from window variables (common in 91160 pages)
                    let unitId = keepMeaningful(window.unit_id)
                        || window.UnitId
                        || window.UnitID
                        || window.__INITIAL_STATE__?.unitId
                        || window.__NUXT__?.unitId;

                    let deptId = keepMeaningful(window.dept_id)
                        || window.DeptId
                        || window.DeptID
                        || window.__INITIAL_STATE__?.deptId
                        || window.__NUXT__?.deptId;

                    // Try to extract from data attributes
                    if (!unitId) {
                        const unitAttr = document.querySelector('[data-unit-id]');
                        if (unitAttr) unitId = unitAttr.dataset.unitId;
                    }
                    if (!deptId) {
                        const deptAttr = document.querySelector('[data-dept-id]');
                        if (deptAttr) deptId = deptAttr.dataset.deptId;
                    }

                    // Try explicit 91160 doctor-page attributes.
                    if (!unitId || !deptId) {
                        unitId = unitId || firstAttr(
                            ['#addMark', '.focus_btn', '[unit_id]', '[doctor_id][unit_id]'],
                            ['unit_id', 'unitId', 'data-unit-id']
                        );
                        deptId = deptId || firstAttr(
                            ['#addMark', '.focus_btn', '[dep_id]', '[doctor_id][dep_id]'],
                            ['dep_id', 'depId', 'data-dept-id']
                        );
                    }

                    // Try links rendered on the schedule area.
                    if (!unitId || !deptId) {
                        unitId = unitId || firstHrefMatch(
                            ['a.orderLogin[href*="/guahao/ystep1/"]', 'a[href*="/guahao/ystep1/uid-"]'],
                            /uid-([^/]+)\/depid-([^/]+)\//,
                            1
                        );
                        deptId = deptId || firstHrefMatch(
                            ['a.orderLogin[href*="/guahao/ystep1/"]', 'a[href*="/guahao/ystep1/uid-"]'],
                            /uid-([^/]+)\/depid-([^/]+)\//,
                            2
                        );
                        deptId = deptId || firstHrefMatch(
                            ['a[href*="/dep/show/depid-"]'],
                            /depid-([^/.]+)\.html/,
                            1
                        );
                    }

                    // Try schedule row ids like "369_200226241_pm".
                    if (!deptId) {
                        const row = document.querySelector('li.liClassData[id*="_"]');
                        const match = normalize(row?.id).match(/^([^_]+)_/);
                        if (match?.[1]) {
                            deptId = keepMeaningful(match[1]);
                        }
                    }

                    // Try to extract from meta tags
                    if (!unitId || !deptId) {
                        const metas = document.querySelectorAll('meta');
                        metas.forEach(meta => {
                            const name = meta.getAttribute('name') || '';
                            const content = meta.getAttribute('content') || '';
                            if (name === 'unit_id' || content.includes('unit_id=')) {
                                const match = content.match(/unit_id[=:]([^&,]+)/);
                                if (match) unitId = keepMeaningful(match[1]);
                            }
                            if (name === 'dept_id' || content.includes('dept_id=')) {
                                const match = content.match(/dept_id[=:]([^&,]+)/);
                                if (match) deptId = keepMeaningful(match[1]);
                            }
                        });
                    }

                    // Try to extract from URL in page context
                    if (!unitId || !deptId) {
                        const docMatch = window.location.pathname.match(/unit_id-([^/]+)/);
                        if (docMatch) unitId = keepMeaningful(docMatch[1]);
                        const deptMatch = window.location.pathname.match(/dep_id-([^/]+)/);
                        if (deptMatch) deptId = keepMeaningful(deptMatch[1]);
                    }

                    return { unitId, deptId };
                }"""
            )

            resolved_unit_id = result.get("unitId")
            resolved_dept_id = result.get("deptId")

            if not resolved_unit_id or not resolved_dept_id:
                html_unit_id, html_dept_id = self._extract_unit_dept_ids_from_html(
                    await self.page.content()
                )
                resolved_unit_id = self._prefer_meaningful_id(
                    resolved_unit_id, html_unit_id, target.unit_id
                )
                resolved_dept_id = self._prefer_meaningful_id(
                    resolved_dept_id, html_dept_id, target.dept_id
                )
            else:
                resolved_unit_id = self._prefer_meaningful_id(
                    resolved_unit_id, target.unit_id
                )
                resolved_dept_id = self._prefer_meaningful_id(
                    resolved_dept_id, target.dept_id
                )

            if resolved_unit_id and resolved_dept_id:
                print(
                    f"✅ Resolved from page: unit_id={resolved_unit_id}, dept_id={resolved_dept_id}"
                )
                return DoctorPageTarget(
                    unit_id=str(resolved_unit_id),
                    dept_id=str(resolved_dept_id),
                    doctor_id=target.doctor_id,
                    source_url=target.source_url,
                    needs_resolution=False,
                )
            else:
                print("⚠️ Could not resolve unit_id/dept_id from page")
                if not resolved_unit_id:
                    print("   - unit_id not found in page")
                if not resolved_dept_id:
                    print("   - dept_id not found in page or still stayed at placeholder 0")
                return target

        except Exception as e:
            print(f"⚠️ Error resolving unit_id/dept_ids: {e}")
            return target

    async def fetch_member_profiles(self) -> list[MemberProfile]:
        context = getattr(self.page, "context", None)
        request = getattr(context, "request", None)
        member_url = "https://user.91160.com/member.html"
        if request is not None:
            response = await request.get(member_url)
            return self.parse_member_profiles(await response.text())

        member_page = self.page
        owns_temp_page = False
        new_page = getattr(context, "new_page", None)
        if callable(new_page):
            member_page = await new_page()
            owns_temp_page = True

        try:
            await self._goto_member_page(member_page)
            return self.parse_member_profiles(await member_page.content())
        finally:
            if owns_temp_page and hasattr(member_page, "close"):
                await member_page.close()

    def parse_member_profiles(self, html: str) -> list[MemberProfile]:
        rows = re.findall(
            r'<tr id="mem(?P<member_id>[^"]+)">.*?<td>(?P<name>[^<]+)</td>.*?<td>(?P<status>[^<]+)</td>.*?</tr>',
            html,
            re.S,
        )
        if not rows:
            raise ValueError("No members found in member.html")

        return [
            MemberProfile(
                member_id=member_id,
                name=name,
                certified=status == "已认证",
            )
            for member_id, name, status in rows
        ]

    def _normalize_member_selection(
        self,
        raw_value: str,
        member_map: dict[str, MemberProfile],
    ) -> str | None:
        candidate = raw_value.strip()
        if candidate in member_map:
            return candidate

        for separator in (":", "："):
            if separator in candidate:
                prefix = candidate.split(separator, 1)[0].strip()
                if prefix in member_map:
                    return prefix

        bracket_match = re.match(r"^\[(?P<member_id>[^\]]+)\]", candidate)
        if bracket_match is not None:
            member_id = bracket_match.group("member_id").strip()
            if member_id in member_map:
                return member_id

        return None

    async def resolve_member_id(self) -> str:
        members = await self.fetch_member_profiles()
        member_map = {member.member_id: member for member in members}

        if self.config.member_id is not None:
            if self.config.member_id not in member_map:
                raise ValueError(
                    "Configured member_id was not found in current account"
                )
            return self.config.member_id

        if len(members) == 1:
            only_member = members[0]
            print(
                "ℹ️ 当前账号下只有一个就诊人，自动选中: "
                f"{only_member.member_id}: {only_member.name}"
                + ("" if only_member.certified else "（未认证）")
            )
            return only_member.member_id

        member_lines = [
            f"{member.member_id}: {member.name}"
            + ("" if member.certified else "（未认证）")
            for member in members
        ]
        while True:
            selected = self.prompt_text(
                "请选择就诊人编号: " + " / ".join(member_lines) + " "
            )
            normalized = self._normalize_member_selection(selected, member_map)
            if normalized is not None:
                return normalized
            print("❌ 输入无效，请输入列表里的就诊人编号。")
