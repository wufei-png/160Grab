# 本机人工 canary 与最小脱敏夹具

S06 工具、S09 文档收口，2026-10-01。只在本机人工执行；CI 不运行 live。
Python 与 userscript 的现场证据分别记录，不能用一个 profile/路径的登录或 synthetic 回归代替另一路径。
未决提交先在原站核对，禁止混跑两路径、多个 browser profile 或多机器。

## 层级与门槛

| 层级 | 允许的自动操作 | 人工确认 | 最终/follow-up |
|---|---|---|---|
| readonly | 读取当前医生页、补全 docid-only 目标、有限查询号源 | 已登录并停留目标页的 ready | 0；不查选就诊人、不打开预约页、不改表单 |
| prepare | 指定场景的表单准备与 readiness | ready + 本次目标/就诊人/日期时段、已有/显式配置字段的 prepare 确认 | 0；无需产品自动提交授权 |
| submit | 上述准备，加持久事务保护的一次最终动作 | ready + prepare + 本次精确 schedule/时段值的 submit 确认，再通过产品 consent | 必须显式 submit、LIVE_E2E=1、LIVE_BOOKING=1、具体场景批准同时成立；无已验证 follow-up 自动动作 |

普通产品 `auto`、有效 consent 不能代替 canary 层级和场景批准。默认最多 3 次逻辑号源查询、
120 秒 run 预算，参数上限分别 10 次/600 秒；轮询至少间隔 3 秒并保留配置的更慢间隔。
人工 ready/prepare/submit 等待计入 run 预算。Python 浏览器启动另有 30 秒、初始首页导航 20 秒预算。
JS 原生确认框可能暂停浏览器 timer；恢复后以单调 deadline 复核，超时不再填表或提交。
人工可以随时拒绝、Stop 或关闭页面；迟到 continuation 无权恢复操作。
无号源/超时是 inconclusive；缺场景、登录态、readiness、profile、互斥或 pending 是 blocker。
`submit_calls`/`submitCalls` 是提交入口调用数，**不等于确认成功或实际 click 数**。
没有现场结果 adapter 时，已进入 click 边界的结果仍是 OUTCOME_UNKNOWN，须查原站预约记录。

## Python 本机操作

使用既有 **160Grab 专用 profile**，不复制 Chrome 认证状态，不自动选/创建替代 profile。
先退出其它 Python/userscript 运行。在本机交互终端设置以下变量；值仅为本次运行输入，
不要写进仓库、粘到普通日志/CI 或录屏。不要对含个人交互提示的终端使用 tee/输出归档。

```bash
export LIVE_E2E=1
export LIVE_LEVEL=readonly
export LIVE_PROFILE="<专用 profile 名>"
export LIVE_DOCTOR_ID="<医生 ID>"
export LIVE_DATE="<YYYY-MM-DD>"
# 可选：LIVE_PROFILES_ROOT；默认 ~/.160grab/browser-profiles
# 可选：LIVE_MAX_POLLS=3；LIVE_TIMEOUT_SECONDS=120
uv run --locked pytest -s -q tests/e2e/test_live_flow.py
```

浏览器打开后人工登录、完成 CAPTCHA，进入目标医生页，再输入 `READY`。
完整 URL 或 docid-only URL 均可；未补全 unit/department 或目标不符则停止，不发送排班请求。
本机全工具 leader 在浏览器启动前获取，live 测试不使用离线测试的临时锁目录。
一次命令只启动显式选择的层级，其余参数化用例跳过。

prepare 另外指定 `LIVE_MEMBER_ID`、`LIVE_HOURS`（单个精确范围，如 `09:00-09:30`），
设置 `LIVE_LEVEL=prepare`。允许填写的真实配置值可通过 `LIVE_CARD`、`LIVE_DISEASE`、
`LIVE_ADDRESS_PROVINCE/CITY/AREA/DETAIL` 提供；默认空。已有值保留，冲突/缺必填信息停止。
在显示本次目标、就诊人、日期/时段及 schedule 后输入 `PREPARE` 才选成员/时段和填写字段。
不自动接受站点协议，不提交、不点击后续控件。

submit 只有在人工已针对**本次具体场景**批准时设置 `LIVE_LEVEL=submit` 和 `LIVE_BOOKING=1`；
再次显示最终 schedule 与时段值后输入 `SUBMIT`。该具体确认接入本次页面/运行的产品授权，
仍复核 consent、readiness、owner 和 durable attempt，再执行一次动作。
不能把代码实现请求或已有产品 consent 当作具体预约批准。

