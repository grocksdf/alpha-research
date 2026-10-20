# AlphaResearch V0.3.5

一个 Research Agent，四个确定性组件。基于本次上传的 V0.1.1 继续开发，Python 3.11+，运行与测试均只依赖标准库。

**代码功能已扩展；已完成当前账户只读接口探测并创建一次明确授权的
`rank(close)` 冒烟 Simulation。** 真实批量研究、真实模型研究和真实数据有效性实验
尚未执行；随附 Demo 仍是合成数据，不能说明 Alpha 有效，也没有资格或积分保证。

## V0.3.5：真实账户设置对账兼容

真实单 Alpha 冒烟发现 BRAIN 会在请求设置之外返回 `startDate`、`endDate` 等
服务端字段。对账现在验证所有请求设置保持一致，同时允许服务端附加只读元数据；
任何请求字段被改变仍会拒绝。如果原 Simulation Location 已不再返回完成结果，则通过
当前账户的 Alpha 列表和 Alpha 详情 GET 找回已创建对象并对账，不能重复发送
Simulation POST。

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
