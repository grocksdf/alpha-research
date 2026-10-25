# AlphaResearch v0.7 连续自主研究验收报告（v07-autoresearch-acceptance）

> 验收人角色：首席 AI 量化工程师 / Autoresearch 测试工程师 / 安全审计。
> 执行日期：2026-10-25。代码基线：`28a6d40`（main，工作树干净）。
> 合规声明：本任务全程**零真实 BRAIN 请求、零真实 Submit、零账号/密钥泄漏、零历史数据库写入**。所有 LLM 调用为真实 API（qnaigc/kimi-k3-free），所有模拟为本地 `DemoSimulator`（合成、`mode=demo`，无 BRAIN）。合成 Sharpe/Fitness 仅用于工程闭环验证，不作为真实绩效。

---

## 0. 结论速览（回答 A–E）

| 问题 | 结论 | 证据等级 |
| --- | --- | --- |
| **A. v0.7 是否真正完成？** | 工程闭环**完成**（代码、安全门禁、真实 LLM 多轮实验、连续运行均验证通过）；但"生产级高质量 Alpha 能力"未证明（无真实 BRAIN 对照）。 | 强（392 回归 + 50 安全测试 + 真实 LLM 4 轮） |
| **B. 系统是否真正自主发现 Alpha？** | **部分成立**：系统自主提出了机制互异、带文献出处的假设并闭环修订；但全部基于合成指标，未在真实数据/BRAIN 上验证，故"发现有效 Alpha"**未证实**。 | 中（有真实 LLM 闭环，无真实绩效） |
| **C. v0.8 是否具备部分记忆能力？** | **是（初步）**：`historical_feedback()` 将历史研究注入新上下文；A/B 对比显示 B 组（有历史）避开了已失败的动量/波动公式、倾向机制互异修订。属初步证据，非干净因果。 | 中（A/B 初步，样本小、非确定性） |
| **D. 能否进入真实 BRAIN 多轮？** | **条件具备**：代码与门禁就绪；但需用户显式授权 + 冻结计划稳定 + 冒烟收据。本轮未授权，未执行。 | 强（代码路径 + 门禁验证） |
| **E. 高质量真实 Alpha 缺什么？** | 见 §9 P0/P1/P2 清单。核心缺口：真实数据对照、独立样本外验证、自相关门禁数据、LLM 与 BRAIN 同预算对照。 | 强 |

---

## 1. §2 代码确认（最新版本核实）

核实方式：`git status` / `git rev-parse HEAD` / `git log -5` + 逐文件阅读源码（非凭旧版本猜测）。

- **HEAD**：`28a6d40 Apply P0P1 complete patch: provenance isolation, formula-quality & proposal-audit gates, live-discovery guard, offline acceptance suite`
- **分支/远端**：`main` ↔ `origin`（`github.com/grocksdf/alpha-research.git`），工作树干净（仅新增本任务交付物）。
- **代码源**：`C:/Users/19708/Documents/ChatGPT/worldquant`（用户确认的最新系统代码；D:/quant 为旧版，不作为代码源）。

六项关键要求逐条核实：

| 要求 | 结论 | 源码位置与事实 |
| --- | --- | --- |
| (a) Live 强制真实 DiscoveryAgent | ✅ | `autonomy.py` 385-389：`mode=='live'` 且生成器非 `DiscoveryAgent`（或其引擎/模型不匹配）即 `raise`；`discovery.py` 仅保留 `DiscoveryAgent` 一个发现类 |
| (b) 固定公式隔离在 Demo Fixtures | ✅ | 固定模板仅在 `_demo_fixtures.py`（`DemoDiscovery`/`CampaignDemoDiscovery`），且仅在 `generator=='demo' && mode=='demo'` 实例化（`autonomy.py` 377-381） |
| (c) LLM 提案来源记录完整 | ✅ | `add_batch`（184-241）逐条记录 `model_id`/`response_id`/`tool_call_id`/`proposal_sha256`/`batch_id`；`_require_live_item_provenance`（280-309）在任意 live 测试/恢复前重验来源收据 |
| (d) Campaign 支持多轮连续 | ✅ | `run()` 循环（349-418）以 `max_cycles` + `max_ideas` + `max_simulations` 为界连续生成-测试-反馈；`autonomy-run` 同计划断点续跑 |
| (e) 历史记忆进入新研究 | ✅ | `historical_feedback()`（172-182）按 `research_context` 摘要复用同上下文的 `research_intents`（仅过滤掉当前 run_id） |
| (f) 旧/新安全门禁保留 | ✅ | 见 §3；`live_research_enabled`/`submission_enabled`/冻结计划/`DemoSimulator` 隔离/`ProposalAudit` 全部在源码中保留且测试覆盖 |

