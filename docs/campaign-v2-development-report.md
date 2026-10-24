# AlphaResearch Campaign v2 — 开发与验收报告

**报告类型：工程能力验证与风险登记；非真实 BRAIN 市场绩效报告**  
**检查日期：2026-10-24（开发工作区）**  
**来源：** https://github.com/grocksdf/alpha-research  
**代码基线：** 公开 v0.6.1 主线接口；本环境无法克隆到可执行工作区，故不能对实际上游代码运行回归。  
**补丁策略：** 新增 Campaign v2 并保留全部 v0.6.1 安全机制，绝不移除冻结预算、改旧 SQLite 哈希或开放 Submit。

## 执行结论

| 项目 | 代码已编写 | 兼容接口离线测试 | 原 v0.6.1 源码测试 | 完整验收 |
|---|---|---|---|---|
| 1. 长生命周期与分级预算 | 是 | 通过 | 未运行 | 未完成 |
| 2. L1 广筛 / L2 深评 / L3 反馈 | 是 | 通过 | 未运行 | 未完成 |
| 3. 持久队列、幂等和旧记录迁移 | 是 | 通过（合成旧事件） | 未运行 | 未完成（缺历史数据库） |
| 4. LLM timeout/token/cost/模型分工 | 代码已写 | 部分验证 | 未运行 | 未完成（缺真实 LLM 适配测试） |
| 5. Demo / Live 与提交安全 | 离线 Demo 及 Live 策略检查 | 离线阻断通过 | 未运行 | Live 限流/真实权限未验证 |
| 6. 新增综合验收测试 | 是 | 通过 | 未运行 | 未完成 |
| P1：失败调用计量与跨批队列 | 是 | 部分通过 | 未运行 | 待实测 |
| P2：IS walk-forward / 多试验防线 / 跨库去重 | 是 | 部分通过 | 未运行 | 待真实 IS 合规证据验证 |

**一条严格的验收规则：** “代码存在”不能推出“功能验收完成”，
“离线模拟通过”不能推出“真实 Alpha 可用”，
“本机 SQLite 哈希链通过”不能推出“持有数据库 root 权限的攻击者无法篡改”。

## 技术改造

### 生命周期和资源

- `campaign_id` 为稳定项目 ID，`lifecycle=continuous` 的
  `max_total_simulations` 必须为 `null`。
- 每批单独建立有界 `Engine` Run，原 `program.md` 对单次资源、
  proposal 和 LLM 调用上限继续生效。
- 项目层可设置独立 `max_total_llm_calls`、`max_total_tokens`、
  `max_total_known_usd`，与每批资源独立。
- 运行时 `timeout_seconds` 与批次 wall-clock 审计分离；
  模型调用前硬性校验剩余时间、prompt 字符数、输出 token 上限。
- `demo_engineering_goal_count` 与需要真实独立证据的 `quality_goal_count`
  是不同指标，不可混用。
- `paused_user`、`paused_fault`、`paused_resource`、
  `terminated`、`completed` 严格状态迁移。

### 三层调度与持久队列

- L1：生成结构化假设，AST 指纹、授权字段、经济机制和父子假设校验；
  多个通过者优先各获一次基础 Simulation。
- L2：只有基础指标有价值者才进入分批敏感性测试，基于
  `breadth_before_depth`、`max_depth_per_batch` 配额控制，
  严禁默认每个新公式立即跑完整三次。
- L3：汇总已测候选与家族信息（上下文压缩 + 字段白名单），
  LLM 可生成附父 ID 修订，但须重新 AST 和研究协议校验。
- SQLite `campaign_queue` 记录八态，审计每次变迁/原因。
  `campaign_duplicates` 记录重复尝试，没有重复 Simulation。
- 若全部产生重复或非法候选，停止当前批次并写入 stagnation 审计，
  避免 LLM 无上限空转。
- 对跨批旧 Run 的未完成 POST 一律 fail-closed，不自动重复写请求。
- 停机后可检查队列，明确 `paused_fault`；已完成真实结果
  必须依据原 context/fingerprint/来源才能复用。

### 历史安全迁移

- 通过只读历史事件 `autonomy_idea` / `autonomy_idea_state` 恢复候选，
  构建新的 `campaign_id` 队列和旧 ID 映射。
- 父子图先拓扑排序；缺失父节点、环、未知实验结果均拒绝猜测。
- 完成结果同上下文同指纹复用；未测试的合法候选排队，
  已经发出但未对账的旧任务保持 deferred。
- 重复迁移同一 `study_id` 会执行只读确认，不重复写队列。
- 旧 `study_id`、`plan_hash`、`snapshot_hash`、原始 SQLite 事件保持原状；
  开始新 Campaign 不等于无视旧哈希冻结安全机制。

### 模型适配

- 继续调用 `DiscoveryAgent.generate` / `ResearchAgent.complete` 的原工具 API。
- 快速模型和推理模型通过冻结的 `llm.fast_model` / `reasoning_model`
  可选配置和审计路由，不在一个冻结 Run 中暗中替换模型身份。
- 失败调用保留真实 token 记录（如供应商返回），未知使用量保持
  `NULL`；额外传输异常兜底记录 `unknown_after_call_exception`。
- 出现不能核算的 token 使用量会让 Campaign 停在
  `paused_resource`，不能通过启动新批次静默把未知费用计为 0。
- 不自动重复 LLM POST，尤其不重复可能产生副作用的写请求。

### 过拟合与证据边界

