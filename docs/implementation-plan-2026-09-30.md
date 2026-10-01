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
| S02 | 已完成（2026-09-30） | `8c167c9`, `f3d0a47`, `c9cd962`, `d81b350`, `040b911`；记录提交见文件历史 | 完整 222 passed, 2 live skipped；contract/integration 17 passed（Chromium 6）；Ruff/Node/锁定离线 sync 通过；只读 review 1 P2 接受并修复，相关 62 passed | Windows/远端 CI/live/frozen 未验证；后续授权/提交/互斥 gate 属于 S03–S05 |
| S03 | 已完成（2026-09-30） | `9a8b33a`, `fcb4e18`, `c04ab7f`, `76bdc60`, `5c9e454`, `1e7dbeb`, `51e9f41`；记录提交见文件历史 | 最终 294 passed, 2 live skipped；contract/integration 39 passed（Chromium 19）；CI 同命令 294 passed, 2 deselected；Ruff/Node/离线锁定 sync 通过；review 2 P2 接受并修复 | 无 live adapter；S04/S05 发布 gate、Windows/远端 CI/frozen 未完成 |
| S04 | 已完成（2026-09-30） | `d9cae53`, `519d83a`, `528f760`, `8199211`, `71afcd2`, `ad1489a`, `47804a5`；记录提交见文件历史 | 最终 449 passed, 2 live skipped；contract/integration 205 passed（Chromium 100）；CI 同命令 449 passed, 2 deselected；Ruff/Node/离线锁定 sync/whitespace 通过；review 1 P1 + 2 P2 接受并修复 | 无 live adapter；S05 发布 gate/跨平台/远端 CI/frozen 未验证 |
| S05 | 已完成（2026-09-30） | `3cdadf4`, `96dea17`, `f0a010f`, `4385214`, `9ebb1dd`, `b13be72`, `f1545ca`；记录提交见历史 | 最终 477 passed, 2 live skipped；contract/integration 221（Chromium 116）；CI 同参数 477 passed, 2 deselected；Ruff/Node/锁定离线 sync/whitespace 通过；review 1 P1 + 3 P2 全接受并修复 | 无跨路径/profile/机器协调；live/跨平台/扩展/远端 CI/frozen 未验证 |
| S06 | 工具/离线已完成（2026-10-01）；live blocked | `b9e110a`, `2a20e9a`, `222dbba`, `44f8c03`, `575ed2f`, `82a7788`；记录提交见历史 | 最终 513 passed, 3 live skipped；contract/integration/canary 257（Chromium 129）；Ruff/Node/锁定离线 sync/whitespace 通过；只读 review 2 P2 全接受并修复 | 缺具体目标/profile/场景批准；两路径 live、真实 fixture/adapter、扩展 sandbox/Windows/远端 CI/frozen 未验证 |
| S07 | 已完成（2026-10-01） | `7284c72`, `6ee5743`, `eed7282`, `b52da53`, `69ad4ba`, `4c81780`；记录提交见历史 | 最终完整 563 passed, 3 live skipped；Ruff/Node/锁定离线 sync/whitespace 通过；review 3 P2 全接受修复；独立合同/集成/canary 270 passed, 1 既有不稳定用例 failed，单独复验 1 passed；详情见下 | 基线可复现互斥测试偶发零 click；两路径 live schema/扩展/跨平台/远端 CI/frozen 未验证 |
| S08 | 已完成（2026-10-01） | `1c461bb`, `4b17437`, `dc6af72`, `d3e61c0`, `7b5c27f`, `d777208`；记录提交见历史 | 最终 649 passed, 5 skipped（3 live / 2 Edge）；Ruff/Node/离线锁定 sync/whitespace 通过；review 两项问题接受修复、最终复核 No findings；Chromium/Chrome 两种 launch 及 macOS arm64 frozen smoke/bootstrap 通过 | Edge 未安装；Windows/Linux、真实登录/预约、扩展 sandbox、远端 CI 未验证；既有双页面用例不稳定性仍保留 |
| S09 | 实现/离线验收收口中（2026-10-01）；review 待执行 | `76a0230`, `c67d12e`；文档提交见历史 | 新增 Chromium 7 passed；CI 同命令 397 passed, 3 live deselected + 259 passed, 2 Edge skipped；完整检查及 review 见本会话证据 | 两路径 live/真实 fixture/adapter、扩展 sandbox、Windows/Linux、Edge、远端 CI/release 未验证；既有双页面不稳定性保留 |

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

### S02 会话证据（2026-09-30）

会话基线 `f14a7ea`；S01 前置完成，合同与 Chromium harness 已实际通过。
本会话仅实现 S02，没有改变已确认决策或执行 S03–S09。按依赖顺序作四个本地提交，不 push：

