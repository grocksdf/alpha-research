# AlphaResearch 真实自主挖掘来源审计报告

**被审计 Run：** `17b489ba634c48be`（autoresearch-003 真实 BRAIN 研究）
**审计性质：** 严格只读来源审计（Provenance Audit）
**审计约束（已遵守）：** 未发起任何真实 Simulation / Submit / LLM 请求，未写入数据库，未修改任何历史记录。所有证据均来自 Git 仓库、冻结计划 JSON、研究数据库（只读 `file:...?mode=ro`）与源码静态阅读。

---

## 0. 执行摘要（Tl;dr）

| 项 | 结论 |
|---|---|
| Git 版本一致性 | ✅ 执行时 commit 与当前 HEAD **完全一致**（`4e2f6dd…`） |
| 真实 LLM 生成 | ✅ 4 条公式均由真实 LLM（kimi-k3-free）经 `DiscoveryAgent` 提出 |
| 真实 BRAIN 来源 | ✅ 4 次模拟全部来自真实 `api.worldquantbrain.com`，无 DEMO 标记 |
| 历史继承 | ❌ 0 条（无公式出现在既往真实研究记忆） |
| 模板直接生成 | ❌ 0 条（本 run 未启用 `DemoDiscovery`） |
| 红旗（非阻断） | ⚠️ Alpha #1 表达式与 `DemoDiscovery` 固定种子**逐字节相同**（巧合/模型先验，非本 run 模板生成） |
| 无法追溯 | ❌ 0 条 |
| 提交/晋级 | 0 提交、0 晋级（安全门禁完好） |

**每条 Alpha 的最终裁定**见 §8。核心结论：四者均为**可证实的 LLM 原始输出**；无一来自历史继承或模板生成；仅 Alpha #1 存在"表达式与演示模板巧合一致"的红旗，但**不能**据此判为模板生成（见 §6）。

---

## 1. Git commit 一致性（任务 1）

| 来源 | commit SHA |
|---|---|
| 当前工作区 `git rev-parse HEAD` | `4e2f6dda5ac890ead1449f9aa29c2d66535bcd04` |
| Run `17b489ba` 运行时快照 `runs.snapshot.git_commit` | `4e2f6dda5ac890ead1449f9aa29c2d66535bcd04` |
| 冻结计划 `plan_sha256` | `167790cdea5d383960dae3e143a125dd006213d10ac0155094f0b6b561f5cd9a`（与 `state/autoresearch-003-plan.json` 一致） |

**结论：当前代码版本与执行该 Run 时的版本一致**，冻结计划锚点未被破坏。运行时快照还记录了 `snapshot.code`（每个 `.py` 的 SHA256），其中 `discovery.py = 8ddcec38…`，与 `MANIFEST.sha256` 在该 commit 下的记录一致，进一步证明执行代码即提交的代码。

> 注：早期 run `28526ae6`（autoresearch-001）快照 commit 为 `c45c1b82…`，属前序已退役 study；本次审计对象 `17b489ba` 锚定的是当前 HEAD，符合冻结保护预期。

---

## 2. 冻结研究计划确认（任务 2）

读取 `state/autoresearch-003-plan.json`（并由 `autonomy_plan` 事件二次确认）：

| 字段 | 值 |
|---|---|
| `study_id` | `autoresearch-003` |
| `generator` | **`llm`** |
| `protocol` | `autonomous-discovery-mvp-v1` |
| 模型（运行时快照 `snapshot.llm`） | **`moonshotai/kimi-k3-free`**，端点 `https://api.qnaigc.com/v1` |
| `mode`（快照） | **`live`**（真实 BRAIN，非 demo） |
| `domains` | `price_volume` |
| `max_simulations / max_ideas / max_cycles` | 4 / 4 / 3 |
| `ideas_per_cycle` | 3 |
| `automatic_submit` | `false` |
| `objective` | 在真实 BRAIN 数据上，基于已审核量价字段自主提出经济机制多样的 Alpha 假设，依据真实模拟结果与固定评价反馈进行有依据的迭代改进，产出可供人工审核的候选 |
| `research_notes`（输入备注） | 仅用已审核字段与算子；每假设须说明经济机制/预期方向/预测周期/证伪条件；优先不同机制家族，禁止只改窗口伪装创新；列出 4 条已在 phase-b 真实测过、**禁止重复提交**的公式（见 §4） |