---

## 2. §3 本地完整回归（实测命令与结果）

以下均为本次实跑、真实命令与输出（未删断言、未跳过失败项）。

### 2.1 完整离线回归套件

```
$ python verification/run_full_offline_suite.py
{"status": "PASS", "tests": 392, "live_llm_requests": 0, "brain_simulations": 0, "submits": 0}
```

- 392 个测试全部通过；`live_llm=0` / `brain_sim=0` / `submits=0`（离线套件以 `OfflineSocket` 阻断一切外连，保证无真实网络操作）。
- 输出中一条 `{"error": "真实新建前先 --dry-run …"}` 为**负向测试的预期拒绝信息**（验证 dry-run 门禁），非失败。

### 2.2 发布清单校验

```
$ python scripts/release.py --check
{"source_files_verified": 126}
```

### 2.3 Doctor（离线配置体检）

```
$ python -m alpharesearch doctor
```
关键字段：`mode=demo`、`live_research_enabled=false`、`submission_enabled=false`、`automatic_submission=false`、`approved_fields=9`、`account_profile_verified=false`、`brain_credentials_in_environment=false`、`llm_key_in_environment=false`。

### 2.4 七项安全门禁专项（50 个测试）

```
$ cd tests && python -m unittest test_live_discovery_guard test_provenance_unit_boundary test_return_contract test_submission_contract_gate
Ran 50 tests … OK
```

七项安全核对结论：

| # | 检查项 | 结论 | 依据 |
| --- | --- | --- | --- |
| 1 | Live 不得出现 DemoDiscovery | ✅ | `test_live_discovery_guard`（20 例）；`autonomy.py` 385-389 硬拒 |
| 2 | LLM 失败不得回退固定公式 | ✅ | `discovery.py` 145→181 `raise`（fail-closed）；`autonomy.py` 417 `raise` 注释"永不回退 Demo"；实验实测 B 组失败 `fallback_used=false` |
| 3 | 无来源可验证的提案不得入真实队列 | ✅ | `add_batch` 189-204 live 来源收据校验；`_require_live_item_provenance` |
| 4 | 模型身份别名规则 | ✅ | `model_identity_policy_sha256` 贯穿 `llm_batch_*` 事件；冻结模型身份与收据一致性校验 |
| 5 | 外部调用缺 ID → 本地相关 ID 或拒绝 | ✅ | `proposal_batch_id`/`response_id`/`tool_call_id` 逐条关联；缺失即 `raise` |
| 6 | 返回契约与 Submit 门禁完好 | ✅ | `test_return_contract` + `test_submission_contract_gate`；`submission_enabled=false` |
| 7 | 不能借重启/配置开关绕过冻结计划 | ✅ | `preview_plan`：study_id 已绑定不同 plan 则 `raise`；快照不一致只能恢复旧任务 |

### 2.5 一次真实回归修复记录（如实披露）

首次跑离线套件报 `FAIL`（`blocker_type=ValueError`，`清单与发布源码范围不一致`）。根因：本任务交付物 `docs/next-live-campaign-plan.md` 为新增文件，未登记进 `MANIFEST.sha256`（125→126 条目）。**修复方式**：调用官方 `scripts/release.py:refresh_manifest()`（内部含 `scan_sources` 凭据扫描，未命中任何密钥），重新生成清单后复跑 → PASS。**未删任何断言、未跳过任何测试。**

---

## 3. §4 真实 LLM 多轮自主实验（离线模拟，无 BRAIN）

### 3.1 实验设计（Group A，无历史）

- `generator=llm`（真实 API：`qnaigc` / `moonshotai/kimi-k3-free`，key 仅经 `LLM_API_KEY` 环境变量，未写入任何文件）。
- 模拟 = `DemoSimulator()`（`mode=demo`、`synthetic=true`、`alpha_id=DEMO_*`，**零 HTTP、零 BRAIN**）。
- 独立 DB（`v07-exp.sqlite3`，仓库外）、独立 plan、独立 run；`domains=["price_volume"]`（demo 目录仅授权该域 9 字段）。
- 预算：`max_cycles=4, max_simulations=12, max_ideas=12, ideas_per_cycle=3`；无硬编码 6 轮上限。

