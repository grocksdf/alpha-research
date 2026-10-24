# AlphaResearch v0.6.1 真实 LLM 本地自主研究实验报告

- **实验日期**：2026-10-21
- **实验负责人**：项目所有者（本地执行）
- **执行环境**：Windows 本机，代码源 `C:\Users\19708\Documents\ChatGPT\worldquant`
- **实验性质**：合成演示环境（demo 模式）下的真实 LLM 自主研究验收；**零真实 BRAIN 交互**

---

## 1. 本地运行环境与代码版本

| 项目 | 值 |
|---|---|
| 代码版本 | git `b2cd4b5`（AlphaResearch v0.6.1，工作树干净，仅有本实验新增的未跟踪文件） |
| Python | 3.13.14（≥ 3.11 要求达标），managed venv：`C:\Users\19708\.workbuddy\binaries\python\envs\default` |
| 第三方依赖 | 仅 pytest（项目本身零第三方依赖，纯标准库） |
| LLM 服务 | 火山方舟 Volce Ark（`https://ark.cn-beijing.volces.com/api/v3`），OpenAI 兼容 Chat Completions 协议 |
| 推理接入点 | `ep-20260731225346-6rs7x`（端点自报底层模型 `glm-5-2-260617`，推理型模型，reasoning tokens 计入 completion_tokens） |
| 密钥管理 | `LLM_API_KEY` 仅通过运行环境变量传递；**未写入任何配置文件、代码、报告或日志** |
| BRAIN 凭据 | 环境中不存在（`brain_credentials_in_environment: false`），全程未请求、未使用 |

**端点前置实测**（实验前验证，非推测）：HTTP 200 普通对话 ✓；`tools` 函数调用 ✓（`finish_reason: "tool_calls"`，参数结构合法）；非流式 JSON ✓。

## 2. 测试结果（任务 1 验收）

| 检查 | 命令 | 结果 |
|---|---|---|
| 环境自检 | `python -m alpharesearch doctor` | 通过：`mode=demo`、`live_research_enabled=false`、`submission_enabled=false`、`brain_credentials_in_environment=false`、`approved_fields=9`、`automatic_submission=false` |
| 单元测试 | `python -m unittest discover -s tests -q` | **OK，全部通过** |
| 测试套件 | `pytest -q` | **329 个测试全部通过** + 207 个子测试 |
| 隔离配置自检 | `doctor --config config/local-llm.toml` | 通过，且 `llm_key_in_environment: true` |

未修改任何业务逻辑、评价规则或安全门禁。

## 3. 实验配置、计划哈希与 Run ID

**隔离配置** `config/local-llm.toml`（由 `config/settings.toml` 复制生成，改动仅隔离项与 LLM 段）：

| 隔离项 | 值 |
|---|---|
| mode | `demo`（合成模拟器，禁 live） |
| database | `state/local-llm.sqlite3`（独立库，未触碰原 `state/phase-b.sqlite3`） |
| catalog | `config/catalog.demo.json`（合成目录，字段标注"仅演示；不得用于 live"） |
| live_research_enabled | `false` |
| submission_enabled | `false` |
| program.md | 未修改（总预算、安全规则、候选评价门槛原样） |

**研究计划** `examples/local-llm-experiment.json` → `state/local-llm-plan.json`：

| 项 | 值 |
|---|---|
| study_id | `local-llm-experiment-001` |
| **plan_sha256** | `d319bea5fb81fd8d858854f1d531bddd91a0a4290cb4aac9a441f5ae8bc3145f`（冻结后未变） |
| generator | `llm`（真实模型，非 DemoDiscovery） |
| 预算 | max_simulations=6，max_cycles=3，max_ideas=9，ideas_per_cycle=3 |
| **Run ID** | `603c5c51562343d1`（两轮实验同一 Run，未重置） |

**dry-run 只读预览核验**（任务 3）：`generator=llm` ✓；`mode=demo` ✓；`simulation_budget=6`（不超 6）✓；目录仅含 price_volume 的 8 个当前可用字段（cap/close/high/low/open/returns/volume/vwap）✓；`network_requests=0`（不连 BRAIN）✓；`submission_enabled=false` ✓；`simulation_created=false`、`database_mutated=false` ✓（预览未建 Simulation、未改库——事实上连 `state/local-llm.sqlite3` 都尚未创建）。

## 4. AI 生成的全部研究假设及公式

两轮共 **6 个假设**，全部包含机制说明、预期方向、预测周期、证伪条件（任务书要求）。所有公式仅使用已审核字段与已审核算子（ts_zscore / ts_corr / ts_sum / ts_delay 均在 catalog.demo.json 批准列表内；字段全部 point_in_time、min_delay=1，无负延迟、无未来信息）。

