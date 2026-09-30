# 当前架构与核验证据

核验日期：2026-09-30；初始代码基线：`16bc71089f34e43ef825f91c28d8413a82f484d0`；
S01 代码核验至 `89bc2ce`；S02 代码核验至 `040b911`；S03 代码/回归核验至 `51e9f41`；S04 代码/回归核验至 `47804a5`。
本文记录已实现事实；目标、依赖、验收见 [实施计划](implementation-plan-2026-09-30.md)。

## 运行路径与已有能力

| 路径 | 调用链与行为 |
|---|---|
| Python | `main.py` → `GrabRunner` → auth/session/scheduler/schedule/booking services → `PageBookingStrategy`。人工登录、打开医生页并确认后，轮询、筛选、填表；auto 须本机明确授权，单次提交，无实证 adapter 时 UNKNOWN 并停止。 |
| Tampermonkey | `userscripts/91160-doctor-page-poller.user.js` 的 doctor/booking controllers。复用浏览器登录态，医生页轮询，跳入 ystep1；`submitMode=auto`（旧 false 迁为 manual_confirm），明确授权后单次提交；未决记录跨刷新/重启阻断。 |

仅支持医生详情通道：入口 `/doctors/index/*`，表单 `/guahao/ystep1/*`，Python 页面内请求
`https://gate.91160.com/guahao/v1/pc/sch/doctor`。channel_2 fixture 不代表科室 provider 已实现。

已有：独立持久化 profile、创建/选择及部分配置写回；profile 名校验；`_user_key/access_hash` 解析、
缓存 key、周期保活和 bounded 人工恢复；随机节流/限频冷却；heartbeat、JSONL、桌面/webhook；
Windows/macOS 打包、frozen browser smoke、默认配置 bootstrap；两路径严格 card/address/date/disease/readiness 填表。

## 缺口定位

Python 路径相对 `src/grab/`，JS 指上述 userscript。

| 缺口 | 入口 |
|---|---|
| S03 提交事务与授权已实现；无已验证 live 结果 adapter | `transactions/store.py/consent.py/evidence.py`；`services/booking.py`；runner/main；JS journal/controller |
| S04 真实值/冲突/readiness 已实现；现场字段与选择语义仍待 canary | Python `booking/form.py/page.py`、`fill_booking_form`；JS snapshot/decision/preparation |
| 最终控件唯一且可操作；无安全证明的 checkIdInfo patch 已移除 | Python `fill_booking_form/_submit_control`；JS readiness/submit guard；未验证 follow-up 已交人工 |
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

S01 阶段尚未实现隐私 sink；S02 数据边界成果见下。提交事务/持久授权见 S03；严格真实值填表与互斥仍属于 S04–S05。

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

## S03 已实现事实

- `BookingState`/`BookingResult`/`RunResult` 贯通 service、runner、CLI；兼容 `success` 仅 confirmed success
  为 true，旧 false 归人工等待。UNKNOWN/人工等待停止整个 run，不再换 slot 或继续轮询；CLI 退出 0/1/2/3。
- Python 全工具共享 `~/.160grab/transactions/journal.json`，私有临时文件 fsync → atomic replace →
  POSIX directory fsync 后才调用一次 Locator.click。JS 同 origin 使用独立 localStorage journal 的单 item
  原子写入和读回，不走日志/设置的宽松 storage fallback；这是提交记录，不宣称 localStorage 可作互斥 CAS。
- journal 保存随机 attempt_id、加盐 HMAC booking_ref、状态/时间、封闭 evidence/failure 类型及人工操作标志；
  不保存 member/doctor 原值、表单、response 或 URL。损坏/未知版本 fail closed，未决不做 retention。
  unresolved SUBMITTING 重启按 UNKNOWN 展示；旧 JS submitting 迁移为无个人字段的 UNKNOWN 记录。
- click 超时、导航 race、限频、丢响应、弱页面变化均归 UNKNOWN；未验证的支付/确认 follow-up 不自动点击。
  无 live adapter 时不推断成功/无副作用。`BookingEvidence` 和 JS adapter seam 只在完整匹配目标/成员/时段的
  synthetic 测试中产生成功或明确拒绝；未把 synthetic schema 当成真实站点证据。
- Python 打开/准备瞬态失败最多三次；确定无匹配不重试；JS DOM 准备最多三次，预约页只读限频预算亦最多三次。
  最终按钮只接受唯一可操作控件；Python Locator、JS native click，移除宽文本和 form.submit fallback。
- Python `booking.submit_mode`、JS `booking.submitMode` 默认 auto，仍需确认。旧 JS true 迁 auto、false 迁人工；
  旧 maxSubmitAttempts 仅迁最多三次的 pre-submit 预算。配置 consent 不构成授权。拒绝保留人工选择；提供撤销入口。
- 授权绑定账号引用、就诊人、医生目标和 submit-v1。当前没有可靠稳定账号 adapter，实际使用运行 nonce：
  Python 本次运行有效并在重新准备登录身份时失效；JS 本次页面有效，刷新/重启需再确认。仅本机保存加盐引用。
  stable-account seam 仅以 synthetic 验证，不提取/复制认证状态。确认只在终端输入/browser dialog 显示绑定值，
  不流入日志/通知；非交互无授权停下。授权不代替站点协议、CAPTCHA 或支付。
