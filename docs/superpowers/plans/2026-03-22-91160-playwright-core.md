# 91160 Playwright Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个基于 Playwright 的 Python 挂号核心链路，支持登录、验证码识别、双通道刷号、条件筛选、预约提交和首版命令行运行。

**Architecture:** 首版采用“浏览器上下文优先”的 Playwright 架构：登录、验证码、预约提交全部走真实页面交互，刷号轮询通过 `page.evaluate(fetch)` 在已登录浏览器上下文内调用站点接口，避免把核心请求退回到浏览器外的独立 HTTP 客户端。Python 侧 `httpx` 只保留给 OCR 服务这类站外依赖；预约层按策略接口设计，首版只实现页面提交流程，并为后续补充浏览器内 `fetch` 提交预留扩展点。

**Tech Stack:** Python 3.11, Playwright async API, httpx, pydantic v2, PyYAML, pytest, pytest-asyncio, ruff

---

## Scope Check

`references/91160.md` 实际包含两个相对独立的子系统：

1. 运行时核心链路：登录、验证码、刷号、预约、调度。
2. 交互式初始化链路：`init` 命令、城市/医院/科室/医生选择、配置文件生成。

本计划只覆盖第 1 项，并补一个“手写 YAML 配置文件”的最小入口来驱动核心链路。`init`、代理、通知、斐斐打码、Docker 支持单独起下一份计划，避免首版同时做两个子系统导致交付面过宽。

## File Structure

### Existing Files To Modify

- `main.py`
  责任：命令行入口，读取配置路径，启动核心 runner。
- `src/grab/browser/playwright_client.py`
  责任：浏览器生命周期、页面导航、页面等待、截图和浏览器上下文内脚本执行帮助方法。
- `src/grab/core/scheduler.py`
  责任：从占位循环升级为“可选定时等待 + 刷号轮询节奏控制”。
- `src/grab/models/schemas.py`
  责任：承载运行配置、登录结果、排班候选、预约表单等核心 schema。

### New Files To Create

- `src/grab/utils/config_loader.py`
  责任：加载 `config.yaml` 并映射到 `GrabConfig`。
- `src/grab/utils/runtime.py`
  责任：随机刷号间隔解析、定时启动时间解析等运行时帮助函数。
- `src/grab/browser/page_api.py`
  责任：封装浏览器上下文内 `fetch`，供刷号轮询在真实会话里调用站点接口。
- `src/grab/services/ocr.py`
  责任：封装 `91160-ocr-server` 调用，只支持首版 OCR 后端。
- `src/grab/services/auth.py`
  责任：验证码抓取、页面登录提交流程、登录重试和状态验证。
- `src/grab/services/schedule.py`
  责任：双通道排班拉取、轮询切换、结果标准化、医生/周几/上下午/时段过滤；排班请求固定经由浏览器上下文内 `fetch` 发出。
- `src/grab/services/booking.py`
  责任：定义预约策略接口并实现页面提交策略；首版不实现 `fetch` 提交策略，只预留接口。
- `src/grab/core/runner.py`
  责任：串联配置、登录、等待、刷号、预约、退出码。
- `config/example.yaml`
  责任：提供首版可运行配置模板，对齐参考文档中的核心字段语义。
- `tests/fixtures/login_page.html`
- `tests/fixtures/channel_1_schedule.json`
- `tests/fixtures/channel_2_schedule.json`
- `tests/fixtures/booking_page.html`
- `tests/fixtures/booking_submit_success.html`
  责任：提供稳定的离线测试样本。
- `tests/utils/test_config_loader.py`
- `tests/utils/test_runtime.py`
- `tests/browser/test_page_api.py`
- `tests/services/test_auth.py`
- `tests/services/test_schedule.py`
- `tests/services/test_booking.py`
- `tests/core/test_runner.py`
- `tests/e2e/test_live_flow.py`
  责任：覆盖核心业务逻辑，执行时优先走单元/契约测试，不依赖真实 91160 环境。
- `tests/e2e/conftest.py`
  责任：读取真实环境变量，控制 live E2E 的 dry-run / destructive-run 开关。

## Delivery Notes

- 实现顺序必须遵守 `@test-driven-development`：先写失败测试，再补最小实现。
- 每个任务结束前执行对应测试；全部完成后执行 `@verification-before-completion`。
- 每个任务独立提交，避免把认证、刷号、预约混成一个大提交。

