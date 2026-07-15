# V2 信用卡负债语义源码调研

> Research Date: 2026-07-15
> Related Code: `backend/app/track_anywhere/domain/journal`, `backend/app/track_anywhere/queries/balances.py`, `backend/app/track_anywhere/api/v2`, `cli/track_anywhere_cli`
> Status: Implemented and verified locally — Option A (2026-07-15); fixed-backup deterministic rehearsal PASS, while exact-image staging and production cutover remain separate release gates

## Background

V2 已经使用显式 debit/credit 和正整数 units，复式记账内核本身可以正确表达信用卡。实施 Option A 之前，公开契约还没有把“信用卡是负债”落实为可验证、可查询、可安全写入的语义：

- `account_type` 只要求非空，任意字符串都能进入 catalog 和数据库；
- journal domain 的账户快照没有 `account_type`，因此无法在业务命令边界校验账户角色；
- `account_balances` 和余额查询统一保存/返回 `debit - credit`，信用卡欠款表现为负数；
- API 只返回一个无语义的 `units` 字段；
- CLI 的通用 `tx record` 要求调用者亲自选择 debit/credit；
- V1 的卡片 profile、余额语义和信用卡服务已在 V2 清理中删除，V2 没有替代入口。

这意味着实施前的 V2 即使保持分录平衡，agent 或客户端仍可能把消费、还款、退款的方向写反；即使写对，余额输出也会把“欠款增加”显示为更大的负数。Option A 已通过严格账户语义、semantic card commands 和 natural balance 输出关闭这些缺口。

固定备份快照也说明这不是理论问题：其中有 5 个 liability 卡账户、23 条相关 posting，18 条仍带 legacy signed 语义。新内核必须先锁定信用卡语义，再做确定性 backfill 和人工审计，不能让迁移脚本猜经济含义。

## Method

没有使用网页摘要或二手文章。本次通过 GitHub CLI 克隆源码到本地，然后读取固定 commit 的实现、测试和文档：

```text
gh repo clone actualbudget/actual /tmp/ta-credit-card-research-20260715/actual
gh repo clone firefly-iii/firefly-iii /tmp/ta-credit-card-research-20260715/firefly-iii
gh repo clone Gnucash/gnucash /tmp/ta-credit-card-research-20260715/gnucash
gh repo clone simonmichael/hledger /tmp/ta-credit-card-research-20260715/hledger
```

选取这四个项目，是为了覆盖四种不同取向：信封预算、个人财务 Web 应用、传统复式记账 GUI、文本复式记账内核。

## Competitive Analysis

### 1. Actual Budget

**Repository:** `actualbudget/actual`
**Commit:** `0c96d7701b9c15181da2d769a8063fa31d7e767c`

Actual 刻意不建立信用卡或 liability 账户类型。信用卡是允许余额为负的普通 on-budget 账户：

- `packages/loot-core/src/types/models/account.ts:3-25`：账户核心字段只有 on/off budget、closed 和同步余额，没有会计类型；
- `packages/loot-core/src/server/accounts/app.ts:134-160`：余额直接求和，不按账户类型翻转；
- `packages/desktop-client/src/components/transactions/table/utils.ts:31-45,69-90`：负数展示为 Payment，正数展示为 Deposit；
- `packages/loot-core/src/server/transactions/transfer.ts:47-92,120-137`：还款生成精确反号的配对 transfer，并建立双向引用；
- `packages/loot-core/src/server/transactions/transfer.ts:24-44`：on-budget 账户间 transfer 清除 category，避免还款被再次计为支出；
- `packages/docs/docs/budgeting/returns-and-reimbursements.md:3-19`：退款作为正数交易回到原消费分类；
- `packages/sync-server/src/app-pluggyai/app-pluggyai.js:170-177,214-228`：provider 适配器对 credit 账户同时归一化余额和交易符号；
- `packages/sync-server/src/app-gocardless/banks/seb_kort_bank_ab.ts:36-77`：个别银行还需要专用符号修正。

Actual 的优点是简单、还款不会重复计费、同步边界会归一化符号。缺点是欠款始终依赖负号，没有自然负债余额、退款来源关系、账单实体或最低还款语义。它适合预算工具，不适合直接作为 agent-facing 严格账本的模型。

