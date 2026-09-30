# 160Grab 改进实施计划

**设计已于 2026-09-30 确认。** 工作目录：`/Users/wufei2/github.com/wufei-png/160Grab`。

新会话读取本文的决策、合同、对应 S 编号章节，以及 [当前架构](current-architecture.md) 即可。
代码入口用于按需定位，不是全文必读清单；不要求阅读外部报告、README 或历史计划。
[启动提示词](implementation-session-prompts-2026-09-30.md) 已指定实现和 review 技能，本文不重复其流程。
只执行当前编号；按下表核对前置成果。代码交付为本地提交，不 push。
review 比较基线取会话开始时的 HEAD，覆盖本会话完整改动。

## 已确认决策

| ID | 决策 |
|---|---|
| D01 | Python/Playwright、Tampermonkey 都维护，共享业务、安全合同与 JSON 场景；交互适配分别实现，无运行时跨语言依赖。 |
| D02 | 两条默认自动最终提交，首次明确确认并保存本地授权；保留人工模式。旧配置不等于授权。 |
| D03 | canary 本机人工执行；CI 只跑离线及本地浏览器。真实最终提交须针对具体就诊人、医生、日期时段单独授权。 |
| D04 | Python 本机进程互斥、userscript 同浏览器跨标签互斥；禁止两路径或多机器混跑同一目标，不建设协调服务。 |
| D05 | 填表仅用用户明确配置或站点已有值；删除数字/通用病情、默认地区，缺必填信息交人工。 |
| D06 | 医生页主通道、人工登录/CAPTCHA、正常限频边界保留。 |

### 自动提交授权

- Python `booking.submit_mode`、JS `booking.submitMode`：`auto | manual_confirm`，默认 auto。
  旧 JS `autoSubmit=false` 保留人工模式，true 迁为 auto，但须取得新授权。
- 两路径授权各存本地运行状态，不以仓库配置 `consent=true` 绕过确认。
  绑定账号、就诊人、医生目标、策略版本，仅存加盐的不透明引用；无法可靠区分账号时仅本次运行有效。
- 首次确认展示目标/就诊人、自动最终提交行为、未知结果需人工核对及禁止混跑；这些个人信息不入日志。
  未授权的非交互运行停下；拒绝后保留人工模式；换绑定对象/策略版本重新确认，支持撤销。
- 此授权不代替站点协议、验证码或支付确认，也不授权实现会话执行真实预约或复制认证状态。

## 业务合同

S01 固化为 `docs/booking-contract.md`；下列规则适用于两路径。

```text
DISCOVERED -> PREPARED -> AWAITING_MANUAL_CONFIRMATION
                      -> SUBMITTING -> CONFIRMED_SUCCESS
                                    -> CONFIRMED_NO_EFFECT
                                    -> OUTCOME_UNKNOWN
```

| 规则 | 必须满足的行为 |
|---|---|
| 提交前 | 身份、目标、时段、readiness、授权、互斥均通过；先持久化 SUBMITTING 再调用副作用动作，写入失败不得 click。 |
| 单次副作用 | 进入 click 边界后异常视为可能已提交；每个已验证 follow-up 控件至多触发一次。未知 follow-up、条款、安全验证、支付交人工。 |
| 未知结果 | 停止整个 run 的提交、换 slot 和轮询；Start/Stop/reset、刷新、重启、lease 过期都不能解除阻断。 |
| 持久记录 | 保存 attempt_id、opaque booking_ref、状态、时间、证据类型、failure_class、human_action_required；不存完整响应、URL query 或诊疗资料。存储损坏/未知版本 fail closed。 |
| 恢复 | 重启后的 unresolved SUBMITTING 按 UNKNOWN 展示，不被 retention 删除；人工核对“已预约/未预约”后解决并留最小审计事件。撤销授权不清未决记录。 |
| 成功证据 | 明确匹配当前预约的成功页、业务响应或订单；200/302、URL 变化、form 消失只是弱观测。没有实证 adapter 时保守 UNKNOWN。 |
| 无副作用证据 | 明确业务拒绝才是 CONFIRMED_NO_EFFECT；timeout、5xx、限频和弱页面变化不算。 |
| 重试 | 仅提交前只读打开/准备的瞬态失败可自动重试，默认最多 3 次；确定字段/时段不匹配不重试；业务拒绝可选下一个号源，不自动重提同一预约。 |
| reconcile | 仅用已验证的匹配目标只读证据；没有证据则引导人工查原站预约记录，不假定已有订单 API。 |
| 结果贯通 | BookingService、runner、RunResult、CLI、JS panel 均区分终态；兼容 success 属性仅在 confirmed success 为 true，false 不代表可重试。CLI exit：成功 0、确定失败 1、人工等待 2、UNKNOWN 3。 |
| 控件 | 唯一且可操作；Python Locator，JS native click。禁止取第一个宽文本候选、无目标验证的单 radio fallback、未核实的 form.submit/requestSubmit。 |

