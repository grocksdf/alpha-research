# v0.8.1 自主发现分析（验证 B）与反馈演化分析（验证 C）

数据来自 `state/v081-real-llm-fresh.sqlite3`（run1，8 次真实 LLM 调用）。
逐条检查，凡未通过检查项一律标注原因，不以任何方式补全。

---

## 一、验证 B：是否真正自主发现

### 1. 逐条检查表

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 假设是否来自原始真实 LLM 响应 | ✅ 全部 4 条 | `v08_role_provenance`：`source = "authenticated_llm_tool_call"`，含真实 `response_id` / `tool_call_id` / `payload_sha256`；`v08_requests.state = completed` |
| 是否存在硬编码公式直接进入候选队列 | ✅ **无** | `v08_queue = 0`（无任何候选入队）；源码不含 `_demo_fixtures`/`DemoDiscovery`/`neighbors(`/字面 `rank(`；`_demo_fixtures.py` 已不存在 |
| 是否生成不同的经济机制 | ✅ 4 个互异机制标签 | 见下表 |
| 公式是否忠实表达模型提出的经济假设 | ✅ | Worker 的 `mechanism_mapping` 逐条对应 Explorer 的机制；batch2 还自述「修正了父假设符号歧义」 |
| 是否只进行了简单窗口参数修改 | ✅ **否** | 3 条表达式使用了不同的算子组合与数据结构（`ts_corr`+`ts_decay_linear`+波动率归一 / VWAP 偏离 / `inverse(ts_rank(volume))` 条件化），非同源窗口变体 |
| 是否产生合法且此前未测试过的表达式 | ⚠️ **未执行合法性判定** | `v08_queue = 0`、`trials = 0` → AST/字段/算子/语义检查管线**未被触发** |
| 模型假设是否含可证伪预测 | ✅ 全部 4 条 | 每条均有显式 `falsification` 字段 |
| 公式是否通过 AST、字段、算子及语义检查 | ⚠️ **未被执行** | 同上 |

### 2. 机制多样性明细

| # | mechanism_label | 核心经济逻辑 | 授权字段 | 引用 |
| --- | --- | --- | --- | --- |
| 1 | `volume_price_divergence` | 成交量显著放大而绝对价格变动有限 → 知情交易者吸筹/派发 | volume, returns, close, subindustry | Kyle 1985；Llorente et al. 2002；Biais et al. 1995；Campbell et al. 1993 |
| 2 | `intraday_volatility_reversal` | 日内高低价区间相对历史异常放大 → 后续均值回复 | high, low, subindustry | De Bondt & Thaler 1985；Jegadeesh & Titman 1993；Parkinson 1980；Bali et al. 2011；Barinov 2014 |
| 3 | `VWAP-to-Close Divergence Reversion` | 收盘显著偏离 VWAP 反映订单流失衡，其中含暂时性成分 | close, vwap, volume | Hasbrouck 1991；Choe, McInish & Wood 1995 |
| 4 | `Volume-Conditioned Daily Reversal` | 反转强度随成交量分位变化；低成交量下极端日收益反转更强 | returns, volume, close | Randolph & Vu 2001；Avramov, Chordia & Goyal 2006 |

**所有 `required_fields` 均在授权目录内**（close/open/high/low/volume/vwap/returns/cap/subindustry）。
**机制标签为自由文本**（Explorer 的 `mechanism_label` schema **无 enum**），即模型自行命名而非从冻结标签表中挑选——这是「非模板化」的直接证据。

### 3. 生成的表达式（真实 BRAIN 语法）

**batch 1（1 条）**
```
group_neutralize(multiply(negative(ts_decay_linear(ts_corr(rank(volume),
  rank(abs(returns)), 20), 3)), ts_delta(close, 5) /
  (ts_std_dev(returns, 20) + 0.001)), subindustry)
```
- 机制映射：量价背离（隐蔽吸筹）；`ts_decay_linear` 加权近期背离、波动率归一
- `expected_direction` 含可证伪陈述：「IC 应为正且显著；若机制无效则长短期收益不显著」
- 引用 Bali et al. 2011（彩票偏好）

**batch 2（2 条）**
```
rank(group_neutralize((vwap - close) / vwap, subindustry))
rank(group_neutralize((0 - ts_delta(close, 1) / close) * inverse(ts_rank(volume, 5)), subindustry))
```
- 机制 1：瞬时订单流压力（Hasbrouck 1991），证伪条件为「IC≈0 或两端同向（知情动量）」
- 机制 2：成交量条件化反转（Avramov/Chordia/Goyal 2006），证伪条件为「高成交量极端股反转同等或更强」
- Worker 自述「移除了父假设 `sign(returns)` 丢弃幅度导致的符号歧义」

### 4. 来源证明逐条核验

`v08_role_provenance` 事件（run1，前 3 条摘录）：

| role | model | response_id | tool_call_id | payload_sha256(前12) | source |
| --- | --- | --- | --- | --- | --- |
| explorer | moonshotai/kimi-k3-free | `chatcmpl-b9cb4c0e…` | `call_270641eb…` | `a18e3776ddc6` | authenticated_llm_tool_call |
| strategist | moonshotai/kimi-k3-free | `chatcmpl-ae231f83…` | `call_ffd10467…` | `95d81bd4872e` | authenticated_llm_tool_call |
| worker | moonshotai/kimi-k3-free | `chatcmpl-dbd56d95…` | `call_af4c4d41…` | `d9dcfb1fe29f` | authenticated_llm_tool_call |