### 2. Firefly III

**Repository:** `firefly-iii/firefly-iii`
**Commit:** `a0d70228bc1401a80e88e273461ec6af2d739374`

Firefly III 当前把信用卡建成带 `ccAsset` role 的资产账户，而不是 liability：

- `config/firefly.php:309-312`：`ccAsset` 是资产账户 role；
- `app/Api/V1/Requests/Models/Account/StoreRequest.php:115-121`：信用卡 role 挂在 asset 上，loan/debt/mortgage 才走 liability；
- `app/Console/Commands/Upgrade/UpgradesCreditCardLiabilities.php:58-76`：旧 credit-card liability 会被迁成 generic Debt；
- `app/Factory/TransactionFactory.php:51-82`：source 永远为负，destination 永远为正；
- `app/Factory/TransactionJournalFactory.php:345-398`：每个 journal 创建一负一正两条 transaction；
- `config/firefly.php:679-780`：合法 source/destination 组合由服务端集中配置，而不是客户端自由拼符号；
- `app/Support/Steam.php:85-115,370-384`：余额就是 signed transaction 求和，信用卡欠款为负；
- `app/Services/Internal/Support/CreditRecalculateService.php:135-217,257-379`：generic liability 余额重算包含大量方向分支和历史方向修正。

Firefly 值得借鉴的是“调用方只传正金额，服务端根据业务意图决定方向”和集中校验合法账户组合。不应照搬 `ccAsset + negative balance`，也不应复制 generic liability 的多分支重算逻辑。

### 3. GnuCash

**Repository:** `Gnucash/gnucash`
**Commit:** `453a4fe8868d10414bcdbdca28054fbc432debf1`

GnuCash 有独立的 `CREDIT` account type，但底层仍是通用账户和 balanced splits：

- `libgnucash/engine/Account.h:107-121`：定义 BANK、CASH、CREDIT、ASSET、LIABILITY 等类型；
- `libgnucash/engine/AccountP.hpp:81-87`：account type 主要为 GUI 展示和格式提示；
- `libgnucash/engine/TransactionP.hpp:58-69`：transaction 至少两条 split 且总和为零，并用信用卡消费拆分举例；
- `libgnucash/engine/Account.cpp:148-184`：信用卡 debit 标为 Payment，credit 标为 Charge；
- `gnucash/gnome/dialog-transfer.cpp:1473-1512`：transfer 统一生成 from negative、to positive；
- `libgnucash/engine/Account.cpp:2277-2331`：raw balance 逐 split 累加；
- `gnucash/gnome-utils/gnc-ui-util.cpp:129-172`：UI 可按 CREDIT/LIABILITY 类型翻转展示符号；
- `libgnucash/engine/Transaction.cpp:2588-2630`：冲正克隆原交易、逐 split 取反、清对账状态，并记录 reversal 关系。

GnuCash 最重要的经验是把 raw arithmetic 与 natural presentation 分层，并通过新 inverse transaction 保留审计链。它的 account type 仅作为 UI hint 对 Track Anywhere 仍然太弱；V2 应把类型变成服务端业务约束。

### 4. hledger

**Repository:** `simonmichael/hledger`
**Commit:** `73d17f9552d4827dda2447641ed46d416eff06b4`

hledger 没有信用卡专属类型，信用卡是 liability 树下的普通账户：

- `hledger-lib/Hledger/Data/Types.hs:167-203`：账户类型是 A/L/E/R/X 等会计类型，子账户继承类型；
- `hledger/Hledger/Cli/Commands/Accounts.hs` 及 manual 的账户声明规则把信用卡归于 Liability；
- `hledger-lib/Hledger/Data/Balancing.hs:197-207,244-270`：最多推导一个缺失 posting，并强制交易平衡；
- `hledger/test/journal/costs.test:556-573`：消费使用 expense 正 posting 和 credit-card liability 负 posting；
- `hledger/test/query-expr.test:184-199`：还款使用 bank -200、card +200，使卡余额从 -400 降至 -200；
- `hledger-lib/Hledger/Data/Types.hs:817-822`：资产/费用 normal positive，负债/权益/收入 normal negative；
- `hledger/Hledger/Cli/Commands/Balancesheet.hs:21-40`：资产负债表对 liabilities 使用 NormallyNegative 并翻转展示；
- `hledger-lib/Hledger/Data/Types.hs:518-572`：journal kernel 只有通用 transaction/posting，没有卡资料、退款或 statement 对象。

