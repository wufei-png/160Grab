# 两路径预约业务合同

合同版本：1；确认日期：2026-09-30。适用于 Python/Playwright 与 Tampermonkey。
本文固化已确认的目标行为，不表示所有保护已实现。实施顺序与交付记录见
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
`auto | manual_confirm`，默认 auto **只能在授权 gate 和发布前置保护完成后启用**。
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

S01 只执行解析层：Python 现有 parser、Node JS hooks、Chromium 中的 Python HTML 解析与
JS DOM hooks 共用一组解析期望。状态声明为 `DISCOVERED`，因为 harness 未准备或提交预约；
零 click 在 Node/Chromium 被捕获。解析通过不代表身份、字段或提交 readiness 已满足。
此套件不调用现有 submit/retry 路径，不证明现有提交安全。

后续能力实现时随之增加可执行 regression，不能用已知红灯测试充当 S01 完成证据：

| 会话 | 必须增加的场景 |
|---|---|
| S03 | 提交后 timeout/navigation/限频、弱成功、reload 未决记录；状态与 click 次数贯通 |
| S04 | 多成员/唯一错误 radio、日期缺失/冲突、card 已有/缺失、地址缺省/必填缺值、病情已有/配置/缺值；双按钮/隐藏/disabled、延迟 DOM；两路径 decision/blockers/member/slot/time parity |
| S05 | 并发 leader、失锁、crash 接管及 pending 阻断 |

本地 Chromium、离线、live 与远端 CI 证据分别记录；FakePage 不算真实浏览器验证。
CI 只使用离线/本地浏览器，不运行真实预约。canary 人工本机执行，具体真实提交需要单独授权。
