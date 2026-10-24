# AlphaResearch Campaign v2 — v0.6.1 additive offline patch

**状态：待在完整 v0.6.1 仓库中安装和执行完整回归；不得视为项目已完成的验收报告。**

本补丁在 `alpharesearch/` 下新增 6 个模块、上游集成测试和两个验收脚本。
**不替换或修改** `autonomy.py`, `memory.py`, `engine.py`, `agent.py`,
`discovery.py`, `brain.py`, `return_contract.py`, `local_validation.py`,
`cli.py`, `program.md` 或安全门禁。补丁仅提供离线 Demo 路径，
`config.mode != "demo"` 时明确拒绝发起 campaign Run。

## 先决条件

- 克隆或准备好**原始** AlphaResearch v0.6.1 工作副本，以及 `config/settings.toml`。
- **先完整备份 `state/` 与原有 SQLite WAL/SHM 相关文件**，停止已有进程再做备份。
- 原历史研究的 `study_id`、`plan_hash`、`run_id`、审计链全部保持不动。
- 本补丁是离线审查环境制作的，未访问你的私有 SQLite，因此不能凭报告预填历史 4 条候选。
- 新增的 `.py` 进入 `common.snapshot()` 的代码文件哈希：旧 Run 不能以新版本静默恢复，这是正常且必须保留的冻结保护。
- 原项目测试未在本环境执行，须在本地首先运行完整回归。

## 安装（先 dry-run）

在已解压的补丁目录运行：

```bash
python install_campaign_v2.py --repo /path/to/alpha-research
python install_campaign_v2.py --repo /path/to/alpha-research --apply
```

安装器仅新增独立模块；若新文件已存在但内容不同，直接拒绝覆盖。

## 创建冻结 campaign 计划

先确认 `config/settings.toml` 的 `mode="demo"`、`safety.live_research_enabled=false`、
`safety.submission_enabled=false`。真实模型生成则需将 `[llm].model` 设置为实际支持 tools 的模型，
并通过环境变量提供 `LLM_API_KEY`；这仅用于模型调用，**不是** BRAIN 登录。

在仓库根目录：

```bash
cp /path/to/patch/campaign-spec.example.json state/my-campaign-spec.json
python -m alpharesearch.campaign_cli --config config/settings.toml \
  plan --spec state/my-campaign-spec.json --output state/campaign-plan-v2.json
```

`lifecycle: "continuous"` 且 `max_total_simulations: null` 才是无固定累计模拟上限；
使用一个很大的整数会被拒绝。有限模式必须显式声明有限模拟上限。
`quality_goal_count` 只记录质量期望；**缺乏独立留出证明时不得完成或自动提交**。

## 执行单批次与持续多批次

```bash
python -m alpharesearch.campaign_cli --config config/settings.toml \
  run --plan state/campaign-plan-v2.json \
  --max-new-simulations 2 --max-steps 12 \
  --max-llm-calls 2 --max-wall-seconds 900 --max-tokens 50000

python -m alpharesearch.campaign_cli --config config/settings.toml \
  drive --plan state/campaign-plan-v2.json \
  --batches 8 --max-new-simulations 2 --sleep-seconds 0

# 显式许可后，持续运行直到 operator pause/terminate/fault 或无新工作:
python -m alpharesearch.campaign_cli --config config/settings.toml \
  drive --plan state/campaign-plan-v2.json \
  --forever --max-new-simulations 2
```

每批次创建新的、有界 `Engine` Run；候选一直保存在
`campaign_queue`，每次运行结束时不会清空。生成结果、状态切换、
模型路由、敏感性链接、运行资源配置均追加原有哈希审计链。

**L1**：不同候选先跑一次基础模拟。
**L2**：只针对达到冻结基础阈值的候选，在剩余预算里分批执行敏感性；
同一批次最多 `max_depth_per_batch` 个新增敏感性 Simulation。
**L3**：只向下一轮模型提供最近候选的经限制指标、原因、家族摘要；
显式优先处理带已测父节点的修订候选，但仍然强制 AST 校验。

分批运行不自动触发真实 BRAIN API，不调用任何 Submit 函数。

## 过程控制、暂停恢复与报告

```bash
python -m alpharesearch.campaign_cli inspect --plan state/campaign-plan-v2.json
python -m alpharesearch.campaign_cli pause --plan state/campaign-plan-v2.json --reason operator
python -m alpharesearch.campaign_cli resume --plan state/campaign-plan-v2.json --reason reviewed
python -m alpharesearch.campaign_cli terminate --plan state/campaign-plan-v2.json --reason operator
```

