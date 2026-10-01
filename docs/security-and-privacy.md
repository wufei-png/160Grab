# 日志、外发、快照和会话数据边界

版本 1，2026-09-30；S09 文档收口于 2026-10-01。适用于 Python 和 Tampermonkey。
S02 数据边界、S03 最小 attempt journal 和 S06 fail-closed fixture converter 已实现；
[集成验收索引](integration-acceptance.md) 区分本地代码证据与尚未执行的现场/平台 gate。

## 普通输出

普通输出使用封闭字段与值白名单，未知字段丢弃，不通过递归正则脱敏放行原文。
JSONL/reporter 的 event、phase、level/state/error 分类必须是已注册枚举；run/attempt/booking 引用
只接受应用生成的 12/32 位十六进制不透明引用，不应把真实 ID 编码成引用。
允许严格类型的计数、延迟、readiness 布尔。嵌套对象、自由 message、selector/value、异常正文、
traceback、服务端文本、页面 title/正文、URL 与 HTML 都不进入普通输出。

姓名、member id、证件、phone、病情、地址、card、cookie value、user_key/access_hash 和认证 header
不得作为日志数据。Python 直接日志只能使用固定消息，禁止格式化运行值和 exception traceback。
登录诊断只收集票据是否存在、验证码 iframe 计数，不再打印站点错误正文或 URL。
真实就诊人选择提示、浏览器表单和本地配置属于用户交互/运行输入，不能转发到日志或通知。

JS console、panel 日志和持久摘要共享固定消息及 typed detail projection。读取 runtime state 时
立即丢弃没有日志 schema 的旧日志/摘要；已有 schema 的条目也重新投影。7 天外、非法及未来时间
条目删除，新日志另有 maxEntries 上限。迁移不清 pendingBooking、submittingBooking 或提交计数。
runtime 与日志分离；当前 durable journal 独立保留 pending，Stop/reset 和日志迁移都不能清除它。

## 通知

webhook body 只发送 event、不透明 run_id/attempt_id、phase、severity、固定安全 message。
不发送整个 event、data、URL、title/subtitle、异常或用户配置；没有敏感外发开关。
桌面通知使用固定 `160Grab` 标题及同一固定消息。HTTP destination/headers 仅用于传输配置，
不得进入 body 或错误输出。失败只产生 `notification_delivery_failed` 分类事件；不改变预约结果，
也不触发预约重试。通知 provider 与 reporter 都保护各自的入口。

## 本地文件与 profile

新建 POSIX 专用目录为 0700、普通文件为 0600。Chromium 启动继承 0077 umask，限制它新建的
profile 文件；既有文件不递归 chmod、不复制/导出 profile。profile 名仍须通过原有校验，
选择/读取时拒绝 linked directory/marker。普通用户配置写回与打包模板创建也经过私有文件入口。

POSIX 写入、读取和清理逐级 `O_NOFOLLOW` 打开目录，使用目录 descriptor 锚定操作；拒绝文件
symlink、非普通文件与 hardlink。目录替换不会让应用的文件写入/删除走到链接目标。
Chromium 自身接受 pathname，因此仍应使用本用户控制的专用目录，避免其它进程同时改动 profile。

Windows 会拒绝观察到的 symlink/reparse point，但 mode 位不是 ACL，目录 descriptor 防替换能力
也不等同于 POSIX。应放在当前 Windows 用户独占的目录；S02 未在 Windows 实测 ACL、junction
或 Chromium 文件权限，不把 POSIX 验证称为跨平台权限证明。

## debug 与原始快照

默认 `logging.include_sensitive_debug: false`。仅设置 `GRAB_DEBUG_DIR` 时，debug 只生成安全 JSON：
事件种类、HTTP status、布尔 readiness、cookie 数量及 secure/httpOnly/session 布尔 metadata。
不保留 cookie 名/域/path/value、URL、title、script、页面正文或异常错误文本。
文件名使用固定应用前缀、UTC 时间及随机引用，不包含调用方 label。

原始 HTML/截图只有同时设置以下两项且目录匹配时才生成：

```yaml
logging:
  include_sensitive_debug: true
```

```bash
export GRAB_DEBUG_DIR="$HOME/.160grab/debug"
```

原始文件仅落在该本地私有目录；不发送 webhook、桌面通知或上传，不可直接提交 Git。
敏感 opt-in 不放宽 metadata、日志或通知。截图先取 bytes，再通过私有文件入口写入；
失败仅记录固定布尔结果。浏览器 smoke 没有敏感配置授权，保持原始快照关闭。

## retention 与 dry-run

普通 JSONL 保留窗口 7 天；debug JSON 与原始 HTML/截图为 24 小时。以文件名中的 UTC 创建时间
判断，恰好边界保留，超过窗口清理。普通运行初始化清日志，浏览器启动和每次快照清 debug。
程序没有在退出后继续运行的后台清理服务；不用工具时应在 24 小时内删除原始快照，或运行清理命令。

```bash
uv run --locked python main.py config.yaml --cleanup-data --dry-run
uv run --locked python main.py config.yaml --cleanup-data
```

debug 清理目录来自 `GRAB_DEBUG_DIR`，不要求打开敏感快照开关；清理不会启动浏览器或选择/读取 profile。
输出只有 eligible/deleted/skipped 计数。dry-run 不写文件或修改权限。
仅清根目录中符合应用命名格式的常规单链接文件；不递归、不处理未知文件、目录、symlink/hardlink。
旧 JSONL 的固定日期/run 命名格式纳入 7 天清理；旧原始快照没有可靠 ownership schema，保留并需
人工核对删除。显式 protected path、profiles 根及带 profile marker 的目录/子目录受保护；attempt
journal/未决记录不属于输出命名空间，不受 retention 影响，不得在以后迁移中复用输出前缀。

## fixture 和认证状态

不提供复制认证状态、profile 或原始响应的导出路径。仓库 fixture 应为手写 synthetic 场景，
或者通过 S06 的 fail-closed converter 导出脱敏最小 DOM/JSON。真实导出须保留 schema 和必要行为，
替换个人与会话值，处理 script、嵌入 JSON、URL 和事件正文；未知敏感字段必须拒绝导出。
S06 converter 已实现但尚未取得真实输入/刷新现场 fixture；原始 HTML/截图仍不是安全 fixture。
使用流程与 source/日期/层级/fingerprint/覆盖元数据见 [现场 canary](live-canary.md#夹具转换与刷新)。

## 验证边界

注入测试覆盖 nested field/message/exception/URL、JS detail/旧日志、通知失败、HTML script 与 cookie；
逐 sink 检查 synthetic canary 零明文命中。真实本地 Chromium 验证安全 debug/JS 输出，
POSIX 测试覆盖 mode、symlink/hardlink、目录替换清理、retention 边界和 dry-run。
本地验证不代表真实预约、站点 session、远端 CI、Windows/Linux 或 frozen/release 验证。

S09 跨模块 Chromium 回归在真实值准备/提交/恢复过程中注入 synthetic 姓名、证件、phone、
member/token/card/address，检查直接日志、JSONL、通知 body/失败分支及 journal 无明文；
JS 检查 console、持久日志和 journal（设置/表单/直接人工提示不归普通输出）。原始 debug 双开关、
retention/link/权限和 converter 拒绝未知 schema 的独立回归仍强制执行。没有归档私有输入、
用户 profile、原始 response/HTML；应用输出不纳入 Git。
