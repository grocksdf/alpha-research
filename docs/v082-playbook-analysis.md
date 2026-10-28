# AlphaResearch v0.8.2 — Playbook 记忆分析（验收 C）

本报告回答：**Playbook 是否真正参与后续研究决策？记忆提升是否有因果证据？** 并如实标注「合成收益 ≠ 真实市场规律」。

---

## 1. Playbook 的职责与判定标准

Playbook 负责把「有来源的实验事实」固化为可复用的经验知识。验收标准（任务书任务 7）：

1. 在真实实验**之后**才提炼经验（不提前、不凭空）。
2. 保存支持证据及适用范围。
3. 被后续研究**检索**。
4. 在 Strategist 决策中有**可验证的引用和应用说明**。
5. 不把合成收益当作真实市场规律。

---

## 2. 记忆三态机制（代码已实现并被本轮数据印证）

| 态 | 含义 | 本轮（v3）证据 |
| --- | --- | --- |
| **retrieved** | 系统读取并提供的历史记忆 | `v08_memory_retrieved=1`（首轮无知识，retrieved=[]，符合预期） |
| **referenced** | 模型在原始决策中引用的可追溯 Memory ID | `v08_memory_influence` 收据存在 |
| **applied** | 模型对具体决策给出含 Memory ID/决策变化/证据依据的结构化影响说明 | 收据存在，`decision_explanations_are_not_causal_proof=true` |

三态严格区分（`autoresearch_v08.py` 记忆引用校验 + `memory_effect` 说明字段）。策略决策的记忆审计记录（`v08_memory_influence`）完整：strategist 的 `decision_memory_refs` 与原始决策逐字段对账。

---

## 3. 实际数据（第三轮 v3）

| 表 / 收据 | 值 |
| --- | --- |
| `v08_knowledge`（Playbook 落库知识） | **0** |
| Playbook 知识提案 | 4 条（2 条本可通过，2 条被 fail-closed 拦截） |
| `v08_memory_retrieved` | 1 次（retrieved=[]） |
| `v08_memory_influence` | 1 次（三态收据） |
| 被后续批次引用/应用 | **无**（仅 1 批，无后续批次） |
| 合成收益被当真实规律 | **无**（代码标注 `synthetic_evidence_not_market_performance=true`） |

---

## 4. 关键发现：Playbook 被 fail-closed 拦截（重要）

Playbook 的 `learn()`（`autoresearch_v08.py:808`）产出 4 条知识提案，其中 2 条把**未模拟的 queued 候选** `10fc2baf`（trial_id=None，证据等级 `unverified`）当作 `supporting_evidence` 引用，触发门禁：

```
ResearchError: 未经核实的试验不能生成有证据知识
```

这导致：
- `v08_knowledge=0`（无任何知识落库）。
- 无知识可被后续批次检索/应用。
- 第三轮在最后一步中止。

**定性（双重含义）**：

1. **安全侧（正面）**：门禁正确工作，Playbook **没有**把未评估的假设或合成收益固化为「证据级知识」。任务 7 第 5 条「不把合成收益当真实规律」被严格守住。
2. **工程侧（问题）**：Playbook 的提示/逻辑允许引用未评估候选，导致 demo 闭环在 `learn()` 阶段确定性卡死。这暴露的是**提示/逻辑层缺陷**（Playbook 对「证据」的边界理解不足），不是安全漏洞。按工作边界，应登记移交开发方，**不自行修改安全门禁**。

---

## 5. 逐条验收结论

| # | 判定标准 | 结果 |
| --- | --- | --- |
| 1 | 在真实实验后才提炼经验 | ✅ 机制正确（`learn()` 只在有 trial 证据后调用；本轮虽被拦截，但拦截理由恰是「未评估不可作证据」） |
| 2 | 保存支持证据及适用范围 | ⚠️ 提案中有 `supporting_evidence_ids` 与 applicability 字段，但因引用未评估证据被拦截，未落库 |
| 3 | 被后续研究检索 | ❌ 无知识落库，无后续批次，未检索 |
| 4 | Strategist 决策有可验证引用/应用 | ❌ 三态收据存在但 applied=[]（无知识可应用），未实证「引用并应用」 |
| 5 | 不把合成收益当真实规律 | ✅ 严格守住（门禁拦截 + 代码标注） |

---

## 6. 因果证据结论

**Playbook 是否真正参与后续研究决策？—— 未实证。**

本轮 `v08_knowledge=0`，三态收据 retrieved/referenced/applied 实质为 `[]`。记忆机制（三态区分、来源校验、决策对账）本身在工作，但「Playbook 经验真正被后续决策引用并应用」这一关键环节**未获实证**。

**记忆提升 Alpha 研究效率是否有因果证据？—— 明确没有。**

代码自身即置 `memory_effectiveness_causally_proven=false`、`four_arm_memory_ab_still_required=true`。因果证明需要单独的四臂 A/B 对照实验（预注册评价方法），本轮**不声称任何因果结论**。

---

## 7. 四臂 A/B 对照（未来工作，非本轮结论）

要证明 Playbook 的因果效果，需在后续单独执行（本报告仅登记方案，不声称已执行）：

| 臂 | 处理 | 目的 |
| --- | --- | --- |
| A | 有记忆 + 有引用 | 主处理 |
| B | 有记忆 + 屏蔽引用 | 区分「检索」与「应用」的贡献 |
| C | 无记忆（空 Playbook） | 基线 |
| D | 随机记忆（打乱来源） | 排除安慰剂效应 |

预注册评价指标（需事先固定）：机制多样性、合法候选率、模拟成功率、跨轮引用次数等。**本轮未执行，不产出任何因果结论。**