### Task 1: Runtime Config And Domain Contracts

**Files:**
- Create: `src/grab/utils/config_loader.py`
- Create: `src/grab/utils/runtime.py`
- Modify: `src/grab/models/schemas.py`
- Create: `config/example.yaml`
- Test: `tests/utils/test_config_loader.py`
- Test: `tests/utils/test_runtime.py`

- [ ] **Step 1: Write the failing tests**

```python
from grab.utils.config_loader import load_config


def test_load_config_supports_core_fields(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
username: "13800138000"
password: "secret"
member_id: "m1"
unit_id: "u1"
dept_id: "d1"
doctor_ids: ["doc-a", "doc-b"]
weeks: [1, 3, 5]
days: ["am"]
hours: ["08:00-08:30"]
sleep_time: "3000-5000"
brush_start_date: "2026-03-24"
enable_appoint: true
appoint_time: "2026-03-24 08:00:00"
booking_strategy: "page"
ocr:
  base_url: "http://127.0.0.1:8000"
""".strip()
    )

    config = load_config(config_file)

    assert config.member_id == "m1"
    assert config.doctor_ids == ["doc-a", "doc-b"]
    assert config.sleep_time == "3000-5000"
    assert config.enable_appoint is True
    assert config.booking_strategy == "page"


def test_parse_sleep_time_range_returns_bounded_value():
    delay = parse_sleep_time("3000-5000")
    assert 3000 <= delay <= 5000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/utils/test_config_loader.py tests/utils/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError` or missing attribute errors for loader / schema fields.

- [ ] **Step 3: Write minimal implementation**

```python
class GrabConfig(BaseModel):
    username: str
    password: str
    member_id: str
    unit_id: str
    dept_id: str
    doctor_ids: list[str] = Field(default_factory=list)
    weeks: list[int] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    hours: list[str] = Field(default_factory=list)
    sleep_time: str = "3000"
    brush_start_date: date | None = None
    enable_appoint: bool = False
    appoint_time: datetime | None = None
    booking_strategy: Literal["page"] = "page"
```

```python
def load_config(path: str | Path) -> GrabConfig:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return GrabConfig.model_validate(data)
```

```python
def parse_sleep_time(value: str) -> int:
    if "-" not in value:
        return int(value)
    start, end = value.split("-", maxsplit=1)
    return random.randint(int(start), int(end))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/utils/test_config_loader.py tests/utils/test_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/example.yaml src/grab/models/schemas.py src/grab/utils/config_loader.py src/grab/utils/runtime.py tests/utils/test_config_loader.py tests/utils/test_runtime.py
git commit -m "feat: add runtime config schema and loader"
```

### Task 2: Browser Context Fetch Helper

**Files:**
- Modify: `src/grab/browser/playwright_client.py`
- Create: `src/grab/browser/page_api.py`
- Test: `tests/browser/test_page_api.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_page_api_fetch_runs_inside_page_context(fake_page):
    api = BrowserPageApi(fake_page)
    payload = await api.get_json("/guahao/v1/pc/sch/dep?unit_id=u1")
    assert payload["result_code"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/browser/test_page_api.py::test_page_api_fetch_runs_inside_page_context -v`
Expected: FAIL with `ImportError` or missing `BrowserPageApi`.

- [ ] **Step 3: Write minimal implementation**

```python
class BrowserPageApi:
    async def get_json(self, path: str, params: dict[str, str] | None = None) -> dict:
        return await self.page.evaluate(
            """async ({path, params}) => {
                const url = new URL(path, window.location.origin);
                Object.entries(params ?? {}).forEach(([key, value]) => url.searchParams.set(key, value));
                const response = await fetch(url.toString(), { credentials: "include" });
                return await response.json();
            }""",
            {"path": path, "params": params or {}},
        )
```

```python
async def run_in_page(self, script: str, arg: dict | None = None) -> Any:
    if self.page is None:
        raise RuntimeError("Call launch() first")
    return await self.page.evaluate(script, arg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/browser/test_page_api.py::test_page_api_fetch_runs_inside_page_context -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/grab/browser/playwright_client.py src/grab/browser/page_api.py tests/browser/test_page_api.py
git commit -m "feat: add browser-context fetch helper"
```