### 第一轮（LLM 调用 1：98.8s，1819 输入 / 4644 输出 token，HTTP 200）

| # | idea_id | 标题 | 家族 | 公式 | 状态 |
|---|---|---|---|---|---|
| 1 | `90ee028d…` | Intraday VWAP Dislocation Reversion | reversion | `-ts_zscore((close - vwap) / vwap, 20)` | 第二轮被测试，否决 |
| 2 | `67e166d8…` | Volume-Return Correlation as Momentum Confirmation | momentum | `ts_corr(returns, volume, 20)` | 第一轮被测试，否决 |
| 3 | `b9bb726c…` | Turnover-Attention Overreaction Reversion | liquidity | `-ts_zscore(volume / cap, 20)` | 排队未测（预算耗尽） |

机制示例（假设 1）：收盘价相对日内成交量加权均价的偏离反映尾盘订单流失衡，流动性提供者与均值回归交易者在随后数日修正该失衡，故高偏离股票预期负向收益——5 日前向 IC 为证伪条件。

### 第二轮（LLM 调用 2：112.1s，2784 输入 / 5185 输出 token，HTTP 200）

| # | idea_id | 标题 | 家族 | 公式 | 父假设 | 状态 |
|---|---|---|---|---|---|---|
| 4 | `cb269df3…` | Volume-Weighted Return Persistence (Momentum Revision) | momentum（修订） | `ts_zscore(ts_sum(returns * volume, 10) / ts_sum(volume, 10), 60)` | **`67e166d8`** | 排队未测（预算耗尽） |
| 5 | `ab602da0…` | Intraday Range Volatility-Resolution Reversion | volatility | `-ts_zscore((high - low) / close, 20)` | 无 | 排队未测 |
| 6 | `3a5861e9…` | Overnight Gap Sentiment Reversion | sentiment | `-ts_zscore(open / ts_delay(close, 1) - 1, 20)` | 无 | 排队未测 |

假设 5、6 的 change 字段明确声明与既有假设的机制区别（日内区间波动消解 vs. VWAP 偏离 vs. 换手注意力），非窗口参数式伪创新。

## 5. 合法、非法、重复与被延后候选统计

| 指标 | 数值 | 说明 |
|---|---|---|
| 总假设数 | 6 | 两轮 LLM 各一批（第一批 3 + 第二批 3） |
| 合法公式 | **6 / 6** | 字段、算子、参数、类型、延迟全部通过校验 |
| 非法公式 | 0 | 无字段不存在/算子非法/负延迟/未来信息问题 |
| 结构重复 | 0 | `similarity_is_return_correlation: false`；各假设 structural_family 指纹互不相同 |
| 经济机制重复 | 0 | 5 个不同机制家族（reversion / momentum / liquidity / volatility / sentiment）+ 1 个同家族的结构性修订 |
| 纯窗口参数篡改 | 0 | 无"只改窗口伪造创新"的情况（窗口 16/24 变体是系统确定性敏感性分析，非 LLM 生成） |
| 实际合成模拟 | 6（2 候选 + 4 敏感性变体） | 敏感性变体由系统对每个候选自动生成 ±窗口 |
| 候选通过评价 | **0 / 2** | 固定评价器全部否决 |
| 进入人工审核短名单 | 0 | `shortlist` 为空 |
| 被延后（排队未测） | 4 | 假设 3、4、5、6 因 6 次模拟预算耗尽未获测试 |

## 6. 不同经济机制的覆盖情况

| 机制家族 | 假设 | 使用的数据关系 |
|---|---|---|
| reversion（微观结构） | VWAP 偏离回归 | close 与 vwap 的日内偏离 |
| momentum（量确认） | 量相关动量 + 成交量加权收益持续性修订 | returns 与 volume 的共动/加权幅度 |
| liquidity（注意力） | 换手过度反应回归 | volume/cap 换手率 |
| volatility（波动消解） | 日内区间回归 | (high-low)/close |
| sentiment（隔夜情绪） | 跳空回归 | open 与前收的跳空 |

5 个机制家族覆盖 5 种不同经济逻辑，全部基于量价数据，未依赖任何已提交/历史公式库。

## 7. 第一轮反馈与第二轮修订的对应关系

**第一轮反馈**（`autonomy_feedback` 事件，interpretation: "IS discovery feedback; constraints are observations, not causal failure explanations"）：

- 假设 2（动量）被否决，原因：`low_fitness`（0.944）、`low_sharpe`（1.18）、`parameter_sensitive`（窗口 20→16/24 敏感性测试失败）
- 缺失证据 5 项：account_profile_unverified、demo_not_live_evidence、missing_check:SELF_CORRELATION、unverified_or_incomplete_daily_pnl、unverified_return_contract

**第二轮的对应动作**（证据链完整）：

