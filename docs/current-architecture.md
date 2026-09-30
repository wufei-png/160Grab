# 当前架构与核验证据

核验日期：2026-09-30；初始代码基线：`16bc71089f34e43ef825f91c28d8413a82f484d0`；
S01 代码核验至 `89bc2ce`；S02 代码核验至 `040b911`。
本文记录已实现事实；目标、依赖、验收见 [实施计划](implementation-plan-2026-09-30.md)。

## 运行路径与已有能力

| 路径 | 调用链与行为 |
|---|---|
| Python | `main.py` → `GrabRunner` → auth/session/scheduler/schedule/booking services → `PageBookingStrategy`。人工登录、打开医生页并确认后，轮询、筛选、填表、自动提交。 |
| Tampermonkey | `userscripts/91160-doctor-page-poller.user.js` 的 doctor/booking controllers。复用浏览器登录态，医生页轮询，跳入 ystep1；默认 `autoSubmit=false`，开启后提交并暂停，可选返回重试。 |

仅支持医生详情通道：入口 `/doctors/index/*`，表单 `/guahao/ystep1/*`，Python 页面内请求
`https://gate.91160.com/guahao/v1/pc/sch/doctor`。channel_2 fixture 不代表科室 provider 已实现。

已有：独立持久化 profile、创建/选择及部分配置写回；profile 名校验；`_user_key/access_hash` 解析、
缓存 key、周期保活和 bounded 人工恢复；随机节流/限频冷却；heartbeat、JSONL、桌面/webhook；
Windows/macOS 打包、frozen browser smoke、默认配置 bootstrap；JS card/address/date/disease/readiness/native click 修补。

## 缺口定位

Python 路径相对 `src/grab/`，JS 指上述 userscript。

| 缺口 | 入口 |
|---|---|
| bool 结果、同 slot 三次 submit、弱成功判定、失败换 slot/继续轮询 | `models/schemas.py::BookingResult/RunResult`；`services/booking.py::submit_with_retry/_is_booking_success/try_book_first_available`；`GrabRunner._poll_and_book`；JS `inspectBookingPage` |
| 假病情/单 radio fallback；Python 缺 card/address/date；JS 默认地区/通用病情、reset 可清 pending | Python `fill_booking_form`；JS `CONFIG_DEFAULTS`、fill/readiness、start/stop/reset |
| 宽控件选择、form.submit、follow-up 待审计 | Python `_trigger_submit_control/submit_booking_via_page`；JS `findSubmitControl/clickFollowupControl/checkIdInfo` |
| 隐私 sink 已按 S02 收口；Windows 权限能力仍未实测 | `observability/privacy.py/safe_logging.py/reporter.py/notifications.py`；`utils/private_files.py/retention.py`；JS `appendLog/state` |
| 缓存 key 失效判断不足、一般网络异常退出 | `ScheduleService._resolve_schedule_user_key/fetch_doctor_schedule/poll` |
| 时间 naive；只有 profile 锁/页面 controller，无业务互斥；无 channel | `core/scheduler.py`、schedule 日期；profile manager、JS sessionStorage；`PlaywrightClient.launch` |
| live 缺 ready/完整目标解析/有限等待闭环 | `tests/e2e/conftest.py::LiveRunner`；待 S06 |

## S01 已实现事实

- `AuthConfig.strategy` 只接受 `manual`。配置 loader 在任何 browser/profile 创建前验证；
  CLI 配置验证失败退出 1，使用固定错误消息避免 Pydantic 输出拒绝值。
  manual 登录与可见浏览器流程保留；移除无实现的 auto 登录入口。
- 顶层 username/password/ocr 仍可读取，有值时 loader 发出只含字段名的弃用提示。
  独立 OCR 模块保留。产品描述改为人工登录的医生页预约辅助工具。
- [业务合同](booking-contract.md) 已固化。`tests/contracts/booking/fixtures/scenarios.v1.json`
  与三份手写 HTML 共享五组解析期望：首个时段、筛选匹配、时间不匹配、缺时段、缺 schedule。
  Python parser 与 Node JS hooks 已执行；Node 缺失会失败，不跳过 JS 验收。
- `tests/integration/test_booking_chromium.py` 在真实 Chromium 的临时 context 中运行相同场景，
  Python 读取浏览器 HTML、JS 使用真实 DOM。所有请求由本地 route 处理，意外请求阻断，
  click 与 submit 均为零。此证据只覆盖解析；未验证真实站点适配或最终提交安全。
- PR/push CI 与 release 验证前置均安装 Node/Chromium，锁定 sync、Ruff、Node 语法检查及
  unit/contract/本地 Chromium 检查；明确排除 live。release 的打包和 frozen smoke 流程保留。

S01 阶段尚未实现隐私 sink；S02 数据边界成果见下。提交事务/持久授权、严格真实值填表与互斥仍属于 S03–S05。

## S02 已实现事实

- [数据合同](security-and-privacy.md) 已固化。JSONL/reporter 使用字段与值白名单，拒绝未知键、
  自由 message、nested selector/value、URL、服务器文本和异常。直接日志使用固定消息与安全 logger，
  CLI 不渲染 exception traceback；登录诊断只保留安全 readiness，真实就诊人选择提示与日志分开。
