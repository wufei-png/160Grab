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

## 2026-10-02 决策对齐审核

对照聊天 `01a0f06b-5a8c-7843-8a7a-67c0da987e48` 的用户确认及 D01–D06，
审查初始实现基线 `16bc710` 至本次开始 HEAD `b90abf3` 的实现、调用路径及回归。
S01–S09 的本地能力已实现；上表现场、扩展 sandbox、平台及远端 gate 仍未取得证据。
没有需要重开的产品取舍；本次修复沿用已确认合同，改动留在工作区。

发现并修复：

- **[P1] 设置变化时中止旧预约控制器 — `userscripts/91160-doctor-page-poller.user.js:2380`**。
  提交等待期间另一标签修改成员、医生或病情，旧控制器仍能用旧授权和字段点击。
  现在准备、等待和 journal 写入前复核相关设置，变化交人工；canary 同样受此限制。
- **[P1] 核对预约页与导航传递的完整目标 — `userscripts/91160-doctor-page-poller.user.js:2413`**。
  当前 URL 的医院、科室或 schedule 与 pendingBooking 不同，仍会沿用医生页身份提交。
  现在在填表和授权前拒绝不一致，并复核配置目标。
- **[P2] 检查已识别字段的原生校验结果 — `src/grab/booking/form.py:303`**。
  非空 card、病情等字段即使 pattern/custom validity 不通过，两适配仍判定 PREPARED。
  现在准备及最终只读复核都保留布尔 blocker，不复制 validationMessage；零提交、无 pending。
- **[P2] 给预约事件传入必填 message — `src/grab/services/booking.py:365`**。
  两个调用缺参数：无效表单触发 TypeError，提交终态事件在异常分支被丢弃。
  现在使用固定消息，真实 RunReporter 验证人工 exit=2 及三种终态事件。
- **[P2] 只将可预约号源交给 Python 预约流程 — `src/grab/services/schedule.py:374`**。
  既有筛选遗漏将满号/过期/停诊等状态当作候选，新终态流程可能在第一项提前停止。
  现在只传 available；两路径闭环增加满号在前、可预约在后的输入。

新增 18 个回归用例，覆盖上述反例及最终复核，仅使用 synthetic 数据；浏览器用例均由本地路由处理。
原实现已复现五项缺陷；没有执行真实站点请求、复制认证状态、真实预约、push 或发布。
最终执行 `LIVE_E2E=0 LIVE_BOOKING=0 uv run --locked --offline --no-sync pytest -q`：
674 passed、5 skipped（3 个 live、2 个未安装 Edge），14 个既有 appoint_time 迁移警告。
Ruff、userscript 与 Node harness 语法、`git diff --check`、相关本地文档链接检查均通过。
本轮审查范围内没有其他已确认缺陷；上表现场、扩展 sandbox、平台及远端 gate 仍待验证。

## 2026-10-02 后续环境验证

使用本次审核后的工作区内容创建显式白名单源码快照，仅包含 tracked 源码、synthetic 测试、
打包文件、userscript、工作流及公开配置模板。没有复制本机 config.yaml、认证状态、profile 或 journal。
快照 SHA256：`25ae37477d2dfb8d15f8248810175347c2f9444d84db58f79f7ac73c577893cd`。
测试后复核当前源码、测试、userscript 与打包文件的逐文件 hash，均与快照一致。