1. **有依据的修订**：假设 4 显式携带 `parent_idea_id = 67e166d8`，修订理由直接回应"parameter_sensitive"——"相关系数在小幅波动中可能虚高；改用成交量加权收益**幅度**而非共动度量，60 日 z-score 归一化跨波动状态"，且其证伪条件**内嵌了窗口扰动测试**（内窗 10→8/12 若翻转 IC 符号即认定仍参数敏感）。这是对解释过的失败约束的针对性改进，不是随机参数搜索。
2. **新机制探索**：假设 5、6 明确引用并区别于既有假设的失败约束与机制边界，转向 volatility 与 sentiment 两个未探索家族。
3. 规划器决策可追溯：两轮各一次 `autonomy_selection` 事件，选择理由均为"新公式 → 未探索结构/域 → 更低 AST 相似度与复杂度"。

## 8. 模拟预算、LLM Token 与费用

| 项 | 数值 |
|---|---|
| 模拟预算 | 6（plan 冻结值）；实际消耗 **6 / 6**，从未超支 |
| 候选测试 | 2（每候选 1 次）+ 确定性敏感性 4（每候选 ±窗口各 1 次） |
| LLM 调用 | 3 次尝试 / **2 次成功**（第 3 次为 transport_error，见第 10 节） |
| 成功调用 token | 输入 4603 + 输出 9829 = 14432（第一轮 1819+4644；第二轮 2784+5185） |
| `token_usage_complete` | **false**——失败调用无 token 计数，累计值仅覆盖 2 次成功调用（如实反映，未粉饰） |
| 墙钟耗时 | LLM 合计 331.0s（98.8 + 120.1 失败 + 112.1）；合成模拟近乎零耗时 |
| 费用 | **未知**。`known_usd: null`、`all_costs_priced: false`——配置未填供应商单价，按项目规矩不推测费用。推理模型输出 token 占比高（6829/14432），若按方舟计费需以实际账单为准 |

## 9. 暂停与恢复验证

**已验证通过**：

1. 第一轮以 `--steps 1` 正常暂停（`state: paused, reason: step_limit`）；
2. 暂停后 dry-run 确认：Run ID 仍为 `603c5c51562343d1`、`simulations_already_reserved=3`、`remaining_budget=3`、`budget_sufficient=true`；
3. 同 Run 续跑未重置预算，第二轮以 `total_budget_exhausted` 正常完成（`state: completed`）；
4. 累计模拟 3+3=6，恰好用尽且未超 6；
5. 未重建数据库、未绕过校验、未更换 study_id 重新获取预算。

## 10. 所有异常及其原因

| # | 异常 | 原因与处置 |
|---|---|---|
| 1 | 第二轮首次 LLM 调用失败：`网络响应未收到；写操作禁止自动重发` | costs 表 id=8：`transport_error`，耗时 120.09s——恰好撞上配置的 120s 超时上限。推理模型携带完整反馈上下文时响应超过 120s。**系统行为正确**：未自动重发写操作、未建 trial、未计 token，事件 33 记录 `paused / execution_or_model_error_requires_review`。端点随后实测健康（HTTP 200 / 6.5s），判定为瞬时超时后原样重试成功 |
| 2 | 尝试将超时调至 300s 被系统拒绝：`计划、代码、字段目录或规则已改变；不能静默更新冻结研究` | **这是设计内的安全行为**：`snapshot()` 将整个 `[llm]` 配置（含 timeout）绑定进冻结计划（`validate_plan` 重建比对）。已将配置**原样还原**为冻结状态（timeout=120），dry-run 复验计划校验通过、预算完好后重试成功。全程未走任何绕过路径 |
| 3 | `token_usage_complete: false` | 失败调用无 token 计数所致（见第 8 节），系统如实标记 |
| 4 | 4 个假设排队未测 | 6 次预算被 2 个候选 + 4 个敏感性变体占满。修订假设（cb269df3）虽已生成但未获测试机会——这是预算约束的真实结果，非故障 |

## 11. 安全边界检查结果

| 边界 | 结果 | 证据 |
|---|---|---|
| 真实 BRAIN Simulation 数量 | **0** | dry-run `network_requests=0`；模拟结果 alpha_id 均为 `DEMO_*` 合成标识 |
| 真实 Submit 数量 | **0** | `submissions` 表 0 行；`automatic_submit: false`；`submission_enabled: false` |
| live 模式 | 从未启用 | 全程 `mode=demo` |
| 原始数据库 | **未触碰** | `state/phase-b.sqlite3` mtime 为实验前（Oct 21 01:36），只读打开验证 4 条历史 trials 完好 |
| 合成指标表述 | 已隔离 | 所有评价带 `not_brain_reported_sharpe: true`；`independent_holdout_validated: false`；`empirical_ai_discovery_validated: false`；报告 notice：模型研究能力与 Alpha 有效性仍需真实同预算对照和独立验证 |
| 密钥落盘 | 无 | `LLM_API_KEY` 仅运行时环境变量；配置文件、代码、日志、本报告均不含密钥 |
| 评价规则/门禁/预算 | 未修改 | program.md、evaluation.toml、安全门禁原样；唯一配置改动（timeout）已被系统拦截并还原 |