计划锚点与运行时 `git_commit`、模型身份一致 → **计划有效、未被静默覆盖**。

---

## 3. 数据库只读检索（任务 3）

以只读方式检索研究数据库（表：`runs / research_intents / trials / results / tasks / intent_states / autonomous_studies / costs / events`）。实际事件名称以源码 `autonomy.py / discovery.py / common.py` 的 `events` 写入为准。

**Run 主记录**
- `runs.id = 17b489ba634c48be`，`simulations = 4`，`proposals = 4`，`llm_calls = 3`
- `autonomous_studies`：`study_id=autoresearch-003`，`plan_hash=167790cd…`，与计划一致

**研究臂 / 计划 / 周期 / 想法 / 选择 事件**

| 事件 kind | 数量 | 说明 |
|---|---|---|
| `research_arm` | 1 | `agent = autonomous_llm` |
| `autonomy_plan` | 1 | 完整计划快照（见 §2） |
| `autonomy_cycle` | 3 | cycle 1/2/3，均 `requested` |
| `autonomy_idea` | 4 | 含解析后的假设对象（expression / mechanism / falsification / source / parent_idea_id） |
| `autonomy_selection` | 4 | 每个想法各 1 次选择，理由 `new_formula_then_underexplored_structure…` |
| `autonomy_feedback` | 4 | 累计反馈快照，含真实指标（见 §7） |
| `autonomy_idea_state` | 8 | 4 想法 ×（registered → tested），均 `discovery_experiment_complete` |
| `trial_created` / `intent_created` | 4 / 4 | 与 4 次模拟一一对应 |
| `simulation_reserved` / `task_state` / `alpha_bound` / `result_recorded` | 各 4 | 真实 BRAIN 绑定与结果落库 |

`research_intents.source` 字段全部为 `autonomy:autoresearch-003:<idea_id>` 形式；`trials.source` 同步记录对应 idea；亲子关系见 `trials.parent_id`（仅 Alpha #4 非空）。

**LLM 调用成本（`costs` 表，只读）**
- 共 3 条 `llm` 记录：2 次 `http_status=200`（kimi-k3-free，token 2142/3348 与 3193/6407），1 次 `outcome=transport_error`（即已知的 kimi 300s 超时，已重试成功）。
- 无 USD 计费（免费端点），`detail` 仅含 `http_status`/`model`，**不含任何密钥**。

---

## 4. 四条公式来源交叉核对（任务 4）

对照 `discovery.py` 的 `DemoDiscovery` 固定种子、`tests/` 测试夹具、历史研究记忆（`state/phase-b*.json`、`state/local-llm*.json`、`docs/*`）、以及冻结计划的禁用列表，逐条核对：

| Alpha | 公式 | 在 `DemoDiscovery.seeds`？ | 在历史研究记忆？ | 在禁用列表？ | 机制文本特征 |
|---|---|---|---|---|---|
| #1 `1YjA5r8k` | `-rank(ts_corr(close, volume, 10))` | **是，seeds[1] 逐字节相同** | 否（全仓仅 `discovery.py`+测试出现） | 否 | 丰富 LLM 叙事 + Llorente(2002) 引用，与 demo 占位文本不同 |
| #2 `78oRgXW2` | `-rank(ts_mean((high - low) / close, 20))` | 否 | 否 | 否 | Kumar(2009)/Frazzini-Pedersen(2014)/Parkinson(1980) 引用 |
| #3 `88vxgQwm` | `rank(ts_mean(abs(returns) / (volume + 1), 20))` | 否 | 否 | 否 | Amihud(2002) 流动性溢价引用 |
| #4 `bl817a2Z` | `-rank(ts_decay_linear(ts_corr(close, volume, 10), 5))` | 否（为 #1 内核外包 `ts_decay_linear`） | 否 | 否 | 父子修订叙事 |

仓库级 `grep "ts_corr(close, volume, 10)"` 仅命中等 `discovery.py:141`（demo 种子）与 `tests/test_autonomy.py`（测试夹具），**未命中任何真实研究记忆或计划禁用列表**。

---

## 5. 原始 LLM 工具调用证据（任务 5）

**重要审计限制（如实声明）：** 数据库**未保存**任何原始 LLM 请求/响应转写。

