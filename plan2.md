# 160Grab 可观测性与通知增强计划

## Summary

在不改变当前“手动接管 + 浏览器上下文刷号 + 页面预约”主链路的前提下，补一层统一的运行事件模型，把“成功通知、结构化运行日志、全链路诊断输出”收敛到同一套机制里。

这次实现按已锁定的取舍推进：

- 通知通道：`自动检测系统的桌面通知 + 通用 Webhook`
- 桌面通知支持：`Windows`、`macOS`
- 通知事件：`挂号成功`、`致命失败`、`长时限频阈值命中`
- 日志形态：`保留终端可读日志 + 新增 JSONL 结构化事件文件`
- 诊断范围：`覆盖登录、目标页捕获、成员解析、排班轮询、预约提交、成功/失败全链路`

## Key Changes

### 1. 新增统一的运行事件与通知层

新增一个 `observability` 子系统，包含三类职责：

- `RunReporter`：统一发出关键运行事件，负责把同一事件同时写到终端日志、JSONL 文件、通知通道
- `JsonlEventSink`：按 JSON Lines 追加结构化事件，不接管全部 `loguru`，只记录被规范化的关键事件
- `NotificationManager`：负责桌面通知和 webhook 投递，通知失败只记录事件，不影响主流程

结构化事件统一字段：

- `ts`
- `run_id`
- `level`
- `event`
- `phase`
- `message`
- `data`

`event` 固定命名，至少覆盖：

- `run_started`
- `manual_login_waiting`
- `target_captured`
- `target_resolution_failed`
- `member_resolved`
- `scheduler_wait_started`
- `schedule_poll_completed`
- `rate_limit_detected`
- `rate_limit_threshold_reached`
- `booking_form_opened`
- `booking_submit_failed`
- `booking_succeeded`
- `run_failed`
- `run_finished`
- `notification_delivery_failed`
- `snapshot_saved`

### 2. 配置模型扩展

在 `GrabConfig` 中新增两个配置段，并更新 `config/example.yaml` 与配置加载测试：

- `logging`
  - `jsonl_dir`: 默认 `~/.160grab/logs`
  - `heartbeat_interval_seconds`: 默认 `300`
- `notifications`
  - `desktop`: 默认 `true`
  - `rate_limit_threshold`: 默认 `3`
  - `webhook.url`: 默认空
  - `webhook.timeout_seconds`: 默认 `5`
  - `webhook.headers`: 默认空字典

默认行为：

- JSONL 文件每次运行生成一个独立文件，文件名带时间戳，启动时在终端打印路径
- `desktop: true` 时自动检测系统
  - `Windows`：使用 Windows provider
  - `Darwin`：使用 macOS provider
  - 其他系统：直接 no-op
- webhook 仅在 `url` 非空时启用
- 本阶段不单独加 Server酱 provider；后续若要兼容，直接作为 webhook provider 或单独 provider 再接

### 3. 桌面通知实现策略

桌面通知适配器固定为按 `platform.system()` 自动选择 provider：

- `WindowsDesktopNotifier`
  - 通过 `powershell.exe -NoProfile -Command ...` 调用 Windows toast
  - 不引入第三方 Python 通知依赖
  - 调用失败只发 `notification_delivery_failed` 事件，不影响主流程
- `MacOSDesktopNotifier`
  - 通过 `/usr/bin/osascript -e 'display notification ...'` 调用系统通知
  - 标题固定 `160Grab`
  - 支持正文和副标题
  - 调用失败同样只记事件，不抛出业务异常
- `NullDesktopNotifier`
  - 供非 Windows/macOS 系统或 `desktop: false` 时使用

通知文案固定：

- 成功：`160Grab 挂号成功`
- 致命失败：`160Grab 运行失败`
- 长时限频：`160Grab 持续限频`

### 4. 入口与主运行链路改造

在 `main.py` 和 `GrabRunner` 引入 reporter，并把“阶段状态”显式化：

- `main.py` 在配置加载后初始化 reporter，贯穿整次 run 生命周期
- `GrabRunner` 维护 `current_phase`，在阶段切换时发事件，在异常时按当前 phase 产出 `run_failed`
- 保持当前退出码语义不变：成功 `0`，失败 `1`
- 保持 `input()` 交互行为不变，不把阻塞 prompt 改造成结构化日志

通知触发规则固定为：