- 相关性：只用已验证的研究期 IS 同人口/成本协议收益计算，
  绝对相关性高于冻结阈值才延后 L2 预算。
- walk-forward：训练/测试长度、step 和 embargo 在计划里冻结；
  同一数据不能看完结果后变更划分。
- 家族熔断：按最近基础实验失效数暂时冷却一家族，
  冷却期后排队，而非永久禁止经济机制。
- 多重试验：保留全部生成尝试数，输出
  `multiple_testing_adjustment_needed`。目前这只是风险登记，
  **没有**对真实样本进行正式显著性校正，也没有统计学证明新 Alpha 有效。
- 独立最终留出/Submit：旧版严格门禁保持不动，工具不读取最终留出；
  从未用 Demo 合成 Sharpe/Fitness 假冒市场绩效。

### Live 设计边界

- 新增 `campaign_live_policy.py` 做离线策略检查：
  账户来源、研究 scope、人工批准的来源和时效、只读 smoke 记录。
- 一次在途限制、每分钟有界频率以及未知远程结果不释放令牌
  都有 SQLite 调度原语，但**未接入实际 BRAIN Engine.Live**。
- `execute_campaign()` 对 `mode=live` 明确拒绝；
  未请求 BRAIN 登录、真实 Simulation 或 Submit。
- 真实 Live 的账户授权、供应商配额、成本/并发实测和
  实际数据支持需要**另行获得明确授权**才可验收。

## 本次完成的测试证据

### 第一层：独立兼容接口离线长跑

测试入口：`verification/offline_harness.py`。

- 已执行 **18 项 unittest：全部通过**。
- 连续 **8 个新 Run，每批 2 次 Demo Simulation**，
  模拟累计为 **2、4、6、8、10、12、14、16**。
- 共 **9 个 L1 Candidate**、**7 个 L2 Sensitivity**，
  所有 Stage C 结果有 7 条持久关联记录。
- 活动项目无固定累计 6 次模拟终止，队列检查通过。
- 其他通过项：暂停恢复、终止、冻结计划、老 Run 不改、
  旧队列只追加迁移、重复迁移幂等、LLM 故障人工恢复、
  未确认旧请求失败暂停、跨库只读镜像变化阻断、重复尝试留痕、
  未知费用标识、累计资源保护、Live 拒绝和串行预约、
  质量目标与 Demo 目标隔离、审计链修改检查。

**证据等级限制：** 本入口的 `Engine`、`Memory`、`Validator`、
`BrainSimulator` 是仿照公开 v0.6.1 规范制作的兼容替身，
**并未调用原仓库内部实现**，不得作为原始源码回归结果。

### 第二层：本地可自动执行、但此次未得到结果

`verification/acceptance.py` 使用安装后真正的 `Engine`、
`Memory`、`Validator` 与原测试目录：
1. 先确认 Demo 和安全开关，并去除子进程 BRAIN/LLM 凭据。
2. 运行完整 `unittest discover`，要求 >=329 个历史+新用例通过。
3. 使用完全隔离的临时 SQLite 自动创建、执行 8 个批次，
   验收 16 次 Demo Sim、L1/L2 公平配额、队列去重和链审计。
4. 把审计、原测试数量和退出状态写入 `state/campaign-acceptance-*.json`。
5. 测试脚本通过 mock 阻断 BRAIN Simulation、Submit 和 ApiClient 网络访问。

**为什么未执行：** 用户完整项目工作区和被 Git 排除的历史
`state/` 文件未挂载在当前执行环境，且工具运行时无法克隆仓库。
这属于测试环境证据缺失，不是“已通过”。

### 第三层：真实 BRAIN / 真实市场验证

本次没有账户授权，**真实 BRAIN Simulation=0、Submit=0**。
需要真实账户授权、平台端权限/限流观察、真实研究样本、独立样本外
和多重试验防过拟合审核。这与 Demo 工程验收是不同工作流，
此次不能标记通过。

## 尚未完成的生产级缺口（务必保留为开放项）

1. **原仓库完整测试：未运行。** 源码级兼容和 329 项回归仍须完成。
2. **真实 4 条历史候选迁移：未运行。** 需提供真实历史 SQLite，
   不能凭 `docs/local-llm-experiment-v061.md` 构造不存在的事件。
3. **真实 LLM 超时、路由、费用、上下文压缩：未实测。**
   可以用真实 LLM 离线 Demo，不能保证所有供应商错误模式都已覆盖。
4. **独立留出真实效果/正式多重试验校正：未获得数据支持。**
   IS walk-forward 是研究过程诊断，不能代替最终留出。
5. **完整 Live BRAIN 能力：未交付。** 目前只有离线授权策略和限流原语，
   实际调度仍被安全关闭。
6. **强不可篡改外部审计：未建设。** SQLite/哈希事件链必须依赖
   外部不可变锚定存储，才能对抗 root 级数据库替换。
7. **真实 Alpha 挖掘能力：尚未验证。**

## 用户需要做什么

**不需要人工调 SQL、改冻结哈希、逐条核对测试数据。**
工程验收脚本负责完成这些检查、生成审计结果。当前无法取得
仓库可执行副本；如需我在此会话完成更高等级的源码测试，
需要把当前仓库源码压缩包与可脱敏历史 `state` SQLite 上传至对话，
但不得上传 BRAIN 口令、API Keys 或其他账户秘密。

新版本的安装、运行与综合验收命令详见 `README-UPGRADE.md`。