### 填表与两路径一致性

共享版本化 synthetic JSON/HTML 场景包含选定 member/schedule/date/time、字段来源、必填 blockers、
submit 候选数、期望状态和 click 次数。Python parser、JS hooks 与各自浏览器适配使用同组期望。

必测：多成员/唯一错误 radio、时间无匹配、日期缺失或冲突、card 已有或缺失、地址字段缺省或必填缺值、
病情已有/配置/缺值、双按钮/隐藏/disabled、延迟 DOM、提交后 timeout/navigation/限频、弱成功、reload 未决记录。
病情/地址已有非空值不无声覆盖，冲突交人工；规则勾选不代替首次接受站点协议。
`checkIdInfo` 空响应 patch 仅在证明是指定 endpoint 的纯解析兼容、未跳过身份校验或警告时保留。
只建立必要的 booking parser/page seam，不做全仓库分层重构。

## 数据合同

S02 固化为 `docs/security-and-privacy.md`。

- 所有 JSONL、直接 loguru、JS console/持久日志、通知、诊断与异常输出采用字段白名单。
  允许不透明 run/attempt 引用、phase/state、计数/延迟、错误分类、布尔 readiness；拒绝未知字段和自由服务端文本。
  姓名、member id、证件、phone、病情、地址、card、cookie、user_key/access_hash、认证 header、URL query、HTML 不入普通输出。
  UI/本地用户配置所需真实值与日志分离；迁移旧 JS 敏感 runtime logs 时丢弃。
- 同时处理 nested selector/value、message、traceback 和通知失败分支，不仅按 key 正则脱敏。
  webhook/桌面只发送 event、不透明 run/attempt 引用、phase、severity、固定安全 message，不开放敏感外发开关。
- 新建 POSIX 专用目录 0700、文件 0600；Windows 单独说明权限能力。保留 profile 名校验，不复制 profile、
  不递归改已有内容；防 symlink 导致写入/清理越界。
- 普通日志保留 7 天。原始 HTML/截图默认关闭，须 `GRAB_DEBUG_DIR` 和 `logging.include_sensitive_debug=true`，
  仅本机保留 24 小时；普通 debug 只存安全结构，cookie metadata 不存 value。
  retention 支持 dry-run，仅清应用拥有的常规文件，保护活动 profile 与未决记录。
- 真实 fixture 仅导出脱敏最小 DOM/JSON，保留 schema/必要行为，替换个人与会话值，处理 script、嵌入 JSON、
  URL 和事件正文；发现未知敏感字段拒绝导出。未证明安全的原始 HTML/截图不入 Git、不上传。

## 优先级与验证

执行顺序为 S01→S09。P0：契约、数据安全、提交安全、填表一致性、互斥、canary；
P1：session/退避、时间、channel。S02 可先消除泄露；S05 是默认 auto 发布前置；
S06 先建立现场证据管道，S07 利用证据分类。CI 检查随能力引入，不等 S09 才加入。

```bash
uv sync --locked --extra dev
uv run --locked ruff check .
uv run --locked pytest -q
node --check userscripts/91160-doctor-page-poller.user.js
```

S01 建立 contract/integration suite 后增加：

```bash
uv run --locked playwright install chromium
uv run --locked pytest -q tests/contracts/ tests/integration/
```

已有环境可用 `--offline --no-sync`。不擅自升级锁文件。CI 安装 Node，不能因缺 Node 跳过 JS 验收。
FakePage 不算本地真实浏览器验证；代码、离线、本地浏览器、live、远端 CI 证据分别记录。

