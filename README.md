# AlphaResearch V0.8.3.post1

**AI Alpha Research Agent：自主研究 · 生成假设 · 发现新因子。**

当前基线为 v0.8.3 P1 语义与结构加固（`0.8.3.post1`，协议 `llm-autoresearch-v083-p1`）。
Playbook 知识体由宿主代码从已核实试验事实确定性投影，模型自由文本（topic/claim/
interpretation）只作为 source-bound 不可变角色载荷存档、不进入受信任知识；
每条知识提案独立 schema 校验与来源裁决，截断/畸形/越级响应 fail-closed 拒绝。
Live BRAIN、Submit 与真实研究开关保持关闭，Demo 是唯一启用的自主研究执行路径。

**490 项离线测试通过；发布源码清单 166 项校验通过；真实 LLM 调用、真实 BRAIN 模拟与提交均为 0。**
新增 16 项 v0.8.3 P1 语义专项测试（对抗性市场断言不入库、伪造回执拦截、截断/畸形 JSON
失败关闭、非数值指标污染拦截等）。

[完整验收与升级说明](docs/STATUS.md) · [v0.8.3 P1 加固说明](docs/v083-p1-knowledge-hardening.md)。

以下为历史版本说明（v0.6.1 及更早），按时间倒序保留；发生冲突时以当前 0.8.3.post1 代码为准。

## v0.6.1 说明（历史）

本版修复 v0.6.0 的收益契约门禁回归：统一 `require_result_return_contract()`，
评价、稳定性统计与最终 Submit 分别直接执行完整验证。来源 GET/API/Alpha、币种、本金、
成本、IS、日期、日收益与摘要不一致，或 verified 带未解决原因时，不能提交，也不计算稳定性分数。

**329 项离线测试通过；17 类最终提交异常均为零模拟 Submit、零提交预留。**
原 v0.6.0 的 302 个测试身份保留，新增 17 类专项和 10 项回归。v0.5.1 原专项文件不可用，
新增 17 项为按已知要求重建的覆盖，不声称逐字恢复原件。
自主研究的两个模块和 29 项测试保持原样；真实研究/提交仍默认关闭。

[完整修复、测试来源和升级说明](docs/v0.6.1-return-contract-gates.md)。

以下是保留的 v0.6.0 自主研究说明：

目标是形成“研究思路 → 合法公式 → 规划实验 → 模拟反馈 → 自主改进 → 候选评审”的闭环。
一个研究 Agent 加确定性校验、执行、评价与记忆组件；Python 3.11+，核心只需标准库。

本版新增：

- 结构化假设和公式生成，记录经济机制、符号、周期、证伪条件与父子谱系。
- 基于已授权且已审核字段的四类研究方向：量价、基本面、微观结构和情绪；缺字段明确待补。
- 确定性规划器：去重、结构多样性、相似度、复杂度和候选加扰动的模拟成本。
- 受总预算和本次批准上限约束的自主循环，支持暂停、GET-only 恢复和同计划续跑。
- 候选报告展示公式、实验依据、风险、相关性、敏感性与提交资格；正式 Submit 始终人工批准。
- 补入 v0.5.1 要求的收益契约双重门禁，未验证文档或身份/数据不一致时拒绝提交。

v0.6.0 历史交付为 302 项离线测试；当前 v0.6.1 见上方验收。真实模型、BRAIN、市场数据与发现能力尚未验收。
本次以公开仓库 v0.5.0 commit `e316f65` 为基线；未取得上一轮 v0.5.1 源码包，收益门禁按已确认要求重新补入。
默认真实研究与提交开关仍关闭；演示固定假设与合成指标仅验证工程行为。

```bash
python -m alpharesearch doctor
python -m unittest discover -s tests -v
python -m alpharesearch autonomy-plan --spec examples/autonomy-demo.json --output state/discovery-plan.json
python -m alpharesearch autonomy-run --plan state/discovery-plan.json --dry-run
python -m alpharesearch autonomy-run --plan state/discovery-plan.json --max-new-simulations 3 --steps 1
python -m alpharesearch autonomy-run --plan state/discovery-plan.json --max-new-simulations 3
```

同一计划最多 6 次合成模拟，含参数扰动，模型调用为 0；重复执行不会重置总预算。
真实模型使用 `examples/autonomy-llm.json`，须先在本地配置模型和安全环境中的密钥。
真实模拟须经账户验收、开关和当前预览哈希确认；没有新增自动提交能力。

完整说明：[v0.6.0 自主发现、接入、恢复与路线](docs/v0.6.0-autonomous-discovery.md)。
验收边界：[版本状态](docs/STATUS.md)。统计模块沿用 v0.5.0，不重复建设。

以下保留历史版本的功能与操作说明；发生冲突时以 v0.6.1 修复说明及当前代码为准。

## v0.5.0：先核实收益，再做离线诊断