- Python `--pending-attempts` 列出不透明 ID；`--resolve-attempt ID --resolution booked|not-booked`
  要求交互核对后输入 VERIFIED；`--revoke-consent` 不清未决。JS 面板提供核对已预约/未预约、撤销；
  Start/Stop/reset/刷新都不清 journal，人工解决留下最小 audit，已提交过的相同 booking_ref 不自动再次 click。
- 最后一次 prepare/settle await 后只读复核 grant 和模式，不因撤销重新弹授权；Python 等待期间撤销、
  JS 另一页面撤销都使尚未开始的事务零 click。Python interactive 人工等待保留页面直到用户按 Enter
  结束交接再关闭浏览器；非交互仍退出 2，不恢复自动操作。

S03 最终全套 **294 passed, 2 live skipped**；共享 contract/integration **39 passed**，其中 Chromium **19**；
CI 同命令 **294 passed, 2 deselected**。Ruff、Node 两份语法、锁定离线 sync、差异空白检查通过，uv.lock 未变。
`51e9f41` 在临时 synthetic profile 上实际关闭并重启 Chromium，验证持久 blocker 阻断且累计 click 仍为 1；
没有复用用户 profile，也不宣称断电/硬崩溃时 browser storage 的 OS fsync 保证。
独立只读 review 比较 `f291831..76bdc60` 与文档草稿，发现两个 P2（撤销窗口、人工交接关闭页面），
均由主实现者独立复核并接受，分别在 `5c9e454`、`1e7dbeb` 修复；无拒绝项。reviewer 独立检查
111 passed（含 Chromium 11）、Ruff/Node/whitespace；修复后由主实现者验证 101/33 passed 及最终全套，
没有声称 reviewer 再审修复提交。
无真实登录/预约或现场 adapter 证据；远端 CI、Windows/Linux/frozen/release 未运行。
S04 填表/身份/日期/readiness 合同和 S05 原子互斥尚未完成，默认 auto 的发布 gate 仍关闭。

## S04 已实现事实

- 仅新增必要的 `booking/form.py` 纯 HTML snapshot/decision 与 `booking/page.py` Playwright seam。
  Browser snapshot 克隆 DOM 并读取 live value/checked/selected/可操作性，避免 outerHTML 默认属性滞后。
  Python/JS snapshot 结构、decision/blockers、成员/slot/time、字段来源共用 preparation.v1.json 的 36 个期望。
  JS 纯决策在 Node 执行，两个 DOM adapter 在临时本地 Chromium context 逐场景运行。
- Python 删除数字病情和任意单 radio fallback；JS 删除默认地区/通用病情、宽松地区匹配、
  hidden member 写回与合成选择事件。两个适配保留已有值，冲突/多候选/身份警告交人工。
  显式 card/病情/address 配置默认为空，值与快照不进入日志或 journal。card 不从证件复制。
- 日期与 selected slot、序列化 schedule data/指定日期 UI 核对，无歧义才能填空值；冲突/非法日期交人工。
  PHP serialized array 按字节长度/深度/节点预算解析，仅唯一当前 schedule record 能提供日期；
  其他 record、重复 key、坏长度或 unsupported object/reference 不提供日期。optgroup 与原生 required radio
  组/disabled validity 纳入两路径 parity。最多三个准备 pass 覆盖延迟 card/disabled 按钮/异步地区级联。
  requested schedule 在 fetch 返回后、任何选择之前绑定；必填 blocker 贯通 service，不换号。
  Python 精确 Locator，JS native click。最终等待后重新核对字段、选择、控件与授权；S03 pending 保护继续有效。
- 站点协议 checkbox 不自动接受。checkIdInfo patch 缺少证明不会跳过身份校验/警告的现场依据，已完全移除，
  Chromium synthetic 回归证明原 AJAX 函数和空响应保持原样。
- JS settingsVersion=4 清除旧自动默认值同名字段；历史来源不可分辨，需重新明确输入。
  卡号输入和完整精确 memberLabel 在面板可配置；Python 字段示例见 config/example.yaml。
- 修复后全套 **449 passed, 2 live skipped**；contract/integration **205 passed**（其中 Chromium **100**，
  均作为全套实际执行）；CI 同参数本地命令 **449 passed, 2 deselected**；
  Ruff、Node 两份语法、锁定离线 sync、whitespace 通过。
  现场没有运行；snapshot 对 card/address/date/disease 的已知控件按存在视为必填，未知现场字段/选择语义
  保守交人工。互斥仍属 S05，默认 auto 发布 gate 仍关闭。

S04 新鲜只读 review 比较 `a1bfdf6..528f760` 及证据草稿，发现 **1 P1 + 2 P2**，全部独立复核接受：
错误 schedule 日期关联、required radio 组误判、Python optgroup 遗漏。分别在 `47804a5`、`71afcd2`、`ad1489a`
修复并执行定向 regression（日期 28 + serialized 8；radio 20；optgroup 20）。无拒绝项。
主实现者另发现 requested schedule 在 fill 后才核对，`8199211` 提前绑定并证明零选择/零提交；相关 5 passed。
reviewer 独立相关 **230 passed**、Ruff/Node/whitespace；独立审阅 `8199211` 并验证相关 Chromium **2 passed**。
其余三项最终修复由主实现者验证，未声称 reviewer 再审。uv.lock 未变；远端 CI/live/Windows/Linux/frozen/release 未运行。