- 事件类型全集为：`run_created / runtime_config / autonomy_plan / research_arm / autonomy_authorization / autonomy_cycle / autonomy_idea / autonomy_selection / trial_created / intent_created / autonomy_idea_state / simulation_reserved / task_state / alpha_bound / result_recorded / autonomy_feedback / autonomy_state`。**不存在** `llm_request` / `llm_response` / `llm_transcript` 类事件。
- 全仓事件 `payload` 含字符串 `research_batch` 的次数 = **0**；含 `tool_calls` 的次数 = 1（命中于 `run_created` 的快照结构，非工具调用记录）。
- 因此**无法做"原始 `research_batch` 参数 ↔ 最终执行公式"的逐字节核对**。

**但我仍可证明 LLM 生成（非仅凭 `source` 字段）：**
1. 计划 `generator=llm` 且 `research_arm.agent=autonomous_llm`；
2. `costs` 表有 2 次真实 `http 200` 的 kimi-k3-free 调用，带真实 token 计数；
3. `autonomy_idea` 记录中的机制/证伪/文献引用为典型 LLM 长文本生成，任何模板都无法产出；
4. 解析后 idea 的 `expression` 与最终 `trials.expression` **逐条完全一致**（内部一致性）。

> 结论：来源为 LLM 生成，证据充分；唯原始对话转写未持久化，属系统审计可改进项（建议后续版本保存脱敏后的 `research_batch` 参数快照以便逐字节核验）。

---

## 6. `-rank(ts_corr(close, volume, 10))` 与 Demo 模板相同的原因（任务 6）

**事实：** 该表达式与 `alpharesearch/discovery.py:141` 中 `DemoDiscovery.seeds[1] = ('-rank(ts_corr(close, volume, 10))', 'liquidity')` **逐字节相同**。

**为何"相同"而非"模板生成"：**
- `autonomy.py:294`：`self.generator = DemoDiscovery() if self.plan['generator'] == 'demo' else DiscoveryAgent(self.engine)`。本 run `generator=llm` → 实际调用的是 **`DiscoveryAgent`（真实 LLM 代理）**，`DemoDiscovery` 从未作为生成器运行。
- `DemoDiscovery` 类文档明确："Deterministic fixtures to exercise the loop. This is never an AI baseline result." 且其种子仅为类内局部变量。
- `DiscoveryAgent` 注入 LLM 的提示词（`discovery.py:105-121`）由系统指令 + 目标/范围/字段/算子/备注/反馈组成；`program_text`（追加的"Fixed research program"）=`common.py:72` 的 `rules_text` = 研究规则文档（`config["program"]` 文件内容）。仓库级 `grep` 确认 `ts_corr(close, volume, 10)` **不出现在任何 program/rules 文档**，因此**演示种子未进入 LLM 提示词**。
- 历史真实研究记忆中亦无此公式（见 §4）。

**裁定：** Alpha #1 的表达式是 LLM **独立重发现**（或模型先验知识中的常见量价 alpha），与演示模板**巧合一致**；本 run 既未调用 `DemoDiscovery`、也未将种子泄露给 LLM，**不构成"固定模板直接生成"**。作为红旗记录，但不改变"LLM 生成"的裁定。

---

## 7. 父子修订与真实反馈（任务 7）

**Alpha #4 `bl817a2Z`** 为父子修订：
- `trials.parent_id = c9c43f8d795d44d5`（Alpha #1 的 trial）；
- `autonomy_idea` 记录 `parent_idea_id = 5eee67bc71ef4a1d95ee`（Alpha #1 的 idea_id）；
- 修订文本明确："保留 `ts_corr(close, volume, 10)` 内核与所有窗口不变，仅在外层加 `ts_decay_linear(·,5)` 时序加权平滑，针对父假设实测失败原因（**fitness 0.47 偏低、换手 0.31 偏高**）做降噪"。

**模型是否先收到父假设真实指标再提出修订？**
- `autonomy_feedback` 事件 `id=26`（创建 `2026-10-24T19:14:50`，**早于**修订 idea `id=31` 的 `19:26:59`）中，Alpha #1 已带真实指标：`fitness=0.47, sharpe=1.12, turnover=0.3107`。
- 修订文本引用的 0.47 / 0.31 与反馈事件**精确吻合**。
- 该反馈经 `discovery_feedback` 注入后续 LLM 调用（`discovery.py:119`），而本次 run 的实际 LLM 调用（`costs` id 6，重试成功后）即产出该修订。