新增分析只接受审核过的日收益序列，不猜测货币/百分比、本金、累计/日频、成本、日历或 IS 边界。
缺证据返回 `insufficient_evidence`；四候选或完整试验史/独立性校准不足时 PBO/DSR 为 `unavailable`。
新报告不改变原评价器的硬门槛，Self Correlation 未 PASS 不得升级；分析不读取最终盲测或反馈给 Agent。

```bash
# 先体验完全合成的离线例子，不访问账户、不创建数据库：
python -m alpharesearch verify-returns --manifest examples/synthetic-returns-manifest.json --output state/demo-contract-review.json
python -m alpharesearch robustness-audit --manifest examples/synthetic-returns-manifest.json --protocol examples/frozen-analysis-protocol.json --output state/demo-robustness.json
# 单参数冻结与去重预览，默认规划预算 0；本版没有 evolution-run：
python -m alpharesearch evolution-plan --spec examples/evolution-study.json --output state/evolution-plan.json
python -m alpharesearch evolution-preview --plan state/evolution-plan.json
```

已有 Alpha 的 `audit-alphas` 可选 `--returns-output`，只导出契约完整的 IS 数据；未验证项保留阻断原因。
`attempt-history` 只读保留失败、无效、未完成和缓存复用记录，不冒充账户完整历史。
`research-report` 汇总已有评审、平台原始指标、费用与诊断；重复输入不算新独立证据。
**没有新增数据库迁移或创建执行入口。** 升级/回滚、成本审核字段、全部 CLI 与方法限制见
[v0.5.0 操作说明](docs/v0.5.0-robust-research.md)。离线示例报告见 [robustness-report.json](examples/robustness-report.json)。

## v0.4.2：继续研究也必须确认预览

`resume --continue-research` 现在与 `run`、`directed-run` 使用相同的预览哈希和本次新建上限。
真实调用可能创建模拟时，缺失确认会在登录前被拒绝；执行器在认证后再次核对，防止预览过期。
排队任务和确定性参数扰动共用本次上限，原 run 总预算与六公式上限仍然生效。

```bash
python -m alpharesearch resume RUN_ID --continue-research --dry-run
python -m alpharesearch resume RUN_ID --continue-research --confirm-preview PREVIEW_SHA256 --max-new-simulations 2
```

不带 `--continue-research` 的恢复仍只使用 GET，不新建或扩展参数扰动。
`metrics=null`、数组、字符串或损坏的外层 JSON 不再触发迁移 AttributeError；
异常记录保持原样并标记 remote unresolved，不影响正常记录迁移，也不释放其串行锁。
完整步骤与版本兼容说明见 [v0.4.2 操作说明](docs/v0.4.2-resume-history.md)。

## v0.4.1：严格串行与真实实验预览

- `Memory.claim()` 在同一 SQLite 事务中检查同账户/API 的所有 run 和研究范围。`dispatching/uncertain/polling`、未确认结束的 `paused/failed` 均阻止新建。
- 新增 `remote_state`，区分远程仍未结束与仅在本地等待补证。仅绑定 Alpha ID 不释放锁；经详情对账确认完成后才可释放。旧库迁移保留快照、历史结果和预算，未知状态保守锁定。
- Recordset 的 schema 名称必须非空、无首尾空白、唯一；对象记录和空记录也不能绕过 schema 校验。
- `run --dry-run` 和 `directed-run --dry-run` 不登录、不调用模型、不创建 run、不迁移数据库。固定候选显示去重后的预计新建次数，LLM 显示预算上界。
- 真实新建使用 `--confirm-preview SHA --max-new-simulations N`；状态/参数/预算改变使旧预览失效，本次上限在创建事务中执行。
- 新增 CI 配置与标准库发布脚本，检查测试、清单、ZIP 和常见凭据模式。CI 工作流未在本次远程执行。

```bash
python -m alpharesearch migrate-state
python -m alpharesearch directed-run --plan state/directed-plan.json --dry-run
# 用刚才的 preview_sha256 和本次允许的次数替换以下值：
python -m alpharesearch directed-run --plan state/directed-plan.json --confirm-preview PREVIEW_SHA256 --max-new-simulations 6
```

`migrate-state` 只针对已有数据库；新项目无需运行。v0.4.0 的计划因源码哈希变化会被拒绝，
不要改哈希复用旧计划。先恢复未完成任务、保存旧研究记录，再为新实验生成新计划。
完整语义、预算与迁移说明见 [v0.4.1 操作说明](docs/v0.4.1-serial-preview.md)。

## V0.4.0：先恢复与补证据，再执行有限实验