### 3.2 结果

```
RUN_STATE {"reason": "no_affordable_candidate_bundle", "status": "paused"}
COUNTS   {"simulations": 10, "proposals": 4, "llm_calls": 4}
```

- **4 个完整周期**（`autonomy_cycle=4`）、**4 次真实 LLM 批量调用**（产生 **12 条假设**）、**10 次合成模拟**（含 6 次参数敏感性邻居）。
- 三个周期行为逐条验证：
  - **Cycle 1 发现**：提出 6 个互异机制基线（5 日反转、Amihud 流动性、低波动、52 周高点动量、注意力量能、小市值规模）。
  - **Cycle 2 反馈改进**：据 Cycle 1 反馈产生**父子修订**——`rank(-ts_sum(returns,5))`（被拒 `parameter_sensitive`）→ 修订为 `rank(-ts_sum(returns*(1-ts_rank(volume,20)),5))`（量能条件化反转）；`rank(ts_rank(close,252))`（被拒 `low_fitness/low_sharpe`）→ 修订为 `rank(ts_sum(sign(returns),120))`（Frog-in-the-Pan 信息离散度动量）。修订文本引用的父指标与 `autonomy_feedback` 精确吻合。
  - **Cycle 3 继续探索**：补全价值/情绪/微观结构/换手等空白族。
- 每条假设均带 `falsification` 证伪条件、`mechanism` 机制、`source` 文献出处、`expected_sign`、`horizon_days`；结构上无两两相同（fingerprint/structural_family 各异）。
- 最终因 `no_affordable_candidate_bundle`（剩余预算不足以覆盖新候选 + 敏感性邻居）**安全暂停**，非崩溃。

---

## 4. §5 连续运行与调度验证

| 能力 | 机制 | 验证结论 |
| --- | --- | --- |
| 自动续跑 | `run()` 循环 `max_cycles`/`max_ideas` 为界连续生成-测试 | ✅ 4 轮实测 |
| 持久队列 | `ideas()` 从 memory DB 读取；`runs`/`autonomous_studies` 表跨进程持久 | ✅ 进程重启后断点续跑 |
| 去重 | `find_equivalent`/`existing_trial`（fingerprint 归一化）；`add_batch` 设 `deferred/normalized_equivalent_requires_review` | ✅ 源码 + 测试 |
| 失败记录 | `llm_batch_failed`（`fallback_used=false`）、`autonomy_idea_state`（`invalid`/`deferred`+reason）、`autonomy_state` | ✅ B 组实测 1 次失败记录 |
| 中断恢复 | `recover_plan` 只读 GET 对账；`_require_live_item_provenance` 恢复前重验来源 | ✅ 源码 |
| LLM 超时暂停+恢复 | `ResearchError` → `paused/execution_or_model_error_requires_review` + `raise`（fail-closed，不回退 Demo） | ✅ B 组实测：首轮 `KeyError` fail-closed 暂停，重跑同 plan 恢复并完成 4 轮 |
| 多样性优先于变体 | `schedule()` 优先级元组 `(existing, family_count, domain_count, similarity, nodes)`：新公式 > 未充分家族 > 未充分域 > 低 AST 相似度 > 复杂度 | ✅ 源码 + `planner_decisions` 实测 |
| 参数变体耗尽防护 | `max_candidates_per_family` 家族上限 + `len(new) > remaining` 跳过 | ✅ 源码 |

> 局限如实记录：本环境**仅支持 Demo 模拟**（`mode=demo`），`max_simulations_including_sensitivity=15` 为 demo 配置预算；真实 BRAIN 的调度/恢复路径虽已由 `BrainSimulator` + `autonomy-recover` 覆盖于代码，但本任务未做真实 BRAIN 实测（受 §10 限制）。

---

## 5. §6 研究记忆 A/B 测试（初步证据）

### 5.1 设计

