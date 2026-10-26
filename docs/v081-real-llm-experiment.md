# v0.8.1 真实 LLM 自主研究实验记录

**实验性质**：真实 LLM 调用 + 合成 Demo 实验（**零真实 BRAIN Simulation、零 Submit**）
**结论摘要**：工程构件全部工作并被实证；**三个研究批次未连续完成**，自主发现能力获部分实证，反馈演化与 Playbook 记忆**未能实证**。

本报告记录真实运行结果。凡未发生、未成功或未记录的事项，一律如实标注，不以任何方式补全或美化。

---

## 1. 代码版本与测试结果

| 项 | 值 |
| --- | --- |
| Git HEAD | `3a7b89ef49e4b42dfbf4bd802c93bead722e197d` |
| 分支 / 工作树 | `main`，实验开始前干净 |
| v0.8.1 补丁 | `5ee96f7`（release-provider-fix）+ `3a7b89e`（final-memory-pricing-fix）均已合并 |
| 全量离线单元测试 | **440 PASS**（`live_llm_requests=0 / brain_simulations=0 / submits=0`） |
| `scripts/release.py --check` | **152 个发布文件校验通过**（任务书所述 147 为历史参考数，当前实际 152） |

### §1 逐项核对

| 核对项 | 结果 | 依据 |
| --- | --- | --- |
| 五角色 Explorer/Strategist/Worker/Critic/Playbook | ✅ | `PHASES = ("explorer","strategist","worker","critic","playbook")`；四个 LLM 角色由 `LLMRoles` 驱动，`Playbook` 负责记忆 |
| `alpharesearch/v08_pricing.py` 存在 | ✅ | 132 行，`reviewed_model_price` + `preflight_llm_budget` |
| `verification/v08_genuine_llm_acceptance.py` 可运行 | ✅ | 参数齐全，实际执行过（有报告产出） |
| `memory_retrieved` / `referenced` / `applied` 三态区分 | ✅ | `autoresearch_v08.py:548-550`，且带 `decision_explanations_are_not_causal_proof: True` |
| 最新价格审核、模型身份及 Token 保护 | ✅ | `reviewed_zero` 生效；零单价须 `zero_price_reviewed=true`；调用前 preflight 预算 |
| 正式研究路径不依赖预设 Alpha 公式 | ✅ | 源码无 `_demo_fixtures`/`DemoDiscovery`/`neighbors(`/字面 `rank(`；`_demo_fixtures.py` 已不存在；Explorer 的 `mechanism_label` **无 enum**（自由文本机制，非冻结标签） |
| Live BRAIN 与正式 Submit 默认关闭 | ✅ | `mode="demo"`、`live_research_enabled=false`、`submission_enabled=false` |

记忆引用校验亦已核对（`validate_memory_references`，`autoresearch_v08.py:442`）：强制同项目、未撤销、最新未被替代、摘要与持久化证据摘要一致。

---

## 2. 实际 LLM 服务与模型身份

| 项 | 值 |
| --- | --- |
| 服务商 | 七牛云 qnaigc |
| 端点 | `https://api.qnaigc.com/v1`（标准 443 HTTPS DNS 主机） |
| 模型 ID | `moonshotai/kimi-k3-free` |
| 冻结审批编号 | `V081-LLM-AUTH-20261026-001` |
| 授权有效期 | 2026-10-25T16:51Z → 2026-10-26T16:51Z（24h，上限 90 天未触及） |
| 数据外发范围 | 仅授权字段/算子目录、Objective 文本、本项目内 v0.8 队列/反馈/记忆摘要；**禁止** BRAIN 账号密码、真实账户数据、LLM_API_KEY 本身、历史冻结库内容 |
| 单价来源 | 供应商**官方模型广场页面** `http://www.qnaigc.com/ai/models`：`Kimi-K3 Free` 条目标注「本模型为免费版本。」「免费调用，不计费」，与同页收费版 `Kimi-K3`（输出 0.1 元/K、缓存输入 0.002 元/K）**明确区分** |
| 审核状态 | `reviewed_zero`（不是 `unknown_price`，也不是未审核的 0） |
| 超时 | `timeout_seconds = 600`（实测单次 16–152s；先前记录带反馈轮在 300s 仍超时，故取 600s；该值为运维参数，不进冻结计划快照） |