## 会话范围与验收

Python 入口路径相对 `src/grab/`；JS 指 `userscripts/91160-doctor-page-poller.user.js`。
除列出的入口，还应按调用关系查看必要测试；下面的工作顺序用于拆分独立有效阶段。

### S01 — 契约、离线基础与产品入口纠偏

依赖：无，设计已确认。入口：`models/schemas.py`、`services/auth.py`、config loader、`main.py`、JS hooks、CI。

工作：固化业务合同、版本化 synthetic 场景；建 `tests/contracts/booking/`、`tests/integration/` 的 Python/JS
与 Chromium harness，加入 Node/Ruff CI；auth 仅支持 manual，auto/未知 strategy 在浏览器启动前报配置错误。
顶层 username/password/ocr 有值时给无敏感值弃用提示，暂不删除字段和独立 OCR 模块；纠正 pyproject 产品描述。
未来安全 regression 随实现加入，不提交已知红灯测试或无调用者大框架。

验收：manual 仍可用；auto fail-fast；synthetic fixture 无敏感输入；Node/Chromium 最小用例实际运行，完整检查通过。

### S02 — 日志、外发、快照和会话数据边界

依赖：S01。入口：reporter/notifications、PlaywrightClient、profile manager、runner/session/booking/main 日志、JS appendLog/state。

工作：Python/JS 输出白名单和日志迁移 → 通知 projection → 权限及原始 snapshot 双开关 → retention/dry-run；落实数据合同。

验收：向 nested field、message、exception、URL、JS detail、notification error、HTML script 注入 synthetic
姓名/证件/phone/member/token/card/address，逐 sink 捕获后零明文命中；未知键丢弃；普通 debug 不产原始 HTML/图；
敏感 opt-in 只落本地；清理不越界；通知失败不改变预约状态。

### S03 — 持久授权与单次提交事务

依赖：S02。入口：BookingResult/RunResult、booking 提交/follow-up、BookingService、runner、main exit、JS start/stop/reset/booking controller。

工作：结果状态与原子 durable attempt store → Python 单次提交及证据 → service/runner/CLI 终态 →
JS 持久 blocker → consent 与配置迁移。授权 gate 有效后才切默认 auto；`autoReturnAfterSubmitFailure` 不得绕过 UNKNOWN。

验收：timeout、navigation race、post-submit 限频、response 丢失和弱页面变化最多 click 一次并 UNKNOWN；
换 slot、刷新和重启零新 click；未授权/拒绝/非交互无授权零 click；损坏存储 fail closed；
正证据成功、业务拒绝 no effect、pre-submit retry ≤3；旧 true 不绕过新授权。无现场正证据时保守 UNKNOWN。

### S04 — 严格真实值填表与两路径 parity

依赖：S03。入口：Python booking parser/fill、JS clinic/address/date/disease/readiness/member blocker/checkIdInfo。

工作：纯解析及 required-field snapshot → Python 精确 member、card/date/disease/address、Locator/readiness →
JS 去默认假值、严格控件、空响应 patch 审计；共享场景验收两适配。

验收：业务合同场景的 decision/blockers/selected member/slot/time 一致；错误/多候选不点击；
真实已有值保留、冲突交人工；必填缺失不换号绕过；真实本地 Chromium 覆盖延迟、disabled、双按钮。

### S05 — Python 本机与 userscript 跨标签互斥

依赖：S04；数据/attempt 合同持续有效。入口：main/runner/booking、profile 锁、JS controller/导航/storage、attempt store。

工作：Python 本机 OS 用户原子 leader（可先整个工具单实例，不能换 profile 绕过）、owner nonce/TTL/renew/release；
JS 同 browser profile origin/scope 用 Web Locks 等可靠原子机制，普通 localStorage/GM 读写不当 CAS；
轮询前 acquire、写前复核 owner，导航 handoff、失锁/后台暂停/crash 接管与 pending 阻断联动。
无可靠原语时禁自动运行并提示人工。

验收：两真实子进程/两个浏览器页面并发仅一个 poll/submit leader；失锁暂停；TTL/crash 可接管只读，
不能解除未决提交；ystep1 导航不产生双 controller。不承诺跨路径、browser profile 或多机器互斥。

