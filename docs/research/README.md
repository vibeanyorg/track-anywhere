# Research Index

本目录保存实现前的源码调研、设计选项和最终选择，便于后续从具体代码证据追溯架构决策。

| 主题 | 日期 | 状态 | 结论 |
| --- | --- | --- | --- |
| [V2 信用卡负债语义](v2-credit-card-liability-semantics.md) | 2026-07-15 | Implemented | 已保留复式记账内核并实现严格账户类型、自然负债余额、typed 信用卡命令、projection/replay 校验和数据库约束。当前 HEAD 不包含 V1 导入或兼容路径；profile/statement 产品层继续 deferred，exact-image staging 和 production cutover 尚未执行。 |
