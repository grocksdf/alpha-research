# 下一轮真实 BRAIN 多轮研究验收方案（next-live-campaign-plan）

> 状态：**规划中，未授权执行。** 本轮任务只做只读核验与方案设计，不创建任何真实 BRAIN Simulation，不提交任何真实 Alpha。
> 数据保护：历史数据库 `state/autoresearch.sqlite3` 在本任务期间**零写入**（仅只读查询）。

## 1. 对 Run `17b489ba` 的只读核验（已完成真实 BRAIN Run）

完整审计见 [`docs/provenance-audit-17b489ba.md`](./provenance-audit-17b489ba.md) 与脱敏 JSON。结论要点：

- **代码版本一致**：执行时快照 `git_commit = 4e2f6dd…` 与当前 HEAD 完全一致，冻结计划 `plan_sha256=167790cd` 有效，未被静默覆盖。
- **真实 BRAIN 证据**：4 次模拟 `api_base_url=api.worldquantbrain.com`、`mode=live`、真实 alpha ID（`1YjA5r8k`/`78oRgXW2`/`88vxgQwm`/`bl817a2Z`，无 `DEMO_` 前缀）、`evidence_source` 含 `/alphas//check//correlations/self` 响应指纹；**0 提交、0 晋级**，安全门禁完好。
- **来源**：4 条公式均为 LLM 原始输出（`generator_origin=llm`），其中 #4 (`bl817a2Z`) 是 #1 的子修订，其修订文本引用的父真实指标 `fitness 0.47 / turnover 0.31` 与 `autonomy_feedback` 事件精确吻合且早于修订 idea → **反馈驱动成立**。
- **质量**：最佳 Sharpe 1.12 < 平台门禁 1.25；4 个全部 FAIL；`shortlisted=0`、`candidate_promotions=0`。
- **本任务期间**：仅对 `state/autoresearch.sqlite3` 做只读查询，未修改、未重建该 Run。

## 2. 下一轮真实 Campaign 验收目标

| 目标 | 系统支撑机制（已存在） |
| --- | --- |
| 多轮真实自动模拟（≥3 cycle） | `autonomy.run()` 循环；`autonomy-run` 同计划断点续跑 |
| 自动分析 BRAIN 反馈 | `AutonomousLoop.feedback()` 读取 trials 的 `sharpe/fitness/turnover` + evaluator `rejections` |
| 据反馈自主修改/提出新 Alpha | `parent_idea_id` 修订闭环（已验证于 17b489ba 的 #4） |
| 不重复已有研究 | `find_equivalent`/`normalized_equivalent` + 上下文级 `existing_trial` 去重；`schedule()` 家族上限 |
| 资源边界安全暂停/恢复 | `run()` 的 `invocation_budget_exhausted` / `total_budget_exhausted`；`autonomy-recover` 只读 GET 恢复 |
| 始终由用户决定 Submit | `submission_enabled=false`（默认）；`submit` 需 review JSON + `approve-alpha-id` + 精确哈希对账；Demo 结果不可提交 |

## 3. 真实研究入口与配置（不可绕过）

- **配置文件**：`config/autoresearch.toml`（`mode=live`，已 gitignore，仅本地）。**不**修改 `config/settings.toml`（demo 默认）。
- **真实研究开关**：`[safety] live_research_enabled` 必须由用户显式打开（默认 `false`）；`execute_plan` 在 live 模式强制校验。
- **账户核对**：`BRAIN_EMAIL` / `BRAIN_PASSWORD` 仅走环境变量；`require_smoke_receipt` 强制单模拟冒烟收据（`state/smoke-001.json`）后方可研究。
- **冻结计划**：`autonomy-plan` 生成 `plan_sha256`（含 `git_commit` / 模型身份 / 代码快照哈希）；**执行前 git 必须稳定**，否则计划失效；执行需 `--confirm-preview <dry-run 哈希>`。
- **模型身份一致性**：LLM `model` 必须等于冻结模型；`ProposalAudit` 校验 `response_id` / `tool_call_id` / `proposal_sha256` 与冻结身份一致，否则拒绝入队/恢复。
- **Live 强制真实 DiscoveryAgent**：`Live 只接受绑定当前执行 Run 的 DiscoveryAgent`；固定 `DemoDiscovery` 在 live 路径被硬拒（源码 `autonomy.py`）。

## 4. 验收执行步骤（dry-run → 执行 → 续跑 → 暂停恢复）

1. `python -m alpharesearch autonomy-plan --spec <下一轮 spec> --output state/autoresearch-004.json`
   - `study_id=autoresearch-004`（继任 003；冻结模型身份变更须启用新 study_id，严禁改 SQLite 绑定绕过）。
2. `python -m alpharesearch autonomy-run --plan state/autoresearch-004.json --dry-run` → 取得 `preview_sha256`。
3. 用户确认预算与 preview 哈希后：
   `autonomy-run --plan state/autoresearch-004.json --confirm-preview <sha> --max-new-simulations N`
4. **续跑**：同 plan 再次 `autonomy-run`（断点续跑，不重建计划）；LLM 超时后 `run()` 记录 `paused` 并审计，重跑同 plan 即恢复。
5. **达资源边界**：自动 `paused`（`invocation_budget_exhausted`）；`autonomy-recover` 只读恢复对账，不新建模拟。
6. **晋级审核**：任何候选晋级需人工审核；`submit` 要求 review JSON + `approve-alpha-id` + 精确哈希；**本轮仍保持 `submission_enabled=false`，正式提交由用户决定**。

## 5. 安全与合规边界（不可越过）

- 不得删除/绕过 `live_research_enabled`、`submission_enabled`、冻结计划、`DemoDiscovery` 隔离、`ProposalAudit` 来源校验。
- 真实模拟仅经 `BrainSimulator`（`account_ref` 核对）；**不得用 `DemoSimulator` 冒充真实绩效**（Demo 结果 `synthetic=true`，evaluator 直接判 `not ready`）。
- 所有 LLM 调用据实计费（`input_tokens`/`output_tokens` + `usd`；缺失费用记 `usd=NULL`，**不记确定的零费用**）。
- 真实 BRAIN 请求与 Submit 由用户显式授权；本任务未授权新建真实 Simulation。

## 6. 本次未授权项（待用户批准）

- 不创建新的真实 BRAIN Simulation。
- 不提交任何真实 Alpha。
- 待用户批准后，按第 4 节步骤单独执行真实试验，并在执行后补做一轮只读来源审计（复用 `docs/provenance-audit-*.md` 方法）。