**费用批准**：已就「该型号官方标注免费、预期真实费用 0 美元」取得用户明确批准后执行。

**密钥处理**：`LLM_API_KEY` 仅经进程环境变量传递，从未写入 TOML/日志/报告；本地配置 `config/v08-provider.local.toml` 已被 `.gitignore` 规则 `config/v08-*.local.toml` 排除（`git check-ignore` 已验证），**不入库**。

---

## 3. 零网络预检查（§3）

```
python -m alpharesearch.v08_cli --config config/v08-provider.local.toml \
  --database state/v081-provider-check.sqlite3 provider-check --allow-remote-llm
```

```json
{"kind":"remote_approved_https","endpoint_host":"api.qnaigc.com","endpoint_scheme":"https",
 "models":["moonshotai/kimi-k3-free"],"approval_ref":"V081-LLM-AUTH-20261026-001",
 "remote_network_allowed":true,"brain_access_allowed":false,
 "provider_authenticated":false,"network_requests":0,"brain_requests":0}
```

| 确认项 | 结果 |
| --- | --- |
| 配置审核通过 | ✅ `remote_approved_https` |
| 模型身份符合授权 | ✅ |
| 研究输入发送范围符合授权 | ✅（授权记录中逐项列明） |
| BRAIN 请求为零 | ✅ `brain_access_allowed=false` / `brain_requests=0` |
| 未产生真实 LLM API 请求 | ✅ `network_requests=0`；`provider_authenticated=false` 属预期 |

---

## 4. 真实执行过程与失败记录（完整、不删改）

### 4.1 关键工程约束（反复验证确认）

`alpharesearch/v08_pricing.py:107`：

```python
output_limit = min(configured_max, 2048, remaining_tokens-estimated_input)
```

**单次调用输出上限被硬编码为 ≤2048**，且 `configured_max`（`[llm.extra_params] max_tokens`）只能降低、无法抬高。而 kimi-k3-free 是深度思考模型，**reasoning tokens 计入 `completion_tokens`**，于是出现「思考耗尽预算 → 输出工具调用前被 `finish_reason=length` 截断」。

该上限属**花费保护常量**，我无权也不可为通过验收修改；以下所有应对均在配置层或资源参数层进行。

### 4.2 尝试时间线（全部为真实运行）

| # | 配置 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 验收脚本默认（max_ideas=4），开启思考 | ❌ `target==original` 同名库碰撞被门禁拒绝 | 报告 `reason: 真实 LLM 验收必须使用不存在的新研究数据库` |
| 2 | 修正配置后重跑 | ❌ `KeyError: 'tool_calls'`，output 恰好 2048 | costs: in 970 / out **2048** / 151.4s |
| 3 | 关闭思考（`thinking={type:"disabled"}`） | ❌ 先 `v0.8 角色记录缺少必须字段` | 后经探针定位实际为模型字段合规问题 |
| 4 | 关闭思考 | ❌ `Explorer 选择了未经授权的字段` | 门禁正确拦截 |
| 5 | max_ideas=2 + 关闭思考 | ⚠️ explorer/strategist/worker 成功，**critic 溢出**（out=2048） | 4 次调用记录 |
| 6 | max_ideas=1 + 关闭思考 | ❌ strategist `memory_effect` 为空/非字符串 | 2 次调用 |
| 7 | **驱动 run1**（max_ideas=2，3 批） | ✅ **batch 1 完整完成**；batch 2 critic 溢出；batch 3 正确拒绝 | 见下 |
| 8 | **驱动 run2**（max_ideas=2，3 批） | ❌ batch 1 critic 溢出；batch 2/3 拒绝 | `state/v081-run2-evidence.sqlite3` |

**run1 是最完整的一次**：`plan_sha256 = a8f6c4186833d7f9a0011c419ed4f5fc10473d1d8d8edcf229f65b30869ec5dd`

| 批次 | 结果 |
| --- | --- |
| batch 1 | ✅ **完整完成** — explorer→strategist→worker→critic 全部 `completed` |
| batch 2 | ❌ `KeyError: 'tool_calls'`（critic 输出打满 2048 被截断） |
| batch 3 | ❌ `ResearchError: 研究活动已暂停或终止`（前批 fault 后正确拒绝继续） |