hledger 证明信用卡不需要污染 journal kernel：严格平衡、账户正常方向和报告归一化就足以保存财务真相。但它缺少 typed reversal/refund relation，不足以满足 Track Anywhere 的审计和 agent 安全要求。

## Design Patterns Summary

| Pattern | Adoption | Implication for V2 |
| --- | --- | --- |
| 平衡分录是唯一余额来源 | 4/4 | 保留 V2 journal，不另建可写“当前欠款”字段 |
| 调用者不应同时决定金额符号和业务方向 | Actual、Firefly 明确；GnuCash UI/hledger parser 辅助 | 对外只收正金额和业务账户，服务端生成 debit/credit |
| 还款是账户间 transfer，不是 expense | 4/4 | `card payment` 必须 Dr Liability / Cr Asset，不能生成 expense reporting line |
| 退款是消费的反向经济影响 | 4/4 | 整单撤销用 reversal；部分退款创建新 inverse journal 并关联原交易 |
| 手续费/利息是独立费用 | 4/4 | Dr Fee/Interest Expense / Cr Card Liability |
| raw 算术与用户余额语义分层 | GnuCash、hledger 显式；Actual、Firefly只保留 raw negative | API 同时命名 raw 与 natural，不能暴露无语义 `units` |
| 卡号、额度、账单日、到期日不属于 journal kernel | 4/4 | profile/statement 是独立 read model/aggregate |
| provider 符号需要边界归一化 | Actual 明确，其他工具由 importer/parser 处理 | 新导入先归一化为可证明的 liability intent；含糊历史 backfill 保留原始 posting，不猜 typed intent |

## Pre-implementation V2 Failure Modes

### 1. Account type is not a type

- `backend/app/track_anywhere/application/catalogs/create_account.py:18-42` 只验证 `account_type` 非空；
- `backend/app/track_anywhere/infrastructure/db/models/catalog.py:86-130` 只存在 `btrim(account_type) <> ''`；
- `backend/app/track_anywhere/domain/journal/models.py:44-50` 的 `AccountSnapshot` 不含 account type；
- `backend/app/track_anywhere/application/journal/post_transaction.py:322-330` 从 repository snapshot 转 domain 时丢弃 `account_type`。

因此服务端无法证明 card 是 liability、payment source 是 asset、charge target 是 expense。

### 2. Balance output exposes the wrong semantic layer

- `backend/app/track_anywhere/infrastructure/projections/synchronous.py:666-691` 将 debit 保存为正、credit 保存为负；
- `backend/app/track_anywhere/queries/balances.py:50-100` reference 和 projection 都返回这个 raw `debit-credit` 值；
- `backend/app/track_anywhere/api/v2/queries.py:62-75,312-326` 只把它序列化为 `units`。

信用卡消费 Dr Expense / Cr Liability 后，card raw units 为负数。这个内部值可用于投影校验，但不应伪装成用户余额。

### 3. Public write path delegates accounting direction to callers

- `backend/app/track_anywhere/api/v2/schemas.py:118-134` 要求客户端提交 posting side；
- `cli/track_anywhere_cli/click_ledger.py:38-58` 和 `command_ledger.py:281-296` 要求用户拼接 `SIDE:AMOUNT`；
- `backend/app/track_anywhere/domain/journal/models.py:12-18` 没有 card charge/payment/refund/fee intent；
- `docs/operations/v2-client-capability-matrix.md:31` 明确 payment instrument/profile 已移除且无 V2 route。

通用 journal 可以保留给高级调用方，但不能作为常见信用卡流程的唯一入口。

## Implemented Design

### Option A: Strict Liability Core + Semantic Card Commands (Selected and Implemented)

保留现有 event-sourced 双分录内核，在其上补齐强类型和业务意图：