- `resume-probe` 优先用已绑定 Alpha ID，其次 Location；Location 失效时用当前账户 Alpha 列表与详情 GET 对账。创建响应不确定也可只读查找，零个/多个匹配均停止，不重发 POST。自动匹配要求创建时间、公式、关键设置和账户全部一致。
- `recover-trial` 支持跨版本只读恢复旧任务；原 run 快照和模拟预算不变，只追加证据。旧版本的新建/提交操作仍受快照保护。
- 设置比较使用固定关键字段协议。服务端额外返回的 `startDate/endDate` 可存在；请求主动指定的日期仍比较。关键字段冲突、缺失和未支持的请求参数都会拒绝。设置中的日期不充当 IS 日期证据。
- `audit-alphas` 读取已有 Alpha 的详情、Check、相关性和 PnL 结构/日期诊断。单位、累计/日频语义、实际日历或 IS 边界未核实时，保留未验证状态，不生成稳定性分数。
- `SELF_CORRELATION` 必须明确 `PASS`；相关性端点的数值不能放行 `PENDING`。Check 的 `PASS/FAIL/PENDING/MISSING` 独立展示，仅通过 GET 刷新。审计命令始终不标记可提交。
- 定向设计固定为两家族、六公式、四个单参数比较对，最多六次 Simulation，无自动附加敏感性实验。稳定 study_id 复用同一 run 与预算。独立留出一次性报告全部预注册比较，结果不回流给 Agent。

快速入口（配置与完整步骤见 [v0.4.0 操作说明](docs/v0.4.0-recovery-evidence.md)）：

```bash
python -m alpharesearch resume-probe state/smoke-001.json
python -m alpharesearch audit-alphas --manifest state/existing-alphas.json --output state/alphas-audit-001.json
python -m alpharesearch directed-plan --study-id directed-001 --output state/directed-plan.json --candidates-output state/directed-candidates.json
```

`resume RUN_ID` 现在默认只恢复已有写请求的 GET；不会创建排队任务或参数扰动。
真实继续研究还须确认当前预览哈希及本次新建上限；未完成创建仍占预算，未知状态不触发重新 POST。
PnL 契约新增 `pnl_unit` 与 `capital_unit` 必须一致；旧缓存缺单位依据会被隔离。

## V0.3.5：真实账户设置对账兼容

真实单 Alpha 冒烟发现 BRAIN 会在请求设置之外返回 `startDate`、`endDate` 等
服务端字段。对账现在验证所有请求设置保持一致，同时允许服务端附加只读元数据；
任何请求字段被改变仍会拒绝。历史流程曾人工用 Alpha 列表和详情 GET 找回对象；
完整自动恢复实现见本版 v0.4.0，不重复发送 Simulation POST。

## V0.3.4：Windows/UTF-8 兼容性修复

配置、规则、目录、报告、预注册和证据文件现在统一显式使用 UTF-8；原子写入的
`fsync` 在 Windows 使用可写文件描述符。上传包的 SHA-256 清单已验证无误，随后在
Windows + Python 3.11 环境修复兼容性问题并通过全部 128 项测试。详见
[V0.3.4 修复说明](docs/v0.3.4-windows.md)。

## V0.3.3：IS 日历首尾边界修复

`reviewed_context()` 和 `daily_evidence_valid()` 现在共用首尾边界检查：IS 起点距首个实际会话、最后实际会话距 IS 终点，任一超过现有 14 天保守阈值则拒绝。旧缓存中的 `complete` 标记不能绕过当前复核，稳定性评价也不会继续给出可用分数。新增回归覆盖 184/150 天反例、单侧缺失及阈值边界；共 128 项测试通过。

该检查只是异常检测，不能认证日历完整性或权威性，尤其不能排除阈值内的漏日。真实数据仍须使用经过核实的完整市场日历。详见 [V0.3.3 修复说明](docs/v0.3.3-boundaries.md)。

## 沿用的 V0.3.2 可靠性协议

- 交易日历、累计现金 PnL/无重置/固定本金/收盘日期语义必须经当前账户核实；缺少这些契约时默认不请求 PnL，不能把文件标记当作平台授权。
- 只保留日历中相邻会话的 IS 内差分。跨缺失交易日的累计差分单独标记，不计作单日观测；缺口、边界缺失或非交易日记录阻止证据完整。日历 SHA-256 和区域一并核对，不猜测周末或假日。
- 固定稳定性窗替代三个完整自然年要求：从 IS 首个会话开始，每 126 个日收益观测一窗、至少 4 窗，覆盖必须完整。未满一窗的尾部计数明确报告，不改变锚点或跳过缺口。规则是新版示例研究政策，不是平台门槛，也不等价于旧版三年要求。
- `worst_window_sharpe` 用于 Pareto 排序；全年统计仅作可选诊断。窗口 Sharpe 是核实本金归一 PnL 的本地计算，不冒充 BRAIN 报告的 Sharpe/Fitness。
- 新增 `probe-evidence`：只读对账已经完成的单 Alpha，记录 ID、公式、设置、指标变化、来源、契约及日历指纹。不会新建模拟或开放批量研究/提交。
- v0.3.1 独立留出登记及研究待验证队列继续保留；前瞻 BRAIN 路径也要求冻结的日频契约与完整交易日覆盖。

