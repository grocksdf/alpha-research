# v0.8.1 Playbook 记忆分析（验证 D）与对照实验方案（验证 §8）

数据源：`state/v081-real-llm-fresh.sqlite3`（run1）。
结论先行：**Playbook 本轮未被实证使用；记忆提升研究效果无因果证据。**

---

## 一、验证 D：Playbook 是否真正发挥作用

### 1. 数据库实测

| 表 / 收据 | 值 | 说明 |
| --- | --- | --- |
| `v08_memory_retrieved`（事件） | 2 条，`retrieved_revision_ids: []`，`excluded_unreliable: 0` | 两次批次启动均读取记忆，但**无任何记忆可读** |
| `v08_memory_influence`（三态收据） | 2 条：`retrieved []` / `referenced []` / `applied []` | 三态全空 |
| `v08_knowledge`（知识条目） | **0** | 无 Playbook 知识被生成 |
| `v08_playbook_revision` 相关 | `knowledge_revisions: []` | 无修订 |
| `v08_role_payloads`（原始响应） | 7 条（explorer×2, strategist×2, worker×2, critic×1） | 原始响应已完整归档 |
| `v08_queue` / `trials` | 0 / 0 | 无候选、无模拟 → 无证据可提炼 |

### 2. 三态严格区分（代码已实现，本轮数据印证）

代码位置：`autoresearch_v08.py:544-554`

| 态 | 定义（代码语义） | 本轮 |
| --- | --- | --- |
| **Retrieved** | 系统从 `v08_knowledge` 读取并提供给模型的历史记忆 ID | `[]` — 首轮无知识，符合预期 |
| **Referenced** | 模型在 `decisions[].memory_ids_used` 中引用、**且通过 `validate_memory_references` 校验**的可追溯 Memory ID | `[]` |
| **Applied** | 模型在 `decisions[].memory_application[]` 中给出 `{memory_id, decision_change, evidence_rationale}` 三元组的结构化影响说明 | `[]` |

三态收据还固定携带：

```json
"decision_explanations_are_not_causal_proof": true
```

### 3. 记忆引用校验的严格性（即使本轮为空，机制已核验）

`validate_memory_references`（`autoresearch_v08.py:442-467`）对每条引用强制：

1. 必须是本批次**已检索**集合的子集，且无重复 → 否则「引用了未检索或重复的记忆 ID」
2. 必须属于**同一 project_id** → 禁止跨项目引用
3. 必须通过 `_eligible_memory_revision` → 禁止引用**已撤销或无有效证据**的记忆
4. 必须是该 topic 的**最新版本** → 禁止引用**已被替代的旧知识**
5. 摘要必须与持久化证据一致（`revision_evidence_sha256` 比对）→ 防止篡改

对应测试已通过（`test_superseded_memory_cannot_be_forced_back_into_strategist`、`test_unretrieved_memory_reference_is_rejected_before_simulation`、`test_memory_retrieved_but_ignored_is_not_applied`）。

### 4. 特别检查项：模型拿到记忆却完全忽略 —— **本轮不适用**

任务要求特别检查「模型虽然拿到了历史记忆，但完全忽略它时，不能被判定为记忆实际应用」。

本轮 `retrieved_revision_ids = []`，即**记忆集合本来就是空的**，因此：
- 不存在「拿到却忽略」的情形
- 也更不可能构成 `memory_applied`

### 5. 一个值得记录的事实：记忆说明机制本身在工作

虽然无记忆可用，但 strategist 的 `memory_effect` 文本字段**确有实质内容**：

| 批次 | 假设 | `memory_effect` 原文（摘录） |
| --- | --- | --- |
| 1 | idx0 | 「No prior experimental evidence exists; this is the first test. Will establish baseline performance metrics for volume-price divergence mechanism.」 |
| 1 | idx1 | 「No prior experimental evidence; first validation test. Will determine whether intraday range expansion captures exploitable mean reversion distinct from standard reversal factors.」 |
| 2 | idx0 | 「Initial microstructure pressure test; results inform whether divergence is transitory or informational」 |
| 2 | idx1 | 「Volume-conditioned reversal test; clarified formulation avoids sign ambiguity and improves interpretability」 |

这说明 **memory_effect 的收集与归档链路是通的**——模型明确陈述了「无历史记忆 → 本次作为基线」。缺的是**可供引用的记忆本身**。

### 6. 一致性检查

要求「原始 Strategist 响应、事件收据和证据引用彼此一致」。本轮：
- 原始响应（`v08_role_payloads.body_json`）的 `memory_ids_used = []`
- 事件收据（`v08_memory_influence`）的 `referenced = []`
- 两者**完全一致** ✅
- strategist 决策回读校验（`autoresearch_v08.py:505-512`）未触发异常

### 7. 验证 D 结论

**Playbook 未被实证用于决策。** 三态 retrieved/referenced/applied 全为 `[]`，`v08_knowledge = 0`。