### 4.3 与验收脚本的唯一差异（如实披露）

驱动脚本 `driver_v081.py` 相对 `verification/v08_genuine_llm_acceptance.py` 的差异**仅** `max_ideas_per_batch=2`（验收脚本内部硬用默认值 4）。

理由：官方 CLI 的 `plan` 子命令原生提供 `--max-ideas-per-batch` 这一可调资源参数；降低它可缩小单次结构化输出体积以适配 2048 上限。**未改动验收脚本本身，未触碰任何安全门禁，未放宽任何验收规则**，且全部失败批次均原样保留。

---

## 5. 全部新假设与生成公式（真实 LLM 产出）

### 5.1 Explorer 自主提出的 4 条假设（run1）

| # | ID 前8位 | mechanism_label | required_fields | 可证伪预测 | 引用 |
| --- | --- | --- | --- | --- | --- |
| 1 | `a613eba6` | volume_price_divergence | volume, returns, close, subindustry | 量价背离信号正向预测收益；若 20 日 volume/abs(returns) 相关性无预测力则证伪 | Kyle 1985；Llorente et al. 2002；Biais et al. 1995；Campbell et al. 1993 |
| 2 | `8ed26855` | intraday_volatility_reversal | high, low, subindustry | 异常日内波幅后反转；若区间压缩/扩张与前瞻收益无关系则证伪 | De Bondt & Thaler 1985；Jegadeesh & Titman 1993；Parkinson 1980；Bali et al. 2011；Barinov 2014 |
| 3 | `c4ca9b82` | VWAP-to-Close Divergence Reversion | close, vwap, volume | 收盘偏离 VWAP 反映暂时性订单流失衡；若 (close−vwap)/vwap 与后续收益无单调关系则证伪 | Hasbrouck 1991；Choe, McInish & Wood 1995 |
| 4 | `a8a3cd83` | Volume-Conditioned Daily Reversal | returns, volume, close | 低成交量下极端日收益反转更强；若反转强度不随成交量条件变化则证伪 | Randolph & Vu 2001；Avramov, Chordia & Goyal 2006 |

**所有 4 条假设的 `required_fields` 全部落在授权目录内**（close/open/high/low/volume/vwap/returns/cap/subindustry 共 9 个字段）。
**4 条机制标签两两互异**，均含显式证伪条件与真实学术引用。
**全部 `parent_hypothesis_id = None`**。

### 5.2 Worker 自主生成的表达式（真实 BRAIN 语法）

**batch 1**（1 条）：

```
group_neutralize(multiply(negative(ts_decay_linear(ts_corr(rank(volume),
  rank(abs(returns)), 20), 3)), ts_delta(close, 5) /
  (ts_std_dev(returns, 20) + 0.001)), subindustry)
```
机制映射：量价背离（隐蔽吸筹），以 `ts_decay_linear` 加权近期背离、以波动率归一；引用 Bali et al. 2011。

**batch 2**（2 条）：

```
rank(group_neutralize((vwap - close) / vwap, subindustry))
rank(group_neutralize((0 - ts_delta(close, 1) / close) * inverse(ts_rank(volume, 5)), subindustry))
```
机制映射分别为：瞬时订单流压力（Hasbrouck 1991）、成交量条件化反转（Avramov/Chordia/Goyal 2006）。Worker 自述「修正了父假设的符号约定歧义」。

### 5.3 合法候选 / 拒绝 / 重复

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 生成的假设 | 4 | 真实 LLM |
| 生成的表达式 | 3 | 真实 LLM |
| **入队候选（v08_queue）** | **0** | batch 1 critic 裁决 `defer`；batch 2 critic 调用失败 |
| **模拟次数（trials）** | **0** | 无候选入队 → 无合成模拟 |
| 重复公式 | 0 | 无候选入队，无重复可判；亦无指纹碰撞 |
| AST / 字段 / 算子 / 语义检查 | 未被执行 | 因无候选进入队列，未触发校验管线 |

**因此：本轮未产生任何合法候选，也未产生被拒绝候选——不是因为门禁拦截，而是因为 critic 未批准、后续批次又因输出上限失败。**