1. `8c167c9`：Python 输出字段/值白名单、固定直接日志、安全 exception/登录诊断，
   JS console/state 日志投影与旧日志迁移；相关回归 **145 passed**。
2. `f3d0a47`：桌面/webhook 最小投影与通知失败隔离；相关检查 **58 passed**。
3. `c9cd962`：私有文件、profile/link 防护、0077 Chromium umask、原始快照双开关及安全 metadata；
   配置模板/写回走私有入口；相关检查 **92 passed**，含本地 Chromium 6。
4. `d81b350`：7 天/24 小时 retention、dry-run/cleanup CLI、profile/未决记录命名空间保护、
   数据合同与诊断 Git ignore；retention/CLI/隐私/浏览器相关检查 **53 passed**，完整套件见下。

最终适用检查：锁定离线 sync（dev）、Ruff、Node userscript/harness 语法、差异空白检查均通过，
`uv.lock` 未改。完整 **222 passed, 2 skipped**；contract/integration **17 passed**（含真实 Chromium 6）；
CI 同命令 `pytest -q -m "not live" --browser chromium` 为 **222 passed, 2 deselected**。
Synthetic canary 覆盖姓名/证件/phone/member/token/card/address，注入 nested selector/value、message、
exception、URL、JS detail/旧日志、notification error、HTML script/cookie，逐 sink 捕获零明文。
原始 opt-in 仅在临时本地目录生成 HTML/PNG，普通 debug 仅安全 JSON；POSIX 权限/link/hardlink
与清理目录替换、retention 边界、dry-run、profile/未决记录保护通过。

验证限制：本机 Node v24.15.0、Playwright 1.58.0、macOS arm64 Chromium；未运行远端 CI、Windows/Linux
或 frozen/release。两个 live 因未启用 `LIVE_E2E=1` 跳过。未读/复制真实 profile/认证资料，未执行真实
登录/预约、上传或现场 fixture 导出；S06 converter 未实现。Windows ACL/reparse 防护能力未实测；
程序退出后没有后台清理服务，raw 文件需按数据合同在 24 小时内人工删除/运行清理命令。
README、future-improvements、未跟踪启动提示词等会话前改动保留且不纳入 S02 提交。

review：delegated-change-review 使用一个新鲜只读 `$review-agent`，比较基线 `f14a7ea`，
覆盖本会话全部四阶段提交、数据合同和完成证据草稿。发现 1 个 P2：固定日志白名单遗漏
`setSummary` 的人工提交等待与限频消息，导致 `panelPhase` 显示 idle/polling 而不是等待/冷却。
主实现者独立核对调用链与复现后接受；没有拒绝项。`040b911` 补齐固定工作流消息、把动态摘要
改为安全固定文本，新增人工等待/医生页及预约页冷却/失败阶段和敏感 detail 回归。
修复后相关 **62 passed**，完整 **222 passed, 2 live skipped**；Ruff/Node/差异空白检查通过，
CI 同命令 **222 passed, 2 deselected**。修复由主实现者验证，未另行声称 reviewer 重审修复提交。
reviewer 独立检查两组相关套件 **75/111 passed**、Ruff/Node/whitespace；额外在临时真实 Chromium
profile 核对新目录/文件均为 0700/0600。没有检查真实认证 profile，也没有执行 live。
完成记录/架构证据另作文档提交，hash 见本文件历史，避免自引用。

### S03 会话证据（2026-09-30）

会话基线 `f291831`；已读取实施计划/当前架构并核对 S02 完成证据和现有调用链。
本会话仅执行 S03；README、future-improvements 与未跟踪启动提示词的会话前改动保留且不提交。
使用 implement-in-stages，四阶段分别本地提交；后续 review 修复、重启验收与记录另行提交，不 push：

1. `9a8b33a`：显式结果状态、私有 atomic durable attempt journal、最小人工解决审计；相关 **11 + 26 passed**。
2. `fcb4e18`：Python 一次 Locator click，未验证 follow-up 交人工、保守证据 seam，service/runner/CLI 终态；
   相关 **67 passed**，完整 **245 passed, 2 skipped**。
3. `c04ab7f`：JS 独立持久 journal、旧 submitting 迁移、Start/Stop/reset/刷新阻断与人工核对入口；
   相关 **21 passed**（Chromium 7），完整 **263 passed, 2 skipped**。
4. `76bdc60`：授权 gate、auto/manual 配置迁移、拒绝/撤销/非交互/绑定与策略版本回归、JS 本地真实 controller、
   两路径共享 submission.v1.json 期望；完整 **290 passed, 2 skipped**，contract/integration **37 passed**（Chromium 17）。

