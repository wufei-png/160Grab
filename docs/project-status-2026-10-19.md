# 2026-10-19 项目审核与下一步

审核起点为 `577546a`，分支 `codex/validation-20261002`，开始时 tracked/untracked 工作区干净。
该验证分支相对远端 `master` 有 33 个提交；远端 master 仍为 `46ddd41`。
查询 GitHub 确认原四平台 CI `37037504950` 成功，绑定 `a27c59e`，不覆盖本轮新增代码。
S01–S09 的本地能力已实现。当前重点是修复提交边界、完成现场适配与诊断；历史计划里的
早期未完成项不能覆盖后续实现记录，也不能把旧 CI 或本地合成场景当成当前现场验收。

## 已复现并修复

| 问题 | 证据与修复 |
| --- | --- |
| P1 Python 预约页完整目标缺校验 | 打开预约页后与提交边界均核对 HTTPS origin、医院、科室、schedule；不符在填表前停止。2026-11-16 用离线合成页面改变目标地址验证；先前的 302 测试有网络隔离缺口，不能作为纯本地重定向证据。 |
| P1 Python 点击等待期间绑定可变 | 遮挡层让 Locator 等待时更改就诊人，原实现仍实际提交。现在先做无点击的 actionability trial，再复核 readiness；浏览器捕获阶段同步比较表单、时段、日期、按钮身份及目标。durable begin 后发生变化也阻止提交事件，已开始的事务仍保守保留 UNKNOWN，不擦除未决。 |
| P1 userscript 借用旧医生 | 手动 Start 会清 pendingBooking，但原实现随后用 lastTarget 的旧 doctorId 为另一个预约页授权、点击。现在医生绑定只来自匹配的导航交接或明确配置，不从 lastTarget 补医生；缺完整目标在填表前停止。 |
| P2 两路径漏验未知非必填控件 | 非必填 email 已有非法值或 custom validity 失败时，原实现点击并写 UNKNOWN，而浏览器没有实际 submit。现在把预约表单内这类原生校验失败纳入 readiness；不读取 validationMessage，不误拦另一个无关表单。 |
| P1 真实排班状态类型不兼容 | 本次真实接口返回 code=1、sch 对象，schedule_id/y_state 均为字符串。原两路径把 y_state 字符串判为 schema_drift。现在只接受整数枚举及其精确字符串 `-3/-2/-1/0/1`；空白、浮点字串、未知值和布尔仍拒绝。Python parser 同步规范化，双路径分类/解析共用反例。 |
| P2 面板恢复到窗口外 | 现场原脚本恢复旧拖动坐标后，右侧日期等设置在窗口外而无法点击。本地 Chromium 同样复现；现在恢复坐标、切换面板内容及窗口 resize 时按实际面板尺寸限制边界，窄窗口仍可点击「自检」。 |

新增的 Chromium 反例在修复前复现错误点击、错误未决或目标借用；修复后检查零提交、
durable pending 保留，以及正常表单仍能进行唯一一次合成点击。既有跨语言准备夹具改用完整
预约 URL；并发延迟回调测试保留完整配置再调整节流，继续执行原 owner/follower 断言。

## 已落地的新功能

```bash
uv run --locked python main.py config.yaml --check
```

该命令不进入产品 leader，不启动浏览器，不发站点请求，不创建/选择 profile，不初始化或修改
journal。输出版本化安全 JSON，检查配置、对应浏览器文件、profile/channel、未决/损坏记录、
目标与成员是否需人工选择，以及启动时间是否需重新确认。`blocked` 退出 2，其余退出 0。
`warning` 表示仍有人工步骤，不能当成预约就绪；`live_checked=false` 明确区分本机检查与现场证明。
存在现成专用 profile 但配置为空时会报告可自动发现，不把它误报为“没有 profile”。

userscript **v0.3.1** 面板新增「自检」按钮：只在内存中检查当前设置、Web Locks、页面目标、
可见性与 durable journal，显示固定中文原因，不发请求，不修改配置/存储，不清 pending。
日志、摘要均不输出成员、医生、卡号、地址、异常原文或服务端响应。

两路径只读 canary 新增闭合的 `schema_blocked`、`login_required`、`network_blocked`、
`rate_limited`、`leader_blocked` 状态并保留查询次数；提交边界后的不确定性仍为 UNKNOWN。
只读测试不再因会话/schema 阻断而丢失安全摘要。

## 本次现场证据

继续使用历史目标及原 Chromium 专用 `profile_1`，用户本次完成登录；查询日期改为
2026-10-19。没有读取、复制或导出其它 profile 的认证资料。

Python 首次实际查询因 schema_drift 停止；另做两次有限的类型诊断，只输出字段类型、计数与
已知协议状态，未保存原始响应。修复后实际运行 `CanaryRunner` readonly，最多 3 次查询、
120 秒，未启用 LIVE_BOOKING，结果为：

```json
{"status":"inconclusive_no_slots","level":"readonly","polls":3,"form_opened":false,"state":null,"submit_calls":0}
```

三次真实请求均通过当前 schema 分类并完成解析；当天没有匹配的可预约号。
这是只读请求/解析证据，不是命中、预约页准备、真实提交或成功结果 adapter 的证据。
最初一次人工等待到期的运行 polls=0，也不计为查询成功。