详见 [V0.3.2 配置、兼容性与验收说明](docs/v0.3.2-reliability.md)。此前修复与旧揭示迁移见 [V0.3.1 说明](docs/v0.3.1-review.md)，其中旧年度门槛已被本版取代。

## 历次完成范围

| 阶段 | 已实现的软件能力 | 实际验收状态 |
|---|---|---|
| V0.1.1 | 非支配 Pareto 分层；假设与模拟分离；多来源、父假设、独立放弃；敏感性引用 | 回归测试通过 |
| V0.1.2 | 默认只读探测；固定 `rank(close)` 单次模拟；恢复/对账；响应结构脱敏；账户与范围绑定 | 脚本化协议测试通过；真实账户待验收 |
| V0.2 | arXiv 元数据检索、字段搜索、证据关联记忆、结构/经济家族、token/时间/费用账本、公平比较 | 本地功能通过；真实 AI 优势待预算相同的对照实验 |
| V0.3 | 数据/公式/规则预注册；因果日频 DSL；隔离 Walk-Forward；一次性最终留出；前瞻追踪 | 合成数据协议通过；真实数据时点、独立性和交易效果待审查 |

系统不会因为代码升级就自动打开真实批量研究或提交。缺完整日频 PnL、固定窗口稳定性、相关性或扰动证据的候选可出现在 `research_queue`，只有证据齐全者进入人工评审 `shortlist`，两者不互相替代。`independent_holdout_validated` 不会因测试成功而自动变为 true。

## 离线启动

在解压后的 `alpha-research` 目录运行：

```bash
python -m alpharesearch doctor
python -m unittest discover -s tests -v
python -m alpharesearch run --agent baseline --report state/baseline-review.json
python -m alpharesearch field-search volume --limit 5
```

默认 demo 模式完成 5 个公式 + 各 2 个扰动，合计 15 次合成模拟。不会联网或调用模型。结果在 `state/`，运行 ID 会在终端打印。

命令入口可选装：`python -m pip install -e .`。直接用 `python -m alpharesearch` 不需要安装依赖。脚本按顺序执行，只有一个研究 Agent，没有并行 Agent 或高并发模拟。

## V0.1.1：假设与模拟的引用关系

- `trials` 是规范化公式 + 账户/范围/字段语义组成的唯一模拟任务。模拟结果只计算一次。
- `research_intents` 是每次独立研究提案；即使公式相同，也新增假设、来源、父假设和创建时间。
- `sensitivity_refs` 把每个研究假设链接到固定设计的扰动任务。扰动可以复用已完成结果，但不会覆盖原假设。
- 每个公式在候选池只计一次。报告包含其全部 `research_intents`，避免重复假设被误算成多个独立因子。
- 放弃的是 `intent_id`，不会把共享公式或其他假设一并放弃。父公式对应多个假设时，修改必须指定 `parent_intent_id`。

报告同时保留公式、失败、敏感性结果、假设谱系、结构家族和审计链。经济家族标签只是研究注释，不能证明收益来源独立。Pareto 层计算在排序外完成，报告 shortlist 包含 `pareto_layer`。

已有 SQLite 记录通过追加式迁移建立引用，不覆盖原结果；旧表保留。旧版本已经丢失的来源信息不能被凭空恢复。源码、配置或目录变化后不能直接恢复旧 run，必须创建新 run；升级前先备份自己的数据库、字段目录和报告。旧数据库不包含在交付包中。

## V0.1.2：先只读，再运行一个真实 Alpha

只在允许 API 自动研究的自己的账户上操作。密码在本机通过隐藏输入读取，也可由安全运行环境提供 `BRAIN_EMAIL` / `BRAIN_PASSWORD`。不写 Cookie 文件，不自动读取 `.env`。不要在聊天、Git 或论文输入中提供凭据。

第一步只读取目录和算子信息：

```bash
python -m alpharesearch probe-api --output state/catalog-probe.json
```

认证需要 POST `/authentication`；研究端点只做 GET。默认不发 OPTIONS、不创建模拟、不检查提交权限。成功读取目录也不等于获得模拟或提交权限。

第二步仅运行一个固定 Alpha：

```bash
python -m alpharesearch probe-api --one-simulation --expression "rank(close)" --output state/smoke-001.json
```

程序先检查只读端点，再独占创建本地意图文件，然后只发送一次创建请求。`rank(close)` 用于验证协议，不以通过平台质量门槛为目标。结果将验证远程公式、所有设置，以及 Sharpe/Fitness/Turnover 是否可以解析。

