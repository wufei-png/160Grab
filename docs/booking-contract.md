# 两路径预约业务合同

合同版本：1；确认日期：2026-09-30。适用于 Python/Playwright 与 Tampermonkey。
S01–S09 已落实代码与离线/本地浏览器保护；现场结果 adapter 仍未验证。
实施顺序与交付记录见
[实施计划](implementation-plan-2026-09-30.md)，当前能力见 [当前架构](current-architecture.md)。
两路径共享规则及 synthetic JSON/HTML 期望，交互适配各自实现，无运行时跨语言依赖。

## 状态与提交边界

```text
DISCOVERED -> PREPARED -> AWAITING_MANUAL_CONFIRMATION
                      -> SUBMITTING -> CONFIRMED_SUCCESS
                                    -> CONFIRMED_NO_EFFECT
                                    -> OUTCOME_UNKNOWN
```

- 提交前必须验证身份、医生目标、日期时段、readiness、授权及互斥。
  必须先持久化 `SUBMITTING` 再调用副作用动作，写入失败不得 click。
- 进入 click 边界后的异常均视为可能已提交。每个已验证 follow-up 控件至多触发一次；
  未知 follow-up、站点条款、安全验证、支付交人工处理。
- `OUTCOME_UNKNOWN` 停止整个 run 的提交、换 slot 与轮询。
  Start/Stop/reset、刷新、重启、lease 过期均不得解除阻断。
- 持久记录只保存 attempt_id、不透明 booking_ref、状态、时间、证据类型、failure_class、
  human_action_required；不保存完整响应、URL query 或诊疗资料。损坏或未知版本 fail closed。
- 重启后的未解决 `SUBMITTING` 按 UNKNOWN 展示，不得被 retention 删除。
  人工核对“已预约/未预约”后解决并留最小审计事件；撤销授权不清未决记录。
- 成功必须有明确匹配当前预约的成功页、业务响应或订单。
  HTTP 200/302、URL 变化、form 消失仅为弱观测；没有实证 adapter 时保守 UNKNOWN。
- 只有明确业务拒绝才是 `CONFIRMED_NO_EFFECT`；timeout、5xx、限频或弱页面变化均不算。
- 仅提交前只读打开/准备的瞬态失败可自动重试，默认最多 3 次。
  确定字段/时段不匹配不重试；业务拒绝可选下一个号源，不自动重提同一预约。
- reconcile 仅使用已验证且匹配目标的只读证据；没有证据则引导人工查原站预约记录，
  不假定站点已有订单 API。
- BookingService、runner、RunResult、CLI、JS panel 必须区分终态。
  兼容 `success` 仅在 confirmed success 为 true；false 不意味着可重试。
  CLI exit：成功 0、确定失败 1、人工等待 2、UNKNOWN 3。

## 授权与互斥

目标配置为 Python `booking.submit_mode` / JS `booking.submitMode` 的
`auto | manual_confirm`，当前默认 auto 仍须授权 gate；本地发布前置回归见
[集成验收索引](integration-acceptance.md)，远端/现场未覆盖 gate 单独保留。
旧 JS `autoSubmit=false` 保留人工模式，true 迁为 auto 但不构成新授权。

授权各存两路径的本地运行状态，不允许以仓库配置 `consent=true` 绕过确认。
绑定账号、就诊人、医生目标与策略版本，仅存加盐不透明引用；账号无法可靠区分时仅本次运行有效。
首次确认展示目标/就诊人、自动最终提交行为、未知结果需人工核对、禁止混跑；个人信息不入日志。
未授权的非交互运行停止；拒绝后保留人工模式；换绑定对象/策略版本重新确认，支持撤销。
此授权不替代站点协议、验证码或支付确认，也不授权实现会话执行真实预约或复制认证状态。

Python 使用本机进程互斥，userscript 使用同浏览器跨标签互斥。
禁止两路径或多机器混跑同一目标；不建设跨路径、跨 browser profile 或分布式协调服务。
医生页为主通道；保留人工登录/CAPTCHA 与正常限频边界。

## 表单与控件

- 填表仅用用户明确配置或站点已有值；禁止数字/通用病情及默认地区。
  缺必填信息交人工，不能换号源绕过。
- 明确匹配 member、schedule、date、time。已有非空病情/地址值不无声覆盖，冲突交人工。
  规则勾选不代替首次接受站点协议。
- 控件须唯一且可操作；Python 用 Locator，JS 用 native click。
  禁止取第一个宽文本候选、无目标验证的单 radio fallback、未核实的 form.submit/requestSubmit。
- `checkIdInfo` 空响应 patch 仅在证明是指定 endpoint 的纯解析兼容，且未跳过身份校验或警告时保留。
- 只建立必要 booking parser/page seam，不做全仓库分层重构。

## 离线场景与覆盖边界

版本化场景位于 `tests/contracts/booking/fixtures/scenarios.v1.json`，同目录 HTML 完全手写，
仅使用 `synthetic-*` 标识与示例日期/时段，不取自真实页面，不含患者、认证或诊疗输入。
场景记录 member/schedule/date/time、字段来源、必填 blockers、submit 候选数、期望状态及 click 次数。
`schema_version` 描述场景格式；`contract_version` 描述本合同。格式改变须明确升级版本。