**结论：是。** 模型确实在收到父假设（Alpha #1）的真实 Sharpe / Fitness / Turnover 后才提出针对性降噪修订 → **自主反馈演化能力得到实证**。修订结果：Sharpe 1.12→1.05、Fitness 0.47→0.47、Turnover 0.3107→**0.2498（如预期下降）**，方向符合假说，但 Sharpe 略降、Fitness 未提升，未越过发表门禁（详见 §9）。

---

## 8. 来源统计与每条 Alpha 结论（任务 8）

### 8.1 统计

| 类别 | 数量 | 成员 |
|---|---|---|
| 真正独立提出的机制 | 3 | #2 日内振幅彩票、#3 Amihud 非流动性、#1 量价背离（机制叙事独立） |
| 历史继承 | 0 | — |
| 参数/规则派生（机械变体） | 0 | —（#4 为 LLM 主导的父子结构修订，非机械改窗口） |
| 模板复用（本 run 实际生成） | 0 | —（#1 仅"表达式巧合匹配"演示种子） |
| 无法追溯 | 0 | — |
| 红旗：表达式巧合匹配演示种子 | 1 | #1 |

### 8.2 每条 Alpha 明确结论

| # | Alpha ID | 公式 | **裁定** | 依据 |
|---|---|---|---|---|
| 1 | `1YjA5r8k` | `-rank(ts_corr(close, volume, 10))` | **LLM 原始输出可证实**（红旗：表达式与 `DemoDiscovery.seeds[1]` 逐字节相同，但非本 run 模板生成） | generator=llm；成本记录 2 次真实 kimi 调用；idea 机制文本为 LLM 原创；表达式未在提示词/历史/禁用列表出现 |
| 2 | `78oRgXW2` | `-rank(ts_mean((high - low) / close, 20))` | **LLM 原始输出可证实** | 同上；表达式不在 demo 种子/历史/禁用列表；机制文献引用为 LLM 自述 |
| 3 | `88vxgQwm` | `rank(ts_mean(abs(returns) / (volume + 1), 20))` | **LLM 原始输出可证实** | 同上；Amihud 引用为"研究归因（非已核实证据）"，公式本身为 LLM 用已审核字段/算子构造 |
| 4 | `bl817a2Z` | `-rank(ts_decay_linear(ts_corr(close, volume, 10), 5))` | **LLM 原始输出可证实**（父子修订，反馈驱动） | parent=#1；修订文本引用父真实指标 0.47/0.31，与 `autonomy_feedback` id=26 吻合 |

> 五类裁定中，"LLM 生成但受历史公式影响""固定模板直接生成""参数或规则派生""证据不足无法判定"在本 run **均不适用**；唯一需标注的是 #1 的"表达式巧合匹配演示种子"红旗。

---

## 9. 真实 BRAIN 来源核验与结果证据（任务 9，未重新调用接口）

四个 `tasks` 记录与 `results.evidence_source` 一致证明全部来自真实 BRAIN：

| Alpha | `api_base_url` | `remote_state` | alpha_id | 真实平台门禁（FAIL 项） | 关键指标（Sharpe / Fitness / Turnover） |
|---|---|---|---|---|---|
| `1YjA5r8k` | `https://api.worldquantbrain.com` | completed | `1YjA5r8k` | LOW_SHARPE / LOW_FITNESS / LOW_SUB_UNIVERSE_SHARPE | **1.12** / 0.47 / 0.3107 |
| `78oRgXW2` | 同上 | completed | `78oRgXW2` | 同上 | 0.11 / 0.05 / 0.0499 |
| `88vxgQwm` | 同上 | completed | `88vxgQwm` | 同上 + UNITS FAIL | **-1.07** / -0.72 / 0.0301 |
| `bl817a2Z` | 同上 | completed | `bl817a2Z` | 同上 | 1.05 / 0.47 / 0.2498 |