每次响应的脱敏结构写入相邻的 `*.shapes.jsonl`：保留路径模板、HTTP 状态、字段类型、数组形状和是否包含重试头；不保存响应数值、请求体、凭据、Cookie、账户/Alpha ID 或 Location 值。私有运行记录含恢复所需的 Alpha ID/Location，权限为仅当前用户；分享时优先分享结构文件。

观察到的结构只表示当前账户该次响应，不是官方稳定接口契约。当前包没有伪造“真实响应 fixture”。

### 恢复与不确定创建

```bash
python -m alpharesearch resume-probe state/smoke-001.json
```

收到 Location 后中断或超时，只恢复 GET，不再 POST。可以在轮询中按 Ctrl-C 后运行此命令验证恢复。指标暂缺时也可再次恢复读取。

POST 超时或进程在保存 Location 前退出，会保留 `uncertain` / `dispatching`。先由账户本人在平台核对是否已创建、公式与设置是否一致，再绑定已存在的模拟：

```bash
python -m alpharesearch reconcile-probe state/smoke-001.json --location https://api.worldquantbrain.com/simulations/KNOWN_ID
python -m alpharesearch resume-probe state/smoke-001.json
```

不要换一个输出文件重新 POST 来恢复不确定任务。系统不承诺跨网络 exactly-once；它保守地记录不确定状态，避免自动重复消耗额度。401/403、验证码和生物验证交回官方账户流程处理，不绕过访问控制。

### 已有 Alpha 的证据验收（不新建模拟）

```bash
python -m alpharesearch probe-evidence state/smoke-001.json --output state/evidence-001.json
```

此命令登录后只 GET 同一个 Alpha 的证据，不 POST 模拟或提交。对账结果和结构追踪会单独保存。先阅读 v0.3.2 说明，核实精确 IS 边界、PnL 语义、固定本金和完整交易日历；默认这些契约均未核实。没有足够元数据就保留缺证据状态，不能用样本年份或日历日间隔猜测。此步骤与单模拟验收是不同的完成项。

### 验收后再开启研究循环

同步完整账户目录：

```bash
python -m alpharesearch sync-catalog --output config/catalog.live.json
```

同步字段默认禁用。审核字段类型、公布/修订时点和 `min_delay`，才可将相应字段的 `point_in_time` 设为 true。AST 检查不能证明底层数据源的时点正确性。更换账户或 scope 后应重新同步。

负责人核对接口、检查项和自己的提交规则，然后修改 `config/settings.toml` 的对应位置：

```toml
mode = "live"
catalog = "config/catalog.live.json"
account_profile_verified = true
account_profile_note = "填写真实核实的时间、账号范围和依据"

[safety]
live_research_enabled = true
submission_enabled = false
validated_probe_record = "state/smoke-001.json"
```

真实研究命令检查本地冒烟记录的 HTTP 来源标记、指标解析状态以及账户、scope 和 API 地址。此文件是操作安全检查，不是授权证明；拥有文件系统权限的人可以伪造它。模拟和提交权限始终由平台服务端判断。另行冻结 `config/evaluation.toml`；示例阈值不是平台官方要求，不能为了得到好结果不断放松。

`live_research_enabled` 与 `submission_enabled` 是独立开关；探测不会替你修改任一开关。所有模拟、扰动和不确定创建共用预算，平台实际配额仍以账户和响应为准。

## V0.2：研究输入、记忆与成本

在 `[llm]` 填写支持 Chat Completions `tools` 的模型和地址，并在本机安全设置 `LLM_API_KEY`：

```bash
python -m alpharesearch run --agent llm --papers examples/research-notes.txt --report state/ai-review.json
```

模型可以查审核后的字段、查发现期实验、提出公式、独立放弃假设、添加证据关联记忆和家族注释。没有 Shell、文件读写、配置修改、提交、最终留出或前瞻报告工具。论文和工具返回的文本都作为不可信研究资料。

默认不向外部论文服务发送查询。需负责人先设置 `[research] paper_search_enabled = true`：

```bash
python -m alpharesearch paper-search "cat:q-fin.TR AND all:factor" --limit 5 --output state/papers.json
```

Agent 的 `search_papers` 使用同一客户端。只搜索 arXiv 标题、作者、版本、日期、链接和摘要，不自动下载全文、不任意抓 URL。请求有数量限制、24 小时缓存和至少约 3 秒的请求节奏；多机器总流量仍需由部署者统一控制。真实外网连通性需在部署环境验收。本次测试使用 XML fixture，没有将其冒充真实检索结果。

研究记忆类别包括 hypothesis、observation、failure_constraint、success_pattern。非假设笔记必须引用本范围的实验 ID，笔记仍标记为研究判断，不能覆盖事实或评价结果。

```bash
python -m alpharesearch research-memory RUN_ID
python -m alpharesearch record-cost RUN_ID --category human --seconds 120 --usd 2 --note "负责人记录的人工成本"
```