当调用失败、LLM 请求结果未知或本地实验结果无法确认时，campaign
进入 `paused_fault`，不得自动重复可能写入远程平台的 POST。
`resume` 是显式操作；若供应商未给 `usage`，真实 token 与费用记录为 `NULL`
且汇总中的 `unknown_*_calls` 保留，**不会当作零费用**。
已有 `Engine.create_run()` 会追加 `runtime_config` 审计事件，
因此不同 Run 的 LLM 超时调整可追踪，但它不修改旧 Run 的冻结快照。

`paused_resource` 表示触及 campaign 级总调用数、token 上限或已知费用上限；
同一冻结预算下简单 resume 不会重置总计数。

## 迁移旧 v0.6.1 四个未测试候选

原始 `state/` SQLite **必须还在**，并且必须包含原先的
`autonomous_studies`、`autonomy_idea` 和 `autonomy_idea_state` 事件。

首先只读查到正确的旧 `study_id`：

```bash
sqlite3 -readonly state/research.sqlite3 \
  'SELECT study_id,run_id,plan_hash FROM autonomous_studies;'
```

然后通过追加事件进行迁移：

```bash
python -m alpharesearch.campaign_cli import-legacy \
  --plan state/campaign-plan-v2.json --old-study-id '<旧 study_id>'
```

迁移将旧候选映射到新 campaign，保留原来的 ID 引用和父子关系：
已完成的实验只复用同上下文同指纹证据；未测试的有效候选进入 `queued`；
结果未知或未完成的旧任务进入 `deferred`，等待人工核查。
不重写 `autonomous_studies`、不改变冻结哈希、不补造不存在的远程结果。

**注意**：迁移在新增文件启用后，旧 Run 的 snapshot 可能与当前代码不同。
该迁移只读取历史证据并创建一个**新** Run，不尝试用新代码恢复旧 Run。

## 跨库公式去重

在创建冻结计划前把已有数据库的**绝对路径**放入 `external_libraries`。
必须是具有 `trials` 和 `tasks` 的可只读 SQLite。
查询限定 `(research_context,AST fingerprint)`；命中则只追加引用记录并
进入 `deferred` 待审，不复制其他库的模拟结果，不改它的任何记录。
如果库丢失或 schema 无法读取，停止而不是忽略异常重复测试。
路径列表冻结，不能在旧计划中偷偷增减。

## 过拟合诊断（仅独立审核的研究期 IS 数据）

现有 `return_contract.verify_return_document` 要求明确的样本角色、
成本、日历、来源等信息；只有 `research_is` 且 `synthetic=false` 可用于：

```bash
python -m alpharesearch.campaign_cli review-is \
  --plan state/campaign-plan-v2.json \
  --documents path/to/candidateA-research-is.json path/to/candidateB-research-is.json \
  --train-size 504 --test-size 126 --min-folds 3
```

诊断计算非重叠 walk-forward 验证窗口及符合人口/成本协议的绝对收益相关性。
高于计划阈值的关联项在后续 L2 预算分配时推迟，不偷偷触及最终留出样本。
原项目的独立留出/验证和原有收益契约始终保留。

以上属于研究期诊断，而不是 BRAIN 真实绩效、独立留出证明或正式提交合格性。
没有真实且审核过的 IS 收益时应返回 `unavailable`，禁止伪造分析。

## 一键执行全仓库离线验收（推荐）

在原仓库安装所有增量文件后，运行：

```bash
python verification/acceptance.py \
  --repo . --config config/settings.toml
```

脚本先校验 `mode=demo`、`live_research_enabled=false` 和
`submission_enabled=false`，移除传给测试子进程的真实 BRAIN/LLM 凭据。
然后自动执行**至少原版 329 项**回归测试，再在临时 SQLite 库中执行
8 个 bounded Run（累计 16 次 Demo Simulation），覆盖跨批队列、L1 和 L2
配额、audit queue、哈希和安全禁用。全部通过后在 `state/` 生成
不可覆盖的 `campaign-acceptance-*.json` 与日志文件。

报告只在全仓库实际验收通过时才写 `overall_status=PASS`；任何失败、
不足 329 项原回归或试验覆盖不够都会标记失败。
脚本不会运行真实 BRAIN Simulation 或 Submit，也不会使用历史 `state/` 研究库。

可单独运行：

