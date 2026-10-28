# AlphaResearch v0.8.2 真实 LLM 自主发现闭环验收

**验收性质**：真实 LLM 调用 + 本地合成 Demo 实验（**零真实 BRAIN Simulation、零 Submit**）。
**结论摘要**：v0.8.2 修复了 P0-1（2048 输出上限）与 P0-2（工具调用解析），真实 LLM 探针与单批五角色闭环均实证通过；但**三轮连续研究未完成**——第三轮在 Playbook 知识固化阶段被 fail-closed 门禁正确拦截（LLM 引用未评估候选作证据），验收脚本判定 `BLOCKED_OR_FAILED`。

本报告记录真实运行结果。凡未发生、未成功或未记录的事项，一律如实标注，不以任何方式补全或美化。

---

## 1. 代码版本与测试结果（任务 1）

| 项 | 值 |
| --- | --- |
| Git HEAD | `813d4b7`（v0.8.2 补丁 `a3121c2` 已合并，随后提交 v081 交付物） |
| `__version__` | `0.6.1`（`alpharesearch/__init__.py`） |
| `pyproject.toml` version | `0.8.2` |
| **版本不一致** | ⚠️ 按要求**记录但不修改**（修改版本号会改变历史冻结研究计划的 `git_commit` 快照） |
| 全量离线测试 | **460 PASS + 301 subtests**，`live_llm_requests=0 / brain_simulations=0 / submits=0` |
| `release.py --check` | **161 个发布文件校验通过**（任务书 153 为历史参考数，当前实际 161） |
| `doctor` | demo 模式、Live+Submit 关闭、approved_fields=9、`account_profile_verified=false` |

---

## 2. 真实模型能力档案（任务 2）

配置文件 `config/v082-approved-local.toml`（**gitignored 未入库**，密钥仅走 `LLM_API_KEY` 环境变量）。

| 项 | 值 |
| --- | --- |
| 服务商 | 七牛云 qnaigc 网关 |
| 端点 | `https://api.qnaigc.com/v1`（HTTPS） |
| 模型 ID | `qiniu/conatus-free` |
| 上下文上限 | 1,024,000（官方页 1000K） |
| 输出上限 | 131,072（= 代码 `_MAX_GUARD_TOKENS` 硬上限） |
| 输出参数 | `output_token_parameter = "max_tokens"` |
| 推理参数 | `reasoning_parameter = "reasoning_effort"`，`reasoning_value = "low"` |
| 五角色输出上限 | explorer/strategist/worker/critic/playbook 各 8192 |
| `tool_choice_required_supported` | true |
| 价格审核 | `reviewed_zero`（官方页标注「限时免费 2026-10-24~11-30，每日 1 亿 Token」） |
| 远程授权 | `approved_models = ["qiniu/conatus-free"]`，审批号 `V082-LLM-AUTH-20261026-001` |

**模型切换过程（如实记录）**：先试 `moonshotai/kimi-k3-free`（qnaigc 免费版），首轮烧完额度后命中 HTTP 429 限流；`nvidia/nemotron-3-ultra-550b-a55b0-free` 稳定 502 上游不可用；最终用户指定 `qiniu/conatus-free`（每日 1 亿 Token 限免额度，比 kimi-k3-free 隐性限流宽松得多）解决 429。

---

## 3. 真实模型输出兼容性（任务 3）

`state/v082-model-probe.json`：**PASS_REAL_LLM_TOOL_COMPATIBILITY**。

| 额度 | HTTP | prompt_tokens | completion_tokens | reasoning_tokens | finish_reason | valid_tool_call |
| --- | --- | --- | --- | --- | --- | --- |
| 4096 | 200 | 342 | 72 | 13 | `tool_calls` | true |
| 8192 | 200 | 342 | 74 | 13 | `tool_calls` | true |

- **P0-1 已解决**：实际发送参数为 `max_tokens`（非 2048），供应商接受，两个额度均正常返回工具调用。
- `reasoning_tokens` 来源 `provider_reported`（真实可取）。
- `finish_reason = tool_calls`，无 `length` 截断。
- `real_llm_requests=2`、`real_brain_requests=0`、`submits=0`。
- 两额度无兼容性差异；无额外费用（`estimated_cost_usd=0.0`）、无超时。
- **注意**：短工具调用成功不等于长 Critic 推理必然成功（任务书明确），本结论不作为完整闭环依据。

---

## 4. 三轮 Autoresearch 执行（任务 4）

命令完全按任务书参数（batches 3 / max-sim 2 / max-llm-calls 8 / repairs 2 / defer 2 / empty 2 / tokens 80000 / max-known-usd 0.01 / wall 1800 / allow-remote-llm）。三轮使用三个独立新库（验收脚本要求数据库必须不存在）。

| 轮次 | 模型 | 结果 | 卡点 | 根因 |
| --- | --- | --- | --- | --- |
| 1（fresh） | kimi-k3-free, reasoning=max | `BLOCKED_OR_FAILED` | worker 在 600s 超时 | 深度思考单次调用暴涨，撞 timeout_seconds=600 |
| 2（fresh-v2） | kimi-k3-free, low+1200s | `BLOCKED_OR_FAILED` | explorer 首步 4s 即败 | HTTP 429 限流（免费模型额度耗尽） |
| 3（fresh-v3） | conatus-free, low | `BLOCKED_OR_FAILED` | playbook 知识固化 | fail-closed 拦截「引用未评估候选作证据」 |