锁定离线 sync（dev）、Ruff、Node userscript/harness 语法、差异空白通过，uv.lock 未改。
本机 Node v24.15.0、Playwright 1.58.0、macOS arm64 Chromium；浏览器仅临时 context/profile 与 synthetic local route，
不访问真实预约站点。两个 live 因未启用 LIVE_E2E 跳过；无真实登录/预约、认证状态复制或现场 fixture 导出。
共享场景和 fake adapter 的正证据/业务拒绝只证明本地合同，实际无已验证 live adapter 时始终 UNKNOWN。
当前账号无法可靠区分，授权仅 Python 本次 run / JS 本次页面有效；不把 cookie/user_key 当成稳定账号 ID。
Python POSIX fsync/权限已验证；JS 采用 browser storage 的同步 atomic item/读回能力，非 OS fsync 保证。
未运行远端 CI、Windows/Linux、frozen/release；S04 填表与 S05 互斥 gate 按依赖留后续，默认 auto 尚不具备发布条件。

review：delegated-change-review/review-agent，一个新鲜只读 subagent 比较 `f291831..76bdc60` 与两份
文档证据草稿，确证两个 P2。主实现者逐项复核后均接受，无拒绝项：

- 撤销入口在提交前 await 期间清空 grant，旧授权布尔值仍可能 click。`5c9e454` 在最后一次 await 后
  只读复核授权与模式；不重新弹确认。Python sleep 中撤销与 JS 另一真实浏览器页面撤销均零 click，
  未产生 pending；相关 **101 passed**（含本地 Chromium 12）。
- Python 人工等待立即返回，使 CLI context 关闭准备页。`1e7dbeb` 保留 interactive context 供人工处理，
  明确提示核对原站记录，用户按 Enter 后才关闭；noninteractive 仍退出 2。相关 **33 passed**。

reviewer 独立检查 **111 passed**（含真实本地 Chromium 11）、Ruff/Node/whitespace。修复后检查由
主实现者完成，未再委派，也未声称 reviewer 重新审查修复提交。`51e9f41` 再补真实 Chromium 关闭/重启：
临时 synthetic persistent profile 重启后复读 journal，累计 click=1，未读取/复用用户认证 profile；
该测试 **1 passed**，不宣称断电/硬崩溃 browser storage 的 fsync 能力。

最终完整 **294 passed, 2 live skipped**；contract/integration **39 passed**（Chromium 19）；
CI 同命令 **294 passed, 2 deselected**。Ruff、Node 两份语法、差异空白检查通过。
未决阻断/人工核对/撤销和非交互 gate 均保持；本会话仅 S03，未执行后续实现或真实预约。
完成记录/架构证据另作文档提交，hash 见本文件历史，避免自引用。

### S04 会话证据（2026-09-30）

起始 review 基线 `a1bfdf6`；依赖 S03 记录/实现已核对。本会话仅执行 S04，不扩展互斥/canary/session/time/channel。
初始 README、future-improvements 和 implementation-prompts 的既有工作树改动均保留，不纳入提交。

1. `d9cae53`：必要纯 booking snapshot/decision seam 与 32 个共享准备场景，合同相关 57 passed。
2. `519d83a`：Python 精确成员/Locator、真实 card/date/病情/address、required blockers 与最终复核。
   相关 75 passed（含新增 Chromium 35）；全套暴露的旧 transaction fake 缺快照已补齐，合同/事务 72 passed。
3. `528f760`：JS 严格同合同适配、旧假值迁移、移除未获证明的 checkIdInfo patch；Node/Chromium parity、
   异步地区级联、延迟 card/按钮、最终字段变化阻断。旧宽松填表 helper 的 mock tests 换为共享真实浏览器回归。

最终修复后全套 **449 passed, 2 skipped**（LIVE_E2E 未启用）；Ruff、userscript/Node harness 语法、
`uv sync --locked --offline --extra dev`、`git diff --check` 通过，uv.lock 未变化。
修复后 CI 同参数本地命令 `pytest -q -m "not live" --browser chromium` 为 **449 passed, 2 deselected**。
review 由一位新鲜只读 reviewer 按 `$delegated-change-review` 对 `a1bfdf6..528f760` 及完成证据草稿执行。
独立检查 **230 passed**、Ruff/Node/whitespace；返回 1 P1 + 2 P2，全部接受，无拒绝项：

- P1 日期关联：仅确认整个 sch_data 含目标 ID 后扫全部日期会取错 sibling schedule。`47804a5`
  改为 bounded serialized array 解析及唯一当前 record 提取，bad length/duplicate/object/reference fail closed；
  新增两个共享日期场景，日期 28 passed、独立 Python/Node parser 8 passed。
- P2 required radio：每个 checked 再 all 会把原生合法组判缺失。`71afcd2` 按 native validity（离线按 form/name）
  处理，包含 disabled 语义；共享回归先证明旧失败，再验证 20 passed。
- P2 optgroup：Python 漏嵌套 option 与选中值。`ad1489a` 统一递归 options/disabled group，新增共享回归，20 passed。