```bash
python -m unittest discover -s tests -p 'test_campaign_v2.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

验收时需要：
- 8 个单批次、每批 2 次新增合成模拟，累积显式超过 6，campaign 仍 active；
- 每批队列已验证、同 fingerprint 不重复、旧 queued 候选后续继续；
- L1 测试至少覆盖多个候选，L2 的新增敏感性不超过每批配额；
- LLM 故障进入 paused_fault 且不会自动重发 API 非幂等调用；
- 手动暂停、恢复、终止以及累计资源保护、旧 Run 冻结哈希不变；
- 所有原有安全门禁回归保持通过；
- 真实 BRAIN Simulation POST = 0，Submit POST = 0。

## 尚未被本补丁验证/交付的事项

1. 本环境无法克隆/运行原仓库，也无法访问被 `.gitignore` 排除的
   `state/local-llm-review.json` 与私有历史 SQLite。附带测试有待在原仓库执行。
2. `quality_goal_count` 的自动完成**刻意关闭**：缺少来自独立留出验收的可信、可审核签署，
   不能把 Demo Sharpe 当作达到质量目标。可继续运行，人工终止或设定有限生命周期。
3. Live BRAIN 模式**明确禁用**，并发、授权、限速、平台多重试验控制均待单独授权和真实环境验收。
4. walk-forward 与相关性模块需真实审核的研究期 IS 收益输入才能得到有意义的结果；
   Demo 指标无法转成市场业绩。
5. 这是工程补丁与离线过程测试，不代表获得真实 Alpha，也不代表可以自动 Submit。

## 二次整改：重要设计和安全细节

1. **无法有效生成时不得 LLM 空转。** 无新增合法候选时终止本批次，
   把重复尝试、无效假设和停滞原因保存到 SQLite 和审计事件。
   优先调度父节点已被测试的修订候选；仍必须重新 AST 验证。
2. **未知 token/费用不能当零。** 原生 `ResearchAgent.complete()` 的
   HTTP/API 错误调用及额外传输异常均可记录耗时，真实供应商未返回
   `usage` 时 token/费用为 NULL；发现无法核算的累计 token 消耗
   会进入 `paused_resource`，必须人工对账而不能换一个 Run 隐式重置。
3. **Campaign 目标和费用分离。** `lifecycle=continuous` 必须明确
   `max_total_simulations=null`。可独立选择运行级 Simulation/token/
   LLM/耗时上限，或单独冻结 campaign 级模型调用、token、美元成本保护。
   `quality_goal_count` 是待验证的真实 Alpha 质量目标；不允许由
   Demo 数据完成。`demo_engineering_goal_count` 只完成合成流程工程目标，
   永远不会声称真实 Alpha 已通过。
4. **不可篡改记录的边界。** 追加哈希事件链、SQLite 更新/删除触发器、
   审计重放和冻结计划约束均已使用，但它们**不能防止掌握数据库文件
   root 权限的人整体替换文件及重算哈希链**。生产级防篡改还需要
   外部不可变对象存储、定期签名/时间戳锚定和隔离密钥。
5. **过拟合。** 冻结相关性阈值、walk-forward 训练/测试长度、
   间隔和 embargo；用已审查的 `research_is` 数据作诊断，
   记录测试数及重复尝试，保护独立最终留出。不输入真实数据时返回
   `unavailable`，不发明经验相关性或统计显著性。
6. **Live 只做离线策略设计。** `campaign_live_policy.py` 包含
   账户范围、时间有效性、人工审核的限流清单前置检查，以及
   持久化串行令牌与调用频率限制器；**尚未连接真实 BRAIN 调度路径**，
   `execute_campaign()` 明确拒绝 `mode=live`。没有 BRAIN 授权和
   平台限流实测，不能宣称完整 Live 已验收。
7. **真实仓库综合验证。** 新增 `verification/acceptance.py` 可自动
   执行全仓库安全回归与 Demo 超 6 次长跑。补丁内附的
   `verification/offline_harness.py` 是独立兼容接口替身，不是原
   v0.6.1 源码实测，不得混淆两种证据层级。

**严谨的最终签收条件：** 安装后执行一次完整 `verification/acceptance.py`，
获得真实仓库的 `overall_status=PASS`，并检查原计划/数据库 SHA、
审计链及无法自动对账的任务记录。真实市场策略质量与平台
授权仍须保留独立的人工审批。用户不需要手工计算 16 次模拟
或分析单批队列，但运行工具必须获得源仓库和私有 SQLite 的本地访问。
