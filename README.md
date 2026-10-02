# 160Grab

健康160医生页预约辅助工具。维护 Python/Playwright 与 Tampermonkey 两条路径：人工登录和 CAPTCHA，读取医生页排班，筛选时段，只使用明确配置或站点已有的真实值准备表单。两路径共享业务和安全合同，交互适配各自实现。

默认 `auto` 须明确授权后才能点击一次最终提交；也可选择 `manual_confirm`。目前没有已验证的现场结果 adapter，点击后的弱页面变化、超时、导航或限频一律是 `OUTCOME_UNKNOWN`，必须在原站核对预约记录。禁止两路径、多个 browser profile 或多机器混跑同一目标。

S01–S09 的代码/离线验收进度见 [实施计划](docs/implementation-plan-2026-09-30.md#完成记录)，现场 pending 单独保留。当前只支持医生详情页通道；科室 fallback 须先取得漏号证据。参考项目：[pengpan/91160-cli](https://github.com/pengpan/91160-cli)。

## 文档入口

- [当前架构与证据边界](docs/current-architecture.md)
- [两路径预约业务合同](docs/booking-contract.md)
- [日志、隐私、权限与清理](docs/security-and-privacy.md)
- [本机人工 canary 与脱敏夹具](docs/live-canary.md)
- [集成验收、配置迁移与未覆盖 gate](docs/integration-acceptance.md)
- [逐会话启动提示词](docs/implementation-session-prompts-2026-09-30.md)
- [历史改进建议](docs/future-improvements.md)（历史原文不作为当前操作说明）

## Python 快速开始

需要 Python 3.11+、uv 和 Playwright Chromium；开发验收还需要 Node（CI 使用 Node 22）。

```bash
uv sync --locked --extra dev
uv run --locked playwright install chromium
cp config/example.yaml config.yaml
uv run --locked python main.py config.yaml --create-profile
uv run --locked python main.py config.yaml
```

`config.yaml` 是已忽略的本地私有输入，勿提交账号、就诊人、诊疗信息或认证资料。专用 profile 不复制系统 Chrome 登录态。首次运行可自动创建同 channel 的 `profile_1`；已有多个 profile 时提示选择，也可用 `--profile-name` 创建指定 profile。

程序在 profile/browser 创建前取得全工具 leader，换 profile 不能绕过。本机只允许一个 Python 自动运行。

1. 用户在程序打开的浏览器内登录，完成验证码，进入医生详情页，然后在终端按 Enter。
2. 程序捕获目标；docid-only 或 `dep_id-0` 须补全 unit/department，无法验证就停止。
3. 读取 `member.html` 校验配置的就诊人；未配置时唯一成员可自动选择，多成员交用户明确选择。
4. 保持 leader，按配置时区等待启动，然后在页面内查询排班。
5. 命中后准备预约页；精确核对成员、schedule、日期、时段、字段和唯一可操作提交控件。缺必填信息或冲突交人工，不换号绕过。
6. `auto` 首次展示绑定对象与行为，输入 `AUTHORIZE` 才取得本地授权。无法可靠区分账号时授权仅本次运行有效；重启/重新登录重新确认。拒绝保留人工选择，非交互无授权零提交。
7. 最终复核授权、readiness 和 owner，先写 durable `SUBMITTING`，再点击一次。UNKNOWN 停整个 run，不继续提交、换号或轮询。

人工等待时，交互终端会保留浏览器页面，按 Enter 结束交接后关闭；非交互运行返回退出码 2。站点协议、安全验证、支付和未验证 follow-up 始终交人工。

## 配置说明与迁移

完整示例见 [config/example.yaml](config/example.yaml)。

| 字段 | 当前行为 |
|---|---|
| `auth.strategy` | 仅 `manual`；`auto`/未知值在浏览器启动前失败。旧 username/password/ocr 有值仅给无敏感值弃用提示，不用于登录 |
| `browser.channel` | `chromium`（默认）/`chrome`/`msedge`；后两者须本机安装，启动失败不降级 |
| `browser.launch_persistent_context` | 默认 true；false 使用临时 context |
| `browser.profile_name` / `profiles_root_dir` | 独立 profile，默认根 `~/.160grab/browser-profiles`；marker 绑定 channel，旧 marker 视 Chromium，禁止跨 channel 复用或移动认证状态 |
| `browser.session_refresh_interval_seconds` | 保留旧字段；正常有效轮询不再周期保活。缺 key 时仅必要低频诊断，至少 60 秒一轮 |
| `browser.session_recovery_max_attempts` / `session_recovery_cooldown_seconds` | 仅 confirmed expired 才 bounded 人工恢复；恢复后重新捕获目标/成员/授权并核对 pending |
| `browser.stealth` | 保留默认补丁，可关闭；不替代站点协议与限频边界 |
| `member_id` / `doctor_ids` / `weeks` / `days` / `hours` | 就诊人和号源过滤；hours 支持整点/半小时区间，如 `8-9`、`09:00-09:30`，预约页再次精确匹配 |
| `sleep_time` | 默认 `3000-5000` ms；轮询下限 3 秒，更小配置不能缩短该下限 |
| `page_action_sleep_time` | 打开/提交前停顿，默认 `400-900` ms |
| `booking_retry_sleep_time` | 仅提交前打开/准备瞬态失败间隔，默认 `2000-4000` ms，最多三次；不重试最终 click |
| `rate_limit_sleep_time` | 只读限频冷却下限，默认 `15000-25000` ms；遵守更长 Retry-After |
| `schedule.timezone` / `late_start_grace_seconds` | 默认 `Asia/Shanghai`/30 秒；刷号日期按此时区取当天 |
| `enable_appoint` / `appoint_time` | 推荐 offset ISO（如 `2030-01-01T08:00:00+08:00`）；旧 naive 按配置时区解释并提示迁移。DST 歧义/不存在拒绝；迟到 ≤30 秒开始，超时须人工确认，非交互停止 |
| `booking.submit_mode` | 默认 `auto`，本地明确授权独立于配置；`manual_confirm` 仅准备后交人工 |
| `booking.clinic_card` / `disease_description` / `address` | 默认为空；只填空控件，已有值保留，冲突交人工；card 不从证件复制，地区只认唯一精确 option value/完整文字 |
| `logging.jsonl_dir` / `heartbeat_interval_seconds` | 默认 `~/.160grab/logs`/300 秒，输出固定消息与白名单字段 |
| `logging.include_sensitive_debug` | 默认 false；原始快照还需 `GRAB_DEBUG_DIR` 双开关，只落本机 |
| `notifications.desktop` / `rate_limit_threshold` | 本地通知、持续限频阈值；消息无个人/页面值 |
| `notifications.webhook.url` / `timeout_seconds` / `headers` | 最小安全事件外发；失败不改变预约状态，不能发送整个响应/异常/表单 |

只读 session 分类为 VALID/EXPIRED/TRANSIENT_FAILURE/RATE_LIMITED/UNKNOWN。timeout/5xx/限频连续失败最多 5 次，第 5 次停下；full jitter base 1s/cap 30s，与轮询/冷却/Retry-After 取最大值。有效业务响应才重置预算；缺 key、旧 cookie 或未知 schema 不等于过期或无号源，UNKNOWN 停下告警。取消不发额外请求。

配置迁移不要求全局 config_version；字段默认和弃用提示独立验收。配置授权开关不能替代实际确认。回滚可切人工模式、Chromium、doctor-only；旧二进制回滚前须人工核对未决记录，不能清 journal 或恢复盲重试、弱成功、伪造字段。

## 未决提交与退出码

| 结果 | CLI exit | 行为 |
|---|---|---|
| CONFIRMED_SUCCESS | 0 | 有匹配结果证据才算成功 |
| CONFIRMED_NO_EFFECT | 1 | 有明确业务拒绝/无副作用证据 |
| AWAITING_MANUAL_CONFIRMATION | 2 | 等待人工操作 |
| OUTCOME_UNKNOWN | 3 | 停止整个 run；先在原站核对 |

Python journal 为 `~/.160grab/transactions/journal.json`，只存不透明引用和最小状态。未解决 SUBMITTING 重启按 UNKNOWN 展示，存储损坏/未知版本 fail closed，retention 和撤销授权不能清 pending。

```bash
uv run --locked python main.py config.yaml --pending-attempts
# 先在原站核对，再使用输出中的不透明 attempt ID；交互输入 VERIFIED
uv run --locked python main.py config.yaml --resolve-attempt ATTEMPT_ID --resolution booked
uv run --locked python main.py config.yaml --resolve-attempt ATTEMPT_ID --resolution not-booked
uv run --locked python main.py config.yaml --revoke-consent
```

人工解决保留 audit。已提交过的相同 booking_ref 不自动再次 click；false success 也不表示可重试。两路径的 journal 和授权独立，互斥不覆盖跨路径/profile/机器。

## Tampermonkey

导入 [userscript](userscripts/91160-doctor-page-poller.user.js)，在已登录的医生页打开右上角 Settings。配置保存于 Tampermonkey storage（无 GM 能力时使用本地 storage）；授权/attempt journal 单独存同 origin localStorage，不与 Python 共享。

1. 明确配置 `memberId` 或完整、唯一的 `memberLabel`；唯一正确候选才可选择，多候选/身份警告交人工。
2. card、病情、Province/City/Area/详细地址默认全空，只用已知真实值。已有非空值不覆盖；未接受的协议不自动勾选。
3. 设置 weeks/days/hours、Appointment From（查询起点）和 Start At（启动时间）；时区默认 Asia/Shanghai，Start At 保存带 offset 的 ISO，DST 需 offset 消歧。迟到的 Auto Start/reload 停下，只允许本 document 新 Start 确认。
4. `booking.submitMode` 默认 auto；旧 `autoSubmit=false` 迁人工，true 迁 auto 仍须新授权。首次验证可选人工模式或本机 readonly/prepare canary。
5. 点 Start 后取得同 browser profile、同 origin 的 Web Lock，解析目标并查询；命中导航到 ystep1，目标文档重新 acquire。
6. 精确准备后，auto 弹出目标/就诊人和行为确认；授权仅本次页面运行有效，拒绝切人工。先 durable SUBMITTING 再 native click 一次，无现场 adapter 时 UNKNOWN 停下。

面板有 Start/Stop、Overview/Settings/Logs、Reset State、撤销授权和人工核对已预约/未预约。Stop/reset/刷新/重启都不清 durable pending；Reset 不清配置，也不重置连续只读失败预算或缩短冷却。后台隐藏、pagehide、失锁或过期暂停；无 Web Locks 禁自动运行。

预约页必须匹配医生页传递的医院、科室及号源。等待提交时变更目标、就诊人、筛选或填表设置会暂停当前操作；重新 Start 后核对更新后的场景和授权。

settingsVersion=5：旧版自动生成的广东/深圳/南山区、通用病情等来源不可区分，迁移时清除后需明确重新输入；v4 已明确的真实字段保留。旧 maxSubmitAttempts 只迁到最多三次的 pre-submit 预算。`autoReturnAfterSubmitFailure` 不能绕过 UNKNOWN，字段/时段不匹配或必填缺失不换号绕过。

缺 key/未知 schema 停下人工核对；明确 10021/login redirect 清缓存并停下等待人工登录后按 Start，不能自动刷新重试。轮询预算/取消/冷却和 Python 合同一致。真实 Tampermonkey sandbox 仍待现场验证。

## 日志与本机调试

普通 JSONL、直接日志、JS console/持久日志、通知只输出固定安全消息、不透明引用、状态、计数和 readiness；不输出姓名、证件、member id、card、地址、诊疗信息、cookie/token、URL query、HTML 或异常正文。

```bash
GRAB_DEBUG_DIR="$HOME/.160grab/debug" uv run --locked python main.py config.yaml
```

上例仅生成安全结构 JSON。只有同时设置 `logging.include_sensitive_debug: true` 才生成原始 HTML/截图；它们只留本机 24 小时，不能直接加入 Git 或上传。新 POSIX 目录/文件为 0700/0600；已有 profile 不递归改权限、不复制，Windows mode 不等于 ACL。详情见 [数据合同](docs/security-and-privacy.md)。

普通日志保留 7 天；debug 保留 24 小时。退出后无后台清理服务，原始快照应在 24 小时内手动删除或运行：

```bash
GRAB_DEBUG_DIR="$HOME/.160grab/debug" uv run --locked python main.py config.yaml --cleanup-data --dry-run
GRAB_DEBUG_DIR="$HOME/.160grab/debug" uv run --locked python main.py config.yaml --cleanup-data
```

只清应用拥有的常规输出，不越界删除 profile/未决记录。真实 fixture 只能经 [canary converter](docs/live-canary.md#夹具转换与刷新) 和人工安全检查导出脱敏最小场景；本仓库目前无新现场 fixture。

## 开发验收

```bash
uv sync --locked --extra dev
uv run --locked playwright install chromium
uv run --locked ruff check .
node --check userscripts/91160-doctor-page-poller.user.js
node --check tests/contracts/booking/node_harness.cjs
uv run --locked pytest -q -m "not live" --ignore=tests/contracts --ignore=tests/integration
uv run --locked pytest -q tests/contracts/ tests/integration/ -m "not live" --browser chromium
```

已有环境可用 `--offline --no-sync`。Node 缺失不能跳过 JS 验收；临时 Chromium context 和 synthetic route 不读取用户 profile，不访问真实站点。完整检查可执行 `uv run --locked pytest -q`，未显式启用 live 时三个现场层级跳过。

PR/push CI 与 release 验证前置安装 Node/Chromium，强制 Ruff、两份 Node 语法、单元/离线 canary、contract/本地 Chromium，设置 LIVE_E2E=0/LIVE_BOOKING=0 并排除 live。CI 矩阵覆盖 Linux、Windows、macOS arm64/Intel，也可手动运行。本地同命令通过不代表远端 CI 已执行，最新结果见 [验收索引](docs/integration-acceptance.md)。

## 打包与发布

[Release workflow](.github/workflows/release.yml) 配置 Windows x64、macOS arm64/Intel 的 PyInstaller bundle；包含 starter config、Chromium 和启动器，目标机器无需 Python。`browser.channel` 默认 Chromium；Chrome/Edge 须目标机器另装并使用独立 profile。

```bash
./packaging/build-macos.sh
```

Windows PowerShell 使用 `./packaging/build-windows.ps1`。产物位于 `dist/release/`：zip、解压目录及 `.sha256`；SHA-256 可用 `shasum -a 256`（macOS）、`sha256sum`（Linux）或 `Get-FileHash -Algorithm SHA256`（Windows）核对。bundle 的 [操作说明](packaging/README-release.md) 随包附带。

可在 [GitHub Releases](https://github.com/wufei-png/160Grab/releases) 查看发布产物；解压后 macOS 使用 `160Grab.command`，Windows 使用 `160Grab.exe`。首次缺配置会生成 config.yaml 并退出，请填好后重新运行。未签名产物可能触发系统信任提示。

release 在 v* tag/手动触发时运行验收、构建、frozen `--help`/`--smoke-browser` 和默认配置 bootstrap，并上传工作流 artifact。分支上的手动执行仅验证构建；只有 v* tag 才进入 GitHub Release 发布。配置发布流程不等于本次执行发布，当前实际平台证据见 [验收索引](docs/integration-acceptance.md)。

## 本机人工 Live Canary

CI 不跑 live。默认不能仅凭 LIVE_E2E=1 启动现场查询或填表：还须显式 readonly/prepare/submit 层级、专用 profile、目标医生、日期、人工 ready；敏感准备和最终动作需本次具体场景批准。

只读与 prepare 均为零最终/follow-up 动作，即使产品 auto 且 consent 有效。submit 还须 LIVE_E2E=1、LIVE_BOOKING=1 和精确场景批准。完整操作、预算、环境变量、两路径证据和脱敏刷新流程见 [live-canary.md](docs/live-canary.md)。缺 profile/目标/批准或号源时记录 blocker/inconclusive，不能把本地回归当成真实预约成功。

## 项目结构

`src/grab/` 包含 browser、core、models、services、booking、transactions、observability、canary 和 utils。共享合同位于 `tests/contracts/`，真实本地浏览器回归位于 `tests/integration/`，人工现场入口位于 `tests/e2e/`。

## License

[MIT](LICENSE)