- JS console、panel 日志和摘要使用相同封闭投影；读取 sessionStorage 时立即丢弃旧日志/摘要，
  已版本化条目也重新投影并过滤 7 天窗口；原有 pending/submitting 与计数保留。
- 桌面/webhook 通知使用最小安全投影；失败只记录固定分类，不改变预约结果。
  webhook destination/header 作为传输配置保留，但不复制到 body/错误输出。
- `private_files` 在 POSIX 逐级 no-follow 并以 directory descriptor 锚定读写/清理；
  新目录 0700、文件 0600，Chromium 启动继承 0077 umask。拒绝 linked profile/marker 与文件 hardlink，
  不复制 profile，不递归 chmod 已有目录；配置写回/打包模板也使用安全入口。
- 普通 debug 只有安全 JSON，cookie metadata 仅为布尔与计数。原始 HTML/截图须显式
  `GRAB_DEBUG_DIR` 加 `logging.include_sensitive_debug=true` 且目录匹配，只写本地私有文件；
  文件名没有 label/个人值，普通日志与通知不包含 raw 内容。生成诊断文件名已纳入 Git ignore。
- `--cleanup-data [--dry-run]` 不启动浏览器，输出仅计数。普通 JSONL 保留 7 天、debug 24 小时，
  只删除根目录匹配应用格式的单链接常规文件；保护 profile 及非输出命名空间的 attempt journal。
  启动/快照时机会式清理；没有退出后后台服务，原始快照需在 24 小时内人工删除或运行清理命令。
  旧原始快照 ownership 不明时不自动删除。S06 现场 converter 仍未实现，不认可原始文件为 fixture。

S02 未改变预约提交/填表/恢复语义，也未执行真实登录/预约或复制认证状态。Windows ACL/reparse
与跨平台/frozen 行为未实测，不能把 POSIX 模式和 descriptor 验证当成跨平台保证。

## 已取得证据

初始基线：2026-09-30 使用现有 `.venv`，`uv run --locked --offline --no-sync pytest -q`：**168 passed, 2 skipped**；
Ruff 和 Node userscript 语法检查通过。两个 live 测试因未启用 `LIVE_E2E=1` 跳过。
现有测试接受“三次 submit 后成功”，绿灯不证明提交安全；打包 browser smoke 不证明预约适配。

S01：`uv sync --locked --extra dev` 成功；`uv run --locked ruff check .` 与 Node userscript/harness
语法检查通过；`uv run --locked pytest -q` 为 **197 passed, 2 skipped**。
合同/集成套件 **16 passed**，其中真实 Chromium **5 passed**；
CI 同命令 `uv run --locked pytest -q -m "not live" --browser chromium` 为 **197 passed, 2 deselected**。
离线 sync 曾因缓存缺 hatchling 失败，随后正常及离线锁定 sync 均成功，未改 uv.lock。
新鲜只读委派 review 结论 **No findings**，reviewer 独立复核相关测试 **48 passed**（含 Chromium 5）
及 Ruff/Node/差异空白检查；无需接受或拒绝修复项。
本机 Node v24.15.0、macOS arm64 Chromium 145.0.7632.6（Playwright v1208）；
CI 配置 Node 22，但未执行远端 CI、Windows/Linux 或 frozen 打包验证。未执行 live，也未读取/复制认证状态。

真实观察仅为 Chrome 的 `https://www.91160.com/` 呈现已登录 UI；未验证服务端 session、医生/排班/成员/预约页
或提交，未导出认证资料。Chrome 用户 profile 与 Python 专用 profile 不共享这份登录证据。
后续 canary 须重新取证；现有 `LIVE_BOOKING=1` 最终提交门槛保留。

S02：锁定离线 `uv sync --locked --offline --extra dev`、Ruff、Node 两份 JS 语法通过。
完整 **222 passed, 2 live skipped**；contract/integration **17 passed**，含真实本地 Chromium **6 passed**。
CI 同命令 **222 passed, 2 deselected**。新增隐私 Chromium 场景使用临时 context、本地 synthetic route，
包含 DOM/script、cookie、URL 与 JS console/storage 注入；普通 debug 只生成安全 JSON、canary 零明文。
注入、通知失败、POSIX 权限/link、retention/dry-run 测试通过；四阶段相关检查为 145/58/92/53 passed。
本机 Node v24.15.0、Playwright 1.58.0；锁文件未改。两个 live 因未启用 `LIVE_E2E=1` 跳过，
未执行远端 CI、Windows/Linux/frozen/release 或现场 fixture 导出。

S02 新鲜只读委派 review 审查 `f14a7ea..d81b350` 及完成证据草稿，发现 1 个 P2：JS 固定消息白名单
遗漏人工等待/限频摘要，导致面板阶段错误。独立复核后接受，`040b911` 补齐固定工作流消息并将
动态摘要改为安全固定提示，回归证明人工等待/两条冷却/失败阶段正确且 detail 仍无敏感明文。
无拒绝项；reviewer 独立检查两组相关套件 75/111 passed、Ruff/Node/whitespace，以及临时真实
Chromium profile 新目录/文件 0700/0600。修复后相关 62 passed、完整 222 passed/2 live skipped、
CI 同命令 222 passed/2 deselected；最终修复由主实现者验证，未声称 reviewer 重新审查修复提交。