主实现者另发现错误 requested slot 在 fill 后才绑定；`8199211` 移到 fetch 返回、任何选择前，相关 5 passed。
reviewer 独立审阅此修复及 Chromium 2 passed。其余三项修复由主实现者验证，没有声称重新委派审查。
最终共享准备场景 **36**；contract/integration **205 passed**，其中真实本地 Chromium **100**，均在全套实际执行。
完成记录/架构及显式空值配置示例另作文档提交，hash 见文件历史，避免自引用。
未执行真实登录/预约、未读取/复制用户认证状态；本地浏览器只用临时 synthetic context/local routes。
无现场字段与成功 adapter；未知现场选择语义仍交人工，S05 互斥发布 gate 尚未完成。

### S05 会话证据（2026-09-30）

会话基线 `a717ba2338147682d9b0a6c293522fc457961902`；已核对 S04 完成记录及真实值 preparation、
持久授权与 pending 合同。仅执行 S05，无 S06–S09 功能或真实预约。按 implement-in-stages 本地提交，不 push。
用户补充授权提交整轮计划的既有 README、future-improvements 和 S01–S09 提示词，
`5d7b87b` 为独立文档提交，并修正 README 的尚未实现表述。

1. `3cdadf4`：Python OS 用户工具单实例、nonce/TTL/renew/release、runner/CLI/请求/写入 guards。
   定向 **185 passed**（含既有真实 Chromium 提交/填表回归）。测试锁根均隔离为临时目录。
2. `96dea17`：JS origin Web Lock controller、独立 journal mutex、导航接棒/后台失效及 pending 联动。
   JS/合同/隐私/双页面相关 **74 passed**；既有提交/填表回归 **154 passed**。
3. `f0a010f` 集成复核：click 后失锁保持 UNKNOWN；keepalive 每次请求前复核；POSIX 默认锁绑定 OS home，
   路径替换失效；实际子进程使用 PageBookingStrategy 单次 click 并证明 crash 后仍 pending。
   修复后 Python 相关 **74 passed**。

完整检查 **471 passed, 2 skipped**（LIVE_E2E 未启用）；contract/integration **218 passed**，
其中真实本地 Chromium **113**，均在全套实际执行。Ruff、userscript/Node harness 语法、
锁定离线 dev sync、会话完整 diff whitespace 通过，uv.lock 未变化。修复前 CI 同参数本地命令为 **471 passed, 2 deselected**；修复后最终检查见下。

验收覆盖真实两个 Python 子进程竞争、kill 后接管、TTL 不续活、换 profile 仍被拒绝、owner/inode/link 防护；
双 Chromium 页面并发只一个 poll/submit leader、关闭页面接管、TTL/hidden/pagehide 旧 continuation 失效、
这些失效后的 pending 恒在且仅可只读接管、ystep1 重新 acquire 且 duplicate controller 为 null、
Web Locks 不可用零自动点击，以及撤销/journal 串行化和旧 pending 初始化竞争。

未执行现场/live 登录或预约；未读取/复制认证状态；浏览器全部临时 synthetic context/local route。
TTL 使用受控 clock、visibility/pagehide 使用受控事件，不宣称真实系统冻结/崩溃已实测。
不承诺跨路径、browser profile 或多机器互斥；Windows 字节锁代码存在但未在 Windows 验证，
Tampermonkey 扩展 sandbox、Linux、远端 CI、frozen/release 未执行。S06 canary/现场 adapter 仍待后续。

review：一个新鲜只读 subagent 按 delegated-change-review / review-agent 检查
`a717ba2..f0a010f` 的本会话完整 diff 与两份文档草稿；独立相关 **44 passed**，
Ruff、Node 语法、完整 diff whitespace 通过。发现 **1 P1 + 3 P2**，主实现者逐项独立确认并全部接受；无拒绝项。

- P1 legacy pending 未全局持久化：新 Chromium regression 先证明另一正常授权 controller 实际 click=1；
  `4385214` 在旧标签的只读 blocker 检查立即排入 journal Web Lock 迁移，无需启动被阻断的 controller。
  新 owner 的 journal 请求排在迁移之后，实际 click=0；相关 **78 passed**。
- P2 同步人工输入阻塞续期：回归先证明真实授权 prompt 等待超 TTL 导致 LeaderLost。
  `9ebb1dd` 用独立 heartbeat 线程及 RLock，正常人工等待继续续期，真实失锁/expired owner 不能复活；
  同步 prompt 后单次提交通过，Python 相关 **70 passed**。
- P2 旧恢复/限频返回任务借新 owner 导航：真实 Chromium 的 session 回归先证明旧任务卸载新 controller；
  `b13be72` 两条延迟导航均捕获并传递原 owner，重新 Start 的新 owner 不能授权旧 continuation。
  相关 **80 passed**，显式受控 3 秒恢复及 15 秒限频边界另 **2 passed**。