- `booking_succeeded`：立即发送桌面通知和 webhook
- `run_failed`：仅在真正终止本次 run 时发送
- `rate_limit_threshold_reached`：当排班或预约路径连续命中限频达到 `notifications.rate_limit_threshold` 时发送一次；任意一次正常轮询或非限频预约响应后重置计数

### 5. 诊断输出统一化

把当前分散在 `print()`、`logger.info()`、快照落盘里的诊断素材统一升级成“终端摘要 + 结构化事件”。

具体改造规则：

- `SessionCaptureService`
  - 保留人类交互提示
  - 把登录页诊断、标签页 URL 列表、目标页解析失败原因、`member.html` 探测结论改为通过 reporter 输出
  - 遇到 `debug_snapshot()` 返回路径时，额外发 `snapshot_saved` 事件
- `ScheduleService`
  - 每次轮询输出标准化摘要：attempt、匹配数、下一次等待、是否刚发生限频
  - 按 `heartbeat_interval_seconds` 补周期性心跳摘要，避免长跑时只剩零碎日志
- `PageBookingStrategy`
  - 保留现有预约页 diagnostics 采集
  - 预约失败时，终端输出简短结论，JSONL 保存完整 diagnostics payload
  - 预约成功时，记录 `schedule_id`、`appointment_label`、目标 URL 等核心字段

目标是：

- 终端只显示适合人工快速判断的结论、原因、下一步动作
- JSONL 保存完整上下文，便于后续筛查和二次分析

### 6. Webhook 具体实现

- 复用现有 `httpx`
- 固定 `POST` JSON
- 负载直接复用结构化事件主体，并补 `title`、`severity`
- 不做重试，不阻塞主流程超过 `timeout_seconds`
- webhook 失败时仅记 `notification_delivery_failed` 事件

## Public Interfaces / Types

新增或扩展的公开配置接口：

- `GrabConfig.logging`
- `GrabConfig.notifications`
- `LoggingConfig`
- `NotificationsConfig`
- `WebhookNotificationConfig`

不改变现有业务主接口语义：

- `GrabRunner.run()` 仍返回现有成功/失败结果
- `AuthService`、`ScheduleService`、`BookingService` 业务行为不变
- `GRAB_DEBUG_DIR` 继续沿用，不改成配置项

## Test Plan

必须补齐以下测试：

- 配置加载
  - 新配置段默认值
  - 显式 webhook headers / timeout / jsonl_dir / heartbeat_interval_seconds
- 桌面通知 provider 选择
  - `platform.system() == "Windows"` 选择 Windows provider
  - `platform.system() == "Darwin"` 选择 macOS provider
  - 其他系统选择 null provider
- Windows 通知
  - 正确调用 `powershell.exe`
  - 调用失败只记事件不抛业务异常
- macOS 通知
  - 正确调用 `/usr/bin/osascript`
  - 标题/正文拼装正确
  - 调用失败只记事件不抛业务异常
- 结构化日志
  - 启动后创建 JSONL 文件
  - 关键事件记录字段齐全
  - 失败事件包含 `phase` 和错误摘要
- 运行链路
  - 成功 run 会发 success 通知并落 success 事件
  - 目标页解析失败或成员解析失败会发 fatal failure 事件/通知
  - 连续 3 次限频只通知一次，恢复正常后重置
- 诊断
  - 登录页卡住时输出标准化诊断事件
  - 预约失败时同时有终端摘要和完整 diagnostics 事件

验收标准：

- 用户在 Windows 或 macOS 本机长时间运行时，成功后都能收到系统通知
- 配置了 webhook 时，成功/致命失败/长时限频三类事件都能收到 JSON 通知
- 终端日志仍然适合人直接看，不被 JSON 污染
- 同一次 run 的所有关键阶段都能在 JSONL 中按 `run_id` 串起来
- 当前 CLI 提示、交互输入和退出码不回归

## Assumptions

- 本阶段不做 Server酱 专用 provider；若后续要兼容，直接在 webhook 抽象之上补
- 本阶段不把所有现有 `loguru` 输出自动结构化，只规范化关键生命周期事件
- 本阶段不改 `GRAB_DEBUG_DIR` 的现有 env 入口，也不把 debug snapshot 机制迁移成配置项
- 本阶段不把 `auto` 登录、代理、Docker 一并带进来，范围只限于通知、结构化日志、诊断输出