| gate | 新证据与剩余范围 |
|---|---|
| Linux 运行 | Ubuntu 20.04.6 x86_64，CPython 3.11.13、Node 24.20.0、锁定 Playwright 1.58.0；offline 组 403 passed / 3 live deselected，contract/Chromium 组 271 passed / 2 Edge skipped；Ruff 与两份 Node 语法通过。合计 674 passed，14 个既有 naive 迁移警告；没有真实站点请求。不能替代 GitHub hosted runner 证据 |
| macOS arm64 frozen | 在隔离源码目录以锁定依赖重新执行 PyInstaller 6.20.0、浏览器装包及 release staging；Mach-O arm64、help、普通 smoke、强制 `PLAYWRIGHT_BROWSERS_PATH=0` 的 bundled-browser smoke、缺配置 bootstrap 均通过。bootstrap 输出与公开模板逐字节相同；ZIP checksum 与内容检查通过。未签名公证、未发布；不是 Windows/Intel Mac 证据 |
| 远端 CI | 既有最近成功 run `36739045332` 只覆盖旧提交 `46ddd41e6ab6`，不覆盖本次修复。CI 已准备四种 runner 的矩阵与手动入口；Release 分支 dispatch 仅构建，发布 job 仅允许 v tag，build 使用只读 contents。actionlint 1.7.12 与空白检查通过；等待当前内容推送到验证分支后执行 |
| 真实站点 | 当前 Chrome 打开首页返回 `net::ERR_BLOCKED_BY_CLIENT`；本机配置没有明确医生、日期、就诊人或选定 profile，尚无人工 ready。没有运行 live canary、收集真实夹具或执行预约；需先解决可访问性并提供明确只读场景 |
| Tampermonkey sandbox | 浏览器 URL 安全策略拒绝打开扩展管理页，尚未确认安装与当前脚本版本；普通 Chromium 注入仍不能当成 sandbox 证据。需用户确认当前扩展和脚本、目标医生页及人工 ready |
| Windows/Intel Mac、Windows ACL/reparse/ready、Edge | 当前机器及 Ubuntu 主机不能验证这些平台；待远端实际运行。Windows ready 仍保守阻断，POSIX 模式测试的 skip 不构成 ACL 正证据；未安装 Edge 的两项 skip 不构成 launch 成功 |

本次 frozen ZIP SHA256：`f46c0461ecf11bab5d49fc2b23f3a68d73e7c6cc32b3320269944bfe355f3713`。
仅使用公开配置模板；未包含 profile、journal、原始页面、认证数据或 cache link 元数据。
源码及本机打包保存在隔离临时目录，Linux 测试也使用隔离临时目录，未改写既有用户运行数据。
以上 Linux 与 frozen 证据绑定本次源码快照；CI 配置的静态校验不是远端执行成功。