当前 ready 使用 POSIX 非阻塞交互 stdin；Windows/捕获 stdin/非交互运行记录 blocker。
退出后浏览器关闭，但 journal 保留；OUTCOME_UNKNOWN 不允许清 journal 后重跑。

## userscript 本机操作

在 Tampermonkey 面板保存具体目标、日期、时段、就诊人及允许使用的真实字段；先关闭 Auto Start。
展开「本机人工 canary」，点击「启用 canary 限制」。限制记录只含固定标记，
同标签页的导航、刷新、Start/reset 都不能解除；场景批准只在当前 document 内存中有效。
结束后关闭标签页，保留未决 journal。不复制 profile，不启用另一运行路径。

readonly 停留医生页；选择 readonly，点击「运行并人工确认」，人工 ready 后有限查询并返回安全摘要。
prepare/submit 由人工核对医生和排班，**在已启用限制的同一标签页**打开对应预约链接；
在面板选层级，重新确认具体场景。prepare 零提交；submit 还须在本次运行勾选
`LIVE_E2E=1`、`LIVE_BOOKING=1` 并接受具体 submit 确认及产品 consent。
这是 JS 本机开关，不能用 Python 进程环境变量替代。不自动换号源、导航/follow-up 或恢复登录。

面板入口在 userscript sandbox 内执行，不依赖向页面导出特权桥。
本地 Chromium 测了 DOM/面板事件、Web Locks、reload 与 canary；真实 Tampermonkey sandbox 尚无现场证据。

### 现场验证需要人工辅助时

先在现有 Chrome 地址栏手动打开站点，记录能否正常访问。`ERR_BLOCKED_BY_CLIENT` 仅表示客户端
拦截了请求，不能仅凭错误码认定具体扩展；人工也受阻时先核对错误页或阻拦工具的明确记录。
若人工可访问而代理操作受阻，由人工执行下面的本机面板步骤并返回安全摘要，无需改变代理访问策略。
扩展管理页 `chrome://extensions/` 由人工打开；自动化工具拒绝该协议不代表 Tampermonkey 安装失败。

1. 确认 Tampermonkey 来自官方扩展商店并启用，允许其在 `https://www.91160.com/` 运行。
   Chrome 138+ 在 Tampermonkey「详情」启用「允许用户脚本」；旧版依照官方说明使用 Developer mode。
2. 已有本脚本时编辑原脚本，完整替换为当前工作区
   [userscript](../userscripts/91160-doctor-page-poller.user.js) 后保存，不新建副本或删除重装，保留已有设置和未决 journal。
   没有原脚本时才新建。若旧脚本正在运行，先在原页面 Stop 并启用 canary 限制，再更新。
   当前版本号仍为 0.3.0，不能仅凭版本号判断已包含审核修复；内容应包含 `bookingSettingsMatch`。
3. 手动登录、完成 CAPTCHA，停留目标医生页。面板 Settings →「筛选时间」关闭 Auto Start；
   Appointment From 填明确验证日期，Submit mode 选择 Manual confirm，点击 Save Settings。
   readonly 无需填写就诊人、卡号、病情或地址。关闭其它预约运行；Python 路径与 userscript 顺序验证。
   验证期间保持该页可见。其它自动化任务操作同一 Chrome 时，先等其结束再验证；
   独立窗口不能保证工具连接或页面可见性不受干扰，页面隐藏仍会触发安全停止。
4. 展开「本机人工 canary」，点击「启用 canary 限制」，选 readonly，再点击「运行并人工确认」。
   仅确认 ready；保留两个 LIVE 复选框未选。默认最多三次查询、120 秒，最终动作必须为零。
5. 返回本地弹窗的 `status`、`level`、`polls`、`submitCalls`、`state`，以及脚本是否由 Tampermonkey 加载。
   `submitCalls` 必须为 0；无号源或 blocker 不是成功现场证据。原始 HTML、cookie 和个人表单值留在本机。
   失锁时摘要为 `leader_blocked`，保留已发起查询的计数；journal 异常仍返回 `OUTCOME_UNKNOWN`，
   必须在原站核对，不能重置 journal 后重跑。