历史 S01 只执行解析层：Python 现有 parser、Node JS hooks、Chromium 中的 Python HTML 解析与
JS DOM hooks 共用一组解析期望。状态声明为 `DISCOVERED`，因为 harness 未准备或提交预约；
零 click 在 Node/Chromium 被捕获。解析通过不代表身份、字段或提交 readiness 已满足。
此套件不调用现有 submit/retry 路径，不证明现有提交安全。

S03–S05 已增加以下可执行 regression；不能用已知红灯测试充当完成证据：

| 会话 | 必须增加的场景 |
|---|---|
| S03 | 提交后 timeout/navigation/限频、弱成功、reload 未决记录；状态与 click 次数贯通 |
| S04 | 多成员/唯一错误 radio、日期缺失/冲突、card 已有/缺失、地址缺省/必填缺值、病情已有/配置/缺值；双按钮/隐藏/disabled、延迟 DOM；两路径 decision/blockers/member/slot/time parity |
| S05 | 并发 leader、失锁、crash 接管及 pending 阻断 |

本地 Chromium、离线、live 与远端 CI 证据分别记录；FakePage 不算真实浏览器验证。
CI 只使用离线/本地浏览器，不运行真实预约。canary 人工本机执行，具体真实提交需要单独授权。

## S04 可执行准备合同

`preparation.v1.json` 的 version=1 是独立的准备场景格式；包含手写 synthetic HTML、
selection（member/schedule/time/date）、明确配置 values、expected（state/blockers/sources/submit_clicks）。
Python 纯解析器与 JS 纯 decision 分别执行；Chromium 再比较两适配的真实 DOM snapshot 和决策。
快照/写入提案只在内存使用，含真实值，禁止送入日志、诊断或提交 journal。

- 成员只认表单中明确成员字段的 radio 或精确匹配的已有 hidden member；配置与候选不匹配、
  多个匹配、禁用及审核/身份警告都交人工。不回写 hidden member，不点击任意单 radio。
  JS memberLabel 改为完整显示文字的唯一精确匹配。
- card、date、病情、地区、详细地址控件存在时按已知表单合同检查必填；缺省地址控件不产生地址 blocker。
  其他 HTML required 控件按原生 validity 复核；离线 radio 按 form/name 分组。地区（含 optgroup）只接受唯一精确 value/完整显示文字，不做包含/简称匹配。
  地址来源可为选定成员属性或显式配置；来源/已有值冲突则保留原值并交人工。
- Python 显式值配置为 booking.disease_description / clinic_card / address；JS 为 booking.diseaseDescription /
  clinicCard 与 address。默认全空。仅填写空控件，已有值保留；不复制 real_card/证件号到 card。
  日期由选定 slot 的日期或页内指定 schedule 的无歧义日期提供，与已有日期互相核对；非法日期交人工。
  PHP serialized array 仅读取唯一当前 schedule record；按 UTF-8 字节长度、深度/节点预算解析，
  重复目标 key、坏长度、unsupported object/reference 等结构不提供日期，绝不取其他 record 的值。
- 准备最多三个 DOM pass，只等延迟字段/地区 options/可操作控件，不通过重开号源绕过必填缺失。
  选择用 Python Locator 与 JS native click；准备后再只读复核选中成员/时段、字段、控件和授权。
  当前时段适配确认 `#delts li.selected`；未识别的现场选择语义须交人工并等待 S06 证据。
- 协议 checkbox 未由站点/用户勾选时交人工，本工具不代替首次接受。checkIdInfo 空响应 patch 已删除：
  未取得 endpoint/原处理分支的安全证明，保留原站 AJAX 与身份警告。
- JS settingsVersion=4 清除旧版本自动生成的广东/深圳/南山区和通用病情的同名值；无法确认来源，
  即使旧值曾由用户输入也须重新核实。v4 明确填写的同名真实值保留。

上述证据全为本地 synthetic；没有访问真实登录/预约页面、复制认证状态或取得现场 adapter。

## S09 集成验收与恢复

`tests/integration/test_acceptance_chromium.py` 补充两路径闭环：实际 CLI 服务组装/JS doctor
controller → 排班解析与筛选 → 预约准备 → 明确授权 → durable 单次提交 → UNKNOWN 停止 →
新 run/reload、Stop/reset、撤销仍阻断 → 人工核对解决及最小 audit。Python 还覆盖 confirmed expired
人工恢复后重新绑定目标/成员/授权。人工登录/成员页传输使用 synthetic seam，不构成现场认证验证。

恢复不是重试提交。没有结果 adapter 时仍 UNKNOWN；人工解决不重启自动操作，
已提交过的同一 booking_ref 不自动再次 click。Python CLI 与 JS 面板的核对/撤销操作见
[README](../README.md#未决提交与退出码)。checkIdInfo patch 继续保持删除，条款/验证/支付交人工。

D01–D06 覆盖映射、配置迁移及所有未覆盖 gate 统一见 [集成验收索引](integration-acceptance.md)。