runner 架构与 workflow_dispatch/permissions 行为依据
[GitHub runner 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
和 [GitHub 工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。

### 人工协作入口补齐

核对现场操作说明时发现：`runtime.autoStart` 已有设置及启动逻辑，但 Settings 面板没有对应开关，
文档要求「关闭 Auto Start」无法直接操作。现增加面板复选框与保存读取；旧配置 true 可以关闭，
不改默认 false、canary 限制或 pending 阻断。新增本地 Chromium 回归检验 true→面板关闭→保存→刷新
后不自动开始。现场协作步骤见 [live-canary.md](live-canary.md#现场验证需要人工辅助时)。

执行 userscript/canary、时间面板及集成闭环相关测试：98 passed；Ruff、Node 语法和空白检查通过。
这是上述 Linux 源码快照之后的 userscript UI 增量；此前 Linux/frozen 结果不冒称覆盖此增量，
Python 与打包源码未再修改。真实站点和 Tampermonkey sandbox 仍等待人工现场证据。

## 2026-10-02 自动化验证推进

用户授权代为执行已说明的验证分支提交、推送与远端检查。
修复与 canary 面板入口提交为 `c7d17ef`，四平台 CI 和 tag 发布门槛提交为 `7885d2b`；
目标分支 `codex/validation-20261002`，包含此前尚未推送的 25 个实现提交。
不合并主分支、不创建版本 tag、不发布 GitHub Release。

包含 Auto Start 增量的最终本机全套：675 passed、5 skipped（3 live、2 Edge），
14 个既有 naive 迁移提示；Ruff、两份 Node 语法、actionlint 和空白检查通过。
待推送历史与路径已核对，synthetic fixture provenance 保留；
没有纳入本机 config.yaml、认证状态、profile、journal、artifacts 或原始页面。
### 首次远端运行及兼容性修复

验证提交 `ceae2fc` 的 [CI run 37028920828](https://github.com/wufei-png/160Grab/actions/runs/37028920828)：
Linux、macOS ARM、macOS Intel 通过；Windows 离线测试失败。
[构建 run 37028928393](https://github.com/wufei-png/160Grab/actions/runs/37028928393)：
macOS ARM 完成测试、打包、frozen help/browser smoke、config bootstrap 并上传 artifact；
Windows 和 Intel 在离线测试阶段失败，release job 按 tag 条件跳过。没有发布 Release。

失败日志指出三类测试问题：

- UTF-8 userscript 的 `Path.read_text()` 依赖 Windows 默认 cp1252，导致解码失败。
  现对测试中的 UTF-8 源码、fixture 和应用输出显式指定编码，未用全局 UTF-8 环境变量掩盖问题。
- Windows 内核拒绝另一句柄改写锁文件，以及锁文件/所在目录重命名。
  nonce 损坏检查改用 owner 自身句柄；Windows 重命名分支断言内核拒绝且 owner 仍有效，
  POSIX 继续断言替换后旧 owner 被 fencing。没有跳过这些 Windows 检查。
- Intel 的同步输入心跳测试使用 120ms 真实 deadline，可能在输入前被 runner 调度拖延。
  改用受控单调时钟与真实 heartbeat 线程确认，仍验证 asyncio 阻塞期间多次续租、超过初始 TTL
  后最终只能点击一次。产品的 TTL、过期阻断与续租逻辑保持原样。

同时补充 Windows frozen help 和 smoke 各自的 `$LASTEXITCODE` 检查，避免首个命令失败被后续成功覆盖。
本机复核 coordination/canary/contracts/integration：320 passed、2 Edge skipped；
browser/observability/transactions/utils：131 passed。Ruff、actionlint、空白检查通过。
修复后重新运行四平台 CI 与三平台构建，结果以各 run 的实际完成状态为准。
文件编码和锁接口参考 [Python pathlib](https://docs.python.org/3/library/pathlib.html#pathlib.Path.read_text)
与 [msvcrt](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking) 文档。

### 现场页面读取进展

先前自动新开标签返回 `net::ERR_BLOCKED_BY_CLIENT`，不代表用户 Chrome 会话不能访问。
用户手动打开后，工具成功读取同一个 Chrome「用户1」中的首页及指定医生页。
医生页实际提示「请登录后查看医生号源」，脚本面板为旧版 v0.2.16、自动提交 ON、运行已停止；
已再次点击 Stop。当前现场代码尚未更新，因此不把该页面当作本次 v0.3.0 的 sandbox 验证。
扩展编辑页的协议策略限制仍生效，已请求用户编辑原脚本并完成站点登录；后续仅限 readonly。
未填写个人表单、未执行真实预约、未清除既有 journal。

### 2026-10-03 远端构建结果与并发测试前提

提交 `7925f14` 的 [构建 run 37030520365](https://github.com/wufei-png/160Grab/actions/runs/37030520365)
已成功完成 Windows x64、macOS ARM、macOS Intel 三个 job，包括所有测试、打包、
frozen help/browser smoke、config bootstrap 和 artifact 上传。release job 为 skipped。

同一提交的 [CI run 37030489022](https://github.com/wufei-png/160Grab/actions/runs/37030489022)
Linux、Windows、Intel 通过；ARM 的双标签测试间歇得到 UNKNOWN 且 0 次点击，未达到其正向 1 次点击断言。
原测试在本机共享浏览器 100 次、冷启动浏览器 100 次没有复现，不能据此认定产品根因。
提交边界中断允许 UNKNOWN 且 0 次点击；该正向测试现先建立真实 booking controller 的既有 leader 前提，
再并发启动 owner 提交与 follower 尝试，仍要求恰好 1 次点击、关闭重启后 pending 阻断。
同时启动选主、owner 失效及晚到回调测试保持独立覆盖；失败时增加闭合状态诊断。
本机 coordination 集成 15 passed；修改后的原测试冷启动重复 50 次通过。
此增量仅修改测试前提，未调整产品 Web Lock、TTL、journal 或提交保护；四平台 CI 再验证。