允许用户脚本的步骤依据 [Chrome 官方说明](https://developer.chrome.com/docs/extensions/reference/api/userScripts)。
完成一条路径并关闭其浏览器后，再运行另一条路径的 readonly；prepare/submit 仍遵守上文具体场景门槛。

## 夹具转换与刷新

原始资料默认不保存。若现场确需原始 HTML，遵守 [数据合同](security-and-privacy.md)
的本机原始 snapshot 双开关、私有目录与 24 小时清理要求；不要用原始文件作 fixture，不上传。
canary harness 本身不自动打开敏感 snapshot 开关。截图不能输入 converter。

`grab.canary.fixtures` 重建最小静态 booking DOM/snapshot 或已知 normalized schedule JSON。
替换成员/排班/卡号/病情/地区/标签等值，保留空值、关联相等性、日期冲突、控件数量/可操作性/
checked/selected、必填 readiness。移除 script（含嵌入 JSON）、URL、事件 handler 及自由正文；
仅保留严格时间范围，不保留原日期。sch_data 中只提取与唯一 schedule 关联的日期，再以脱敏日期重建。
非法 sch_data/jzdate 日期、未知表单字段/JSON 键拒绝导出。已知认证字段丢弃，工具自身面板配置不导出。

```bash
uv run --locked python -m grab.canary.fixtures booking \
  "$CANARY_PRIVATE_INPUT" "$CANARY_SANITIZED_OUTPUT" \
  --captured-on 2026-10-01 --level prepare --path python --source live
# schedule 模式接受 {data:{schedules:[...]}}，可带 result_code=0|1。
```

输入/输出都采用 no-follow 私有文件入口；输出文件必须不存在，失败只给固定分类，不打印原文。
输出 envelope 必含版本、采集日期、层级、路径、live/synthetic、覆盖范围及结构 fingerprint。
fingerprint 只计算 schema 形状，不散列个人值。HTML 是静态 parser/readiness 夹具，
`raw_behavior_preserved=false`；站点脚本、异步级联/网络效果须另写 reviewed synthetic harness。
不能据此推断真实提交成功或认证效果。非 normalized provider JSON（如 sch 树）目前拒绝导出，
需在本机核对新 schema 后扩展显式 converter，不能用删未知键的方法伪造成功刷新。

导出后检查 envelope、零明文注入、Python/JS 解析及本地 Chromium parity，再将**明确列出的脱敏输出**
加入 Git。真实 fixture 不得抹掉此前 synthetic 覆盖；缺现场输入时保留已有夹具并记录刷新 blocker。
仓库 `tests/canary/fixtures/booking.minimal.v1.json` 由已有 synthetic 场景转换，source=synthetic，
用于验证实际 CLI 导出和两适配 parity，不属于现场证据。

## 本次证据（2026-10-01）

| 路径 | 层级/覆盖 | 现场状态 |
|---|---|---|
| Python | 本地 Chromium：docid-only→完整目标→查询；readonly/prepare 零最终动作；有效产品 grant 仍受层级保护；人工拒绝/预算/pending | 未运行 live：未提供明确 profile、医生、日期/时段/就诊人及具体批准 |
| userscript | 本地 Chromium：readonly 无成员/表单修改；prepare/submit gates、次数/ready timeout、UNKNOWN blocker、reload、实际 panel 事件 | 未运行 live：未提供当前目标/场景与 Tampermonkey 现场输入 |
| converter | synthetic 注入/unknown schema/link 拒绝、结构 fingerprint、静态状态与 Chromium 双适配 parity | 未刷新真实夹具；无现场结果证据，未增加 live adapter |

共享全套检查与 review 结果见 [实施计划完成记录](implementation-plan-2026-09-30.md#完成记录)。
后续每次现场记录路径、日期、层级、预算/计数、安全终态、覆盖与 blocker；不记录个人值、URL query、
原始 response/HTML。真实提交证据只能用匹配本次场景的已验证业务结果，弱页面变化仍为 UNKNOWN。

## S09 保留的现场 gate

本轮仅运行 offline/local Chromium，没有执行上述现场命令或真实预约。Python 与 userscript
仍分别缺专用 profile/当前目标/具体场景批准和真实 schema/结果 adapter/fixture 刷新证据；
未决记录不能因集成验收或文档迁移被标为已核对。Tampermonkey 扩展 sandbox、Windows ready
仍未验证。现场或远端 CI 必须记录新的实际运行，不能继承本地 synthetic 结果。

CI/release 显式设 LIVE_E2E=0/LIVE_BOOKING=0 并用 `-m "not live"` 排除现场测试，
contract/本地 Chromium 和 offline canary 照常执行。全部验证与发布边界见
[集成验收索引](integration-acceptance.md)；文档中的人工现场入口不构成场景批准。