`v08_role_payloads` 同时保存 `body_json`、`original_raw_sha256`、`canonical_body_sha256`，可与 strategist 决策回读校验（代码 `autoresearch_v08.py:505-512` 强制「记忆说明与原始模型提案不一致」即拒绝）。

### 5. 验证 B 结论：**部分成立**

**已实证**：真实 LLM 自主提出了 4 个互异经济机制，自主生成了 3 条符合 BRAIN 语法的表达式，字段全部合规，均带显式证伪条件与学术引用，来源为真实带认证的工具调用（非脚本注入、非模板）。

**未实证 / 必须声明的限制**：

1. **零模拟**——`trials = 0`，`v08_queue = 0`。因此**没有任何公式被判定为合法候选或经过测试**，自主发现的**有效性完全未经验证**。
2. **不得称为新因子**。这 4 个机制（量价背离、日内波幅反转、VWAP 偏离、成交量条件化反转）都是**金融文献中的已知教科书机制**，模型为其附上真实引用恰恰说明其来源。**不能因为公式由 LLM 生成，就声称它是金融领域从未出现过的新因子。**
3. 唯一入队前的提案被 critic 裁决为 `defer`（非 reject 亦非 approve），即模型自身也认为需要更多证据。

---

## 二、验证 C：是否根据反馈自我改进

### 1. 逐条检查表

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| Cycle 2/3 收到上一轮实际实验反馈 | ❌ **否** | `trials = 0`、`results = 0`；`v08_batch_completed`: `base_simulations = 0` |
| 解释候选失败或表现不佳的原因 | ❌ 无候选被测试 | `v08_queue = 0` |
| 根据反馈修订经济机制或公式 | ❌ 无 | 所有假设 `parent_hypothesis_id = None` |
| 为修订建立合法 `parent_hypothesis_id` | ❌ **无** | 4 条假设全部 `parent_id = None` |
| 避免无意义重复失败实验 | ❌ 无法评价 | 无失败实验记录 |
| 方向无价值时主动寻找其他方向 | ⚠️ **有意图证据，未落地** | batch 2 strategist 对 idx1 给出 `action: revise` |

### 2. 唯一的「修订意图」证据（但未落地）

batch 2 strategist 决策（`chatcmpl-4d049e97…`）：

```
idx 0 | action: test   | priority 85 | VWAP-to-close 偏离：最直接检验瞬时订单流压力
idx 1 | action: revise | priority 78 | rationale: 当前预测符号约定有歧义
        （'ts_rank(sign of returns)' vs 'Positive alpha for oversold'），
        证伪标准（成交量条件性）需在修订后重述
```

这是模型**自主**判定某假设需要修订（而非直接测试），属于真实的自我评估行为。但：

- 该 `revise` 决策**未产生带父链接的新假设**（`v08_hypotheses` 中 batch 2 两条假设 `parent_id` 均为 `None`）
- batch 2 的 critic 调用随后因输出溢出失败，批次 fault，修订链路中断

### 3. 根因说明

反馈演化无法实证的**直接原因**是：

> **critic 将 batch 1 唯一提案裁决为 `defer`（而非 `approve`）→ 零候选入队 → 零模拟 → 零实验证据 → 后续批次无反馈可消费 → 无法形成父子修订链**

这是模型自身保守决策与工程失败叠加的结果，**不是**系统缺陷。系统行为完全正确（defer 是合法裁决，且 defer 后确实不产生候选）。

### 4. 验证 C 结论：**未满足**

**未提供任何一条真实可追溯的父子假设关系**，因此**不宣布反馈演化能力在本轮得到验证**。

按任务要求：**未修改实验结果，未硬塞修订公式**，如实报告未满足原因（零模拟导致无反馈存在）。

---

## 三、两轮运行的横向对比（含失败模式）

| 维度 | run1 | run2 |
| --- | --- | --- |
| LLM 调用 | 8（explorer/strategist/worker/critic × 2 批） | 4（batch 1 四角色） |
| 完成批次 | **1** | 0 |
| 假设数 | 4 | 2 |
| 表达式数 | 3 | 1 |
| 入队候选 / 模拟 | 0 / 0 | 0 / 0 |
| 失败点 | batch 2 critic 输出溢出 | batch 1 critic 输出溢出 |

**共同失败点：critic 阶段**。critic 的输入最大（含全部 worker 提案，实测 2526–2605 input tokens），而输出需为每个提案填充 6 个长文本字段（falsifiability / mechanism_fidelity / point_in_time_risk / semantic_uncertainty / pseudo_novelty_risk / rationale），最接近 2048 硬上限。

---

## 四、复现方式

```bash
# 前提：LLM_API_KEY 经环境变量传入，config/v08-provider.local.toml 已配置且不入库
python driver_v081.py 2      # max_ideas_per_batch=2，连续 3 批
```

审计查询（只读）：

```sql
SELECT role, model, state, response_id, tool_call_id FROM v08_requests ORDER BY created;
SELECT id, parent_id, body FROM v08_hypotheses;
SELECT payload FROM events WHERE kind='v08_role_provenance';
SELECT COUNT(*) FROM v08_queue;   -- 候选入队
SELECT COUNT(*) FROM trials;      -- 合成模拟
SELECT COUNT(*) FROM tasks;       -- 真实 BRAIN HTTP（应为 0）
SELECT COUNT(*) FROM submissions; -- 真实 Submit（应为 0）
```