自动记录模拟创建/轮询时间、模型调用时间和供应商返回的 token usage。缺失 usage 或价格显示未知。`[cost]` 可填写每百万输入/输出 token 的美元价格；据此计算的是估算，不是账单，缓存折扣和其他计费需要另行核对。数据、计算、人工费用可以追加，不能将未知总费用写成零。

## 公平比较 AI 与固定规则

冻结同一账号、scope、字段目录、源码、评价规则与总模拟预算；两个实验臂用独立数据库，避免跨臂缓存。可以复制 `config/settings.toml` 为两个配置，保持根目录下 `config/` 的路径规则，只分别修改 database。

```bash
python -m alpharesearch --config config/baseline.toml run --agent baseline --report state/baseline-review.json
python -m alpharesearch --config config/ai.toml run --agent llm --report state/ai-review.json
python -m alpharesearch compare state/baseline-review.json state/ai-review.json --output state/comparison.json
```

比较器检查协议、baseline/llm 标签、实际模拟支出和跨轮缓存，输出每次模拟的合格公式数、结构家族数、完成率与成本。不同预算、协议不匹配、跨轮缓存或 demo 会明确标为不可比较。单轮差异只是描述性结果，`ai_superiority_established` 不会自动为 true。还需多轮独立、预先规定的最终验证。

去重减少的是重复计算，研究假设数量与独立公式数量分开统计。已冻结的最低稳健性、缺失检查和相关性门槛仍在固定评价器内，不因 AI 提案而降低。

## V0.3：独立的本地时间验证

本地验证与 BRAIN 仿真是两套协议。本地内核实现审核 DSL 的日频子集（最多 300 资产、100000 行；不代表 BRAIN TOP3000 验证），使用历史数据计算信号，按 `delay >= 1` 对齐，目标是下一观测交易日的价格收益。它没有复刻 BRAIN 的账户数据、模拟设置中的行业中性化、Decay、Truncation、费用模型或提交评分；表达式中某些分组算子的实现不等于平台完整组合构建。

真实数据必须由负责人确认：研究权限、PIT 公布/修订语义、历史成分与退市覆盖、价格调整方式和交易日历。没有这些保证，就不能宣称独立、无幸存者偏差或可以交易。本项目不提供外部市场数据，也不默认将 BRAIN 历史 PnL 充当独立留出集。

### 数据契约

CSV 至少包含：

```text
date,session,asset,available_date,eligible,close
```

`session` 是按交易日历连续递增的整数，同日各资产相同；缺整日会被拒绝。`available_date` 必须等于该日，当前仅接受同日已公布的日频快照；不能用未来公布数据回填过去。`eligible` 是历史 0/1 可交易成分标记。另提供公式所需的字段列，`subindustry` 可为符号分组。

可交易资产缺下一期价格会停止评估，不静默删除退市或缺失收益。数据提供者需妥善提供总收益/退市覆盖。约束上限为 100 MB、100000 行、300 资产、3000 日期、20 个候选。标签只支持一个交易日；多日重叠标签须另行实现相应清洗。

### 先用合成数据验收协议

```bash
python examples/make_synthetic_panel.py --output state/local-demo
python -m alpharesearch validation-freeze --panel state/local-demo/panel.csv --candidates examples/baseline.json --plan state/local-demo/plan.json --ledger state/local-demo/validation.sqlite3 --output state/local-demo/manifest.json
python -m alpharesearch walk-forward --manifest state/local-demo/manifest.json --ledger state/local-demo/validation.sqlite3 --output state/local-demo/walk-forward.json
python -m alpharesearch final-holdout --manifest state/local-demo/manifest.json --ledger state/local-demo/validation.sqlite3 --output state/local-demo/final.json
```

合成示例故意使用宽松训练门槛，目的只是覆盖流程；其中 Sharpe 或正负收益没有金融含义。不要沿用示例阈值作为真实研究标准。生成器不覆盖已有文件，再次实验需使用新目录。

### 冻结与隔离规则

研究计划必须声明由数据负责人维护的稳定 `dataset_id`，例如 `licensed-us-equities-pit-v1`；同一逻辑数据集不随文件、公式、窗口或 study_id 重命名。`[validation] holdout_registry` 默认是项目根目录下的 `state/holdout-registry.sqlite3`，所有研究计划共享。它与命令行 `--ledger` 的实验状态文件分离；更换研究账本不会更换该登记。多工作区/机器须由负责人统一使用同一登记服务或账本，本地 SQLite 不自动跨机器协调。

已使用过 V0.3.0 最终留出的数据，升级前须按修复说明导入旧记录。旧 manifest 不继续执行，旧报告可归档读取。

