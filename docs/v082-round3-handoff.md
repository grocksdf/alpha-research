# v0.8.2 真实 LLM 闭环验收 —— 第三轮问题与结果（移交开发方）

> 本材料仅记录第三轮（数据库 `state/v082-real-llm-fresh-v3.sqlite3`）的**问题与结果**，不含前两轮，不含解决方案建议。

## 一、运行环境（第三轮）

| 项 | 值 |
| --- | --- |
| 代码版本 | v0.8.2（HEAD `813d4b7`） |
| 模型 | `qiniu/conatus-free`（七牛 qnaigc 网关，`https://api.qnaigc.com/v1`） |
| 推理参数 | `reasoning_effort = low` |
| 模式 | `mode=demo`，`live_research_enabled=false`，`submission_enabled=false` |
| 数据库 | `state/v082-real-llm-fresh-v3.sqlite3`（全新） |
| 运行时长 | 4 分 8 秒 |

## 二、最终状态

- 验收结果：`BLOCKED_OR_FAILED`
- `error_type`：`ResearchError`
- `reason`：`未经核实的试验不能生成有证据知识`
- 真实 BRAIN Simulation：0
- 真实 Submit：0

## 三、发生了什么（完整过程）

batch 1 的五角色全部完成（explorer → strategist → worker → critic → playbook），但在 **Playbook 知识固化（`learn()`）阶段**抛异常中止，后续批次未执行。

具体链路：

1. Explorer 产出 4 条假设。
2. Worker 产出 6 次公式（4 个初始提案 + 2 个变体），全部通过确定性校验。
3. Critic 审查：6 次经济审查，其中 1 次 `defer`（生成了修订项）。
4. 3 个候选入队（`v08_queue`）。
5. **2 个候选完成 Demo 模拟**（synthetic），**1 个候选停留在 `queued` 未模拟**。
6. Playbook 产出 4 条知识提案，其中 2 条引用了那个**未模拟的 queued 候选**作为「支持证据」。
7. `learn()` 校验该候选证据等级为 `unverified`，抛出 `ResearchError("未经核实的试验不能生成有证据知识")`，中止 batch 1。

## 四、核心问题（一句话）

**Playbook 在 `learn()` 阶段把「仅被 Critic 批准、但从未执行过模拟」的候选（`10fc2baf`，`trial_id=None`，证据等级 `unverified`）当成了支持证据来引用，触发了 `autoresearch_v08.py:808` 的 fail-closed 门禁，导致整个 batch 中止、`v08_knowledge=0`。**

## 五、问题定位（代码位置）

- 触发点：`alpharesearch/autoresearch_v08.py:808`
  ```python
  if "unverified" in grades:
      raise ResearchError("未经核实的试验不能生成有证据知识")
  ```
- 证据等级判定：`alpharesearch/autoresearch_v08.py:656-668`（`evidence()`）
  - 有 trial 且 `synthetic=true` → `synthetic_process`
  - 有 trial 且 `mode=live` → `research_is`
  - 无 trial（`trial_id=None`）→ `unverified`

## 六、结果数据（全部为真实落库数据）

### 6.1 假设（4 条，`v08_hypotheses`，`parent_id` 全部 None）

| ID 前8位 | mechanism（摘要） |
| --- | --- |
| `014c7c57` | Close-to-VWAP 偏离回复 |
| `4196e894` | 成交量异常（注意力代理） |
| `692ee463` | 隔夜/日内客户群差异 |
| `fd1322c1` | 成交量条件化短期反转 |

### 6.2 候选（3 条，`v08_queue`）

| ID 前8位 | trial_id | state | reason |
| --- | --- | --- | --- |
| `10fc2baf` | **None** | `queued` | LLM_critic_approved_AST_verified（**未模拟**） |
| `3bd4479b` | `f76df8cd` | `evaluated` | result_saved |
| `e20ed405` | `5c8f02ba` | `evaluated` | result_saved |

### 6.3 模拟结果（2 条，`results`，均为 synthetic）

