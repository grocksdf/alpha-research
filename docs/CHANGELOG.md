# V0.6.0 — Autonomous discovery MVP

- Added one-model structured economic hypotheses and formulas, catalog-grounded research domains, deterministic planning and a bounded simulation/feedback/revision loop.
- Added immutable plan/run binding, event checkpoints, parent hypothesis links, same-plan resumption, GET-only recovery and current-preview per-invocation approval. All deterministic sensitivity trials share the cap.
- Reused discovery-only historical memory; model inputs exclude raw responses, normalized return documents, final holdouts and forward samples.
- Added preliminary candidate reports using the existing fixed evaluator, with missing evidence and risks kept explicit. No automatic submit tool.
- Restored the v0.5.1 requirements absent from the public v0.5.0 repository: evaluator and independent final submission revalidate the return document and bind it to approved Alpha/account/formula/settings/daily evidence.
- 302 offline tests pass: all 270 baseline cases retained plus 32 new cases. Demo templates and synthetic simulations are labeled. No real model/account/market acceptance or GitHub push.

# V0.5.0 — Verified returns and offline robust research

- Added explicit cash-PnL/NAV/simple-return contracts, audited calendar/sample/cost semantics, and immutable normalized series. Existing-Alpha GET audits can export verified IS inputs; missing evidence stays explicit.
- Added seeded moving-block bootstrap, shared anchored-window calculations, full frozen parameter neighborhoods, and incremental cost scenarios without double-charging base net costs.
- Added conditional CSCV PBO and DSR, with conservative minimum data and full-history/independence requirements; unavailable is never reported as zero overfitting risk.
- Added read-only local attempt inventories retaining invalid, failed, unfinished and cached proposals without claiming account-wide completeness.
- Added frozen one-dimensional evolution plans and offline deduplication previews. No evolution executor or additional Simulation approval; default planned budget remains zero.
- Added reproducible JSON reports that cannot override the existing evaluator, Check or identity gates. No new database schema/migration; old snapshots and records remain intact.
- Shipped explicitly synthetic examples and v0.4.2 upgrade/rollback instructions. No real BRAIN evidence, market validation, model comparison, submission or remote CI performed.
- 270 offline tests pass (212 retained, 58 added), including constructed PBO=0/PBO=1 cases and DSR probability at its analytical null threshold.

# V0.4.2 — Resume approval and corrupt history

- Added GET-only resume dry-run and uniform live confirmation, rechecked inside Engine after authentication.
- Queued tasks and deterministic sensitivity share one per-invocation cap, bounded by the original run/design budget; read-only resume has zero creation permission.
- Guarded historical result/metrics container types before completion checks. Invalid rows remain unresolved without blocking valid-row migration or releasing locks.
- 212 offline tests pass: v0.4.1's 192 plus 20 new regressions. No live account operations or remote CI executed.

# V0.4.1 — Strict serialization, parsing and offline preview

- Serialized account/API claims across runs and research contexts, including polling and unresolved paused/failed tasks; added explicit remote lifecycle state and conservative migration.
- Rejected duplicate/empty/invalid recordset schema names, including empty and object-row recordsets.
- Added offline dry-run, deduplicated creation estimates, stale-preview rejection and per-invocation creation caps for run/directed-run.
- Added migration, cross-run/concurrent claim, preview and release-integrity regressions.
- Added CI configuration, fixed-source ZIP verification and a limited credential-pattern scan. Remote CI and real account acceptance have not run this session.

# V0.4.0 — Recovery and evidence before optimization

- Account-list/detail GET recovery for dead Locations and ambiguous creates; zero/multiple/incomplete matches fail closed.
- Persisted account/API/dispatch/Alpha bindings; uncertainty blocks new writes in the research context. Default resume creates no simulations or neighbors.
- Upgrade-safe GET recovery preserves historical snapshots and simulation spend.
- Shared key-setting protocol permits extra server dates while enforcing actual parameter conflicts.
- Existing-Alpha audits retain provenance and unverified PnL date/schema diagnostics without guessing units, calendars or IS bounds.
- PnL/capital units must agree; explicit SELF_CORRELATION PASS is required, even with numeric correlation evidence.
- Frozen six-formula reversal/VWAP plan, stable budget, single-parameter logs and one-shot reporting of all prespecified independent comparisons.
- 160 offline tests pass. No live account, real evidence, simulation, market validation, model inference or submission executed this session.

# V0.3.5 — Live settings reconciliation

- Real BRAIN smoke testing showed that Alpha details may append server-owned settings such as `startDate` and `endDate`.
- Probe/evidence reconciliation now requires every requested setting to match while allowing additional server metadata.
- Changed requested scope values still fail reconciliation.
- Added a regression test; 129/129 tests pass. The original Alpha was recovered through owner-scoped GET endpoints without repeating the Simulation POST.