**第三轮（v3）是推进最深的一次**，batch 1 五角色全部完成：

| 角色 | 状态 | 输出 |
| --- | --- | --- |
| explorer | completed | 4 条互异机制假设 |
| strategist | completed | 选定研究方向与实验规格 |
| worker | completed | 3+ 条 BRAIN 语法表达式 + 变体 |
| critic | completed | 6 次经济审查，1 次 defer |
| playbook | completed | 4 条知识提案（**其中 2 条被门禁拦截**） |

---

## 5. 重点复测上次失败原因（任务 5）

### P0-1：Token 截断 — ✅ 已解决
v0.8.2 的 `v08_pricing.py` 输出上限已由 `min(configured_max, 2048, ...)` 改为 `min(configured_max, provider_cap, context_cap-input, ...)`，**无 2048 常量**。探针实测 4096/8192 均正常，`finish_reason=tool_calls`。全批 5 次调用无一次 `length` 截断。

### P0-2：工具调用 — ✅ 稳定
`v082_protocol.py` 严格解析：5 次调用全部 `state=completed`、`finish_reason=tool_calls`、`source=authenticated_llm_tool_call`、含 `response_id/tool_call_id/payload_sha256` 完整来源证明。无 `KeyError: tool_calls`。

### P0-3：公式自我修复 — ⚠️ 未自然触发，非伪造
6 次 `v08_formula_validation_attempt` 全部是 worker 产出**初始提案与合法变体**（如 `ts_zscore` 替换 `ts_mean`、`signed_power(...,2)` 替换线性权重），**未出现非法算子/字段错误**，因此「收到错误后自行修复」路径未被自然触发。按任务书要求，本轮未另做隔离错误反馈实验；该项**如实记为未获实证**。

### P0-4：队列推进 — ✅ approve/defer 均实证，reject 未出现
- `v08_candidate_queued: 3`（critic approve 后入队）。
- `v08_critic_deferred: 1`（depth 1，创建 source-linked `v08_revisions` 项 `c4578eaf`，state=pending，带完整 defer 理由）。
- 3 个候选里 2 个进入 Demo 模拟（trials=2、results=2），1 个停在 queued（未模拟）。
- `reject` 未出现（本轮无非法公式触发硬拒）。

---

## 6. 失败根因（第三轮，fail-closed 正确行为）

Playbook 的 `learn()`（`autoresearch_v08.py:808`）产出 4 条知识提案：

| 知识 | 引用证据 | 证据等级 | 结果 |
| --- | --- | --- | --- |
| item 1 Close-to-VWAP spread | `e20ed405`（已模拟） | synthetic_process | 可通过 |
| item 2 Volume-conditioned reversal | `3bd4479b`（已模拟） | synthetic_process | 可通过 |
| item 0 Session-split clientele | `10fc2baf`（**queued 未模拟**） | **unverified** | ❌ 拦截 |
| item 3 Evidence grading 汇总 | 含 `10fc2baf` | **unverified** | ❌ 拦截 |

`learn()` 校验引用证据的 grade 集合，发现含 `unverified`（未评估候选 `10fc2baf` 的 trial_id=None），抛 `ResearchError("未经核实的试验不能生成有证据知识")`，中止 batch 1 → 验收脚本捕获为 `BLOCKED_OR_FAILED`。

**定性**：这是 fail-closed 门禁**正确工作**——LLM（playbook 角色）过度激进，把「仅 Critic 批准但从未模拟」的假设当作支持证据引用，被门禁正确拦截。**不是安全漏洞，不是收益伪造**。但它暴露了一个真实的提示/逻辑层问题：playbook 允许引用未评估候选，导致 demo 闭环在 `learn()` 阶段确定性卡住。按工作边界，这属「算法/提示层」问题，应登记移交开发方，**不自行修改安全门禁**。

---

## 7. 安全、恢复与费用（任务 8）

| 审计项 | 第三轮（v3） | 三轮汇总 |
| --- | --- | --- |
| 真实 BRAIN Simulation（tasks HTTP） | 2（**synthetic=true**，非真实 BRAIN） | 2（全部 synthetic） |
| 真实 Submit（submissions） | **0** | **0** |
| 正式研究固定公式模板 | 无（`automatic_fallback=false`，源码无预设公式） | 无 |
| 历史研究库被覆盖 | 无 | 无 |
| 错误后清库重造成功 | 无（三轮三个独立新库，失败记录全保留） | 无 |
| 未知 Token/费用 | 无（5 次调用全部落库有值，`reviewed_zero`） | 探针调用已落库 |

**费用（第三轮，已审计）**：5 次 LLM 调用，`input_tokens=19862`、`output_tokens=19793`、`usd=0.0`（reviewed_zero）、`seconds≈243`。五角色各 1 次，全部 `price_status=reviewed_zero`、`unknown_usage_or_cost=false`、`soft_budget_exceeded=false`。

