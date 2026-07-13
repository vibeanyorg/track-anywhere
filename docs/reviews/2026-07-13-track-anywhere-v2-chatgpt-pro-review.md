# Track Anywhere V2 ChatGPT Pro 架构评审归档

- 日期：2026-07-13
- 对话：https://chatgpt.com/c/6a54527d-3554-83e8-b962-59979eb8b736
- 模型界面标识：5.6 Sol / Medium
- 输入包：`output/track-anywhere-v2-pro-review-20260713.tar.gz`
- 输入包 SHA-256：`e8ab3be22bd21a20069ec8862a60b99aa0cd525ed61bce32d104702ed2320f13`
- 说明：以下为 Pro 回复原文归档；正式决策以同日 V2 设计文档为准。

---
最终裁决

推荐 Track Anywhere V2 采用“财务核心事件溯源 + 目录/工作流 CRUD + 同库事务内关键投影”的适度事件账本。

不建议纯事件溯源，也不建议继续修补 V1。

V2 的唯一财务真相是不可变的 Journal Event；journal_transactions、journal_postings、account_balances 是可删除重建的投影。资产、Book、账户、分类目录、预算规则、支付工具、附件、认证属于普通 CRUD，不进入账本事件流。

当前候选方向总体正确，但必须修正六点：

不能把所有对象都事件化。 Account 名称、预算配置、支付工具等没有必要承担事件重放复杂度。

不能把 Reporting Line 与 Posting 混成同一类事实。 Posting 是 accounting fact；分类、商户、项目、报销状态是 reporting dimension。

每币种平衡意味着 FX 必须显式使用 trading accounts。 不能让一笔 CNY→USD 交易直接以两个不同资产 posting “总体折算后平衡”。

hash chain 是审计证据，不是会计约束。 FK、复合归属约束、金额 CHECK、延迟平衡触发器仍必须存在。

幂等不要设计成可过期 lease worker。 单 PostgreSQL 事务内 reservation、事件追加、同步投影和 receipt 完成，可以消除 takeover/fencing 的大部分复杂度。

SQLite 只能做领域快速测试。 PostgreSQL 才是账本集成测试和迁移门禁，不能用 SQLite “模拟通过”数据库不变量。

1. 最终架构
Mermaid
使用事件溯源的范围

使用 append-only 事件：

已确认 Journal Transaction

Reversal

财务事实相关的外部引用修正

Reporting Line 的分配、替换、撤销

FX 成交事实

Investment lot acquisition/disposal/allocation

普通 CRUD：

users、auth identities、credentials

books 及成员权限

assets 目录

accounts 目录及生命周期

categories、counterparties、projects

budgets、recurring rules、drafts

payment instruments/profiles

attachments

import job、quarantine、运维配置

Draft 是工作流状态，不是账本事实。确认 Draft 时生成新命令；确认成功后 Draft 只记录 confirmed_transaction_id，不得成为重放依赖。

权威性顺序

ledger_events：财务与分类历史的权威来源。

CRUD 目录表：当前目录定义的权威来源。

同步/异步 projection：可重建读取模型。

API response cache：仅允许带 position/version 的短期缓存；V2 第一阶段不实现跨请求缓存。

彻底移除全库启动 hydration 和 StorageReadCache。

2. 领域边界
对象	职责	边界与不变量
Book	租户及账本隔离边界	每个事件、账户、交易都属于一个 Book
Asset	数量单位及精度策略	ledger_scale 创建后不可修改；输入与显示精度可调整
Account	posting 的归属目标	固定 Book、固定 Asset；有 posting 后不可换 Book/Asset
Journal Transaction	一次原子会计事实	至少两个 posting；每个资产分别借贷平衡
Posting	最小会计原子	正整数 units + debit/credit；不可修改、删除
Reporting Line	分析维度分配	不影响余额；可通过新事件重新分类
Reversal	原交易的完整补偿交易	包含逐项反向 posting；唯一引用被冲正交易
Classification	分类/商户/项目/必要性/报销维度	单独版本化，不能改变 posting
FX	多资产交换语义	每资产通过专用 trading account 分别平衡
Investment Lot	数量、成本、处置匹配	独立于现金余额；lot allocation 必须可重放
Valuation	某时点市场观察	非会计事实，不改成本与余额；普通时间序列 CRUD
FX 的明确模型

例如用 700 CNY 买入 100 USD：

Debit：USD wallet，100 USD

Credit：USD trading account，100 USD

Debit：CNY trading account，700 CNY

Credit：CNY bank，700 CNY

事件 metadata 记录：

base asset/units

quote asset/units

fee transaction 或 fee posting

外部成交引用

汇率是由两组精确数量推导出的展示值，不是平账所依赖的浮点数。

投资模型

普通 posting 记录现金、证券资产账户的数量变化；lot 子域记录：

acquisition quantity

original cost asset/units

remaining quantity

disposal allocation

realized gain/loss transaction reference

不得用 average_cost float 或更新历史 lot。FIFO/Specific ID 是 disposal 命令的确定性规则；最终 allocation 必须写入事件，不能重放时重新选择。

3. 事件目录

所有事件共有 envelope：