### S06 — 本机人工 canary 与脱敏 fixture 刷新

依赖：S05。入口：`tests/e2e/`、session 目标解析、booking readiness、fixture converter。

工作：`readonly / prepare / submit` 分层、人工 ready、docid-only 完整解析、timeout/次数预算及无号源 inconclusive；
安全 converter、schema fingerprint 与日期/层级/覆盖元数据；建立 `docs/live-canary.md`，按现场证据补 adapter。
readonly 不选成员/改表单；prepare 不触发最终/follow-up；敏感填表需要具体授权。

验收：即使产品 auto 且 consent 有效，readonly/prepare submit 恒为 0。最终 submit 同时需要明确层级、
`LIVE_E2E=1`、`LIVE_BOOKING=1` 和本次具体场景批准。两路径分别记录证据；缺目标/profile/号源则
完成工具、离线验收并记录 live blocker，不能无限等或假造成功。未取得现场证据不阻碍无关代码推进。

### S07 — session 分类与只读 bounded backoff

依赖：S06 工具完成及可用证据，S03 提交合同保持有效。入口：schedule/session/page_api/errors、runner recovery、JS poll/session。

工作：`VALID / EXPIRED / TRANSIENT_FAILURE / RATE_LIMITED / UNKNOWN` 与 schema_drift 分类，明确失效清缓存，
仅必要诊断/恢复做低频 probe；统一只读 retry owner，连续瞬态失败默认最多 5 次、base 1s/cap 30s/full jitter，
遵守 poll 下限、Retry-After 和既有限频冷却下限；连续限频最多 5 次后暂停。有效业务响应才 reset budget，
新一轮不清连续失败；cancel 立即传播。confirmed expired 才 bounded 人工登录，UNKNOWN/schema drift 停告警；
恢复后重新核对目标、成员、授权和 pending。

验收：5xx/timeout 预算内可恢复、耗尽确定退出；缺 key/旧 cookie/失效 code/redirect 分类正确；
未知 schema 不当空号源；无请求风暴或分层重试放大；cancel 无额外请求；不借只读 retry 重新 submit。

### S08 — 时间与受控 browser channel

依赖：S07。入口：scheduler、schedule 日期、schemas/runtime、PlaywrightClient/main/profile、JS startAt。

工作：`schedule.timezone=Asia/Shanghai`、`late_start_grace_seconds=30`，内部 aware 时间；旧 naive 按配置时区
解释并提示迁移，早到等、迟到 ≤30s 开始、超时人工确认/非交互停；刷号日期按配置时区、JS 存带 offset ISO，
DST 歧义拒绝，间隔用单调时钟。`browser.channel=chromium|chrome|msedge` 默认 Chromium，两种 launch 路径
正确透传；未安装报错不降级。profile marker 加 channel，旧 marker 视 Chromium，不移动目录/跨 channel 复用；
create/warmup/packaged 一致，不扩 flags/locale/stealth。

验收：冻结 clock 覆盖时区、迟到边界、DST、cancel、亚秒等待不 busy loop；Chromium smoke；
参数 test 覆盖所有 channel，安装了 Chrome/Edge 才分别实测；profile 不匹配拒绝。默认打包行为保持兼容。

### S09 — 集成验收与文档迁移

依赖：S01–S08 代码及离线验收，live pending 仍保留。入口：contract/integration、CI/release、配置及项目文档。

工作：补本地浏览器跨模块回归，CI 强制 Ruff/Node/unit/contract/Chromium，PR 不跑 live；
收口配置/README/booking/security/canary/当前架构，记录未覆盖 gate。

验收：D01–D06、授权→准备→提交→停止→恢复闭环、UNKNOWN 持久阻断和隐私 gate 全覆盖；完整适用检查通过。
检查全部输出，私有输入不入 Git；涉及打包行为才跑适用 frozen smoke，缺平台不得冒称验证。

## 迁移、发布与延期项

- 不强制全局 config_version 大迁移；字段默认、弃用提示和 JS settings version 迁移各自验收。
  旧 maxSubmitAttempts 仅迁到 pre-submit 预算。