```text
positive business amount
          │
          ▼
credit_card.charge/payment/refund/fee
          │ validate account type/subtype + asset + relation
          ▼
canonical positive-unit debit/credit postings
          │
          ├── immutable journal/event store
          └── raw debit-credit projection
                         │ account normal side
                         ▼
              named natural balance response
```

具体边界：

1. 增加严格 `AccountType`：`asset | liability | equity | income | expense | fund | system`，应用层和新 Alembic migration 都 fail closed。
2. 增加独立 `account_subtype` slug；card 命令要求 `account_type=liability` 且 `account_subtype=credit_card`。`legacy_credit_card` 只在 backfill adapter 中归一化，不进入新写入契约。
3. journal projection 继续保存 raw `debit-credit` 以保持确定性；查询改为显式返回 `raw_accounting_units`、`natural_units`、`normal_side` 和 `balance_semantics`。
4. liability 额外返回 `outstanding_units=max(natural_units, 0)` 与 `overpayment_units=max(-natural_units, 0)`；不再保留含糊的裸 `units`。
5. 增加 API/CLI 语义命令，所有 amount 必须为正：
   - charge：Dr Expense / Cr Card Liability；
   - payment：Dr Card Liability / Cr Cash Asset；
   - refund：Dr Card Liability / Cr original Expense；
   - fee/interest：Dr Fee Expense / Cr Card Liability。
6. 整单撤销复用现有 reversal；部分退款必须关联原 charge、累计不可超过可退金额，并生成新 journal，禁止改写历史分录。
7. 将业务 intent 持久化为 typed transaction kind/relation，保证 replay、审计和 backfill 不依赖 endpoint 名或 description 猜测。
8. card profile、credit limit、statement close/due date、minimum payment、reconciliation 单独设计，不进入本轮 journal kernel。

**Benefits:** 方向安全、agent API 简单、余额语义明确、兼容不可变审计和确定性 backfill。
**Delivered:** catalog、migration、event schema、query、API、CLI、typed projection/reversal guards、replay 和 backfill verifier 已形成同一套可执行契约。Owner-sealed historical generic-event admission 与 typed card 的 DB-level active-account guard 仍是明确的 production-cutover blockers。
**Implementation Complexity:** Medium-High。

### Option B: Natural Balance Patch Only

只增加严格账户类型和 natural balance 查询，仍让 API/CLI 调用者通过通用 journal 自己提交 debit/credit。

**Benefits:** 改动小，可以快速让信用卡欠款显示为正。
**Trade-off:** 没解决最危险的问题——agent 仍会把消费/还款/退款方向写反；也无法验证 card/asset/expense 账户组合。
**Implementation Complexity:** Low。

### Option C: Full Credit-Card Product Domain Now

在 Option A 之外，本轮同时实现 card profile、额度、账单周期、statement items、最低还款、到期状态和对账匹配。

**Benefits:** 能回答“本期应还多少、何时到期、是否逾期”，产品能力完整。
**Trade-off:** statement 是对账事实而非 ledger primitive；和内核修复一起做会扩大迁移与验证面，延迟最关键的方向安全修复。
**Implementation Complexity:** High。

## Comparison Summary

| Dimension | Option A | Option B | Option C |
| --- | --- | --- | --- |
| 消费/还款方向安全 | 强 | 弱 | 强 |
| 自然负债余额 | 完整 | 完整 | 完整 |
| 退款审计链 | 完整 | 无 | 完整 |
| 账单日/最低还款 | 延后、边界清晰 | 无 | 本轮实现 |
| backfill 可验证性 | 强 | 中 | 强但范围大 |
| 实现风险 | 中 | 低但遗留高风险 | 高 |

## Decision and Outcome

已选择并实现 **Option A**。

原因：

1. 它修的是当前真实故障边界：任意账户类型、无语义负余额、客户端手工选择借贷方向。
2. 它沿用四个参考项目共同验证过的双分录内核，同时比 negative-card 模型提供更安全的自然负债语义。
3. 它保留 Track Anywhere 已有的不可变事件、reversal、idempotency 和 replay 优势。
4. 它把 statement/profile 留在独立边界，之后可以增量实现，不会污染或阻塞财务真相层。

## Historical cutover contract