---

## 6. 经济机制多样性

4 条假设覆盖 4 个不同机制族：

1. **量价背离 / 隐蔽吸筹**（成交量 vs 绝对收益的背离）
2. **日内波幅均值回复**（high-low 区间相对历史水平的压缩/扩张）
3. **VWAP-收盘偏离回复**（日内订单流失衡的暂时性成分）
4. **成交量条件化反转**（反转强度随成交量分位变化）

多样性评价：机制**互异且非同一公式的窗口/参数变体**。但必须明确——这些都是**金融文献中的已知教科书机制**（Kyle、Hasbrouck、Parkinson、De Bondt & Thaler 等），模型为其配上真实引用即可说明来源。**不得声称它们是金融领域从未出现过的新因子。**

---

## 7. 父子假设与反馈演化链路

| 检查项 | 结果 |
| --- | --- |
| Cycle 2/3 是否收到上一轮实际实验反馈 | ❌ 无反馈存在（`trials=0`，无任何实验证据） |
| 是否解释候选失败/表现不佳原因 | ❌ 无候选被测试，无表现可解释 |
| 是否据反馈修订经济机制或公式 | ❌ 无 |
| 是否建立合法 `parent_hypothesis_id` | ❌ 4 条假设全部 `parent_id = None` |
| 是否主动转向其他方向 | ⚠️ 有**意图证据**：batch 2 strategist 对 idx1 给出 `action: revise`，理由是「当前预测的符号约定存在歧义（`ts_rank(sign of returns)` vs 'Positive alpha for oversold'）」；但该修订**未落地为带父链接的新假设**（critic 随后失败） |

**结论：反馈演化能力本轮未获实证。** 未提供可追溯的父子假设关系。根因是**零模拟**——critic 将唯一的 batch 1 提案裁决为 `defer`，导致没有任何实验发生，后续轮次因而无反馈可消费。不存在「修改实验结果或硬塞修订公式」的行为。

---

## 8. Playbook 生成、检索、引用与决策说明

| 表 / 收据 | 值 |
| --- | --- |
| `v08_knowledge`（Playbook 知识条目） | **0** |
| `v08_memory_retrieved` 事件 | 2 次，`retrieved_revision_ids: []`、`excluded_unreliable: 0` |
| `v08_memory_influence` 三态收据 | 2 条：`retrieved []` / `referenced []` / `applied []`；`decision_explanations_are_not_causal_proof: true` |
| `v08_queue` | 0 |
| `knowledge_revisions` | [] |

**三态严格区分**（代码已实现并被本轮数据印证）：

- **Retrieved**：系统读取并提供的历史记忆 —— 本轮为 `[]`（首轮无任何知识，符合预期）
- **Referenced**：模型在原始决策中引用的可追溯 Memory ID —— 本轮为 `[]`
- **Applied**：模型对具体决策给出含 Memory ID / 决策变化 / 证据依据的结构化影响说明 —— 本轮为 `[]`

补充事实：strategist 的 `memory_effect` 文本字段**确有实质内容**（如「No prior experimental evidence exists; this is the first test. Will establish baseline performance metrics…」「Initial microstructure pressure test; results inform whether divergence is transitory or informational」），说明**记忆说明机制本身在工作**；只是当时**没有记忆可供应用**。

**特别检查（要求项）**：本轮不存在「模型拿到记忆却完全忽略」的情形——因为记忆集合本来就是空的，故不构成 `memory_applied`。

**必须明确**：`memory_applied` 表示存在可审计的模型决策说明，**不等于**已用因果实验证明记忆提高了 Alpha 研究效率。

---

## 9. Token、费用与未知计量

### 9.1 已审计（落库）计量

| 运行 | LLM 调用 | input tokens | output tokens | 合计 | USD | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| run1（主） | 8 | 17,684 | 9,649 | 27,333 | 0.0 | 356.3s |
| run2 | 4 | 9,021 | 5,890 | 14,911 | 0.0 | 239.6s |
| **合计** | **12** | **26,705** | **15,539** | **42,244** | **0.0** | 595.9s |