- 发布默认 auto 前：两路径合同、隐私、授权、单次提交/pending 阻断、单域互斥回归全部通过。
  回滚可切人工模式、Chromium、doctor-only；不能恢复盲重试、弱成功、原始 webhook、伪造字段。
  旧二进制回滚前人工核对未决记录，不能清新 journal。
- 科室 fallback 仅在 doctor 认证/结构/健康正常而独立现场证据证明漏号后立项；届时默认 off、
  共享 request budget、slot 去重，禁止并行 fan-out。expired/rate limit/schema drift 不触发 fallback。
- 成员/目标配置写回排在本轮之后；已有 profile 写回、打包等保留。
  OCR、代理、激进 stealth、Docker-first、分布式协调不进入本轮。

## 完成记录

| 会话 | 状态 | commits | 检查 / review / 现场证据 | 剩余 gate |
|---|---|---|---|---|
| S01 | 已完成（2026-09-30） | `4dc188f`, `e57aa95`, `89bc2ce`；记录提交见文件历史 | Ruff/Node 通过；完整 197 passed, 2 live skipped；contract/integration 16 passed（含 Chromium 5）；只读 review: No findings，独立复核 48 passed | 远端 CI/live/跨平台 release/frozen 未验证；后续安全 gate 仍属 S02–S05 |
| S02 | 未开始 | — | — | S01 |
| S03 | 未开始 | — | — | S02 |
| S04 | 未开始 | — | — | S03 |
| S05 | 未开始 | — | — | S04 |
| S06 | 未开始 | — | — | S05 |
| S07 | 未开始 | — | — | S06 工具与可用证据 |
| S08 | 未开始 | — | — | S07 |
| S09 | 未开始 | — | — | S08 |

完成时记录实际 commit、检查/review 结果、skip 原因及 live 层级/日期/覆盖；更新架构中的已实现事实。
若证据改变已确认产品决策，再与用户确认该决策。

### S01 会话证据（2026-09-30）

本会话仅执行 S01，无前置依赖；实现按三个独立阶段提交，完成记录/架构更新另作文档提交
（文档提交 hash 见本文件 Git 历史，避免自引用）：

1. `4dc188f`：manual-only 配置验证、CLI 浏览器启动前 fail-fast、无敏感值弃用提示、产品描述纠偏。
   配置/auth/CLI 测试 32 passed；Ruff 通过。
2. `e57aa95`：业务合同、版本化手写 synthetic JSON/HTML、Python/Node parser suite。
   contract 测试 11 passed；不含真实个人或认证输入。
3. `89bc2ce`：真实 Chromium harness、Node 缺失强制失败、PR/push/release 检查前置。
   合同/集成 16 passed；完整 197 passed, 2 skipped；CI 同命令 197 passed, 2 deselected。

`uv sync --locked --extra dev`、Ruff、Node userscript/harness 语法与 diff whitespace 检查均通过。
离线 sync 曾因 hatchling 不在缓存失败，正常锁定 sync 后已解决，后续离线锁定 sync 亦通过，uv.lock 未改。
本机 Node v24.15.0、macOS arm64 Chromium 145.0.7632.6（Playwright v1208）；Chromium 5 个
最小用例使用临时 context 与本地 synthetic route，click/submit 为 0，不访问真实站点。
两项 live 测试因未启用 `LIVE_E2E=1` 跳过；CI 排除 live。未执行远端 CI 或跨平台/frozen smoke，
未读取/复制认证状态、未进行真实预约。本会话的解析绿灯不证明 S03/S04/S05 的安全合同已实现。

review：使用 delegated-change-review/review-agent 技能，由一个新鲜只读 subagent 审查
`16bc710..89bc2ce` 全部三个提交与 S01 文档更新，结论 **No findings**。
reviewer 独立复核相关测试 **48 passed**（含 Chromium 5）、Ruff、两份 JS 语法及差异空白检查。
无 P0–P3 finding，接受/拒绝项均无，因此无需修复；未扩展到后续会话。
审查覆盖边界同上述验证记录，最终仅将结论与完成状态写回文档。
README、future-improvements、启动提示词的会话前改动保留且未纳入本会话提交；
按任务要求更新并纳入原先未跟踪的本计划与 current-architecture 文件。