**输出上限/费用软限制**：未绕过。`max_known_usd_per_batch=0.01`（零单价模型下恒不触及，非供应商账单）。

---

## 8. 七问结论（任务 9）

### A. 2048 输出限制是否真正解决？ — **是**
`v08_pricing.py` 已移除 2048 常量，探针实测 4096/8192 均 `finish_reason=tool_calls`，全批无 `length` 截断。P0-1 关闭。

### B. 模型的结构化输出是否稳定？ — **是（在短/中任务上）**
5 次真实调用全部产生合规 Schema 工具响应，无缺字段、无 `KeyError`。但本轮未触发「长 Critic 深度推理」的极限场景（conatus-free reasoning_tokens≈13，浅于 kimi-k3 的 60-100），故「长推理稳定性」仍不能据此宣称已验证。

### C. Worker 是否能自主纠错并完成模拟？ — **部分实证**
Worker 自主产出了合法表达式与**变体**（ts_zscore 替换、signed_power 替换等），且 2 个候选完成 Demo 模拟。但**未触发非法算子/字段错误**，故「收到错误→自我修复」这一特定路径未获实证（P0-3 未自然触发，未伪造）。模拟是合成 Demo（`synthetic=true`），**不是真实 BRAIN 模拟**。

### D. 是否完成真实 LLM 三轮连续研究？ — **否**
三轮均 `BLOCKED_OR_FAILED`，且卡点各不相同（超时 / 限流 / fail-closed 拦截）。第三轮虽五角色单批完整跑通，但未形成「三轮连续」闭环。

### E. 是否出现真实可追溯的反馈修订？ — **部分（defer 链路真实，父子假设未形成）**
Critic 的 1 次 `defer` 生成了 source-linked `v08_revisions` 项（depth 1，带 defer 理由与 worker/critic 来源 ID），这是**真实的可追溯反馈修订入口**。但该修订状态为 `pending`（尚未被后续 Worker 消费落地为新表达式），且 4 条假设 `parent_id` 全 None，**未形成父子假设链**。反馈机制被实证触发，但未走完整「修订→再模拟」闭环。

### F. Playbook 是否真正参与后续研究决策？ — **未实证（且被门禁拦截）**
`v08_memory_retrieved=1`、`v08_memory_influence=1`（记忆三态收据存在），strategist 决策记忆审计记录完整。但 `v08_knowledge=0`（playbook 的 2 条知识提案被 fail-closed 拦截，未落库），故**无知识可被后续批次检索/应用**。记忆机制（retrieved/referenced/applied 三态区分、来源校验）本身在工作，但「Playbook 经验真正被后续决策引用」未获实证；因果效果明确未证明（代码自身置 `memory_effectiveness_causally_proven=false`）。

### G. 还有什么阻止 v0.9 Live BRAIN 准入？ — 见下节

---

## 9. 阻止 v0.9 Live BRAIN 准入的清单

**P0（阻塞级）**
1. **Playbook 引用未评估候选导致闭环卡死**：playbook 提示/逻辑允许把「仅 Critic 批准、未模拟」的假设当证据引用，触发 `learn()` 的 unverified 拦截，使 demo 三轮闭环在最后一步确定性失败。需在提示/逻辑层约束 playbook 只能引用「已落库且有 grade 的 evidence」（移交开发方）。
2. **三轮连续闭环未达成**：当前没有任何一次三轮连续完成（受超时/限流/门禁三个独立因素影响）。需在解决 P0-1 后以稳定模型重跑并跑通 ≥3 批。
3. **P0-3 自我修复路径未实证**：需一次隔离的、明确标为诊断用途的错误反馈实验（不预置答案）。

**P1**
4. 真实 BRAIN 账户配置校验：`account_profile_verified=false`、`schema_reviewed=false`、`costs_reviewed=false`、BRAIN `discovery_evidence`/`pnl_evidence` 均 `unverified`，需真实账户核实。
5. 授权字段目录仅 9 个，严重限制可表达机制空间。

**P2**
6. Playbook 因果效果四臂 A/B 对照（见 `docs/v082-playbook-analysis.md`）需先建立预注册评价方法。

---

## 附：证据文件

| 文件 | 内容 |
| --- | --- |
| `state/v082-model-probe.json` | 任务 3 探针报告（PASS） |
| `state/v082-real-llm-acceptance.json` | 任务 4 首轮验收报告（BLOCKED） |
| `state/v082-real-llm-acceptance-v2.json` | 任务 4 二轮（429 限流，BLOCKED） |
| `state/v082-real-llm-acceptance-v3.json` | 任务 4 三轮（fail-closed，BLOCKED） |
| `state/v082-real-llm-fresh.sqlite3` | 首轮实验库 |
| `state/v082-real-llm-fresh-v2.sqlite3` | 二轮实验库 |
| `state/v082-real-llm-fresh-v3.sqlite3` | 三轮实验库（证据最完整） |
| `config/v082-approved-local.toml` | 本地配置（gitignored，无密钥） |