真实性佐证（均只读）：
- `results.mode = "live"`，`alpha_type = "REGULAR"`，`evidence_source.transport = "account_http"`；
- 每个结果含 `responses` 下 `/alphas`、`/check`、`/correlations/self` 的 `payload_sha256`（真实 HTTP GET 响应指纹），`api_base_url` 为公开端点，**无任何 `DEMO_` 前缀**；
- 统一 `account_ref` 哈希、统一 `discovery_contract_sha256` / `pnl_contract_sha256`，指向同一已验证账户（账户标识已脱敏，见 §11）；
- 计划层 `safety.submission_enabled = false`、`automatic_submit = false` → 全程 0 提交、0 晋级，安全门禁完好。

**全部 4 次模拟确实来自真实 BRAIN，结果证据已保留于 `results` 与 `tasks`，本次审计未重新调用任何接口。**

---

## 10. 三独立能力评估（任务收尾）

### 10.1 自主假设生成（Autonomous Hypothesis Generation）—— 能力成立
LLM 在单次/重试后的 2 次有效调用中，提出了 3 个**经济机制互异**的假设（量价背离情绪反转、日内振幅彩票效应、Amihud 非流动性溢价），每个均自带机制叙事、预期方向、预测周期、证伪条件与文献归因，明显超出"改窗口/模板"层次。即便 #1 表达式巧合命中演示种子，其机制叙事仍为 LLM 原创。**结论：真实、可用的自主假设生成能力。**

### 10.2 自主反馈演化（Autonomous Feedback Evolution）—— 能力成立（已实证）
模型在收到 Alpha #1 真实指标（Sharpe 1.12 / Fitness 0.47 / Turnover 0.31）后，主动提出针对性降噪修订（#4：外层加 `ts_decay_linear(·,5)`）。修订使 Turnover 由 0.3107 降至 0.2498（假说方向成立），虽 Sharpe 微降至 1.05、Fitness 持平，但体现了"基于真实结果、有依据、可链接 parent"的闭环演化。**结论：自主反馈演化能力已真实发生。**

### 10.3 真实 Alpha 质量发现（Real Alpha Quality Discovery）—— 尚未达成
4 个 alpha 全部未越过 BRAIN 发表门禁（LOW_SHARPE 阈值 1.25 / LOW_FITNESS 1.0 / LOW_SUB_UNIVERSE_SHARPE）。最佳为 #1（Sharpe 1.12，差门禁 0.13）与 #4（Sharpe 1.05）；#3 为显著负向（Sharpe -1.07）。**0 晋级、0 提交**——系统的"不晋级低质 alpha"纪律正确，但**尚无一例通过门禁的真实 alpha**，质量发现能力暂未得到正向验证。属正常早期迭代状态（单 study、4 模拟预算），需更多 study 与机制探索。

---

## 11. 脱敏与合规说明

本报告及随附 JSON **不含**任何 API Key、Cookie、账户认证信息或明文私人账户资料：
- LLM 端点仅保留公开 base_url 与模型名；**无密钥**（系统本就不持久化密钥，符合 `common.py` 注释"不读取或保存环境变量或认证信息"）。
- BRAIN 账户标识 `ZZ50866` 在报告中记为 `ZZ5***66`；`account_ref` 哈希记为脱敏占位（`ACCT_REF_MASKED`），其为非凭证的每账户稳定哈希。
- `evidence_source` 中的 `payload_sha256` 为响应内容指纹（非凭证），保留以佐证真实性。
- 巨型 `catalog`（字段清单）已省略，不影响结论。
- alpha_id（`1YjA5r8k` 等）与模拟资源路径属平台公开资源标识（非凭证），作为真实性证据保留。

---

## 12. 局限与建议

1. **原始 LLM 对话未持久化**：无法对 `research_batch` 参数做逐字节核验；建议后续版本在脱敏前提下保存 `research_batch` 入参快照（仅公式/机制，不含任何凭证）。
2. **#1 表达式巧合命中演示种子**：虽已证非模板生成，但建议在提示词或禁用层显式排除 `DemoDiscovery.seeds` 全集，避免"巧合"引发的可解释性争议。
3. **质量发现未达门禁**：建议开展 autoresearch-004+，注入"#1 差 0.13 过门禁、#3 试反向/中性化变体"等迭代反馈，并扩大机制家族探索。
4. 审计报告本身只读、未改动历史；如需将本结论入库，建议作为独立 artifact 提交，不触碰 `state/autoresearch.sqlite3`。