| 组 | 历史记录 | 模型 | 域 | 目标 | 预算 |
| --- | --- | --- | --- | --- | --- |
| A | 无 | kimi-k3-free | price_volume | 互异假设多轮 | 同 |
| B | 提供（1 验证 + 2 失败） | kimi-k3-free | price_volume | 据历史避重 + 创新 | 同 |

- B 组种子历史（`seed_history.py` 离线注入，非真实 LLM）：已验证流动性赢家 `-rank(ts_corr(close,volume,10))`、已失败波动 `-rank(ts_std_dev(returns,30))`、已失败动量 `rank(ts_mean(returns,60))`。

### 5.2 结果与初步结论

| 指标 | A（无历史） | B（有历史） |
| --- | --- | --- |
| 假设数 | 12 | 9（另 1 条 `invalid`） |
| 精确重复种子公式 | 0 | 0（系统去重 + 模型避重共同作用） |
| 家族覆盖 | 7 族（reversion/liquidity/volatility/momentum/sentiment/value/other） | 6 族（无 value） |
| 已失败动量的处理 | 提出 `ts_rank(close,252)` 动量基线（被拒） | 未重复朴素动量；仅 1 条"路径效率条件化动量"修订（被判 `invalid`） |
| 已失败波动的处理 | `ts_std_dev(returns,60)`（窗口 60） | 主动改用窗口 20 + 提出 Parkinson 日内极差修订（`log(high*inverse(low))`） |
| 已验证流动性家族 | 提出 Amihud（不同机制） | 提出 Amihud 变体（不同机制） |

**初步证据**：B 组（有历史）表现出可观察的记忆复用——① 未逐字重复已失败公式；② 对"已失败"的动量族明显克制（仅 1 条且被验证器拒）；③ 对"已验证"的流动性族沿其机制方向延展而非复制。**但**两组的差异同时受 LLM 运行间非确定性、样本量小（各 1 次 4 轮）影响，**不能下干净因果结论**；且两组都独立发现了相同的教科书因子（5 日反转、Amihud、低波动、规模、隔夜情绪），说明 LLM 先验知识本身已包含这些因子。**无 holdout 泄漏**（两组 DB 独立，B 的历史种子为合成数据，不含任何真实样本外信息）。

---

## 6. §7 真实 BRAIN 计划（只读核验 + 方案，未执行）

详见 [`docs/next-live-campaign-plan.md`](./next-live-campaign-plan.md)。要点：

- **Run `17b489ba` 只读核验**：`git_commit=4e2f6dd` 与当时一致、4 次真实 BRAIN 模拟（真实 alpha ID、`api_base_url=api.worldquantbrain.com`）、0 提交、0 晋级；#4 为 #1 的反馈驱动子修订。本任务期间该历史库**零写入**。
- **下一轮真实 Campaign**：`autoresearch-004` 继任 study；dry-run → `--confirm-preview` → 断点续跑 → 资源边界暂停 → `autonomy-recover` 对账；Submit 始终由用户决定。
- **未授权项**：本轮不新建真实 BRAIN Simulation、不提交真实 Alpha。

---

## 7. §8 客观证据表

| 指标 | Group A（无历史） | Group B（有历史） | 合计 |
| --- | --- | --- | --- |
| 研究周期（cycles） | 4 | 4（+1 次 seed 离线周期） | 9 |
| 真实 LLM 调用（batch） | 4 | 5（4 成功 + 1 失败） | 9 |
| 候选公式（proposals） | 4（batch）→ 12 假设 | 4（batch）→ 9 假设 | 21 假设 |
| 合法/独立假设数 | 12 | 9（+1 `invalid`） | 21 |
| 被拒公式 + 原因 | 3（parameter_sensitive / low_fitness / low_sharpe） | 4（parameter_sensitive / low_fitness / low_sharpe） | — |
| 父子修订 | 2（反转 + 动量） | 1（Parkinson 波动修订）+ 1（路径效率动量，被判 invalid） | 4 |
| 本地模拟 基础/变体拆分 | 10（含 6 敏感性邻居） | 10（含 6 敏感性邻居） | 20 |
| 重复复用 | 0 精确重复 | 0 精确重复 | 0 |
| 记忆使用 | 无 | 避开已失败动量/波动、沿已验证流动性延展 | — |
| 暂停/恢复 run ID | `36ea336eb0184d80`（安全暂停） | `87c32f1cd0974d89`（首轮 fail-closed 暂停 → 重跑恢复完成） | — |
| 来源记录完整性 | 100%（model_id/response_id/tool_call_id/proposal_sha256） | 100% | — |
| **真实 BRAIN 请求** | **0** | **0** | **0** |
| **Submit** | **0** | **0** | **0** |