普通 Chrome 首次打开被 `ERR_BLOCKED_BY_CLIENT` 拦截，使用可见页面的 Reload 后成功进入原
医生页并实际读到 Tampermonkey v0.3.0 面板。原脚本已 Stop，Auto Start 关闭、自动提交 off，
本标签启用 canary 限制；没有从该路径发起预约操作。新版源码尚未安装至扩展。
工具明确拒绝访问 `chrome-extension://`，且禁止通过替代手段绕过；自动更新到此停止。
本地 Chromium 注入执行覆盖 v0.3.1 的 DOM、面板、存储与互斥，不能代替真实扩展 sandbox。

## 仍需推进的待办

1. **P0：新版 userscript 现场收口。** 在现有脚本中完整替换为工作区 v0.3.1，保留原脚本身份、
   GM 设置及 journal；不新建副本、不删除未决记录。普通 Chrome 单独人工登录后运行 readonly。
   Python 已关闭，两个路径须顺序验证；返回闭合摘要，不能将本地通过直接标成 sandbox 通过。
2. **P1：真实预约页准备与结果证据。** 目前仅获授权做真实只读；准备表单、最终提交仍需具体场景。
   待本机核对真实字段/DOM、脱敏夹具和匹配结果后，才实现现场结果识别/只读核对 adapter。
   当前没有已验证的 live adapter，最终点击仍必须 UNKNOWN 并由用户核对原站。
3. **P1：当前增量跨平台与交付。** 本轮需重新跑各平台 CI/frozen 验证后再考虑合并或发布；
   未推送、未合并、未打 tag。既有四平台成功记录仅覆盖旧版本；本机仍缺 Edge。
4. **P2：首次配置向导。** 在已实现的 profile 写回与本轮自检上，增加可选的医生/成员配置写回，
   减少重复输入；不自动猜就诊人或写默认诊疗资料。

科室 fallback 继续等待独立漏号证据。自动 CAPTCHA、代理、分布式协调不因本轮自检而立项。

## 最终验证与交付范围

基线全套为 680 passed、2 skipped、3 live deselected。2026-10-19 当时记录的执行命令：

```bash
LIVE_E2E=0 LIVE_BOOKING=0 uv run --locked --no-sync pytest -q -m 'not live' --browser chromium --tb=short
```

结果 **729 passed、2 skipped、3 deselected**，耗时 142.44 秒。两项 skip 为本机未安装 Edge，
三项 deselected 为显式排除的 live；14 项既有 naive appoint_time 迁移提示保持不变。
新增 49 项回归；包含真实本地 Chromium、跨语言合同、进程/标签互斥与隐私检查。
Node 的隐私/事务单元测试面板替身补齐实际 DOM 本来具有的 style 属性，原断言全部保留；
窗口边界与内容增长由真实 Chromium 的两个窗口尺寸独立验证，没有用这些替身证明布局。

Ruff、userscript 与 Node harness 语法、`git diff --check HEAD`、相关 Markdown 链接均通过。
实际运行 `main.py config.yaml --check` 返回 warning：本机浏览器、可自动发现的专用 profile、
无 pending 的 journal 均正常；目标/成员仍需人工选择，服务端会话保持 unverified。
没有为得到绿灯而启用真实预约或降低一次提交/未决阻断断言。

最初缺少锁定版本的 Chromium 缓存，已通过 Playwright 安装补齐；Python 3.11.5、Node 24.15.0、
Playwright 1.58.0、pytest 9.0.2。`uv.lock`、公开配置模板和打包源码没有改动。
当时将常规浏览器测试视为仅处理 synthetic route；2026-11-16 发现其中一项 302 重定向的
后续请求可能绕过路由，因此该记录不能证明整个测试过程完全离线。上面单列的现场只读不混入
该测试数量。

截至 2026-10-19 审核时，代码和测试保留在工作区，未创建提交、未推送、未合并、未发布。新版本
frozen 与各平台 CI 仍需在交付前执行。没有把本机配置、profile、journal、原始页面、认证资料或
响应加入仓库。

## 2026-11-16 工作区复核

复核时发现 Playwright 仅拦截了合成的 302 首次请求，后续重定向可访问真实站点；当次完整测试和
针对性复现中，少量合成目标页面 GET 到达站点。测试使用全新临时浏览器上下文，未加载用户 profile
或登录资料，也没有提交预约。已将浏览器测试上下文设为离线，同时保留路由提供的合成响应；回归测试
验证重定向无法连到本地测试服务器。目标变化测试改为合成页面加载时同步修改地址，不再依赖 302。

另补上提交保护与结果证据之间的边界：保护器拦截点击、状态丢失或无法读取时，已开始的事务继续保持
`OUTCOME_UNKNOWN`，不调用结果适配器；保护器完整且点击正常时仍可使用匹配的业务证据。状态因导航
丢失时也会保守保留未决记录。

使用本机与锁文件版本一致的环境执行
`LIVE_E2E=0 LIVE_BOOKING=0 .venv/bin/pytest -q -m 'not live' --browser chromium --tb=short`：
**734 passed、2 skipped、3 deselected**。两项 skip 为本机未安装 Edge，三项为排除的 live 测试；
14 条 naive 时间迁移提示仍在。Ruff、变更的 Python 文件格式检查、Node 语法检查和差异空白检查通过。
这是本机离线验证，不覆盖新版跨平台 CI、frozen 交付或真实站点的预约流程。
