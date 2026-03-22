# 91160 Manual-Takeover Handoff

## Why This File Exists

`references/91160.md` 仍然有价值，但它描述的是原 Java 版能力全景，不适合继续承载当前 Python/Playwright 版本已经变更后的目标。

这份 handoff 文档用于记录：

- 当前已经确认的最终目标
- 已完成的实现进度
- 剩余待办
- 继续工作的风险和建议

## Current Final Target

当前项目的目标，已经不再是“默认自动登录 + OCR”。

现在确认后的最终目标是：

1. 默认策略改成 **手动登录接管**
   - 程序启动后自动打开 `https://user.91160.com/login.html`
   - 用户自己完成登录
   - 用户自己导航到目标医生详情页
   - 只有当用户在终端按一次 Enter 后，程序才读取一次当前 URL
   - 程序不需要跟踪中间导航变化

2. 当前只支持 **医生详情页通道**
   - 支持的 URL 形态：
     `https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html`
   - 程序从这个 URL 一次性解析 `unit_id`、`dep_id`、`docid`
   - 排班轮询只走 `/guahao/v1/pc/sch/doctor`
   - 原科室排班页通道暂不实现，保留 TODO

3. 排班轮询仍然必须走 **浏览器上下文内 `page.evaluate(fetch)`**
   - 不回退到浏览器外部 `httpx` 轮询

4. 预约仍然走 **页面策略**
   - 只实现 `page` strategy
   - `fetch` strategy 仍然不实现

5. `member_id` 现在是 **可选配置**
   - 如果 `config.yaml` 里写了 `member_id`，程序会在登录后访问 `https://user.91160.com/member.html` 校验它是否存在
   - 如果没写，程序会访问 `member.html`，列出当前账号下的就诊人并让用户在终端选择
   - 不采用“让用户在预约页手动勾选成员，然后程序再去猜”的方案

6. `hours` 的语义仍然是 **精确匹配**
   - 但配置输入支持整点简写归一化：
     - `8-9` -> `08:00-09:00`
     - `18-18` -> `18:00-18:00`
   - 非法格式应报错并退出

7. 自动登录降级为 **fallback 骨架**
   - `auth.strategy = auto` 仍保留入口
   - 但 OCR 主流程已经不再是默认方案
   - 当前 `auto` 只保留 TODO：
     “实现请依次点击 x x x 三个字”的脚本能力

## Current Progress

下面这些已经做完了。

### 1. 配置和运行时契约

- `GrabConfig` 已改为手动登录优先模型
- `member_id` 已改为可选
- 增加了 `auth.strategy`
- `hours` 已支持归一化和格式校验

相关文件：

- `src/grab/models/schemas.py`
- `src/grab/utils/config_loader.py`
- `src/grab/utils/runtime.py`
- `config/example.yaml`

### 2. 默认登录流程已切换到手动接管

- `AuthService` 默认会打开 `https://user.91160.com/login.html`
- 不再默认走 OCR 登录
- `manual` 模式下只打印指引，不在这里阻塞等待 Enter
- 真正的 Enter 确认发生在读取当前医生页 URL 前

相关文件：

- `src/grab/services/auth.py`

### 3. 会话接管能力已实现

- 新增 `SessionCaptureService`
- 已支持：
  - 按 Enter 后读取当前 URL
  - 解析医生详情页 URL
  - 访问 `member.html`
  - 校验配置里的 `member_id`
  - 缺失时终端询问用户选择就诊人

相关文件：

- `src/grab/services/session.py`

### 4. 排班和预约主链路已改成运行时注入目标

- `ScheduleService` 已去掉默认双通道主路径，只保留医生通道
- `BookingService` / `PageBookingStrategy` 已改成在运行时接收 `target + member_id`
- `GrabRunner` 已改成：
  1. 手动登录
  2. Enter 后捕获目标医生页
  3. 解析/选择 `member_id`
  4. 等待预约时间
  5. 轮询医生通道
  6. 页面预约

相关文件：

- `src/grab/services/schedule.py`
- `src/grab/services/booking.py`
- `src/grab/core/runner.py`
- `main.py`

### 5. 文档和 live harness 已更新到手动模型

- README 已描述新的默认交互流程
- `config/example.yaml` 已改成手动模式示例
- `tests/e2e` 已迁移到手动登录模型，默认跳过

相关文件：

- `README.md`
- `config/example.yaml`
- `tests/e2e/conftest.py`
- `tests/e2e/test_live_flow.py`

## Verification Status

本次实现已经验证过：

- `uv run pytest -v`
  - 结果：`28 passed, 2 skipped`
- `uv run ruff check .`
  - 结果：`All checks passed!`

还做过一轮 CLI 交互冒烟：

- `uv run python main.py config/example.yaml`
- 已确认程序会：
  - 启动浏览器
  - 打开登录页
  - 打印“手动完成登录并导航到目标医生页”的提示
  - 在终端等待 Enter
- 在未导航到医生详情页就直接回车时，程序会明确报错：
  `Only doctor detail pages are supported for now`

这说明默认入口已经切到手动接管，不会再去走 OCR。

## Remaining Work

当前还没完成的工作，优先级从高到低如下。

### High Priority

1. 用真实站点做一次手工 smoke
   - 验证当前医生详情页 URL 解析是否真实可用
   - 验证 `member.html` 的 DOM 结构是否和当前解析逻辑一致
   - 验证 `/guahao/v1/pc/sch/doctor` 的真实返回结构

2. 用真实站点验证预约页提交流程
   - 校验页面表单字段名
   - 校验最终成功判定
   - 校验是否需要更多隐藏字段

### Medium Priority

3. 完成 `auto` fallback
   - 保持 `auth.strategy = auto`
   - 实现“请依次点击 x x x 三个字”验证码能力
   - 当前这里仍是 TODO

4. 决定是否恢复第二种排班通道
   - 当前只做了医生详情页通道
   - 科室排班页通道暂未实现

### Low Priority

5. 优化交互体验
   - 更好的提示文案
   - 更清晰的成员选择输出
   - 可选的配置持久化

## Known Risks

这些是下一个会话需要重点关注的真实站点风险。

1. 医生详情页 URL 形态如果变了，当前正则解析会失效
2. `member.html` 如果 DOM 结构变了，成员解析会失效
3. 排班接口 `/guahao/v1/pc/sch/doctor` 的真实 payload 可能与当前 fixture 不完全一致
4. 预约页当前只做了首版字段回填，真实页面可能还需要额外参数
5. `auto` 策略目前并不能工作，只是明确保留了 TODO

## Recommended Next Step

在新的会话里，优先做这件事：

1. 保持当前手动接管主链路不变
2. 用真实账号跑一次默认流程
3. 先只验证：
   - 医生详情页 URL 解析
   - `member.html` 成员解析
   - 医生排班接口
4. 如果这三项通过，再继续验证预约页字段和最终提交

不要先去扩第二种排班通道，也不要先去补自动登录。

## Files To Read First In A New Session

- `docs/superpowers/handoffs/2026-03-23-91160-manual-takeover.md`
- `docs/superpowers/handoffs/2026-03-23-91160-manual-takeover-prompt.md`
- `README.md`
- `main.py`
- `src/grab/services/auth.py`
- `src/grab/services/session.py`
- `src/grab/services/schedule.py`
- `src/grab/services/booking.py`
- `tests/services/test_session.py`
- `tests/core/test_runner.py`