### LLM 用量与计费（据实记录）

| 组 | 成功调用 | 输入 token | 输出 token | 费用（USD） |
| --- | --- | --- | --- | --- |
| A | 4 | 14738 | 18733 | 未知（`usd=NULL`，`all_costs_priced=false`） |
| B | 4 | 12317 | 25718 | 未知（同上） |
| B 首轮失败 | 1（transport 未返回 usage） | **未记录**（`vendor_usage_unknown_if_transport_failed=true`） | **未记录** | **未记录** |

> 诚实声明：qnaigc 端点不返回费用，故 `usd` 一律 `NULL`，`known_usd=null`、`all_costs_priced=false`——**缺失费用未按确定零成本记录**。B 组首轮失败的调用因 transport 在返回 usage 前即失败，其 token 与费用**不可知**，如实留空、未编造为零。总 token（成功调用）A+B = 27055 输入 / 44451 输出。

---

## 8. 关键源码位置索引

- `alpharesearch/autonomy.py`：`historical_feedback()` 172-182；`add_batch()` 184-241；`schedule()` 243-278（多样性优先级）；`_require_live_item_provenance()` 280-309；`run()` 349-418（fail-closed 417）；`preview_plan()`（冻结计划校验）。
- `alpharesearch/discovery.py`：仅 `DiscoveryAgent`；`generate()` 调 `begin`/`captured`/`verify_source`，坏工具调用即 `raise`。
- `alpharesearch/engine.py`：31 行 `DemoSimulator() if mode=='demo' else BrainSimulator()`。
- `alpharesearch/brain.py`：`DemoSimulator`（383-403，合成、无 HTTP）；`BrainSimulator`（160）。
- `alpharesearch/_demo_fixtures.py`：固定模板隔离点。

---

## 9. §9 高质量真实 Alpha 缺口（P0/P1/P2）

**P0（进入真实研究前必须）**
1. 真实数据对照：用 BRAIN 真实模拟（同预算）验证当前闭环产出的因子是否具备样本外有效性；当前所有 Sharpe/Fitness 均为合成，不可外推。
2. 自相关门禁数据：demo 环境 `SELF_CORRELATION=MISSING`（`missing_check:SELF_CORRELATION`），需真实 `check/correlations/self` 数据填补。
3. 独立样本外验证（holdout）：`independent_holdout_validated=false`，需拆出真实时间外样本。

**P1（提升因子质量）**
4. 授权字段扩展：demo 目录仅 `price_volume` 9 字段；真实研究需 `approved_fields` 覆盖基本面/情绪/分析师等域（需用户授权）。
5. LLM 稳定性：kimi-k3-free 存在非确定性（B 组首轮 `KeyError`），需重试策略或更稳定模型 + 同预算对照。
6. 过拟合防线：`max_candidates_per_family` 已有限制，但跨家族/跨窗口的过拟合量化（多重检验校正）仍需补强。

**P2（工程增强）**
7. 调度器的持久化队列与 campaign 级预算（三层调度）——已登记至 `docs/brain-autoresearch-roadmap.md`，属开发方算法/架构范畴，本任务不自行改源码。
8. 费用可观测：当前 LLM 端点无费用返回，难以做预算精算；建议接入带 `usage`+`cost` 的计费端点。

---

## 10. 合规与限制确认

- 无真实 BRAIN 请求、无真实 Submit、无真实 Simulation（`real_brain_requests=0`、`submissions=0`，由 DB 查询佐证）。
- 无账号/密钥泄漏：API key 仅经环境变量，未写入任何文件（已 `grep` 复核）；无 `BRAIN_*`/`LLM_API_KEY`/token 明文进入报告或配置。
- 历史数据库（`state/autoresearch.sqlite3`、`state/phase-b.sqlite3`）零写入。
- 未删除任何安全门禁断言；一次 MANIFEST 清单刷新为官方工具、含凭据扫描，属正常发布流程。
- 合成指标始终标注 `synthetic=true` / `demo_not_live_evidence`，未当作真实绩效。