event_id UUID
book_id UUID
book_position BIGINT
global_position BIGINT
stream_type
stream_id UUID
stream_version INT
event_type
event_schema_version SMALLINT
payload JSONB
command_id UUID
actor_id
correlation_id UUID
causation_event_id UUID?
recorded_at TIMESTAMPTZ
effective_at TIMESTAMPTZ
previous_hash BYTEA
event_hash BYTEA

recorded_at 由数据库产生；领域重放不得依赖它。业务排序使用 book_position，展示时间使用 effective_at。

必须支持的 V2 事件
JournalTransactionPosted.v1

单个原子事件，payload 必须包含完整：

transaction ID、memo、purpose、effective_at

ordered postings

每个 posting 的 ID、position、account ID、asset code、side、amount_units

external references

transaction kind：standard、opening、adjustment、fx、investment_cash 等

不能拆成 TransactionCreated + 多个 PostingAdded。

JournalTransactionReversed.v1

单个原子事件，同时包含：

reversal transaction ID

reverses_transaction_id

原 transaction event ID/hash

完整反向 postings

reason

不能只保存“引用原交易，重放时临时生成反向 posting”，否则规则升级可能改变历史结果。

同一原交易最多一个完整 reversal：

UNIQUE(book_id, reverses_transaction_id)

如果 reversal 本身错误，新增其反向交易；不编辑原 reversal。

ReportingLinesAssigned.v1

包含 transaction ID、classification revision、完整的新 reporting-line 集合。采用replace-all snapshot，而非难以重放的字段 patch。

每行包含：

line ID、position

amount asset/units

line type

category ID + category version ID + path/name snapshot

counterparty/project

necessity/reimbursement status

memo

同一资产的 reporting line 合计不得超过其被分类 posting 的适用数量；expense/income 分类的精确对应规则由 transaction kind validator 决定。

ReportingLinesCleared.v1

明确清空分类，不使用空 payload 暗示删除。

InvestmentLotAcquired.v1

完整保存 lot ID、instrument asset、quantity units、cost asset/units、fees、linked transaction。

InvestmentLotDisposed.v1

完整保存 disposal quantity、逐 lot allocation、proceeds、cost basis 和 linked transaction。重放时不得重新运行 FIFO。

暂不做事件

Account rename、Category rename、Budget update、Counterparty merge、Attachment scan、Draft update 都使用 CRUD + audit log。

Schema 管理

payload 必须由事件类型专属 Pydantic model 校验。

JSON Schema 固化到仓库。

同一 event_type + schema_version 永不改变含义。

projector 接受明确版本；升级通过纯函数 upcaster。

禁止 dict[str, Any] 直接进入 event writer。

canonical serialization 使用 RFC 8785/JCS 等价规则或项目内固定实现；hash 输入必须跨 Python 版本稳定。

4. PostgreSQL 物理模型与 DDL 草案

下面是 V2 核心表的接近可执行版本。辅助目录字段可在此基础上增加。

SQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE account_status AS ENUM ('active', 'closed');
CREATE TYPE posting_side AS ENUM ('debit', 'credit');
CREATE TYPE receipt_status AS ENUM ('processing', 'completed');

CREATE TABLE books (
    book_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    base_asset_code varchar(16),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE assets (
    asset_code varchar(16) PRIMARY KEY,
    kind varchar(32) NOT NULL,
    ledger_scale smallint NOT NULL CHECK (ledger_scale BETWEEN 0 AND 30),
    input_scale smallint NOT NULL CHECK (
        input_scale BETWEEN 0 AND ledger_scale
    ),
    display_scale smallint NOT NULL CHECK (
        display_scale BETWEEN 0 AND ledger_scale
    ),
    name text NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('active', 'disabled')),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0)
);

ALTER TABLE books
ADD CONSTRAINT fk_books_base_asset
FOREIGN KEY (base_asset_code) REFERENCES assets(asset_code);

CREATE TABLE accounts (
    book_id uuid NOT NULL REFERENCES books(book_id),
    account_id uuid NOT NULL,
    asset_code varchar(16) NOT NULL REFERENCES assets(asset_code),
    account_type varchar(32) NOT NULL,
    subtype varchar(64),
    name text NOT NULL CHECK (btrim(name) <> ''),
    status account_status NOT NULL DEFAULT 'active',
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    closed_at timestamptz,
    PRIMARY KEY (book_id, account_id),
    UNIQUE (account_id),
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);

CREATE INDEX ix_accounts_book_asset_type
ON accounts(book_id, asset_code, account_type);

CREATE TABLE book_event_heads (
    book_id uuid PRIMARY KEY REFERENCES books(book_id),
    last_position bigint NOT NULL DEFAULT 0 CHECK (last_position >= 0),
    last_hash bytea NOT NULL CHECK (octet_length(last_hash) = 32)
);

CREATE SEQUENCE ledger_global_position_seq;

