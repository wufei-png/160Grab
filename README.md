# 160Grab

健康160自动挂号脚本，默认采用“手动登录接管 + 自动刷号/预约”的 Playwright 流程。

## 项目定位

本项目的参考项目是 [pengpan/91160-cli](https://github.com/pengpan/91160-cli)。

但 `160Grab` 并不是对 `91160-cli` 的逐行移植，也不是简单把 Java 改写成 Python。它的核心目标更聚焦：

- 保留“刷号 + 条件过滤 + 预约提交”这条真正有价值的主链路
- 把高风险、最像真人的环节交还给用户自己处理，例如登录、切换账号、进入目标医生页
- 把重复、耗时、容易出错的环节交给脚本，例如轮询排班、筛选时段、打开预约页、回填表单、提交重试

换句话说，这个项目的设计重点不是“尽可能全自动”，而是“尽量像真实浏览器用户，同时保留脚本在重复操作上的效率优势”。

## 参考项目与演进思路

`91160-cli` 很完整，覆盖了 `init/register` 命令、`config.properties` 生成、OCR 打码、代理、双通道刷号、Docker 打包等一整套 CLI 工作流。

`160Grab` 继承的是它对业务链路的理解，但在实现策略上主动做了取舍：

- 运行时从 `Java + OkHttp/Retrofit` 切到 `Python + Playwright`
- 默认登录模型从“账号密码 + OCR 自动登录”切到“手动登录接管”
- 核心请求尽量放回真实浏览器上下文，而不是让浏览器外部 HTTP 客户端长期独立刷接口
- 当前优先打磨医生详情页主链路，而不是先把初始化工具、代理池、通知系统全部补齐

这个演进方向对应的原始设计想法很明确：**用 Playwright 和真实浏览器会话降低反爬风险，用人工操作接管最敏感的部分，再用脚本完成重复刷号和提交流程。**

## 与 91160-cli 的差异

| 维度 | `91160-cli` | `160Grab` |
| --- | --- | --- |
| 技术栈 | Java 8 + Spring + OkHttp/Retrofit | Python 3.11 + Playwright |
| 默认登录方式 | 自动登录，依赖验证码/OCR 能力 | 手动登录接管，`auto` 入口暂保留为 TODO |
| 运行介质 | 以 CLI + HTTP 请求为主 | 以真实 Chromium 浏览器会话为主 |
| 刷号方式 | 支持通道 1 / 通道 2 轮询，可配代理 | 当前只聚焦医生详情页通道，排班查询尽量走浏览器上下文内请求 |
| 抗风控思路 | 代理、重试、配置驱动 | `playwright-stealth`、持久化 profile、人工登录、页面内请求、随机节流和退避 |
| 配置体验 | `init` 生成 `config.properties`，需要先准备较多 ID | 手写 `config.yaml`，并通过“用户手动打开医生详情页”减少前置配置负担 |
| 预约执行 | 以 CLI 主流程发起 | 先打开真实预约页，再按页面结构回填和提交 |
| 当前目标范围 | 功能面更宽，含 OCR / 代理 / Docker / 初始化 | 主链路更窄，但更强调浏览器真实会话和人工接管 |

这也意味着两者不是简单的新旧替代关系：

- 如果你更看重“完整 CLI 工具箱”，`91160-cli` 的覆盖面更广
- 如果你更看重“真人参与 + 真实浏览器 + 尽量贴近页面行为”，`160Grab` 更适合继续迭代

## 设计思路

当前版本的核心设计原则如下：

1. **浏览器优先，而不是浏览器外强行模拟。**  
   登录、Cookie、LocalStorage、页面跳转、预约提交都尽量留在真实 Chromium 环境中完成，减少“脚本会话”和“真人会话”割裂带来的不稳定。

2. **把最敏感的步骤交给人。**  
   用户自己登录、自己进入目标医生详情页，脚本只在用户确认后读取一次当前 URL 并接管后续流程。这样既减少了自动登录和页面导航的脆弱性，也更符合“人工操作结合脚本挂号”的初衷。

3. **把最重复的步骤交给脚本。**  
   一旦目标医生页和就诊人确定，脚本负责轮询、筛选、打开预约页、选择时段、回填表单和失败重试，避免手工高频刷新。

4. **抗风控不是单点技巧，而是整条链路的组合。**  
   这里只把 `playwright-stealth` 当作底层补丁之一；真正有意义的是“持久化 profile + 真实浏览器上下文 + 人工登录 + 页面内请求 + 随机节流 + 访问过多冷却退避”一起工作。

## 使用亮点

- **手动登录接管**：默认不要求把账号、密码、OCR 服务地址写进仓库配置，敏感步骤留给用户自己完成
- **持久化浏览器 profile**：可以长期复用登录状态、Cookie 和常用浏览器环境，减少每次运行的准备成本
- **医生详情页直接接管**：你只需要手动打开目标医生页并按一次 Enter，脚本就能从当前 URL 提取目标信息
- **浏览器上下文内刷号**：排班查询尽量复用真实页面会话，避免把核心轮询完全退回浏览器外部
- **页面级预约提交**：命中条件后打开真实预约页，按页面中的真实表单结构选择时段并提交
- **成员自动校验/选择**：未配置 `member_id` 时，程序会自动读取 `member.html` 并提示你选择当前账号下的就诊人
- **节流与退避可配置**：`sleep_time`、`page_action_sleep_time`、`booking_retry_sleep_time`、`rate_limit_sleep_time` 可以分别控制轮询、页面动作、重试和风控冷却
- **调试证据可落盘**：通过 `GRAB_DEBUG_DIR` 可以保存 HTML、截图和最近的页面事件，便于定位真实站点变化

## 后续改进

结合 `91160-cli` 的成熟能力、当前代码结构和已有 handoff 文档，下一阶段最值得做的并不是“把所有缺失功能一次补齐”，而是按下面的顺序推进：

- 先建立真实站点 smoke 基线，并把医生页、`member.html`、排班接口、预约页的真实变化固化回测试夹具
- 在当前持久化 profile 路线之上补 `channel="chrome"` 一类 branded browser 支持，优先提升真实浏览器一致性
- 借鉴 `91160-cli` 的 `init` 体验，但只做适配当前手动接管模型的轻量写回助手，而不是重做一整套 ID 初始化系统
- 增加成功通知、结构化运行日志和更清晰的诊断输出，提升长时间刷号时的可用性
- 仅在前面几项稳定后，再考虑把 `91160-cli` 的第二刷号通道思路作为 fallback 引回当前架构

更详细的优先级、原因和取舍见 [docs/future-improvements.md](/Users/wufei2/github.com/wufei-png/160Grab/docs/future-improvements.md)。

## 环境要求

- Python 3.11+
- uv (包管理)
- Chromium (Playwright 浏览器)

## 快速开始

```bash
uv sync
uv run playwright install chromium
cp config/example.yaml config.yaml
uv run python main.py config.yaml --create-profile
uv run python main.py config.yaml
```

`config.yaml` 只用于本地运行核心链路。默认模式下，不需要把账号、密码或 OCR 地址写进仓库；程序会优先复用持久化浏览器 profile，然后打开登录页，由用户手动登录并导航到目标医生页。

## 配置说明

`config/example.yaml` 覆盖当前支持的核心字段：

- `auth.strategy`: 默认是 `manual`，程序会打开登录页并等待用户在目标医生页按 Enter 确认；`auto` 只保留占位，点击文字验证码尚未实现
- `browser.launch_persistent_context`: 默认是 `true`，主链路优先复用持久化 profile；设为 `false` 时退回旧的临时浏览器上下文
- `browser.profile_name`: 可选；为空时会自动检测唯一 profile，或在多 profile 情况下提示你选择
- `browser.profiles_root_dir`: profile 根目录，默认是 `~/.160grab/browser-profiles`
- `browser.stealth`: 默认是 `true`，启动后会应用 `playwright-stealth` 补丁；如遇兼容性问题可以手动关闭
- `logging.jsonl_dir`: 结构化运行事件日志目录，默认是 `~/.160grab/logs`
- `logging.heartbeat_interval_seconds`: 长时间刷号时输出轮询心跳摘要的间隔秒数
- `notifications.desktop`: 是否启用桌面通知；会自动检测当前系统并在 Windows / macOS 上尝试发送本地通知
- `notifications.rate_limit_threshold`: 连续命中限频多少次后触发“持续限频”通知
- `notifications.webhook.url`: 可选；配置后会在成功、致命失败、持续限频时发送 JSON webhook
- `notifications.webhook.timeout_seconds` / `notifications.webhook.headers`: webhook 超时和请求头配置
- 配置读写统一走 `ruamel.yaml`，其中 profile 回写会尽量保留原注释和格式
- `member_id`: 可选；若留空，程序会在登录后读取 `member.html`，列出当前账号下的就诊人并让用户选择
- `doctor_ids` / `weeks` / `days` / `hours`: 刷号过滤条件，留空表示不限制
- `sleep_time`: 刷号间隔，支持固定值或范围值，例如 `3000`、`3000-5000`
- `page_action_sleep_time`: 页面动作间隔，用于打开预约页、点击提交前增加随机停顿
- `booking_retry_sleep_time`: 同一号源重试间隔，避免 `goto/click` 短时间连续突发
- `rate_limit_sleep_time`: 命中“访问次数过多”提示后的冷却时间
- `hours` 支持整点/半小时区间写法，例如 `08:00-08:30`、`8-9`、`9.5-10`、`9:30-10`
- `hours` 会在预约页按真实可约时间点做区间匹配；例如 `9-19` 会命中 `09:00-09:30`、`09:30-10:00` 等时间点，并优先提交第一个匹配项
- `enable_appoint` / `appoint_time`: 是否等待到指定时间再开始刷号
- `booking_strategy`: 首版固定为 `page`

## Profile 创建

首次使用持久化 profile 时，先运行：

```bash
uv run python main.py config.yaml --create-profile
```

也可以显式指定 profile 名：

```bash
uv run python main.py config.yaml --create-profile --profile-name profile_1
```

create-profile 流程会：

- 创建 profile 目录和 marker 文件
- 用 `launch_persistent_context` 打开该 profile
- 默认打开空白页，供你自行做少量暖机
- 不强制要求登录 160，也不建议把登录 Chrome 账号当作必要步骤

如果直接运行主程序且当前机器还没有任何 profile，程序现在会自动创建一个 `profile_1` 并继续主流程，不再因为“缺少 profile”直接退出；`--create-profile` 仍然适合想先单独暖机的人。

## 默认交互流程

1. 程序打开 `https://user.91160.com/login.html`
2. 用户手动完成登录
3. 用户手动导航到支持的医生详情页，例如：
   `https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html`
4. 用户回到终端按 Enter
5. 程序只在这一刻读取一次当前 URL，提取 `unit_id` / `dep_id` / `docid`
6. 程序读取 `https://user.91160.com/member.html` 校验或选择就诊人
7. 程序开始浏览器上下文内刷号，并在命中条件后打开预约页和提交

当前版本的节流策略：

- 刷号轮询继续使用 `sleep_time`
- 打开预约页、点击提交前会插入 `page_action_sleep_time` 随机停顿
- 同一号源重试失败后会等待 `booking_retry_sleep_time`
- 如果接口或页面包含“您单位时间内访问次数过多”等提示，会触发 `rate_limit_sleep_time` 冷却退避

当前只支持医生详情页通道；科室排班页通道仍是 TODO。

## 浏览器调试

如果登录后页面没有跳转到医生详情页，可以把页面证据落盘，方便直接看真实 DOM 和截图：

```bash
GRAB_DEBUG_DIR=artifacts/browser-debug uv run python main.py config.yaml
```

当脚本在非医生详情页继续等待时，会自动保存：

- 当前 URL 和标题
- 页面 HTML
- 全页截图
- 最近的 console / pageerror / requestfailed 事件

除页面快照外，程序现在还会在 `logging.jsonl_dir` 下写入结构化 JSONL 运行事件，便于回放一次运行中的关键阶段、限频命中、预约失败诊断和通知投递结果。

## 打包与发布

仓库现在提供了基于 PyInstaller 的跨平台打包入口，目标是保留当前“终端交互 + headed Chromium”的运行方式，同时让目标机器不需要预装 Python。

手动构建：

```bash
./packaging/build-macos.sh
```

Windows PowerShell：

```powershell
./packaging/build-windows.ps1
```

构建脚本会先把 Chromium 下载到独立 staging 目录，再在冻结完成后拷回最终 bundle，避免 macOS 上 PyInstaller 处理 Chromium 内部 app bundle 时触发签名冲突。

构建产物会输出到 `dist/release/`，其中包含：

- `160Grab-<platform>-<arch>.zip`
- 解压后的同名目录
- 对应的 `.sha256` 校验文件

校验 zip 完整性时，可以把本地计算出的 SHA-256 与同名 `.sha256` 文件中的第一列进行比对，例如：

- macOS: `shasum -a 256 dist/release/160Grab-macos-arm64.zip`
- Linux: `sha256sum dist/release/160Grab-macos-arm64.zip`
- Windows PowerShell: `(Get-FileHash .\dist\release\160Grab-windows-x64.zip -Algorithm SHA256).Hash`

发布流水线：

- `.github/workflows/ci.yml` 在 `push` / `pull_request` 时执行 `uv sync --extra dev` 和 `uv run pytest -q`
- `.github/workflows/release.yml` 在推送 `v*` tag 或手动触发时构建 Windows/macOS 发布包，并通过 `gh release create` 发布到 GitHub Releases

## Live E2E

live E2E 现在也是手动登录模型：浏览器会打开登录页，用户完成登录并导航到目标医生页后，在 pytest 所在终端按 Enter。默认允许真实排班查询、刷号、打开预约页和填写表单；只有 `LIVE_BOOKING=1` 时才会点击最终提交。

必填环境变量：

- `LIVE_E2E=1`

可选环境变量：

- `LIVE_DOCTOR_IDS`
- `LIVE_WEEKS`
- `LIVE_DAYS`
- `LIVE_HOURS`
- `LIVE_BRUSH_START_DATE`
- `LIVE_SLEEP_TIME`
- `LIVE_MEMBER_ID`
- `LIVE_BOOKING`

live E2E 侧未单独提供 `page_action_sleep_time`、`booking_retry_sleep_time`、`rate_limit_sleep_time` 的环境变量映射，沿用 `GrabConfig` 的默认值；若要在真实浏览器里细调这些间隔，请使用 `main.py` 与本地 `config.yaml`。

运行命令：

```bash
LIVE_E2E=1 uv run pytest tests/e2e/test_live_flow.py -v -m live
LIVE_E2E=1 LIVE_BOOKING=0 uv run pytest tests/e2e/test_live_flow.py::test_live_flow_submits_only_when_live_booking_enabled -v -m live
```

## 项目结构

```
src/grab/
├── browser/   # Playwright 客户端和浏览器内 fetch
├── core/      # runner 和定时等待
├── models/    # 配置和领域模型
├── services/  # 手动登录接管、排班、预约
└── utils/     # 配置加载、访问频率提示解析与运行时工具
```