# V0.3.4 — Windows/UTF-8 compatibility

- All production text/config/report reads and writes now explicitly use UTF-8.
- Probe atomic persistence uses a writable descriptor for `fsync` on Windows.
- Windows test fixtures that read generated UTF-8 artifacts now specify UTF-8.
- The original V0.3.3 archive manifest was verified before changes; 128/128 tests pass on Windows with Python 3.11.
- No research policy, evaluator threshold, BRAIN permission gate, or submission behavior changed.

# V0.3.3 — IS 日历首尾边界修复

- 创建上下文与缓存校验共用首尾边界异常检测，使用既有 14 天协议阈值。
- 首尾截断的旧缓存即使标记 complete，也不能通过当前复核或稳定性评价；历史事实不重写。
- 复现原版创建/缓存两个失败路径；新增 9 项回归，共 128 项通过，覆盖单侧缺失及 14/15 天阈值。
- 不改变固定窗口策略，不新增 Agent 功能；真实语义与市场有效性尚未验证。

# V0.3.2 — BRAIN Evidence Reliability

- `pnl_evidence.py`：显式累计现金 PnL、固定本金、无重置及收盘会话语义契约；区域与覆盖范围匹配的审核日历和 SHA-256。
- 仅相邻交易会话产生单日差分；缺失会话、非会话记录及边界缺失单独记录。异常长会话间隔拒绝；不把多年差分当作单日观测。
- 日频证据完整性在评价时重新核验，不信任缓存的 complete 标志。无契约默认不取 PnL；旧缓存不能自动升级成可靠日频证据。
- 固定 126 会话/至少 4 窗替代 3 个完整自然年，窗口锚定 IS 首日，完整覆盖，尾部单列；Pareto 字段改为 worst_window_sharpe。这是不同的研究政策，需重新冻结，不能混合比较新旧运行。
- `probe-evidence` 只 GET 已有 Alpha，完整对账与来源指纹；脚本响应不计为真实验收。
- 前瞻 BRAIN 路径使用同一日频契约，拒绝跨缺口追加。保留隔离的前瞻账本与共享留出登记。
- 未执行真实账户、真实 AI/市场数据验证；真实批量研究及提交默认关闭。

# V0.3.1 — 研究证据隔离修复

- `holdout_registry.py`：独立揭示登记、稳定数据身份、固定协议、重叠区间拒绝、规范化观察指纹、并发原子预留、冻结后登记身份核对；失败也消耗揭示权限。
- 旧版已暴露区间显式导入，不重新计算；旧 manifest 禁止继续揭示。导入重叠区间保留各自全部覆盖范围。
- BRAIN：取消年份白名单；默认未核实的 IS 日期契约关闭序列读取，核实后按精确双端点过滤；排除跨边界年度汇总。
- 评价器与报告：旧缓存无精确来源则隔离；新增研究待验证队列，提交门槛不降低；GET-only 证据刷新不消耗模拟额度。
- 明确本地规模、设置不等价与本地冒烟凭据的信任边界。
- 保持一个 Research Agent，真实批量研究/提交默认关闭。

# 本次增量

输入为本次上传的 V0.1.1，保留其正确的 Pareto 分层和单 Alpha 协议。

## V0.1.1 正确性补完

新增 research_intents / intent_states / sensitivity_refs，区分公式模拟任务与研究意图。重复公式不覆盖来源，多假设均可独立链接、放弃和报告。candidate_promotions 仅为兼容旧状态保留。新增针对多次提升、预算用尽后的缓存引用、父假设歧义和排序稳定性的测试。

## V0.1.2 接入验收工具

只读探测默认只 GET 目录/算子；固定 rank(close) 单次创建、保存 Location、GET 恢复、人工对账、账户/设置校验。新增形状脱敏记录；关闭真实循环和提交。真实循环入口核对匹配账户/范围的已完成 HTTP 冒烟记录，fixture 不解锁。未执行真实账户验收。

## V0.2 研究支持

有界 arXiv 元数据检索和缓存、字段搜索、证据关联记忆、家族标签、时间/token/费用账本、两研究臂协议核验和效率比较。Agent 工具权限不包含最终留出、追踪、提交或任意网络/文件访问。未执行真实模型赛马或证实效率收益。

## V0.3 时间有效性协议

预注册并冻结数据、源码、公式和规则；因果日频内核、单日收益标签、PIT/交易日历输入约束、Walk-Forward 与 embargo、单次最终揭示和归档恢复；独立的前瞻注册/观测账本。仅完成合成数据协议验收，真实数据与时间观察仍待完成。
