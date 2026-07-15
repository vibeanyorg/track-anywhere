# Research Index

本目录保存实现前的源码调研、设计选项和最终选择，便于后续从具体代码证据追溯架构决策。

| 主题 | 日期 | 状态 | 结论 |
| --- | --- | --- | --- |
| [V2 信用卡负债语义](v2-credit-card-liability-semantics.md) | 2026-07-15 | Implemented / Fixed-backup verified | 已保留复式记账内核并实现严格账户类型、自然负债余额、typed 信用卡命令和 fail-closed 历史语义 review contract；固定备份的两目标回放、独立 verifier、零 quarantine 和确定性对比已通过，证据见 [2026-07-15 回填验证](../operations/v2-backfill-verification-2026-07-15.md)。profile/statement 产品层继续 deferred；DB-level generic-event admission、active-account guard、exact-image staging 和 production cutover 尚未完成。 |