- 全部 12 次调用的 `input_tokens` / `output_tokens` **均有值**（`costs` 表中 NULL 行数 = **0**）
- `usd = 0.0` 且 `estimated = 1`：即**按已审核零单价计算的估算值，不是供应商账单**
- 每行 detail 均含 `usd_is_estimated_not_provider_invoice: true`、`vendor_usage: "reported"`
- `v08_llm_budget_observed` 事件：`unknown_usage_or_cost: false`、`remote_invoice_hard_capped: false`、`soft_budget_exceeded: false`

### 9.2 未知计量（如实标注为 unknown，不记为 0）

除上表外，本次还进行了约 **13 次诊断性探针调用**（用于定位 2048 上限与模型合规问题）。这些调用写入了临时数据库并已丢弃，**其 token 用量未持久化、无法追回**，因此**标记为 unknown，不声称其为 0**。

### 9.3 软上限与真实账单的区别（保留）

`--max-known-usd-per-batch 2.0` 是**实验示例软预算**，在零单价模型下恒不触及；代码明确标注 `remote_invoice_hard_capped: false`，即它**不是供应商账单上限**。`preflight_llm_budget` 也自述：「token 计量对未知供应商分词、开销、隐藏推理与开票费用不可知，属保守估算」。

---

## 10. 实验暂停、恢复与异常

| 异常 | 系统行为 | 评价 |
| --- | --- | --- |
| 单批 critic 输出溢出 | `v08_llm_request_failed`（`automatic_fallback: false`）→ `v08_control fault` → `paused_fault` → `v08_batch_failed`（`no_fallback: true`、`no_retry_of_uncertain_writes: true`） | ✅ **正确 fail-closed**：未用固定候选替代、未自动重试未知写状态 |
| 暂停后继续下一批 | `ResearchError: 研究活动已暂停或终止` | ✅ 正确拒绝 |
| 目标库与配置默认库同名 | `真实 LLM 验收必须使用不存在的新研究数据库` | ✅ 门禁正确（我先误配、被拦下后修正） |
| 模型选用授权外字段 | `Explorer 选择了未经授权的字段` | ✅ 门禁正确拦截 |
| 模型结构化字段缺失/为空 | `v0.8 无效结构化字段: memory_effect` | ✅ 门禁正确拦截 |

**中断恢复**：本轮未执行跨进程 resume。失败批次的原始事实链全部保留，未清空数据库重造成功记录。

---

## 11. 安全与来源审计

| 审计项 | 结果 |
| --- | --- |
| 真实 BRAIN Simulation（`tasks` 表 HTTP 记录） | **0** |
| 真实 Submit（`submissions`） | **0** |
| 合成模拟（`trials` / `results`） | 0 / 0 |
| 使用固定模板生成正式候选 | **无**（`automatic_fallback: false`；源码无预设公式） |
| 修改安全门禁或提交资格 | **无** |
| 历史研究库被覆盖 | **无** — `state/autoresearch.sqlite3` mtime 停留 2026-10-25 03:33，`state/phase-b.sqlite3` 停留 2026-10-21 01:36，均早于本轮实验（10-26 01:xx） |
| 来源证明完整性 | ✅ 每条角色记录含 `model` / `response_id` / `tool_call_id` / `payload_sha256`，`source = "authenticated_llm_tool_call"` |
| 未返回 Token/费用记为 unknown | ✅ 探针用量标 unknown；落库调用全部有值 |
| 合成 Sharpe/Fitness 称为真实收益 | **无** — 本轮零模拟，无合成指标产生；代码亦标注 `synthetic_evidence_not_market_performance: true` |
| 计划与代码可复现 | ✅ `git_commit=3a7b89e...` 记入 run 快照，`plan_sha256` 固定 |

---

## 12. 下一轮真实 BRAIN 研究的准入条件

在进入 v0.9 受控真实 BRAIN 实验前，必须满足：

