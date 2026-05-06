# 160Grab

健康160自动挂号脚本，默认采用“手动登录接管 + 自动刷号/预约”的 Playwright 流程。

## 环境要求

- Python 3.11+
- uv (包管理)
- Chromium (Playwright 浏览器)

## 快速开始

```bash
uv sync
uv run playwright install chromium
cp config/example.yaml config.yaml
uv run python main.py config.yaml
```

`config.yaml` 只用于本地运行核心链路。默认模式下，不需要把账号、密码或 OCR 地址写进仓库；程序会打开登录页，由用户手动登录并导航到目标医生页。

## 配置说明

`config/example.yaml` 覆盖当前支持的核心字段：

- `auth.strategy`: 默认是 `manual`，程序会打开登录页并等待用户在目标医生页按 Enter 确认；`auto` 只保留占位，点击文字验证码尚未实现
- `browser.stealth`: 默认是 `true`，启动后会应用 `playwright-stealth` 补丁；如遇兼容性问题可以手动关闭
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
