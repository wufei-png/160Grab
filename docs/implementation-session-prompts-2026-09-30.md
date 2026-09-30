# 160Grab 分会话实现提示词

整体设计已于 2026-09-30 确认。按 S01–S09 顺序，每次将对应文件的完整段落复制到新会话。
提示词只负责定位任务；前置条件、核心决策、验收与 review 规则均在
[实施计划](implementation-plan-2026-09-30.md)。每个会话用 `implement-in-stages` 实现，最后用 `delegated-change-review` review。

每份提示词有仓库副本，以及 `/tmp/160grab-session-prompts/` 下的独立副本。

| 会话 | 独立提示词 | /tmp 副本 |
|---|---|---|
| S01「契约、离线基础与产品入口纠偏」 | [S01.md](implementation-prompts/S01.md) | [S01.md](/tmp/160grab-session-prompts/S01.md) |
| S02「日志、外发、快照和会话数据边界」 | [S02.md](implementation-prompts/S02.md) | [S02.md](/tmp/160grab-session-prompts/S02.md) |
| S03「持久授权与单次提交事务」 | [S03.md](implementation-prompts/S03.md) | [S03.md](/tmp/160grab-session-prompts/S03.md) |
| S04「严格真实值填表与两路径 parity」 | [S04.md](implementation-prompts/S04.md) | [S04.md](/tmp/160grab-session-prompts/S04.md) |
| S05「Python 本机和 userscript 跨标签互斥」 | [S05.md](implementation-prompts/S05.md) | [S05.md](/tmp/160grab-session-prompts/S05.md) |
| S06「本机人工 canary 与脱敏夹具刷新」 | [S06.md](implementation-prompts/S06.md) | [S06.md](/tmp/160grab-session-prompts/S06.md) |
| S07「session 分类与只读 bounded backoff」 | [S07.md](implementation-prompts/S07.md) | [S07.md](/tmp/160grab-session-prompts/S07.md) |
| S08「时间与受控 browser channel」 | [S08.md](implementation-prompts/S08.md) | [S08.md](/tmp/160grab-session-prompts/S08.md) |
| S09「集成验收与文档迁移收口」 | [S09.md](implementation-prompts/S09.md) | [S09.md](/tmp/160grab-session-prompts/S09.md) |