**P0（阻塞级）**
1. **解除 2048 单次输出硬上限** —— 建议将 `v08_pricing.py:107` 的 `2048` 改为可配置（如 `[llm] max_output_tokens`，保留安全默认值），否则深度思考类模型无法产出合规工具调用。本轮已用两条独立证据复现（kimi-k3-free 与 conatus-free 均 `finish_reason=length` + 零 tool_calls）。
2. **模型结构化输出稳定性** —— 当前免费模型在长 schema 下间歇性省略/置空必需字段（`memory_effect`、`parent_hypothesis_id`）。需更换更稳定的模型，或引入受限重试（仅限**确定未产生费用**的失败，不得重试未知状态写操作）。
3. **完成一次完整的多批次闭环**（≥3 批，含真实的 approve→模拟→反馈→修订链路）后，方可评估是否进入真实 BRAIN。

**P1**
4. 真实 BRAIN 账户配置校验：`account_profile_verified = false`、`schema_reviewed = false`、`costs_reviewed = false` 均需先以真实账户核实。
5. 授权字段目录扩展：当前 demo 目录仅 9 个字段，严重限制可表达的机制空间。

**P2**
6. Playbook 因果效果对照实验（见 `docs/v081-playbook-memory-analysis.md` 的四臂方案）需先建立预注册评价方法。

---

## 13. 五项结论

### A. v0.8.1 真实 LLM 工程闭环是否通过？ — **部分通过**

**通过的**：授权与 SSRF 防护、价格审核与调用前预算、真实模型身份冻结、完整来源证明（`authenticated_llm_tool_call` + response_id/tool_call_id/payload_sha256）、记忆三态区分、未经授权字段拦截、fail-closed（无固定候选替代、不重试未知写状态）、暂停后拒绝继续、零 BRAIN / 零 Submit。**一个批次（batch 1）五角色完整跑通。**

**未通过的**：**三个研究批次未连续完成**——验收脚本自身判定为 `BLOCKED_OR_FAILED`。阻塞根因是代码层 2048 输出上限与深度思考模型的冲突（P0-1），非配置可解。

### B. AI 是否确实自主生成不同机制的 Alpha？ — **是（部分实证）**

实证：4 条假设、4 个互异机制标签、3 条真实 BRAIN 语法表达式，字段全在授权目录内，均带证伪条件与真实学术引用，来源标记为真实带认证的工具调用。
**限定**：零模拟，未证明任何公式是合法候选或有效 Alpha；且这些是**已知教科书机制**，**不得称为金融领域新因子**。

### C. AI 是否确实根据反馈自主修订 Alpha？ — **未获实证**

`trials=0`（无任何模拟），因此**不存在可供消费的实验反馈**；4 条假设 `parent_id` 全为 `None`；无父子假设链路。仅有 strategist 主动提出 `revise` 的**意图证据**，但未落地。
**不满足，原因如实报告**：critic 将唯一提案裁决为 `defer`，导致实验链未启动。未修改结果、未硬塞修订。

### D. Playbook 是否被真实用于决策？记忆提升是否有因果证据？ — **未被实证使用；无因果证据**

`v08_knowledge=0`，三态收据 retrieved/referenced/applied 全为 `[]`。记忆说明机制本身工作（`memory_effect` 有实质文本），但**当时无记忆可应用**。
**因果效果：明确未证明**（代码自身亦置 `memory_effectiveness_causally_proven: false`）。

### E. 是否具备进入 v0.9 受控真实 BRAIN 实验的条件？ — **不具备**

需先解决 P0-1（2048 上限）、P0-2（模型稳定性）、P0-3（完整多批次闭环），并完成真实账户配置校验（当前 `account_profile_verified=false`）。在此之前进入真实 BRAIN 既无工程依据，也会把本轮已暴露的可靠性问题带入真实计费环境。

---

## 附：证据文件

| 文件 | 内容 |
| --- | --- |
| `state/v081-real-llm-fresh.sqlite3` | 主实验库（run1：8 次真实调用、4 假设、3 表达式） |
| `state/v081-run1-evidence.sqlite3` | run1 备份（同上） |
| `state/v081-run2-evidence.sqlite3` | run2 备份（4 次调用，batch1 critic 失败） |
| `state/v081-real-llm-acceptance.json` | 验收脚本自身报告（`BLOCKED_OR_FAILED`） |
| `config/v08-provider.local.toml` | 本地实验配置（**已被 gitignore，不入库**；不含密钥） |
| `driver_v081.py` | 三轮驱动脚本（与验收脚本差异仅 max_ideas） |
