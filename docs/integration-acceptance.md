# S09 集成验收与迁移索引

日期：2026-10-01；会话基线 `ec8dd5c`。依赖 S01–S08 代码及离线验收完成；S06 工具已完成，
两路径 live 仍 blocked，不影响本地 S09。本文是当前验收/迁移入口，历史各阶段证据保留在
[实施计划](implementation-plan-2026-09-30.md#完成记录) 与 [当前架构](current-architecture.md)。

## D01–D06 与合同覆盖

下表列出实际执行的回归入口；本地 synthetic 正证据仅检验 adapter seam，不能当成现场结果。

| 决策/验收 | 强制回归入口 | 合同与证据边界 |
|---|---|---|
| D01 双路径、共享版本化期望 | `tests/contracts/booking/`、`tests/contracts/session/`、`tests/integration/test_preparation_chromium.py` | Python/Node 实际执行相同决策，Chromium DOM parity；无运行时跨语言依赖 |
| D02 auto 授权与人工模式 | `tests/transactions/test_consent.py`、`tests/integration/test_submission_chromium.py`、`tests/integration/test_acceptance_chromium.py`、`tests/test_main.py` | 接受/拒绝/无交互/人工模式、最终撤销复核、退出 0/1/2/3；当前账号身份不可靠，授权仅本 run/document |
| D03 本机人工 canary | `tests/canary/`、`tests/e2e/`（CI 排除 live） | offline/Chromium 验 readonly/prepare 零提交、submit 双开关/精确批准/预算；未执行真实预约 |
| D04 单域 leader | `tests/coordination/test_local_leader.py`、`tests/integration/test_coordination_chromium.py` | 真实子进程/两个 Chromium 页面、TTL/visibility/pagehide fencing、接管保留 pending；无跨路径/profile/机器协调 |
| D05 真实值与严格控件 | `tests/contracts/booking/fixtures/preparation.v1.json`、`tests/integration/test_preparation_chromium.py` | 多成员/错误 radio/date/card/address/disease、隐藏/disabled/双按钮/延迟 DOM，缺值/冲突交人工；删除 checkIdInfo patch |
| D06 doctor-only、人工登录及限频 | `tests/services/test_auth.py`、`tests/contracts/session/`、`tests/integration/test_session_chromium.py` | auth manual-only、confirmed expired 才人工恢复、单 owner 五次只读失败预算、Retry-After、cancel；不恢复最终 click |
| 准备→授权→提交→停止→恢复闭环 | `tests/integration/test_acceptance_chromium.py` | CLI 实际 build_runner；JS doctor→ystep1 navigation/controller；唯一 click、UNKNOWN 停下，新 run/reload/撤销仍阻断，人工 resolve/audit；Python confirmed expired 恢复重绑。登录/成员传输使用 synthetic seam |
| UNKNOWN、写前 durable、损坏 fail closed | `tests/transactions/`、`tests/contracts/booking/test_submission_contract.py`、`tests/integration/test_submission_chromium.py` | timeout/navigation/限频/丢响应/弱成功、换 slot/刷新/浏览器重启零新 click，写失败不 click，未解决 SUBMITTING 展示 UNKNOWN |
| 隐私与私有输入 gate | `tests/observability/`、`tests/utils/test_private_files.py`、`tests/utils/test_retention.py`、`tests/integration/test_privacy_chromium.py`、`tests/canary/test_fixtures.py`、新增闭环回归 | nested/free text/异常/通知失败/DOM/script/cookie 注入逐 sink 零明文；未知键拒绝；debug 双开关、POSIX 权限/link/清理及安全 converter |
| aware 时间、channel/profile | `tests/core/test_scheduler.py`、`tests/userscripts/test_schedule_time.py`、`tests/integration/test_schedule_time_chromium.py`、`tests/integration/test_browser_channels.py`、`tests/utils/test_profile_manager.py` | 时区/DST/迟到/亚秒/cancel，三 channel 参数及 marker；branded 实测仅安装时执行 |

## 配置迁移收口

| 旧设置/操作 | 当前迁移与操作 |
|---|---|
| `auth.strategy=auto`，username/password/ocr | strategy 在启动前拒绝；旧顶层字段保留解析并只提示字段名，不用于登录，独立 OCR 模块无登录调用者 |
| Python booking / JS autoSubmit | 默认 auto 必须本地确认；旧 JS false→manual_confirm、true→auto，不构成授权。配置 consent=true 无效；撤销不清 pending |
| JS 自动生成病情/广东/深圳/南山区 | settingsVersion=5 包含 v4 清理规则，旧来源不可辨值清除后重新明确配置；v4 已明确字段保留。无默认卡号/病情/地址 |
| JS maxSubmitAttempts、autoReturnAfterSubmitFailure | 只迁到最多三次 pre-submit 预算；不能绕过 UNKNOWN 或重试最终 click |
| JS Start/Stop/Reset/refresh | durable pending 不清，连续只读失败预算/冷却不被 reset 绕过；人工解决留 audit，已提交同 booking_ref 不再自动 click |
| naive appoint_time / JS startAt | 按 schedule.timezone（默认 Asia/Shanghai）解释并提示，JS 存规范 ISO；推荐 offset ISO，DST fold/gap 拒绝。超宽限期非交互停，交互须确认 |
| 旧 profile marker 无 channel | 视 Chromium；chrome/msedge 用独立 profile，不移动目录/复用或复制认证状态，缺安装不降级 |
| 240s keepalive、低于 3s poll | 保留旧兼容字段；有效 poll 不周期 probe，缺 key 必要诊断至少 60s。poll 最小 3s；Retry-After/冷却取更长值 |
| 只设 GRAB_DEBUG_DIR 或旧敏感 runtime logs | 仅安全 JSON；原始 HTML/图须再显式 include_sensitive_debug，仅本机 24h，旧敏感日志丢弃，pending 保留。清理不处理未知 ownership 的旧原始文件 |

不要求全局 config_version。README、config/example.yaml、booking/security/canary 文档为当前说明；
历史 plans/handoffs/建议原文不作为执行指令。旧 binary 回滚前人工核对 pending，不能删新 journal。

## 必跑命令与 CI

```bash
uv sync --locked --extra dev
uv run --locked playwright install chromium
uv run --locked ruff check .
node --check userscripts/91160-doctor-page-poller.user.js
node --check tests/contracts/booking/node_harness.cjs
uv run --locked pytest -q -m "not live" --ignore=tests/contracts --ignore=tests/integration
uv run --locked pytest -q tests/contracts/ tests/integration/ -m "not live" --browser chromium
uv run --locked pytest -q
```

有锁定环境时可用 `--offline --no-sync`。Node 缺失失败，不 skip；Chromium 缺失失败，不以 FakePage 替代。
CI/release 前置显式 LIVE_E2E=0/LIVE_BOOKING=0、排除 live，两份语法和两组测试必须通过；
PR 不请求真实站点。release 另保留平台构建、frozen smoke/config bootstrap。
本次 CI 同命令本机两组 **397 passed, 3 live deselected** 与 **259 passed, 2 Edge skipped**。
完整 **656 passed, 5 skipped**（3 live / 2 Edge），14 个固定 naive 迁移提示；
Ruff/两份 Node/锁定离线 dev sync/whitespace、文档链接/示例 YAML parity 通过。
新鲜只读 review `ec8dd5c..2cacc1a`：**No findings**，无需修复；独立复核两组同参数检查
和新增七个 Chromium 用例通过。详情见实施计划，不把本机命令当成远端 CI/release 证据。

## 尚未覆盖的 gate

| gate | 状态/所缺证据 |
|---|---|
| 两路径 live、真实 fixture/schema/结果 adapter | blocked：缺专用 profile/明确目标/当前场景批准；无真实登录/预约/刷新证据；没有 adapter 时提交仍 UNKNOWN |
| Tampermonkey 扩展 sandbox | 未实测；本地 Chromium 页面执行不能代替扩展 storage/沙箱现场证据 |
| Windows/Linux 运行、Windows ACL/reparse/ready | 本机 macOS 证据不能替代；Windows ready 已保守阻断；Linux CI 配置尚未远端执行 |
| Edge 实际 launch | 未安装，两个 smoke skip；参数合同执行。Chromium/已安装 Chrome 临时 profile 两种 launch 在本次 suite 执行 |
| 远端 CI/release | 未执行；本次仅本地提交，不 push/发布；各平台成功记录须等实际流水线 |
| frozen | S09 不改打包代码/配置值，不重跑；S08 macOS arm64 frozen smoke/bootstrap 是历史证据，不能当成本次/其它平台验证 |
| 既有双页面提交用例稳定性 | S07 基线曾复现偶发零 click/UNKNOWN；本次通过不宣称根因已消除，不降低一次提交/持久阻断断言 |

本次变更仅手写 synthetic 回归、CI 和文档。核对 staged 全部路径，不纳入本机 config.yaml、
artifacts、profile、journal、原始页面、截图、cookie 或服务端响应；既有 ignored 私有输入不改写。
现场执行遵循 [live-canary.md](live-canary.md)，代码实现授权不等于具体真实预约批准。