| trial_id | 表达式 | fitness | sharpe | mode |
| --- | --- | --- | --- | --- |
| `f76df8cd` | `-ts_rank(ts_sum(returns, 5), 20) * (1 - group_rank(ts_mean(volume, 5), subindustry))` | 1.28 | 1.60 | demo |
| `5c8f02ba` | `-group_rank(ts_mean(close / vwap - 1, 10), subindustry)` | 1.504 | 1.88 | demo |

（alpha_id 为 `DEMO_` 前缀，`synthetic=true`，合成数据非真实市场收益）

### 6.4 修订（1 条，`v08_revisions`）

| ID 前8位 | state | depth | 说明 |
| --- | --- | --- | --- |
| `c4578eaf` | `pending` | 1 | Critic `defer` 生成，未被子流程消费 |

### 6.5 Playbook 知识提案（4 条，`v08_role_payloads` role=playbook）

| item | supporting_evidence_ids | 结果 |
| --- | --- | --- |
| item0 | `10fc2baf`（未模拟） | ❌ 触发 unverified 拦截 |
| item1 | `e20ed405`（已模拟） | 可通过 |
| item2 | `3bd4479b`（已模拟） | 可通过 |
| item3 | `e20ed405` + `3bd4479b` + `10fc2baf`（含未模拟） | ❌ 触发 unverified 拦截 |

### 6.6 用量与安全

| 项 | 值 |
| --- | --- |
| LLM 调用 | 5 次（五角色各 1 次，全部 `completed`） |
| input / output tokens | 19862 / 19793 |
| 费用 | usd=0.0（`reviewed_zero`） |
| 耗时 | ≈243 秒 |
| `v08_knowledge` | 0 |
| 真实 BRAIN Simulation | 0 |
| 真实 Submit | 0 |
| 父子假设（parent_id 非空） | 0 |

## 七、事件序列（关键事件，`events` 表，按 id 序）

```
v08_batch_started
v08_llm_endpoint_authorized（1 次）
5 × v08_llm_request_started → v08_llm_budget_preflight → v08_llm_budget_observed
  → v08_role_payload_archived → v08_llm_request_completed → v08_role_provenance
4 × v08_hypothesis_created
6 × v08_formula_validation_attempt
6 × v08_critic_economic_review（含 1 次 v08_critic_deferred）
3 × v08_candidate_queued
2 × trial_created → simulation_reserved → task_state(polling) → result_recorded → v08_candidate_state(evaluated)
1 × v08_memory_retrieved + 1 × v08_memory_influence
v08_control(action=fault, state=paused_fault)  ← ResearchError
v08_batch_failed(error_type=ResearchError, no_fallback=true, no_retry_of_uncertain_writes=true)
v08_batch_terminal(reason=ResearchError, status=failed)
```

## 八、关键事实清单（供开发方核对）

1. 五角色单批全部完成，证明 v0.8.2 的 2048 输出上限、工具调用解析、公式校验、Critic 队列推进在本轮均正常工作。
2. 唯一的失败点在 **Playbook 的 `learn()` 知识固化**：LLM（playbook 角色）把未模拟候选 `10fc2baf` 当作支持证据引用。
3. `10fc2baf` 的状态是 `queued`、`trial_id=None`、reason=`LLM_critic_approved_AST_verified`——即「被 Critic 批准、通过了 AST 校验，但尚未执行模拟」。
4. 门禁按设计 fail-closed 拦截，未伪造知识、未清库重造。
5. 证据等级三态：`synthetic_process`（有模拟+合成）/ `research_is`（有模拟+live）/ `unverified`（无模拟）。

## 九、证据文件

| 文件 | 说明 |
| --- | --- |
| `state/v082-real-llm-fresh-v3.sqlite3` | 第三轮完整实验库（含全部事件、请求、载荷来源证明） |
| `state/v082-real-llm-acceptance-v3.json` | 第三轮验收脚本报告（BLOCKED） |
| `config/v082-approved-local.toml` | 本地配置（gitignored） |