CREATE TABLE ledger_events (
    global_position bigint PRIMARY KEY
        DEFAULT nextval('ledger_global_position_seq'),
    event_id uuid NOT NULL UNIQUE,
    book_id uuid NOT NULL REFERENCES books(book_id),
    book_position bigint NOT NULL CHECK (book_position > 0),
    stream_type varchar(32) NOT NULL,
    stream_id uuid NOT NULL,
    stream_version integer NOT NULL CHECK (stream_version > 0),
    event_type varchar(64) NOT NULL,
    event_schema_version smallint NOT NULL CHECK (event_schema_version > 0),
    command_id uuid NOT NULL,
    actor_id varchar(128) NOT NULL,
    correlation_id uuid NOT NULL,
    causation_event_id uuid REFERENCES ledger_events(event_id),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    previous_hash bytea NOT NULL CHECK (octet_length(previous_hash) = 32),
    event_hash bytea NOT NULL CHECK (octet_length(event_hash) = 32),
    UNIQUE (book_id, book_position),
    UNIQUE (book_id, stream_type, stream_id, stream_version),
    UNIQUE (book_id, event_hash)
);

CREATE INDEX ix_events_book_effective
ON ledger_events(book_id, effective_at, book_position);

CREATE INDEX ix_events_stream
ON ledger_events(book_id, stream_type, stream_id, stream_version);

CREATE TABLE command_receipts (
    actor_id varchar(128) NOT NULL,
    operation varchar(96) NOT NULL,
    idempotency_key_hash bytea NOT NULL CHECK (
        octet_length(idempotency_key_hash) = 32
    ),
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    command_id uuid NOT NULL UNIQUE,
    status receipt_status NOT NULL,
    fence_token bigint NOT NULL,
    result_status smallint,
    result_body jsonb,
    first_event_position bigint,
    last_event_position bigint,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    PRIMARY KEY (actor_id, operation, idempotency_key_hash),
    CHECK (
      (status = 'processing'
       AND result_status IS NULL
       AND result_body IS NULL
       AND completed_at IS NULL)
      OR
      (status = 'completed'
       AND result_status IS NOT NULL
       AND result_body IS NOT NULL
       AND completed_at IS NOT NULL)
    )
);

CREATE SEQUENCE command_fence_seq;

同步投影：

SQL
CREATE TABLE journal_transactions (
    book_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    source_event_id uuid NOT NULL UNIQUE REFERENCES ledger_events(event_id),
    source_position bigint NOT NULL,
    effective_at timestamptz NOT NULL,
    transaction_kind varchar(32) NOT NULL,
    memo text NOT NULL DEFAULT '',
    purpose text NOT NULL DEFAULT '',
    reverses_transaction_id uuid,
    PRIMARY KEY (book_id, transaction_id),
    FOREIGN KEY (book_id) REFERENCES books(book_id),
    FOREIGN KEY (book_id, reverses_transaction_id)
      REFERENCES journal_transactions(book_id, transaction_id),
    UNIQUE (book_id, reverses_transaction_id)
);

