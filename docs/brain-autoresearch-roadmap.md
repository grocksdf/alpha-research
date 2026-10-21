# BRAIN Autoresearch — 真实研究路线图

> 2026-10-22 由项目负责人定调：**只保留 BRAIN Autoresearch 一种形态**。
> 本地 Demo（Local Discovery）模式终止——它已完成 v0.6.1 系统验收的使命；真实 BRAIN
> 账户与字段目录就绪后，合成数据对"挖出可提交因子"这一目标没有研究价值。
>
> **工作边界**：本文档作者的开发工作只限于配置与运行层（配置文件、研究计划、运行编排、
> 证据记录），以及负责人明确授权的"参数表面级别"源码修复（P0-a/P0-b，已实施）。
> 算法与架构类改动一律登记为下方"移交开发方"需求，不自行修改。
>
> **永久规则**：正式 Submit 永远人工批准；只用 WorldQuant 数据；评价器/安全门禁/
> 研究规则（program-phase-b.md）不改；样本内指标不得当作样本外证据。

---

## 1. v0.6.1 现状：真实研究已具备的能力（纯配置即可用）

| 能力 | 状态 | 证据 |
|---|---|---|
| live 模式 + 真实模拟 | ✅ | `state/smoke-001.json`：rank(close) 真实模拟（sharpe 0.06） |
| 单模拟冒烟门控 | ✅ | `[safety] validated_probe_record` 已满足，未通过冒烟不能批量 |
| 正式提交默认关闭 | ✅ | `submission_enabled = false`，评价与提交规则以实时 checks 为准 |
| 字段/算子白名单 | ✅ | 9 个时点已验证量价字段 + 19 个算子；其余 4358 个字段按安全设计默认禁用 |
| 留出期保护 | ✅ | `protect_final_holdout = true`；holdout registry 跨研究共享 |
| 计划冻结校验 | ✅ | 配置/目录/规则任何变化都会阻止静默继续（本次实测有效） |
| 哈希链审计 + 成本记录 | ✅ | 全事件链、逐条 token/耗时/HTTP 状态 |
| LLM 工具面收敛 | ✅ | 模型只有研究工具，无文件/终端/提交权限 |
| 失败保留与反馈回注 | ✅ | 失败假设入库，下一轮 LLM 收到反馈 |

## 2. 配置层方案（已就绪，待负责人放行）

- **`config/autoresearch.toml`**：由 phase-b.toml 派生，仅改数据库（`state/autoresearch.sqlite3`，
  不碰 phase-b 历史）与 `[llm]`（火山方舟端点）；规则/评价器/目录/提交开关原样保留。
- **`examples/autoresearch-001.json`**：4 模拟研究计划（程序规则上限：`max_simulations_per_run=4`、
  `max_proposals_per_run=4`）；已把 phase-b 已测 4 个公式写进 research_notes 避免重复浪费额度。
- **批量方式**：顺序执行多个计划（autoresearch-001/002/003…），每个计划独立冻结、独立预算、
  独立审计；holdout registry 保持共享。"不设固定累计上限"在 v0.6.1 中表现为计划序列
  （见移交需求 P2-a：campaign 级预算）。
- **运行序列**（放行后执行）：
  ```bash
  export LLM_API_KEY=<Ark密钥>   # 只从环境读取
  python -m alpharesearch --config config/autoresearch.toml autonomy-plan \
      --spec examples/autoresearch-001.json --output state/autoresearch-001-plan.json
  python -m alpharesearch --config config/autoresearch.toml autonomy-run \
      --plan state/autoresearch-001-plan.json --max-new-simulations 4 --report state/autoresearch-001-review.json
  # 恢复/续跑：同一计划再次执行即可（Run ID 不变，预算不重置）
  ```

## 3. 源码级需求（按优先级）

> **2026-10-22 更新**：经负责人授权，P0-a 与 P0-b 两项"参数表面级别"修复已由配置/运行层
> 直接实施并通过验收（340 项测试全过、MANIFEST 96/96、端到端验证见下）。
> P1/P2 涉及算法与架构，仍移交开发方，未动源码。

### P0-a `[llm]` 请求体透传 — ✅ 已实施（2026-10-22）
`agent.py` 原请求体为固定字段，无法配置传入 `thinking` 等参数。现已支持
`[llm.extra_params]` 白名单透传：`thinking` / `reasoning_effort` / `temperature` /
`top_p` / `max_tokens`，未列出的字段一律拒绝（防止配置夹带未审查参数或覆盖工具面）。
extra_params 属于模型身份，冻结进计划快照。
**验收**：`tests/test_llm_params.py`（11 项，含"配置不能覆盖 tools/messages/tool_choice"回归）。

### P0-b 运维参数与研究参数分离 — ✅ 已实施（2026-10-22）
`snapshot()` 原将整个 `[llm]`（含 timeout_seconds）绑定进冻结计划哈希，调一次超时即触发
冻结校验拦截。现已分离：模型身份（base_url/model/extra_params）与研究规则保持冻结；
`timeout_seconds` 等运维参数可调整，且每次建 run 时向哈希链审计事件写入
`runtime_config`（含 previous_runtime 与 adjusted 标记），调整历史可审计。
**端到端验证**：计划以 timeout=90 冻结 → 调整为 120 → dry-run 照常通过
（snapshot_sha256 不变）；此前同一操作必被拦截。换模型仍会被拦截（身份冻结有效）。

### P1-a 三层实验调度
当前每个候选固定"1 次基础模拟 + 确定性敏感性变体"。目标：L1 广筛（每假设 1 次基础模拟，
先过语法/重复/复杂度）→ L2 深评（仅对有潜力候选做参数敏感性）→ L3 反馈驱动（淘汰候选保留
失败证据，驱动下一批研究）。说明：调度数量是策略参数，不是新的固定预算。

### P1-b 持久化假设队列
v0.6.1 中未测假设随运行结束消失（本次本地实验 4 个假设含唯一反馈修订排队未测即消失）。
目标：未测/失败假设进入持久队列，跨运行存活；带 parent_idea_id 的修订假设优先测试。

### P1-c 失败调用的 token 计量
transport_error 时当前正确保持"未知"（不假记 0），但无部分计量与重试策略。目标：
区分"无用量"与"计量缺失"，失败请求可配置有限重试且不重复 POST 写操作。

### P2-a campaign 级预算与完成条件
目标：预算耗尽 / 达成质量目标 / 负责人主动暂停 / 平台故障暂停 均可正常结束研究；
计划序列之上支持 campaign 级总预算与跨计划续跑。

### P2-b 过拟合防线增强
目标：独立留出样本不参与参数选择；样本外 walk-forward；因子相关性预筛（提交前省 BRAIN
额度）；同机制家族连续无过线自动熔断。全量试验留痕已具备。

### P2-c 跨库公式去重
新数据库看不到 phase-b 已测公式（当前靠 research_notes 人工告知，已临时规避）。
目标：规范化公式指纹的跨运行/跨库注册表，相同公式直接复用结果，不重复消耗真实额度。

## 4. 明确不做

不启用自动提交；不连接未授权端点；不修改评价器/门禁/研究规则；不动系统源码；
不把样本内 Sharpe/Fitness 说成真实收益；不假记或估算 token 费用；不覆盖任何既有
研究数据库与证据（phase-b.sqlite3、smoke-001.json、catalog.live.json 均只读使用）。