**记忆提升研究效果：无因果证据。** 代码自身亦置 `memory_effectiveness_causally_proven: false`。

必须明确：`memory_applied` 表示存在**可审计的模型决策说明**，**不等于**已用因果实验证明记忆提高了 Alpha 研究效率。

---

## 二、验证 §8：正式记忆效果对照实验方案

### 1. 现有 Benchmark 能力评估

`benchmarks/evaluate_memory.py`（91 行）是**独立、只读的 A/B 评估器**：

- 输入：预先冻结的 JSONL 记录
- 四臂常量：`ARMS = ('rule_random_baseline','llm_no_memory','llm_full_playbook','llm_shuffled_memory')`
- 输出指标：`attempted` / `valid_rate` / `duplicate_rate` / `feedback_revisions` / `simulation_count` / `mechanism_diversity` / `legal_candidates_per_simulation` / `platform_checks_passed` / `independent_passes` / `evidence_grades`
- 强制约束：
  - 双臂必须**配对**（同一 task_id 四臂齐全），否则 `INCOMPLETE`
  - 双臂的 `authorized_catalog_sha256` 与 `budget_sha256` 必须相等，否则 `INCOMPLETE`
  - `evidence_grade` 只能是 `synthetic_process` / `research_is` / `verified_market`
  - 只要任一臂非 `verified_market`，整体状态降为 `ENGINEERING_ONLY`
  - 恒返回 `memory_improvement_proven: false`、`real_alpha_effectiveness_proven: false`

**能力判定：具备完整的离线记录与配对校验骨架，但当前只能进行离线记录/一次演示，无法自动产生因果证据。**

### 2. 四臂对照方案（不泄漏留出数据）

| 臂 | 处理 | 目的 |
| --- | --- | --- |
| **A. `llm_no_memory`** | LLM 全程不提供 Playbook 记忆（检索结果置空） | 基线：无记忆时的机制多样性/合法率/重复率 |
| **B. `llm_full_playbook`** | LLM 提供本项目完整、最新、未被替代的 Playbook 知识 | 处理组 |
| **C. `llm_shuffled_memory`** | 提供**打乱配对**或**跨项目无关**的记忆（同体量、同格式） | 对照：区分「记忆内容」与「 merely 提供上下文」的效应 |
| **D. `rule_random_baseline`** | 独立规则/随机搜索基线 | 下界参照：检验 LLM 是否优于朴素搜索 |

### 3. 必须统一的维度（任务要求）

| 维度 | 要求 |
| --- | --- |
| 研究任务 | 同一 `task_id`、同一 Objective 文本 |
| 字段目录 | 同一 `authorized_catalog_sha256` |
| 验证器 | 同一 evaluation / AST / 字段 / 算子 / 语义管线 |
| 资源计量 | 同一 `budget_sha256`（simulations、LLM calls、tokens、USD） |
| 评价方法 | **预注册**：指标定义、显著性检验与多重比较校正须在实验前固定 |

### 4. 预注册评价方法

- **主指标**：`legal_candidates_per_simulation`（每单位模拟产生的合法候选数）
- **副指标**：`mechanism_diversity`、`duplicate_rate`、`feedback_revisions`
- **统计**：对每个 `task_id` 在四臂间做配对比较；多重比较需预注册校正（如 Holm–Bonferroni）
- **排除条件**：任一臂缺失或资源不等 → `INCOMPLETE`，**不得**输出优越性结论

### 5. 当前状态标记

> **因果效果尚未证明。**

本轮仅完成 1 个批次、零模拟、零知识条目，**连四臂中的一臂都未形成完整记录**。因此：

- **不**将本轮三轮实验解释为统计显著的学习效果
- **不**声称 Playbook 提升研究效率
- 现有 `benchmarks/evaluate_memory.py` 只能在未来真实配对数据齐备后给出 `PAIRED_DESCRIPTIVE_ONLY` 或 `ENGINEERING_ONLY`

### 6. 达成可判定所需的条件

1. 解除 P0-1（2048 输出上限），使 ≥3 批次可稳定完成
2. 每臂至少 N 个配对 `task_id`（N 需按预注册的功效分析确定，本轮不具备）
3. 所有臂使用同一冻结计划下的授权目录与预算（当前 `plan_sha256` 已可冻结，具备条件）
4. 严格保持 `protect_final_holdout = true`，禁止在研究过程中触碰留出集

---

## 三、小结

| 问题 | 结论 |
| --- | --- |
| Playbook 是否生成了知识？ | 否（`v08_knowledge = 0`，因无实验证据可提炼） |
| 是否被检索？ | 检索了，但集合为空 |
| 是否被引用？ | 否 |
| 是否被实际用于决策？ | **否** |
| 记忆提升是否有因果证据？ | **无** |
| 现有 Benchmark 能否支持四臂对照？ | 有骨架（配对/资源相等/证据等级校验齐备），但**当前只能离线记录，因果效果尚未证明** |