- P2 Python 失锁后继续恢复登录：真实 LocalLeader.release 回归先证明再次 login goto，以及 cooldown 后未停止。
  `f1545ca` 恢复入口与准备登录入口复核 owner，失锁返回人工等待且零新登录请求；Python 相关 **67 passed**。

reviewer 还以真实 Chromium 确认正常导航事件顺序为 pagehide 后 visibilitychange(hidden)，
原 handoff 保留；该路径无 finding，不作额外改动。四项修复由主实现者验证，未声称 reviewer 再审修复提交。
最终修复后完整 **477 passed, 2 skipped**（LIVE_E2E 未启用）；CI 同参数本地命令
`pytest -q -m "not live" --browser chromium` 为 **477 passed, 2 deselected**。
contract/integration **221 passed**，其中真实本地 Chromium **116**，均在完整/CI 同参数检查实际执行。
Ruff、userscript/Node harness 语法、锁定离线 dev sync、会话完整 diff whitespace 通过，uv.lock 未改。
完成记录/架构及 README 进度另作文档提交，hash 见文件历史，避免自引用。

### S06 会话证据（2026-10-01）

会话起点 `46ddd41e6ab65d59027850dfb6251cb36c3cdba0`；已先读本计划与当前架构，核对 S05 完成记录、
实际 leader/nonce/TTL/lease fencing，以及 S02 隐私、S03 consent/attempt、S04 readiness 合同。
仅执行 S06，按 implement-in-stages 本地提交，不 push。未读取私有 config/profile/认证状态。

1. `b9e110a`：Python bounded manual canary，显式 readonly/prepare/submit；ready 与完整目标解析；
   具体场景批准与底层 scope gates；无号源/timeout/pending 明确终止；live harness 使用指定专用 profile、
   browser 启动前获取实际全工具 leader。定向 **51 passed, 3 live skipped**；协调阶段复核 **24 passed, 3 skipped**。
2. `2a20e9a`：userscript canary panel/有限 owner、跨刷新限制及最终审批；最小静态 DOM/normalized JSON
   converter、schema fingerprint 与日期/路径/层级/source/覆盖；CLI 导出 synthetic 样例与 Chromium 双适配 parity。
   定向 **84 passed**；更早提交/互斥 Chromium 相关 **112 passed**。所有网站 URL 均 route 到 synthetic 内容。
3. `222dbba`：完整收集发现 canary/core 同名测试模块冲突；补 canary 包标记，联合 **44 passed**。
   此处修正未完成阶段的验证安排，单独提交测试集成修复。
4. `44f8c03`：[本机现场操作与证据](live-canary.md)，更新架构已实现事实与两路径现场 blocker。
   修复前全套 **510 passed, 3 live skipped**，Ruff、userscript/Node harness 语法、锁定离线 dev sync、whitespace 通过。

新鲜只读 subagent 按 delegated-change-review / review-agent 审查
`46ddd41..44f8c03` 完整 diff、相关调用及文档；独立 canary **33 passed**、whitespace 通过。
发现 **2 P2**，主实现者逐项独立复现并全部接受，无拒绝项；新增 regression 先证明缺陷：

- 非法来源日期丢失 HTML blocker：snapshot 仍 date.conflict，但 renderer 丢弃后可能可准备。
  `575ed2f` 对非法 sch_data/jzdate 来源日期拒绝导出，继续保留合法日期之间的冲突；相关 **20 passed**。
- 空 time_range 被占位文本改变筛选：原始空值允许预约页进一步筛选，导出后提前被拒绝。
  `82a7788` 保留空值；实际 Python 与 Node JS filter regression、converter/排班相关 **55 passed**。

最终修复后全套 **513 passed, 3 live skipped**；contract/integration/canary **257 passed**，
其中真实本地 Chromium **129**（既有 116 + 新增 13），均在全套实际执行。
Ruff、userscript/Node harness 语法、锁定离线 dev sync、会话完整 diff whitespace 通过，uv.lock 未改。
远端 CI 与 CI 同参数命令未运行。两项最终修复由主实现者验证，reviewer 独立检查覆盖修复前 target。
普通日志/通知仍使用 S02 白名单；fixture 出错只给固定分类，新文件 no-follow/private 创建且不覆盖。
现有 fixture 未被现场原文替换；新增样例 source=synthetic，static DOM 不复制网络/站点脚本。
非 normalized sch provider JSON 暂无安全 converter，当前拒绝，不伪造现场 schema 或结果 adapter。