固定 V1 快照中的信用卡相关交易并不都能安全恢复为 typed intent：其中同时存在
普通消费、还款、余额快照/系统调整、方向修正、冲销以及多资产多腿交易。仅凭账户方向
无法区分 charge、fee、snapshot 和 correction，也无法为部分退款证明唯一的原消费关系。
因此 backfill 采用以下 fail-closed 边界：

1. `legacy_credit_card` 账户确定性归一化为 `liability + credit_card`；
2. 历史 transaction/reversal 原样转为通用不可变 Journal event，保持 posting、时间、
   raw balance 和冲销关系，不根据 memo 或 description 猜 typed intent；
3. cutover 后的新交易必须使用 typed card commands；这些交易才进入
   `credit_card_transactions` 并参与退款额度约束；
4. typed refund 只接受 cutover 后的 typed charge。引用历史 generic transaction 会明确
   拒绝，而不会悄悄降级。历史交易需要整单取消时，只能对原事件做 exact reversal；
   替代经济事实必须用对应的 typed card command 重新录入。若替代事实无法由现有 typed
   command 表达，或必须绑定历史 generic transaction，则必须先批准并执行一套可证明、
   可重复的 deterministic migration，不能使用 generic card adjustment；
5. [cutover regression test](../../backend/tests/v2/backfill/test_source_target_semantics.py)
   同时证明历史保持 generic、新账户 subtype 正确、历史 typed refund fail closed，以及
   同一回填账户上的新 charge/refund 正常工作。

这个边界不是 V1 compatibility layer，而是“不从含糊历史猜经济意图”的数据完整性约束。

## Implementation and Verification Checklist

实现、固定备份重放、逐账户审计和全套本地验证已完成。Exact-image staging 与下面列出的
DB trust-boundary hardening 仍是 release/cutover gates；它们不影响本地 Option A 验证，
但会阻止发布或切换。

### Phase 1: Contract and balance semantics

- [x] 先写账户类型、subtype、natural balance、overpayment 的失败测试；
- [x] 增加 domain enum、DB constraint migration 和 catalog contract；
- [x] 改造 balance query/API/CLI 输出，并保留 raw projection parity；
- [x] 验证 asset、liability、income、expense、equity、fund、system 全部 normal side。

### Phase 2: Semantic writes

- [x] 先写 charge/payment/refund/fee 的 posting、错误账户组合、正金额和幂等失败测试；
- [x] 增加 application commands、API routes 和 CLI commands；
- [x] 持久化 typed intent；
- [x] 确保 payment 不生成 expense reporting line。

### Phase 3: Refund/reversal and migration proof

- [x] 测试 full reversal、partial refund、multiple refunds、over-refund rejection；
- [x] 增加 typed original/refund relation 和投影；
- [x] 修复 backfill rehearsal blockers，并在临时 PG17 上重放固定备份；
- [x] 对 5 个 card accounts 做逐账户 raw/natural balance、posting parity 和 reversal audit；
- [x] 跑 V2 backend、contract、CLI、replay、backfill 全套验证。

### Production-cutover hardening

- [ ] 数据库拒绝任何未由 owner/migrator 预先封存精确 event ID + payload hash 的
  generic credit-card posting；runtime 写入的 review artifact 只能作为确定性证据，不能
  作为数据库授权；
- [ ] typed card DB guard 对 card 与 counter account 都强制 `status=active`，覆盖直接
  runtime/committer 绕过 application validator 的路径；
- [ ] 从 clean committed source 完成 exact-image isolated staging，并生成独立验收证据。

### Deferred: statement product layer

- [ ] card profile、limit 和 provider metadata；
- [ ] statement cycle、due date、minimum due；
- [ ] statement item matching 和 reconciliation；
- [ ] reminder/automation。

## References

- `actualbudget/actual@0c96d7701b9c15181da2d769a8063fa31d7e767c`
- `firefly-iii/firefly-iii@a0d70228bc1401a80e88e273461ec6af2d739374`
- `Gnucash/gnucash@453a4fe8868d10414bcdbdca28054fbc432debf1`
- `simonmichael/hledger@73d17f9552d4827dda2447641ed46d416eff06b4`
- `docs/adr/0002-debit-credit-posting-model.md`
- `docs/architecture/ledger-domain-optimization.md`