### Task 3: OCR And Login Workflow

**Files:**
- Create: `src/grab/services/ocr.py`
- Create: `src/grab/services/auth.py`
- Modify: `src/grab/browser/playwright_client.py`
- Test: `tests/services/test_auth.py`
- Test: `tests/fixtures/login_page.html`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_login_retries_when_ocr_returns_invalid_code(auth_service):
    result = await auth_service.login(max_attempts=2)
    assert result.success is True
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_login_uses_page_fill_and_click(auth_service, fake_page):
    await auth_service.login(max_attempts=1)
    assert fake_page.filled["#username"] == "13800138000"
    assert fake_page.clicked == ["#login_submit"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_auth.py -v`
Expected: FAIL because `AuthService` / `OcrClient` / Playwright login flow do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class OcrClient:
    async def recognize(self, image_bytes: bytes) -> str:
        response = await self._client.post("/ocr", files={"file": ("captcha.png", image_bytes, "image/png")})
        return response.json()["result"]
```

```python
class AuthService:
    async def login(self, max_attempts: int) -> LoginResult:
        for attempt in range(1, max_attempts + 1):
            captcha = await self.fetch_captcha_bytes()
            code = await self.ocr_client.recognize(captcha)
            await self.page.fill("#username", self.config.username)
            await self.page.fill("#password", self.config.password)
            await self.page.fill("#checkcode", code)
            await self.page.click("#login_submit")
            if await self.is_logged_in():
                return LoginResult(success=True, attempts=attempt)
        return LoginResult(success=False, attempts=max_attempts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/grab/services/ocr.py src/grab/services/auth.py src/grab/browser/playwright_client.py tests/services/test_auth.py tests/fixtures/login_page.html
git commit -m "feat: add captcha-backed login workflow"
```

### Task 4: Dual-Channel Schedule Fetching And Filters

**Files:**
- Modify: `src/grab/browser/page_api.py`
- Create: `src/grab/services/schedule.py`
- Modify: `src/grab/models/schemas.py`
- Test: `tests/services/test_schedule.py`
- Test: `tests/fixtures/channel_1_schedule.json`
- Test: `tests/fixtures/channel_2_schedule.json`

- [ ] **Step 1: Write the failing tests**

```python
def test_channel_rotation_uses_both_endpoints(schedule_service):
    assert schedule_service.next_channel().value == "CHANNEL_1"
    assert schedule_service.next_channel().value == "CHANNEL_2"


def test_filter_slots_by_doctor_week_day_and_hour(schedule_service, channel_1_payload):
    slots = schedule_service.parse_channel_1(channel_1_payload)
    filtered = schedule_service.filter_slots(
        slots,
        doctor_ids=["doc-1"],
        weeks=[2],
        days=["am"],
        hours=["08:00-08:30"],
    )
    assert [slot.schedule_id for slot in filtered] == ["sch-1001"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_schedule.py -v`
Expected: FAIL because browser-context polling / dual-channel parsing / filtering do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class BrushChannel(str, Enum):
    CHANNEL_1 = "CHANNEL_1"
    CHANNEL_2 = "CHANNEL_2"


async def fetch_channel_1(self, unit_id: str, dept_id: str, date: str) -> dict:
    return await self.page_api.get_json("/guahao/v1/pc/sch/dep", {...})


def filter_slots(...):
    return [
        slot for slot in slots
        if (not doctor_ids or slot.doctor_id in doctor_ids)
        and (not weeks or slot.weekday in weeks)
        and (not days or slot.day_period in days)
        and (not hours or slot.time_range in hours)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_schedule.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/grab/browser/page_api.py src/grab/services/schedule.py src/grab/models/schemas.py tests/services/test_schedule.py tests/fixtures/channel_1_schedule.json tests/fixtures/channel_2_schedule.json
git commit -m "feat: add dual-channel schedule polling and filters"
```

### Task 5: Booking Strategy Interface And Page Submission

**Files:**
- Create: `src/grab/services/booking.py`
- Modify: `src/grab/models/schemas.py`
- Test: `tests/services/test_booking.py`
- Test: `tests/fixtures/booking_page.html`
- Test: `tests/fixtures/booking_submit_success.html`

- [ ] **Step 1: Write the failing tests**

```python
def test_page_booking_strategy_builds_form_from_booking_page(page_booking_strategy, booking_page_html):
    form = page_booking_strategy.parse_booking_form(booking_page_html, member_id="member-1")
    assert form.member_id == "member-1"
    assert form.schedule_id == "sch-1001"


@pytest.mark.asyncio
async def test_page_booking_strategy_retries_same_slot_three_times(page_booking_strategy):
    result = await page_booking_strategy.submit_with_retry(slot_id="sch-1001", max_attempts=3)
    assert result.success is True
    assert result.attempts == 3


def test_booking_service_exposes_page_strategy_only_by_default(booking_service):
    assert booking_service.strategy_name == "page"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_booking.py -v`
Expected: FAIL because booking strategy abstraction / page strategy / submit retry flow do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class BookingStrategy(Protocol):
    async def submit_with_retry(self, slot_id: str, max_attempts: int = 3) -> BookingResult: ...


class PageBookingStrategy:
    async def submit_with_retry(self, slot_id: str, max_attempts: int = 3) -> BookingResult:
        for attempt in range(1, max_attempts + 1):
            form = await self.fetch_booking_form(slot_id)
            if not form.is_valid:
                continue
            if await self.submit_booking_via_page(form):
                return BookingResult(success=True, attempts=attempt)
        return BookingResult(success=False, attempts=max_attempts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_booking.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/grab/services/booking.py src/grab/models/schemas.py tests/services/test_booking.py tests/fixtures/booking_page.html tests/fixtures/booking_submit_success.html
git commit -m "feat: add page booking strategy"
```

### Task 6: Orchestration, Scheduled Start, And CLI Entry

**Files:**
- Create: `src/grab/core/runner.py`
- Modify: `src/grab/core/scheduler.py`
- Modify: `main.py`
- Test: `tests/core/test_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_runner_waits_until_appoint_time_before_polling(runner, frozen_clock):
    await runner.run()
    assert frozen_clock.sleep_calls == [5, 5, 5]


@pytest.mark.asyncio
async def test_runner_stops_after_successful_booking(runner):
    result = await runner.run()
    assert result.success is True
    assert result.booked_slot_id == "sch-1001"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_runner.py -v`
Expected: FAIL because runner orchestration and scheduling behavior do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class GrabRunner:
    async def run(self) -> RunResult:
        await self.auth_service.ensure_login()
        await self.scheduler.wait_until_ready()
        async for slots in self.schedule_service.poll():
            result = await self.booking_service.try_book_first_available(slots)
            if result.success:
                return RunResult(success=True, booked_slot_id=result.slot_id)
        return RunResult(success=False, booked_slot_id=None)
```

```python
async def main() -> None:
    config = load_config(Path(sys.argv[1] if len(sys.argv) > 1 else "config.yaml"))
    async with PlaywrightClient(headless=True) as browser:
        result = await build_runner(config, browser).run()
        raise SystemExit(0 if result.success else 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py src/grab/core/runner.py src/grab/core/scheduler.py tests/core/test_runner.py
git commit -m "feat: add core runner and cli entrypoint"
```

### Task 7: End-To-End Verification And Operator Docs

**Files:**
- Create: `tests/e2e/test_live_flow.py`
- Create: `tests/e2e/conftest.py`
- Modify: `README.md`
- Modify: `config/example.yaml`

- [ ] **Step 1: Write the failing live E2E tests**

```python
@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_flow_reaches_booking_confirmation_page(live_runner):
    result = await live_runner.run(until="booking_confirmation")
    assert result.logged_in is True
    assert result.schedule_checked is True
    assert result.booking_form_opened is True


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_flow_submits_only_when_live_booking_enabled(live_runner):
    result = await live_runner.run(until="final_submit")
    assert result.submitted is bool(int(os.environ.get("LIVE_BOOKING", "0")))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LIVE_E2E=1 uv run pytest tests/e2e/test_live_flow.py -v -m live`
Expected: FAIL because live E2E harness and env-driven gating do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def pytest_collection_modifyitems(config, items):
    if os.getenv("LIVE_E2E") != "1":
        skip_live = pytest.mark.skip(reason="set LIVE_E2E=1 to run live tests")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)
```

```python
class LiveRunner:
    async def run(self, until: Literal["booking_confirmation", "final_submit"]) -> LiveRunResult:
        await self.auth_service.ensure_login()
        slots = await self.schedule_service.poll_until_match()
        form = await self.booking_service.open_booking_form(slots[0])
        if until == "final_submit" and os.getenv("LIVE_BOOKING") == "1":
            submit_result = await self.booking_service.submit_open_form(form)
            return LiveRunResult(logged_in=True, schedule_checked=True, booking_form_opened=True, submitted=submit_result.success)
        return LiveRunResult(logged_in=True, schedule_checked=True, booking_form_opened=True, submitted=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `LIVE_E2E=1 uv run pytest tests/e2e/test_live_flow.py::test_live_flow_reaches_booking_confirmation_page -v -m live`
Expected: PASS if real account, OCR service, and target params are valid.

Run: `LIVE_E2E=1 LIVE_BOOKING=0 uv run pytest tests/e2e/test_live_flow.py::test_live_flow_submits_only_when_live_booking_enabled -v -m live`
Expected: PASS and no final submit is executed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_live_flow.py tests/e2e/conftest.py README.md config/example.yaml
git commit -m "test: add live end-to-end smoke coverage"
```

### Task 8: Operator Docs And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `config/example.yaml`

- [ ] **Step 1: Write the failing documentation check**

```bash
rg "config.yaml" README.md
```

Expected: no runnable operator documentation for the new core workflow.

- [ ] **Step 2: Add minimal operator documentation**

```markdown
uv sync
uv run playwright install chromium
cp config/example.yaml config.yaml
uv run python main.py config.yaml
LIVE_E2E=1 uv run pytest tests/e2e/test_live_flow.py -v -m live
```

- [ ] **Step 3: Run the full automated verification**

Run: `uv run pytest -v`
Expected: PASS

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 4: Run a manual smoke flow**

Run: `uv run python main.py config/example.yaml`
Expected: 程序能完成配置加载、浏览器启动、登录前置检查；若没有真实账号或 OCR 服务，程序应以明确错误信息退出，而不是堆栈崩溃。

Run: `LIVE_E2E=1 LIVE_BOOKING=0 uv run pytest tests/e2e/test_live_flow.py::test_live_flow_reaches_booking_confirmation_page -v -m live`
Expected: 程序能直接完成真实登录、排班查询、刷号匹配和预约填写，但停在最终提交之前。

- [ ] **Step 5: Commit**

```bash
git add README.md config/example.yaml
git commit -m "docs: document playwright core workflow"
```

## Risks And Guardrails

- 91160 页面和接口没有稳定测试环境，所有 HTML/JSON 解析都必须用 fixture 固化，真实站点只做手工 smoke。
- 登录和预约成功判断不要只依赖 HTTP 状态码，必须同时检查页面跳转、URL 变化或关键文案，避免误判。
- `doctor_ids`、`weeks`、`days`、`hours` 过滤要允许空列表，空列表代表“不限制”，否则首版配置会非常脆弱。
- `sleep_time` 需要兼容固定值和范围值，例如 `3000` 与 `3000-5000`。
- 刷号接口请求必须固定通过浏览器上下文内 `fetch` 发出，不能在实现过程中退回独立 `httpx` 轮询，否则会重新引入反爬风险。
- `booking_strategy` 首版只能落地 `page`，`fetch` 方案只允许留接口和 schema，不允许在本次实现里扩 scope。
- live E2E 默认允许真实登录、排班查询、刷号和预约填写；只有 `LIVE_BOOKING=1` 时才允许真正提交预约。
- live E2E 必须通过环境变量注入真实账号、密码、OCR 服务地址和目标挂号参数，不能把敏感信息写入仓库配置文件。
- 本计划故意不接入 `init`、代理、通知；如果实现过程中试图顺手加入，视为 scope creep。

## Implementation Order

1. Task 1 建立配置与 schema。
2. Task 2 建立会话共享基础设施。
3. Task 3 打通 OCR + 登录。
4. Task 4 实现双通道刷号与过滤。
5. Task 5 实现预约提交。
6. Task 6 串成完整 runner。
7. Task 7 增加真实站点 live E2E 冒烟。
8. Task 8 做文档与最终验证收尾。