现场层级/覆盖：Python 与 userscript 都**未执行 live**；未提供明确目标/profile/日期时段/就诊人及具体场景批准。
用仅设置 LIVE_E2E=1 的命令核对 harness：**3 skipped**，固定 blocker 为缺显式 level/profile/doctor/date，
浏览器启动前退出，无真实登录/请求/预约；这不是一次现场 readonly 取证。
本地真实 Chromium 临时 context/local route 覆盖 docid-only→目标→号源、readonly 零表单写入/两低层级零最终动作、
有效产品 consent 不能绕过层级、具体批准拒绝、次数/ready timeout 与迟到 fencing、pending/reload、实际 panel 事件。
真实 fixture 刷新、两路径 live 结果/adapter、Tampermonkey 扩展 sandbox、Windows 人工 ready、远端 CI、
frozen/release 均未验证；POSIX TTY 是当前 Python ready 能力边界。未执行 S07–S09。

### S07 会话证据（2026-10-01）

只执行 S07；会话开始工作树干净，review 基线为 `c16601e`。S06 工具与 synthetic 证据可用；
真实目标/profile/场景批准与现场 schema/adapter 仍缺，不把 UNKNOWN 当空号源或凭 cookie 断言有效。
本地按依赖顺序提交，未 push，未读取/复制用户认证状态或执行真实预约：

1. `7284c72`：session assessment、业务 schema 保守分类、Retry-After 解析；页面传输单次请求，
   移除 transport/context.request fallback 与嵌套 global 重试。分类/Page API/限频 **26 passed**，
   browser/session/schedule/canary 相关 **67 passed**。
2. `6ee5743`：Python 单一只读预算（默认五次失败）、有效业务响应 reset、缺 key 低频诊断、
   confirmed expired 人工恢复与 pending 重查、UNKNOWN 人工结果/安全告警。相关 **167 passed**。
3. `eed7282`：JS 单次 transport/分类/连续失败预算、取消、人工恢复与 Start/reset fencing；
   共享版本化 synthetic session 场景和 Chromium 请求计数/Retry-After regression；补齐既有 synthetic
   fixture 的业务 code/必需排班分类字段。Python 缺 key 探测的低频窗口继续保留原瞬态分类，
   200 业务限频同样保留 Retry-After。相关最终 **125 passed**。

实现后的完整 **556 passed, 3 live skipped**；contract/integration/canary **267 passed**，
包含实际本地 Chromium。锁定离线 dev sync、Ruff、userscript 与 Node harness 语法、会话完整 whitespace 通过；
uv.lock 未改。上述修复前结果经完整复验再次得到 **556 passed, 3 live skipped**。
最终修复后检查见下；不把修复前结果冒称最终状态。

退避仅属于 schedule read owner，默认第 5 次连续失败退出/暂停（四次等待），
base 1s/cap 30s/full jitter，加 poll >=3s、Retry-After 与限频冷却 floor；连续瞬态/限频混合亦不 reset。
Python 新 generator 与 JS Start/reset 不清预算；有效业务 response 才 reset。Python 正常轮询不再 keepalive probe，
缺 key 最多每 60s 一轮；只有显式失效 code/已知 login redirect 才进入 bounded 人工恢复。
JS 停下交人工登录，Start 重新核对目标，booking 继续核对成员/授权/pending；不再自动刷新假定恢复。
缺 key/未验证 schema 保守停下，已有只读 fetch 预算从不重新进入最终 submit/follow-up。

已知验证限制：既有双页面提交 Chromium 测试偶发零 click/UNKNOWN。对比临时载入的
`c16601e` 源码五次运行一次失败，当前源码五次全通过；未决阻断保持，不削弱断言或修改无关提交合同。
后续联合 **125 passed** 与全套结果分别记录，不掩盖中间失败。未执行两路径 live、真实 schema/fixture 刷新、
Tampermonkey 扩展 sandbox、Windows/Linux/远端 CI/frozen/release；仅临时 Chromium 与 synthetic route/transport 证据。
S08/S09 未执行。

S07 review：新鲜只读 subagent 按 delegated-change-review / review-agent 审查
`c16601e..eed7282` 完整 diff 与两份证据草稿，独立相关 **128 passed**、whitespace 通过。
发现 **3 P2**，主实现者逐项独立复现，全部接受，无拒绝项；每个新增 regression 先证明缺陷再验证修复：

- `b52da53`：混合 normalized/sch payload 被分类为 VALID，空 normalized 分支却让 parser 消费未验证 sch。
  没有现场优先级合同，两个表示同时存在时保守拒绝为 schema_drift；共享 JSON、Python parser 与 Chromium
  controller 证明不打开预约页，相关 **31 passed**。
- `69ad4ba`：Stop/Start 只保留次数预算，取消中的 cooldown 没有 deadline，导致 Retry-After60s 内立即请求。
  保存 next-read 截止时间并在 controller 首次/每次请求前执行，Stop/Start/reset 保留剩余冷却；同 document
  有单调 deadline，跨 document 存 epoch deadline。超长 server hint 分段计时避免 JS timer 溢出；相关 **32 passed**。