- `validation-freeze` 固定数据文件哈希、候选集、规则、scope、代码版本及最终候选。最终候选只按发现期数据选择，写入独立账本和 manifest。
- Walk-Forward 每折只用先前训练窗选公式；训练标签结束日期在测试开始之前，保留 embargo。测试窗互不重叠，不读取最终留出期的价格值。反复查看折外报告后，它也成为研究数据。
- 本地训练选择先执行 IC、费用后 Sharpe、覆盖率、换手等固定门槛，再按最差年份、IC、换手排序，不让 Agent 修改规则。
- 最终留出在读取其数值前占用唯一揭示记录；只计算已冻结候选。同一 dataset_id 的相同或重叠留出范围不能通过改 study_id、数据文件字节或实验账本重复计算；等值规范化指纹补充检测误改别名。失败发生在揭示后也不会自动重试。
- 数据或代码在冻结后改变，验证会拒绝运行。最终结果不写入研究数据库，也没有 Agent 可调用的读取工具。

报告丢失可恢复已归档副本，不重新计算留出：

```bash
python -m alpharesearch validation-result synthetic-protocol-demo --ledger state/local-demo/validation.sqlite3 --output state/local-demo/recovered-final.json
```

保留共享揭示登记、实验账本和数据归档；删除/替换登记、故意修改数据身份或把结果手动喂回模型，都会破坏研究约束。冻结后发现共享登记缺失/身份变化会拒绝执行。本地文件机制不是对拥有文件系统权限的恶意操作者的密码学隔离。`independent_holdout_validated` 保持 false，直到数据与独立性经过外部审查；协议完成不等于策略有效。

组合采用横截面信号排序后中心化、总绝对权重归一为 1；费用按再平衡的绝对权重变化扣除，权重随收益漂移。忽略借券可得性、冲击、融资、容量及撮合细节，仅用于日频研究诊断。年化频率由冻结计划给定。

## 前瞻追踪

先从研究报告冻结一个候选，起点必须晚于注册当天：

```bash
python -m alpharesearch track-register --review state/ai-review.json --trial-id TRIAL_ID --start YYYY-MM-DD --ledger state/forward.sqlite3
```

实际日期到来后，可只读拉取同一个 BRAIN Alpha 的后续 PnL，或由负责人提供有明确来源的收益 CSV：

```bash
python -m alpharesearch track-update MONITOR_ID --brain --ledger state/forward.sqlite3
python -m alpharesearch track-update MONITOR_ID --csv forward-returns.csv --source "填写数据与费用语义" --ledger state/forward.sqlite3
python -m alpharesearch track-report MONITOR_ID --ledger state/forward.sqlite3
```

CSV 为 `date,net_return`。未来日期、冻结起点之前的日期和已记录日期的不同值会被拒绝；相同观测重复导入是幂等的。少于 60 条标记短记录，不补零伪造缺失日期。未核实交易日历时不年化。BRAIN 模式核对账户、公式、设置和 bookSize，只使用起点之后的数据；若平台还没有这些日期，就报告没有新观测，不重新模拟历史。

BRAIN 的 PnL 差分除以 reported bookSize 不保证与扣除所有现实费用后的收益相同，来源语义保留在报告中。前瞻账本与研究记忆隔离，Agent 没有追踪读取工具。本版本提供命令，不会在 ChatGPT 或服务器上自动安排定时任务。

## 人工审批提交仍独立关闭

没有批量提交或 Agent 提交工具。只有负责人另行打开 `submission_enabled`，且候选通过固定评价并出现在已归档评审包后，才可显式批准一个精确 Alpha ID：

```bash
python -m alpharesearch submit RUN_ID TRIAL_ID --review state/ai-review.json --approve-alpha-id EXACT_ALPHA_ID
```

提交前重新验证；先记录意图再 POST；不明确时只恢复查询。测试、冒烟、论文检索和本地验证均不解锁提交。真实平台仍可能拒绝，系统不保证积分或研究顾问资格。

## 工程与证据位置

- `program.md`：研究规则和硬预算。
- `alpharesearch/validator.py`、`evaluator.py`：固定校验与分层评价。
- `memory.py`、`engine.py`：引用、谱系、预算和恢复。
- `probe.py`、`trace.py`：单次真实接入流程和脱敏结构。
- `papers.py`、`research.py`、`benchmark.py`：检索、结构化记忆、家族及比较。
- `local_validation.py`、`tracking.py`：隔离时间验证和前瞻观测。
- `docs/verification.txt`：本次实际执行的检查及未执行项。
- `docs/references.md`：核查过的上游项目和方法资料。

不可覆盖的历史表与审计链用于发现意外修改，不是独立托管的防篡改账本。每轮记录代码哈希、Git commit（存在时）和运行时版本；可自行 `git init` 后按自己的仓库流程提交源码，`state/` 与凭据不进入 Git。源码包附校验清单，运行数据不打包。


## v0.8: LLM-only autonomous research (offline Demo)

The new, separately installed `alpha-research-v08` command is a source-verified
Explorer → Strategist → Worker → Critic → Playbook loop with **no built-in Alpha
formula seeds, economic mechanism enum or automatic parameter variants**.