## 12. v0.7 开发建议

1. **LLM 超时与推理模型适配**： reasoning 类模型在携带反馈上下文时超过 120s 上限。建议：(a) 支持流式或长超时作为**运行级**参数（不进冻结快照，或提供显式重新冻结+审计通道）；(b) 失败调用在 review 报告中单列（本次 `token_usage_complete:false` 已如实但不够醒目）；(c) 考虑对 reasoning tokens 的显式计量。
2. **预算与测试队列**：6 次模拟无法测完 6 个假设，4 个假设（含唯一的反馈修订）未获测试即终止。建议引入"每循环预算 + 显式延后队列"，并在结束报告中列出未测假设及原因，便于续跑。
3. **dry-run 端点预检**：`doctor` 只检查 `LLM_API_KEY` 是否存在，不探测端点可达性与 tools 支持。建议在冻结计划前做一次最小连通性探测（本次靠人工预先实测）。
4. **计划冻结粒度**：`[llm]` 整段进入快照导致运维参数（超时）无法调整。建议将"研究相关"（model/base_url）与"运维相关"（timeout）分离哈希。
5. **候选淘汰原因的结构化沉淀**：本次 `parameter_sensitive` 否决成功驱动了有针对性的修订，证明反馈闭环有效；建议将否决原因沉淀为假设生成时的显式约束输入（目前已通过 feedback 实现，可进一步强化）。

---

## 三个独立结论

### 结论 1：工程闭环是否通过 —— **通过**

计划创建 → 只读预览 → 真实 LLM 调用 → 假设校验 → 规划器选择 → 合成模拟 → 固定评价 → 反馈回注 → 暂停/恢复 → 预算审计，全链路真实跑通且证据完整（60 条哈希链审计事件、成本逐条记录）。两起异常（传输超时、配置变更）均被系统自身的安全设计拦截或如实记录，未发生数据损坏、预算作弊或静默变更。doctor、329 项测试、隔离配置验证全部通过。

### 结论 2：真实 LLM 自主研究能力是否通过初步验收 —— **通过（附条件）**

- **真实模型参与**：3 次调用记录（2 次成功，HTTP 200，token 计量），产出非固定 Demo 模板，每个假设含机制/方向/周期/证伪条件；
- **因子创新**：6 个假设覆盖 5 个经济机制家族，零结构重复、零纯窗口篡改；
- **反馈迭代**：第二轮对被否决假设做了携带 `parent_idea_id` 的针对性修订（直接回应 parameter_sensitive，证伪条件内嵌窗口扰动测试），并探索了两个新机制——属于"有依据的修订"，不是随机参数搜索；
- **附条件**：6 个假设仅 2 个获得测试即耗尽预算，修订假设未经验证；且 2 个已测候选均被固定评价器否决（合成指标低 + 证据缺失），尚无任何候选值得人工审核。"初步验收通过"指**研究流程能力**达标，不指任何 Alpha 质量达标。

### 结论 3：真实市场 Alpha 有效性是否已经验证 —— **尚未验证**

本次实验零真实 BRAIN Simulation、零真实 Submit，全部指标来自合成模拟器。合成 Sharpe/Fitness 不构成真实市场绩效或独立样本外证据。真实 Alpha 有效性必须留待 live 模式 + 真实同预算对照 + 独立 holdout 验证，本次及可预见的后续 demo 实验均**不能**声称已验证。

---

## 生成文件位置

| 文件 | 说明 |
|---|---|
| `config/local-llm.toml` | 隔离实验配置（已还原为冻结状态；`[llm]` 段含端点/模型，无密钥） |
| `examples/local-llm-experiment.json` | 研究计划 spec（按任务书指定内容） |
| `state/local-llm-plan.json` | 冻结计划（plan_sha256 `d319bea5…`） |
| `state/local-llm.sqlite3` | 隔离实验数据库（1 run / 6 trials / 40 events / 0 submissions；原库未动） |
| `state/local-llm-review.json` | 实验报告 JSON（假设、反馈、规划决策、成本、安全标志） |
| `docs/local-llm-experiment-v061.md` | 本报告 |

以上文件均为任务书允许创建的隔离实验配置、计划、运行输出与验收报告；未提交 git（工作区保持未跟踪状态，由实验负责人决定是否入库）。