- `4c81780`：缺 key 的 5xx probe 把服务端120s hint 丢成60s本地下限；5xx/429 probe 均保留 hint。
  真实 SessionCaptureService→ScheduleService 调用链的 synthetic response 验证实际 sleep=120s 且 cancel 无第二次请求；
  相关 **63 passed**。

最终修复由主实现者验证；没有声称 reviewer 再审修复提交。
最终完整 `uv run --locked --offline --no-sync pytest -q` 为 **563 passed, 3 live skipped**；
Ruff、userscript 与 Node harness 语法、锁定离线 dev sync、完整会话 diff whitespace 通过，uv.lock 未改。
最后单独 contract/integration/canary **270 passed, 1 failed**；唯一失败仍是基线可复现的双页面提交
零 click/UNKNOWN 用例，随后单独复验 **1 passed**。修复前联合为 **267 passed**。
明确保留这项测试不稳定性，未修改断言或用一次绿灯声称稳定；S07 新增分类/预算/cancel 与三项修复 regression 全部通过。
两路径现场 schema/adapter、Tampermonkey 扩展 sandbox、Windows/Linux、远端 CI 与 CI 同参数命令、
frozen/release 未验证。S08/S09 未执行；本地提交不 push。


### S08 会话证据（2026-10-01）

会话开始工作树干净，比较基线 `acec545`。已读计划/当前架构并核对 S07 完成成果；
两路径现场 schema/adapter 的缺口持续保留。本会话仅执行 S08，使用 implement-in-stages 本地提交，不 push：

1. `1c461bb`：Python `schedule.timezone=Asia/Shanghai`、`late_start_grace_seconds=30`，
   appoint_time 归 aware UTC。旧 naive 按配置时区解释并发固定迁移提示；DST 重复/不存在的 local time 拒绝，
   显式 offset ISO 可消歧。早到等待、迟到 ≤30s 开始，超时仅交互新确认可开始；拒绝/非交互 runner 返回人工等待，零 poll/booking。
   保留亚秒 delay，asyncio 单调 timer、cancel 传播；查询日期按配置时区计算。相关 **60 passed**。
   新增并锁定 `tzdata==2026.4` 及 frozen 数据收集，仅此新增依赖，未升级已有依赖；无系统 zoneinfo 回归通过。
2. `4b17437`：JS settingsVersion=5、`schedule.timezone/lateStartGraceSeconds`，旧 startAt 转带 Z 的 ISO 并提示。
   按配置时区显示面板及查询日期；明确配置的 v4 真实字段保留。DST ambiguous/gap 与非法时间拒绝，
   读取非法旧配置停自动，面板保存后才能纠正；不将非法时间当立即开始。
   超时仅本 document 新 Start 可弹确认，autoStart/reload 停下；等待使用可取消 performance.now timer。
   共享 session/JS/时间相关 **91 passed**，补面板 offset DST/亚秒 round-trip 后定向 **23 passed**（Chromium 8）。
3. `dc6af72`：`browser.channel=chromium|chrome|msedge` 默认 Chromium；后两者两种 launch 都传 channel，
   失败只调用一次并关闭 driver，不降级。Chromium 省略 channel 参数，保留 bundled browser/headless-shell 行为。
   marker v1 增加 channel，缺 channel 的旧 marker 只作 Chromium 且不重写；未知版本/channel 拒绝。
   配置指定 profile 不匹配拒绝；未指定时只选择同 channel，必要时在原 root 新建不同目录，不移动/复制认证状态。
   CLI/create/warmup/live harness 一致透传；不扩 flags、locale 或 stealth。
   配置/参数/profile/CLI/真实浏览器相关 **92 passed, 2 skipped**（Edge 未安装）；
   Chromium 与本机 Chrome 分别实际运行 transient/persistent 临时 profile smoke；不访问真实预约站点。

macOS arm64 在 `/tmp/160grab-s08-frozen-dist` 独立 PyInstaller 构建，按既有 staging helper 收入本机已缓存的
Chromium v1208/headless-shell/ffmpeg，不覆盖仓库原 build/dist。`PYTHONTZPATH='' PLAYWRIGHT_BROWSERS_PATH=0`
运行新 binary `--smoke-browser` exit 0，证明确实使用 bundled Chromium，默认配置验证可读取 bundled tzdata。
未读取/复制用户 profile，未执行真实登录、预约或 live fixture 刷新；Edge/Windows/Linux/扩展 sandbox/远端 CI/release 仍未验证。

收尾合同检查：`d3e61c0` 修正 S07 当前 document 仍比较 epoch 的冷却路径；跨 document 只将 epoch hint
导入一次单调截止时间。时钟 ±3600s regression 修复前负向失败、正向通过；修复后相关 **39 passed**。
Stop/Start 仍保留冷却，wall-clock 校正不缩短/放大本 document 间隔。