```bash
python -m alpharesearch.v08_cli --config config/settings.toml \
  --database state/v08-fresh.sqlite3 plan \
  --project-id offline-new --objective 'Discover and falsify new authorized mechanisms' \
  --output state/v08-plan.json

# Requires a real tools-capable LLM and LLM_API_KEY.
# Remote HTTPS needs frozen [llm.remote_authorization] AND --allow-remote-llm.
# The default Demo run NEVER contacts BRAIN or Submit.
python -m alpharesearch.v08_cli --config config/settings.toml \
  --database state/v08-fresh.sqlite3 drive --plan state/v08-plan.json --batches 3
```

No LLM endpoint/key means **no autonomous candidate**; Scripted offline
fixtures are only under `tests/`, never a product candidate fallback.
Original v0.6 frozen runs and submission gates are preserved.

For reviewed remote HTTPS providers (or local HTTP loopback), follow
`docs/v08-llm-provider-authorization.md`. Real LLM acceptance is an opt-in
networked Demo test in `verification/v08_genuine_llm_acceptance.py`, not a
scripted-model pass. A valid ChatGPT conversation is not an API credential.
The independent `benchmarks/evaluate_memory.py` is included in source ZIP and
Python wheel distributions; it never generates production candidates.

See `docs/v08-hardcoded-audit.md`, `docs/v08-architecture-playbook.md`,
`docs/v08-experiments-ab.md`, `docs/v08-next-brain-validation.md`, and
`docs/v08-compatibility.md`. Complete offline safety regression:
`python verification/v08_module_matrix.py --repo . --report-dir /tmp/v08-check`.

## v0.8.3 Playbook evidence consistency (offline safety update)

A queued Alpha proposal is **not** simulation evidence. Playbook now receives
only completed, persisted trial evidence, and source-bound knowledge proposals
are reviewed independently. Rejection of an invalid proposal does not erase
accepted sibling evidence or previously completed Demo simulations. Immutable
per-item review receipts enable safe reconciliation after an operator-controlled
fault recovery without resending any model request or Simulation.

**Compatibility:** v0.8.3 uses the `llm-autoresearch-v083` frozen plan protocol;
create a new dedicated database. Historical v0.8.2 plans remain immutable and
cannot be silently upgraded. Demo remains the only enabled autonomous research
execution path; Live BRAIN and Submit are not authorized by this release.
See `docs/v083-playbook-P0-fix.md` for the audit and test scope.


## v0.8.3 P1 Playbook semantic and structure hardening (0.8.3.post1)

The live research/Submit gates are unchanged and disabled. The production
Alpha generator remains **LLM-only**. This update changes the frozen protocol
from `llm-autoresearch-v083` to `llm-autoresearch-v083-p1`; old study databases
**must be kept with their original code**, not resumed with this build.

- Only actual persisted, completed trials count as evidence. The host now
  constructs the knowledge `claim`, `applicability`, `uncertainty`, numeric
  observations and scope from the verified trial ledger, **not** model prose.
  All metrics from Demo are labeled synthetic. No performance promise or
  market-validity statement from a model can become a stored knowledge fact.
- The model still proposes a topic, interpretation, uncertainty, questions and
  citations. Its exact source-bound text is kept in the immutable role payload
  and identified by a SHA256 digest. This is an *unverified interpretation*,
  not part of the trusted/retrieved knowledge content. Topic identifiers are
  hashed rather than treating a free-text title as a research fact.
- Retrieved Playbook memory explicitly includes its evidence grade, verified
  applicability, uncertainty and observations. Memory retrieval/reference/
  application audit remains separate, not causal evidence of model uplift.
- For a well-formed, complete and attributed Playbook tool call, each knowledge
  item is schema-validated and reviewed independently. An invalid sibling
  receives an immutable rejection receipt; valid siblings may be accepted.
  A truncated tool response, malformed JSON, wrong tool, malformed envelope or
  absent model provenance is **never partially accepted**.
- No automatic resubmission is added. The existing explicit manual bounded
  model-retry path and fail-closed unknown-write handling are retained.

For reproducible offline tests run `python verification/v08_module_matrix.py
--repo . --report-dir /tmp/alpha-p1-offline`. Genuine three-round research is
**not covered** by scripted evidence and requires separate model-price approval
and an authorized API key before `verification/v08_genuine_llm_acceptance.py`.


### v0.8.3.post2 semantic-contract safety update

See [research-role semantic contracts and privacy boundary](docs/v083-semantic-contract-and-privacy.md).
This offline-only release derives role guidance from the actual tool schema and executable cross-field contract rules, and distinguishes true credential indicators from citable scholarly URLs. No fixed Alpha formulas or families were added. Historical frozen projects require their original code identity; new tests use a new plan and Demo-only database.