CREATE TABLE journal_postings (
    book_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    posting_id uuid NOT NULL,
    position smallint NOT NULL CHECK (position >= 0),
    account_id uuid NOT NULL,
    asset_code varchar(16) NOT NULL,
    side posting_side NOT NULL,
    amount_units numeric(38,0) NOT NULL CHECK (amount_units > 0),
    PRIMARY KEY (book_id, posting_id),
    UNIQUE (book_id, transaction_id, position),
    FOREIGN KEY (book_id, transaction_id)
      REFERENCES journal_transactions(book_id, transaction_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (book_id, account_id)
      REFERENCES accounts(book_id, account_id)
      ON DELETE RESTRICT,
    FOREIGN KEY (asset_code) REFERENCES assets(asset_code)
);

CREATE INDEX ix_postings_account_transaction
ON journal_postings(book_id, account_id, transaction_id);

CREATE INDEX ix_postings_transaction_asset_side
ON journal_postings(book_id, transaction_id, asset_code, side);

CREATE TABLE account_balances (
    book_id uuid NOT NULL,
    account_id uuid NOT NULL,
    asset_code varchar(16) NOT NULL,
    balance_units numeric(48,0) NOT NULL,
    as_of_position bigint NOT NULL CHECK (as_of_position >= 0),
    PRIMARY KEY (book_id, account_id, asset_code),
    FOREIGN KEY (book_id, account_id)
      REFERENCES accounts(book_id, account_id),
    FOREIGN KEY (asset_code) REFERENCES assets(asset_code)
);

这里仍需加两个 PostgreSQL constraint trigger：

Account asset constraint：posting 的 asset_code 必须等于 account 的资产。普通复合 FK 无法直接表达，建议在 account 增加：

SQL
UNIQUE(book_id, account_id, asset_code)

然后 posting 使用三列复合 FK，取代当前两列 FK。

Deferred transaction balance trigger：事务提交前验证每个受影响 transaction：

SQL
GROUP BY book_id, transaction_id, asset_code
HAVING SUM(CASE side WHEN 'debit' THEN amount_units
                     ELSE -amount_units END) <> 0

同时验证 posting 数量至少为 2。触发器设为：

SQL
DEFERRABLE INITIALLY DEFERRED

这不是对 application validator 的替代，而是最后一道数据库门禁。

Reporting projection：

SQL
CREATE TABLE reporting_lines (
    book_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    classification_revision integer NOT NULL CHECK (
        classification_revision > 0
    ),
    line_id uuid NOT NULL,
    position smallint NOT NULL CHECK (position >= 0),
    line_type varchar(32) NOT NULL,
    asset_code varchar(16) NOT NULL,
    amount_units numeric(38,0) NOT NULL CHECK (amount_units > 0),
    category_id uuid,
    category_version integer,
    category_snapshot jsonb,
    counterparty_id uuid,
    project_id uuid,
    necessity varchar(16) NOT NULL,
    reimbursement_status varchar(24) NOT NULL,
    memo text NOT NULL DEFAULT '',
    source_event_id uuid NOT NULL REFERENCES ledger_events(event_id),
    PRIMARY KEY (book_id, transaction_id, line_id),
    UNIQUE (book_id, transaction_id, classification_revision, position),
    FOREIGN KEY (book_id, transaction_id)
      REFERENCES journal_transactions(book_id, transaction_id)
);

异步 projector：

SQL
CREATE TABLE projection_checkpoints (
    projection_name varchar(96) PRIMARY KEY,
    projector_version integer NOT NULL,
    last_global_position bigint NOT NULL DEFAULT 0,
    last_event_id uuid,
    state varchar(16) NOT NULL
      CHECK (state IN ('running', 'paused', 'failed')),
    lease_owner uuid,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE projection_failures (
    projection_name varchar(96) NOT NULL,
    global_position bigint NOT NULL,
    event_id uuid NOT NULL,
    projector_version integer NOT NULL,
    error_code varchar(64) NOT NULL,
    error_detail text NOT NULL,
    attempts integer NOT NULL,
    first_failed_at timestamptz NOT NULL,
    last_failed_at timestamptz NOT NULL,
    PRIMARY KEY (projection_name, global_position)
);
Hash 计算

event_hash = SHA-256(domain_separator || canonical_envelope || canonical_payload)。

包含：

book ID、book position

stream ID/version

event type/schema version

command/correlation/causation

effective_at 的固定 UTC 格式

previous hash

canonical payload

不包含：

global position

数据库默认产生的 recorded_at

projection 数据

每次 append：

SELECT ... FROM book_event_heads WHERE book_id=? FOR UPDATE

使用 last_position + 1 和 last_hash

计算并插入事件

CAS 更新 head：

SQL
UPDATE book_event_heads
SET last_position = :new_position, last_hash = :new_hash
WHERE book_id = :book_id
  AND last_position = :old_position
  AND last_hash = :old_hash;

必须影响一行，否则整个事务失败。

5. 精确金额
最终表示

API 接收十进制字符串，禁止 JSON number。

领域层解析为 Python Decimal。

验证完成后立即转换为 Python int amount_units。

数据库存 numeric(38,0)。

禁止 float、SQL runtime cast、科学计数法和隐式 rounding。

换算：

amount_units = exact_decimal × 10^ledger_scale

只有结果为整数才合法。

单个数量限制：

0 < amount_units <= 10^38 - 1

余额投影使用 numeric(48,0)，避免大量合法 posting 求和时过早溢出；每次更新仍做产品级最大余额检查。

三种 scale

ledger_scale：历史无损存储尺度，资产创建后不可变。

input_scale：新命令允许的小数位。

display_scale：默认显示位数，不改变事实。

USDT blocker 的唯一正确处理

将 V2 USDT 定义为：

ledger_scale = 8
input_scale = 6
display_scale = 2 或 6

历史 1.12345678 USDT 转为 112345678 units，无损导入。

新输入 1.1234567 因超过 input_scale=6 被拒绝；backfill 使用显式的 historical_import policy，允许最多 ledger_scale=8。

不得：

把两条历史记录舍入到 6 位；

为历史 USDT 创建另一个资产代码；

把所有新 USDT 输入放宽为 8 位；

在事件 payload 同时保留 units 和可产生歧义的 decimal amount。

SQLite

创建领域 ScaledUnits value object 和 SQLAlchemy TypeDecorator：

PostgreSQL：NUMERIC(38,0) ↔ Python int

SQLite 单元测试：canonical base-10 string ↔ Python int

但以下测试强制 PostgreSQL 17：

38 位边界

deferred constraint trigger

concurrent receipt reservation

book head locking/CAS

numeric aggregation

migrations、backfill、projection rebuild

FX 与 investment

FX 的成交价格只用于展示：

rate = quote_units × 10^base_scale /
       (base_units × 10^quote_scale)

使用 Decimal 按响应精度格式化，不存作权威金额。

Investment：

quantity_units 使用 instrument asset 的 ledger scale。

cost_units 使用 cost asset 的 ledger scale。

lot 同时保存两种资产及 units。

market valuation 是独立 observation，不修改 lot cost basis。

6. 命令事务与跨进程幂等
Receipt key

唯一范围：

(actor_id, operation, SHA-256(raw idempotency key))

request hash 必须基于：

operation schema version

canonical normalized request

book ID

actor authorization scope

原始 key 不落库、不进日志。

单事务状态机
Mermaid

具体算法：

开启 READ COMMITTED 数据库事务。

尝试插入 processing receipt，fence_token=nextval(...)。

唯一键冲突时 SELECT ... FOR UPDATE：

hash 不同：返回 409 IDEMPOTENCY_KEY_REUSE；

completed：返回原 status + body；

processing：它只能是本事务自己可见，或等待另一事务结束后的结果。

读取并锁定 book_event_heads。

验证命令、append event、更新同步 projection。

把 receipt 更新为 completed，保存稳定 response。

一次 COMMIT。

processing receipt 不应在已提交状态长期存在。 worker 崩溃会回滚 receipt、事件和投影，因此无需 lease takeover。fence_token 用于诊断和防止未来引入外部 side effect worker 时混淆执行代次，不作为当前超时接管机制。

特殊失败

两个进程同 key、同 payload：一个写入，另一个等待唯一索引/行锁，随后 replay。

同 key、不同 payload：确定返回 409，不执行第二个命令。

commit outcome unknown：客户端必须用同 key 重试；若已提交则 replay，否则重新执行。

响应丢失：同上，receipt 返回完全相同的业务结果。

worker 在 commit 前崩溃：全部 rollback。

worker 在 commit 后响应前崩溃：receipt 已完成，重试 replay。

数据库连接断开：服务端不得推测成功或失败；返回可重试的 503 COMMIT_OUTCOME_UNKNOWN。

外部副作用：账本写事务不直接调用第三方；写 transactional outbox，由独立消费者幂等处理。

7. 投影策略
同步投影

与事件、receipt 同一事务更新：

journal_transactions

journal_postings

account_balances

reversal uniqueness/status

当前 reporting_lines

Book 的 committed position

原因：

写后立即读取交易和余额；

API response 可准确返回 as_of_position；

不允许成功响应后余额暂时缺失。

account_balances 不是权威事实，只是同步物化缓存。任何时候都必须能从 posting 重建。

余额更新：

SQL
INSERT ... ON CONFLICT ...
DO UPDATE SET
  balance_units = account_balances.balance_units + EXCLUDED.balance_units,
  as_of_position = GREATEST(
      account_balances.as_of_position,
      EXCLUDED.as_of_position
  );

同一事件只能应用一次；projection 写入以 source_event_id 或 applied-event table 防重。

异步投影

月度分类汇总

budget actuals

net worth

FX performance

investment lots/read views

search/document views

analytics

异步 worker 按 global_position 消费，使用：

SQL
SELECT ... FOR UPDATE SKIP LOCKED

或单 projector advisory lock。Checkpoint 与投影写入同一事务。

重建

为 projector version 建 shadow tables，例如 budget_actuals_v3_build。

从 position 1 重放。

记录 source event count、last position、校验摘要。

与现有 projection 做 parity/diff。

单事务 rename/swap view。

保留旧表直到观察期结束。

Projector 遇到未知 schema/version 必须暂停并报警，禁止跳过。

Projector bug 修复后：

bump projector version；

从头构建 shadow projection；

不能原位“修几行后继续”，除非修复脚本本身有完整验证和审计。

8. Backfill
总体策略

Backfill 是独立、可重复执行的 ETL 应用，不放进 API 启动过程，也不由 Alembic 执行。

输入必须是只读恢复库或固定 dump，不直接连生产写库。

确定性 ID

使用固定 namespace UUIDv5：

v2_book_id        = UUIDv5(namespace, "book:" + v1_book_id)
v2_account_id     = UUIDv5(namespace, "account:" + v1_account_id)
v2_transaction_id = UUIDv5(namespace, "transaction:" + v1_transaction_id)
v2_posting_id     = UUIDv5(namespace, "posting:" + transaction_id + ":" + position)
v2_event_id       = UUIDv5(namespace, "event:transaction:" + transaction_id)
v2_line_id        = UUIDv5(namespace, "line:" + transaction_id + ":" + position)

禁止 backfill 时调用 UUID4。

排序

每个 Book 事件顺序固定为：

effective_at UTC
transaction_id canonical bytes
event kind ordinal

不能使用 V1 自增 ID 之外的不稳定数据库返回顺序，也不能使用 backfill 执行时间。

recorded_at 对导入事件使用一个由 manifest 固定的时间，不参与 hash。更好的做法是 payload 标明 origin=v1_backfill，source_snapshot_id 和源主键。

流程

Extract

读取 schema version、table counts、source dump hash。

导出 canonical NDJSON manifest。

Inventory

检查跨 Book 引用、孤儿 FK、重复 position、无效 Decimal、未知资产、反转关系。

Normalize

legacy signed posting 转为明确 debit/credit。

绝不修改数量。

Validate

按 Book/transaction/asset 平衡。

分类快照与 category version 对应。

Generate

生成确定性命令及事件。

Append

每个 transaction 一个数据库事务。

import receipt 唯一键为 (snapshot_id, source_table, source_pk)。

Checkpoint

保存最后 canonical source key，而不是 OFFSET。

Verify

全量 parity 和重放。

Seal

生成 source manifest hash、event terminal hash、验证报告。

Quarantine

以下情况必须阻断该 Book 的切换：

不平账

posting 无 account

account/book/asset 不一致

无法解析的 amount

反转循环/多重冲正

transaction 内重复 position

不可确定映射的 legacy signed semantics

reporting line 超出可分配金额

Quarantine 表保存 source identity、原始 canonical row hash、错误码和人工决定。不能把坏行“暂时跳过后通过”。

USDT

两条 8 位 USDT 记录进入正常事件流，不进入 quarantine，因为 V2 ledger_scale=8。报告单独列出它们，证明 units 和重放结果逐位相等。

分类快照

保留 V1 line 上实际引用的 category version。

若只有 category ID，使用交易确认时可证明存在的版本。

无法确定时保留 category ID/name/path 的 source snapshot，并标记 historical_resolution=unverified；这不影响财务余额，但进入报告质量警告。

不得拿当前分类名称覆盖历史路径。

9. 正确性门禁

切换前全部必须通过，任何一项失败均 BLOCKED。

结构门禁

所有复合 Book FK 有效。

每个 posting 的 account/asset 匹配。

每笔 transaction 至少两个 posting。

每 transaction/asset：debit units = credit units。

reversal 唯一、无循环、反向 posting 完全匹配。

无 float、无 amount string runtime cast。

数量与 parity

逐 snapshot、Book、account、asset 比较：

account balance units 精确相等；

transaction count；

posting count；

reporting line count；

reversal count和关系；

asset totals；

category totals；

investment quantities、cost basis；

USDT 8 位原值。

不能只比较全局汇总，因为两个账户等量错位会相互抵消。

Event 门禁

book position 从 1 连续无洞。

stream version 从 1 连续无洞。

previous hash 与前一事件 hash 相等。

全链重新计算 hash 相等。

head position/hash 等于末事件。

event payload 全部通过对应版本 schema。

同一 source snapshot 重跑产生相同 event IDs、顺序、payload、terminal hash。

重放门禁

至少执行两次独立重放：

空库 → 正式 projector

空库 → 独立 verifier/reference reducer

比较：

表级 canonical dump hash；

每账户余额；

当前 reporting lines；

reversal 状态；

investment lot 状态；

terminal checkpoint。

Reference reducer 不得调用正式 projector 代码，否则是同一 bug 自证正确。

并发门禁

PostgreSQL 多进程测试：

20 个并发请求、相同 key/payload，只产生一个事件批次；

相同 key、不同 payload，只允许一个成功，其余 409；

同 Book 并发 100 次，position 连续、hash chain 正确；

不同 Book 不被全局锁串行化；

commit unknown 后重试只 replay；

worker kill -9 后不存在半笔交易或永久 processing receipt。

10. 切换与回滚

虽然 V1 已停用，仍采用可验证切换流程：

固定 V1 dump，并记录 SHA-256。

在隔离 PostgreSQL 17 restore。

完整 backfill 至新的 V2 数据库/新 schema。

运行全部 parity、重放、并发和 API 验证。

至少再从原 dump 做一次全新空库重复演练。

比较两次 terminal hashes 和 projection canonical hashes。

V2 只读 staging 验证。

如确认 V1 仍可能有尾部写入，正式 cutover 前停写并重新导出：

若 snapshot hash 未变，直接使用已验证结果；

若有 delta，不做临时 dual-write，重新执行完整 backfill。

切换连接指向 V2。

生产部署不在当前授权范围内。

回滚不是把 V2 数据倒灌 V1。cutover 初期若失败：

停止 V2 写入；

保留 V2 数据库供诊断；

若 V1 确实仍可运行，临时恢复 V1 服务和原备份；

否则修复代码后从已封存 source dump 重新生成 V2；

不在损坏 projection 上手工改余额。

V2 一旦接受了 V1 不具备的新交易，就不能无损回到 V1；因此实际部署前还需要单独的 production cutover gate。

11. 目标代码结构
backend/app/track_anywhere/
├── api/
│   ├── dependencies.py
│   ├── errors.py
│   └── v2/
│       ├── books.py
│       ├── accounts.py
│       ├── journal.py
│       ├── classifications.py
│       └── investments.py
├── application/
│   ├── command_bus.py
│   ├── unit_of_work.py
│   ├── idempotency.py
│   ├── journal/
│   │   ├── post_transaction.py
│   │   ├── reverse_transaction.py
│   │   └── classify_transaction.py
│   └── investments/
├── domain/
│   ├── money/
│   │   ├── scaled_units.py
│   │   └── asset_policy.py
│   ├── journal/
│   │   ├── commands.py
│   │   ├── events.py
│   │   ├── models.py
│   │   └── validators.py
│   ├── reporting/
│   └── investments/
├── infrastructure/
│   ├── db/
│   │   ├── models/
│   │   ├── event_store.py
│   │   ├── command_receipts.py
│   │   ├── uow.py
│   │   └── repositories/
│   ├── projections/
│   │   ├── synchronous/
│   │   └── asynchronous/
│   ├── serialization/
│   │   ├── canonical_json.py
│   │   └── event_registry.py
│   └── outbox/
├── queries/
│   ├── accounts.py
│   ├── journal.py
│   ├── reports.py
│   └── investments.py
└── observability/
    ├── metrics.py
    └── audit.py

backend/tools/backfill_v1/
├── extract.py
├── inventory.py
├── normalize.py
├── generate.py
├── load.py
├── checkpoint.py
├── quarantine.py
├── verify.py
└── manifest.py

backend/tests/
├── unit/
├── postgres/
├── concurrency/
├── replay/
├── backfill/
├── contract/
└── fixtures/
应删除而非迁移的 V1 模块

在 V2 功能替代并通过门禁后删除：

storage_read_cache.py

storage_snapshot.py

storage_snapshot_loader.py

service_state_hydration.py

service_persistence/*

可变 ledger.Ledger

transaction_builder.py 中重复 validator

FinanceService 大型 mixin facade

OrmStorage 大型 mixin facade

dirty collectors/directories

String amount 与 runtime cast 路径

as_of_ledger_version = transaction_count

legacy signed 的在线命令入口

可保留但重写边界：

FastAPI auth/security

CLI transport/rendering

Alembic 工具链

attachments/auth CRUD

API contract test harness

不要在旧 facade 内逐步嵌入 V2；建立独立 package，再切换 router。

12. 逐 Task TDD 实施计划
Phase 0：冻结契约和测试基线
Task 0.1 金额契约

先写失败测试：

decimal string 到 units 的精确转换；

超 input scale 拒绝；

38 位边界；

float/JSON number/scientific notation 拒绝；

USDT historical 8 位允许、新输入 7 位拒绝。

实现 ScaledUnits 和 AssetPolicy。

验收：纯单测通过，golden vectors 固化。

Task 0.2 会计不变量

失败测试：

少于两个 posting；

零/负 amount；

跨 Book；

account/asset 不匹配；

每资产不平；

FX 无 trading leg；

reversal 不完整。

实现唯一 JournalValidator。删除第二套规则前先做 differential tests。

重做条件：validator 需要查询投影余额才能判断普通交易平衡，说明边界设计错误。

Phase 1：V2 schema
Task 1.1 核心目录 DDL

失败测试验证：

Book/Account/Asset 复合 FK；

account 有 posting 后不能改 asset/book；

ledger_scale 不可修改。

实现 Alembic v2_0001_core_catalog.

Task 1.2 Event store DDL

失败测试：

duplicate book position；

duplicate stream version；

无效 hash 长度；

event schema version 非正；

非 object payload。

实现 event/head tables。

Task 1.3 Journal projection DDL

失败测试：

跨 Book posting；

account asset mismatch；

duplicate position；

延迟提交时不平账被拒绝；

平衡交易可原子提交。

实现 constraint trigger。

验收：只认 PostgreSQL 17 integration tests。

回滚条件：任何关键约束只能靠 API router 保证。

Phase 2：Event append 与幂等
Task 2.1 Canonical event codec

失败测试：

dict key 顺序不同 hash 相同；

timezone 表示统一；

Decimal/float 不可进入 payload；

schema upcaster golden fixtures；

单字节变化改变 hash。

实现 typed event registry 和 canonical serializer。

Task 2.2 Book append

失败测试：

同 Book 并发 position 连续；

不同 Book 并发；

stream expected version 冲突；

head CAS 失败整体回滚。

实现 PostgresEventStore.append_batch()。

Task 2.3 Receipt

失败测试：

跨进程同 key 同 payload；

不同 payload；

handler exception；

connection kill；

response loss；

commit outcome unknown retry。

实现单事务 receipt。

验收：至少两个独立 Python 进程对真实 PostgreSQL 测试。

Phase 3：Journal commands
Task 3.1 Post transaction

失败测试覆盖 standard、opening、adjustment、transfer、FX。

实现：

typed command

authorization

account lookup

validation

event construction

sync transaction/posting/balance projections

completed receipt response

验收：API 成功返回后新连接立即读到相同 balance 和 position。

Task 3.2 Reversal

失败测试：

同一交易并发冲正；

跨 Book 引用；

反向 side/units 不完全一致；

replay；

reversal-of-reversal。

实现完整 compensating event。

Task 3.3 Classification

失败测试：

reporting change 不改变余额；

replace-all revision；

stale expected revision；

historical category snapshot；

replay结果确定。

Phase 4：查询与 API V2
Task 4.1 Journal queries

失败 contract tests：

pagination 按 (effective_at, book_position) 稳定；

as_of_position 正确；

无全库 cache；

worker A 写、worker B 立即读。

Task 4.2 Account balance

失败测试：

projection balance；

posting 聚合 reference balance；

多资产；

reversed transaction；

closed account 历史读取。

API 使用 as_of_book_position，彻底删除伪 as_of_ledger_version。

Task 4.3 CLI

CLI 只调用 V2 API，不复制会计规则。金额仍作为 string 传输。

Phase 5：异步投影与投资
Task 5.1 Projector framework

失败测试：

checkpoint 与写入原子；

crash 重试；

duplicate delivery；

unknown event version 暂停；

shadow rebuild/swap。

Task 5.2 Budget/net worth

先实现最小 report projection；证明 framework 可重建，不扩展 ERP 范围。

Task 5.3 Lot

先写 acquisition/disposal/FIFO allocation golden cases，再实现。V2 首次发布若投资不是 cutover 必需，可在普通账本完成后单独 gate，但模型位置现在固定。

Phase 6：Backfill
Task 6.1 Canonical extract

失败测试：

source row 顺序变化不影响 manifest；

snapshot/schema 不匹配拒绝；

checkpoint 恢复无漏行。

Task 6.2 Normalize

使用已恢复的生产 fixture，覆盖 legacy signed、reversal、category snapshot、USDT 8 位。

Task 6.3 Load/re-run

失败测试：

同 snapshot 执行两遍零新增；

中途 kill 后续跑；

source row 变化导致 manifest mismatch；

quarantine 阻止 seal。

Task 6.4 Independent verifier

不得 import production projector。完成逐 Book/account/asset parity 和 terminal hash。

验收：从同一 dump 两次空库演练输出完全相同摘要。

Phase 7：删除 V1 与最终 gate

V2 contract tests 通过。

Router/CLI 切换到 V2。

删除 hydration/cache/facade/legacy amount path。

rg 门禁确认无运行时 String amount cast、legacy signed command。

全套 PostgreSQL、concurrency、replay、backfill 测试。

构建镜像并仅部署隔离 staging。

当前阶段停止，不部署生产。

13. 运维与可观测性

必须暴露：

ledger_command_duration_seconds{operation,outcome}

ledger_append_duration_seconds

ledger_events_appended_total{event_type}

ledger_book_lock_wait_seconds

ledger_stream_conflicts_total

idempotency_replays_total

idempotency_payload_conflicts_total

commit_outcome_unknown_total

projection_lag_events{projection}

projection_lag_seconds{projection}

projection_failures_total{projection,event_type}

hash_chain_verification_timestamp

hash_chain_verification_failures_total

balance_parity_mismatches

unbalanced_transaction_rejections_total

backfill_rows_total{entity,status}

backfill_quarantine_total{reason}

backfill_last_source_key

backfill_terminal_hash_match

告警：

任意 hash mismatch：P0，暂停写入调查。

同步 balance parity mismatch：P0。

processing receipt 在已提交数据中出现：P0。

异步 projection lag 超 5 分钟或指定 event count：P1。

backfill quarantine > 0：切换阻断。

Book lock p95 持续升高：容量/热点预警。

日志必须带：

command_id

hashed idempotency identity

book_id

event position range

correlation_id

projection name/version

禁止记录原 idempotency key、凭据、完整附件或敏感 memo。

每日/每次发布后巡检：

增量 hash chain；

随机账户 posting 聚合 vs balance projection；

head vs terminal event；

orphan/复合归属检查。

14. 风险登记
严重度	风险	预防	检测
P0	USDT 8 位被舍入	ledger 8/input 6，历史策略显式分离	golden fixture、逐 posting units parity
P0	并发幂等双写	receipt/event/projection 单事务 + 唯一索引	多进程并发测试、duplicate command metric
P0	跨 Book posting 污染	三列复合 FK	schema test、cross-book verifier
P0	不平账事件被提交	application validator + deferred trigger	commit rejection、周期 parity
P0	Backfill 漏行或重复	UUIDv5、keyset checkpoint、source manifest	count/parity、二次空库重跑
P0	Projector bug 改错余额	同步 projector 小而纯、reference reducer	balance parity、shadow rebuild
P1	Hash 在版本升级后不稳定	固定 canonical codec 与 golden bytes	CI hash fixtures、全链复算
P1	FX 被错误折算平账	每资产平衡 + trading accounts	FX invariant tests
P1	Reversal 只记录引用，未来生成结果漂移	事件保存完整反向 posting	exact reversal verifier
P1	Investment FIFO 重放结果随规则改变	disposal event 固化 allocation	lot replay golden cases

额外需要持续控制的两项：

大型 facade 复活：通过模块依赖测试禁止 domain/application import API 或旧 service。

用 SQLite 结果替代 PostgreSQL gate：CI 将 PostgreSQL integration/backfill/concurrency 设为强制检查。

15. 明确推荐版本与最少用户决策
推荐版本

Track Anywhere V2.0 Ledger Core：

PostgreSQL 17

财务事实事件溯源

目录与工作流 CRUD

每 Book position/hash chain

每 stream optimistic version

单事务跨进程幂等

Journal/Posting/Balance 同步投影

Reporting/Budget/Net Worth/Lot 异步或独立投影

正整数 numeric(38,0) units

USDT ledger_scale=8 / input_scale=6

FX 显式 trading accounts

V1 固定 dump → 确定性 backfill

无兼容层、无 dual-write、当前不部署生产

仍需用户选择的事项

架构层面只剩两个非阻断产品选择，不应阻塞核心实现：

USDT 默认 display_scale 采用 2 还是 6。
推荐 6，UI 可按场景去除尾零。

V2 首次 cutover 是否同时开放 investment lot UI。
推荐核心账本先发布，lot schema/event contract 同期落地，但 UI 和高级收益报表作为后续独立 gate。

除此之外，事件边界、金额模型、幂等策略、投影一致性、backfill 和切换方式都不应再留作实施阶段临场决定。当前下一步应从 Phase 0 金额与会计不变量测试 开始，而不是继续扩写架构文档或修补 V1。