新鲜只读 reviewer 按 delegated-change-review / review-agent 审查本会话完整 diff 和文档草稿，
初审两项问题均由主实现者独立复现、接受、逐项修复，无拒绝项：

- `7b5c27f`：真实 datetime-local 把 `:00`/`.250` 规范化为无秒/`.25`，严格字符串比较不能保留原 DST offset。
  使用中立 UTC wall value 语义比较；两个真实 Chromium regression 修复前均失败，修复后时间相关 **27 passed**。
- `d777208`：正常 CLI 内层失败处理先转 SystemExit，吞掉外层 BrowserLaunchError 安全安装提示。
  保持原安全 run_failed/run_finished 事件再传播特定异常；transient/persistent 两条 regression 修复前失败，
  修复后 CLI/browser/profile **78 passed**。

同一 reviewer 已复核最终 `acec545..d777208`（包含收尾单调修复）并给出 **No findings**；
独立初始相关 **124 passed, 2 Edge skipped**，最终时间 **27 passed**、CLI/browser/profile **78 passed**，
Node/whitespace 通过。最终代码完整 **649 passed, 5 skipped**（3 live 未启用、2 Edge 未安装）；
其中 contract/integration/canary 共 290 个用例在完整套件实际执行，**288 passed, 2 Edge skipped**。
14 warnings 均为 legacy naive 时间固定迁移提示。Ruff、userscript/Node harness 语法、锁定离线 dev sync、
完整比较基线 whitespace 通过。新增 tzdata 后 uv.lock 只锁入该依赖，原依赖未升级。
最新 `d777208` 的 macOS arm64 frozen 构建及 bundled Chromium smoke 再次 exit 0；
默认 bootstrap exit 0，生成的 0600 配置验证 Chromium/Asia/Shanghai/30s 默认值。
已知基线双页面互斥用例本次完整套件通过，但未修复或声称稳定。S09 未执行；本地提交不 push。
完成记录/架构证据另作文档提交，hash 见本文件历史，避免自引用。

### S09 会话证据（2026-10-01）

会话基线 `ec8dd5c`，初始 tracked/untracked working tree 干净；ignored 本地 config/artifacts/
旧 build/dist/cache 保留且未纳入提交。仓库及父目录无适用 AGENTS.md。仅执行 S09，本地提交不 push。
S01–S08 代码/离线前置已完成；S06 工具完成及 live blocker 符合本会话依赖，不申请/执行真实预约。

按 implement-in-stages 分三个阶段：

1. `76a0230`：七个跨模块 Chromium synthetic 闭环，实际 CLI 服务组装与 JS doctor→ystep1
   controller，准备/授权/唯一 click/UNKNOWN 停止/新 run/reload/撤销阻断/人工 resolve audit，
   confirmed expired 恢复重绑和隐私 sink。定向 **7 passed**；早一轮联合合同/集成/canary
   **294 passed, 2 Edge skipped**（当时收集六个新增场景，随后追加恢复场景并定向验证）。
2. `c67d12e`：CI/release 双 live 开关关闭，Ruff/两份 Node/离线和 contract/Chromium 独立
   mandatory gates；YAML 解析检查通过，同命令两组分别 **397 passed, 3 live deselected**
   和 **259 passed, 2 Edge skipped**。没有触发远端 CI/release。
3. 文档提交见历史：README/配置注释/booking/security/canary/架构/release 说明迁移收口，
   [集成验收索引](integration-acceptance.md) 映射 D01–D06、合同闭环和所有未覆盖 gate。
   完整 **656 passed, 5 skipped**（3 live 未启用、2 Edge 未安装），14 个固定 naive 时间迁移提示；
   Ruff/Node 两份语法/锁定离线 dev sync/whitespace、变更文档本地链接、示例 YAML 语义一致性
   全部通过。新鲜只读 review 待执行，结论在收尾后记录。

授权/人工 ready/成员传输用 synthetic seam，实际浏览器只访问 route 生成的内容；
不复制认证状态、不打开私有本地 config/profile，不归档原始 HTML/图/response。Node、Chromium
不能缺失跳过，branded smoke 只在安装时执行。示例配置仅改注释，uv.lock/打包代码/默认值未改；
S09 不涉及打包行为，未重跑 frozen，S08 macOS arm64 frozen 是历史证据。

剩余：两路径 live schema/结果 adapter/真实 fixture、真实扩展 sandbox、Windows/Linux/Windows
ACL/ready、Edge 未安装、远端 CI/release 未验证；既有双页面用例偶发零 click/UNKNOWN 根因
未声称解决。无现场输入与具体批准，live blocker 不因 S09 文档迁移解除。
