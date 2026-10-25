# v0.8 记忆能力就绪评估报告（v08-memory-readiness）

> 目的：评估 v0.8 是否具备"部分记忆能力"——即将历史研究记录注入新的自主研究上下文，并据此影响假设生成方向。
> 方法：A/B 对比实验（§6），离线合成模拟，真实 LLM。日期 2026-10-25，代码基线 `28a6d40`。

---

## 1. 结论

**v0.8 已具备"部分记忆能力"的工程基础，且 A/B 初步证据支持其产生可观察的行为差异，但尚不足以宣称"完整记忆/干净因果"。**

- ✅ **机制已实现**：`historical_feedback()`（`autonomy.py` 172-182）按 `research_context`（scope+catalog+mode 摘要）检索同上下文的历史 `research_intents`，在排除当前 run 后，以**同一字段允许清单**（`id/expression/hypothesis/source/family/state/error` + `metrics{sharpe/fitness/turnover}`）注入新循环的 `generate()` 上下文。
- ✅ **初步行为证据**：B 组（有历史）相对 A 组（无历史）在"避开已失败公式、沿已验证方向延展、对已失败家族更克制"上表现出可观察差异。
- ⚠️ **边界**：样本量小（各 1 次 4 轮）、LLM 运行间非确定性、两组共享 LLM 先验知识，故差异不能归因于单一因果；且两组均独立发现相同教科书因子。

---

## 2. 记忆机制现状

| 维度 | 现状 | 备注 |
| --- | --- | --- |
| 历史注入 | `historical_feedback()` 复用同 `research_context` 的 prior intents | 字段允许清单，不含原始响应/前视数据 |
| 字段范围 | 仅 `id/expression/hypothesis/source/family/state/error` + 指标摘要 | 不注入 return 文档、OS 数据、前视 |
| 隔离 | 过滤当前 `run_id` | 避免自指 |
| 上下文键 | `research_context`（scope+catalog+mode 摘要） | 不同域/目录/模式不串扰 |
| 持久化 | `research_intents`/`intent_states`/`runs`/`autonomous_studies` 表 | 跨进程持久 |
| 去重 | `find_equivalent`/`existing_trial` + `normalized_equivalent_requires_review` | 与记忆独立、叠加生效 |

**已存在但受限**：`research_notes` 表（`0 rows`）与 `sensitivity_refs` 未在 A/B 中被 LLM 显式利用——当前记忆复用主要靠 `historical_feedback()` 的假设级反馈，而非结构化笔记检索。这是 v0.8 "部分"而非"完整"记忆的原因之一。

---

## 3. A/B 实验设计（复述 §6）

| 组 | 历史 | 模型/域/目标/预算 |
| --- | --- | --- |
| A | 无 | 同（kimi-k3-free / price_volume / 互异多轮 / 4×3） |
| B | 1 验证 + 2 失败 | 同 |

B 组种子历史（合成，`seed_history.py` 离线注入，非真实 LLM、非 BRAIN）：
- 已验证流动性赢家：`-rank(ts_corr(close, volume, 10))`
- 已失败波动：`-rank(ts_std_dev(returns, 30))`
- 已失败动量：`rank(ts_mean(returns, 60))`

---

## 4. 结果对比

| 指标 | A（无历史） | B（有历史） | 观察 |
| --- | --- | --- | --- |
| 假设数 | 12 | 9（+1 invalid） | B 略少，可能更聚焦 |
| 精确重复种子 | 0 | 0 | 系统去重 + 模型避重 |
| 家族覆盖 | 7 | 6 | 相近 |
| 动量（已失败）处理 | 朴素动量 `ts_rank(close,252)`（被拒） | 仅"路径效率条件化动量"修订（判 invalid），未重复朴素动量 | **B 更克制** |
| 波动（已失败）处理 | `ts_std_dev(returns,60)` 窗口 60 | 改窗口 20 + Parkinson 日内极差修订 `log(high*inverse(low))` | **B 主动换机制** |
| 流动性（已验证）处理 | Amihud 水平溢价 | Amihud 变体（`abs(returns)*inverse(volume*close)`） | 双方均沿机制延展 |

### 关键差异解读

1. **B 组对"已失败"家族明显克制**：种子历史明确标记了"中期动量失败"，B 组全程未提出朴素动量因子（A 组提出了 `ts_rank(close,252)` 并消耗了一次模拟 + 一次参数敏感性）。这是记忆注入的最直接行为证据。
2. **B 组对"已验证"流动性沿机制延展而非复制**：B 未逐字复用 `ts_corr(close,volume,10)`，而是提出机制相近但字段组合不同的 Amihud 非流动性因子——符合"复用已验证方向、避免公式重复"的预期。
3. **B 组波动族成为主攻方向**：B 的波动族产生 2 个 tested + 1 个父子修订（Parkinson），可能是"波动族历史有记录"使该方向被赋权，也可能是 LLM 非确定性。

---

## 5. 诚实边界与反例

- **非干净因果**：A/B 各仅 1 次运行，kimi-k3-free 单次 54-220s、存在已知非确定性（B 组首轮曾 `KeyError` 失败）。上述差异**可能**部分来自采样噪声。
- **共享先验**：两组都独立提出 5 日反转、Amihud、低波动、规模、隔夜情绪——这些是 LLM 训练语料里的教科书因子，记忆注入**并非**产生它们的必要条件。
- **去重叠加**：系统级 `find_equivalent` 已能拦截精确重复，故"0 精确重复"不能单独归功于记忆，而是"记忆提示 + 系统去重"共同结果。
- **无 holdout 泄漏**：B 的种子为合成数据，不含真实样本外信息；两组 DB 独立。

---

## 6. 就绪度结论与建议

| 就绪度 | 判定 |
| --- | --- |
| 机制就绪 | ✅ 可用（`historical_feedback` 已接线，字段隔离合规） |
| 行为就绪 | ⚠️ 初步证据支持，需更大样本确认 |
| 生产就绪 | ❌ 尚不足以依赖记忆做决策（需真实数据 + 稳定模型 + 更大样本） |

**进入真实 BRAIN 前对记忆能力的建议**：
1. 增加 A/B 样本（每组 ≥3 次独立运行）以区分信号与噪声。
2. 用更稳定的 LLM（或加温度/重试控制）降低运行间非确定性对归因的干扰。
3. 明确"复用已验证结果"与"创新机制"的量化指标（如：父子修订率、跨家族机制距离、已失败家族复现率）。
4. 结构化 `research_notes` 的检索利用（当前未接 LLM），把"部分记忆"升级为"可检索记忆"。
