# AlphaResearch v0.8 开发与分级验收报告

**审查日期：** 2026-10-25
**Git 基线：** 本地导入提交 `bf232d2f85452615b4d163de9245ddda5855f44e`（用户提供的 P0/P1 完整发布 ZIP；非远端 HEAD）
**Git 分支：** `v0.8-autonomous-research`
**安全约束：** 无真实 BRAIN Simulation、无 Submit、无真实外部 LLM 请求、无旧研究数据库修改。

## 1. 实际源码改动

- 新建独立 LLM-only `alpharesearch/autoresearch_v08.py` 研究回路与 `alpharesearch/v08_cli.py`，五角色 Explorer、Strategist、Worker、Critic、Playbook；公式完全来自来源可验证的 Worker Tool Call，经济机制无预定义标签池。
- 旧内置 `DemoDiscovery` 和 `CampaignDemoDiscovery` 与 `directed`、`evolution`、`baseline` 生成来源转入 tests 专用 fixtures，生产 CLI 不再提供那些生成入口。
- 原 Engine、Campaign 去掉生成后自动套公式/改参数的行为；留存历史敏感性**只读**验证和独立收益/Submit 门禁。旧历史回归单独加载 tests-only 旧协议夹具以核验冻结安全规则，不能把夹具表现当作新产品功能。
- Playbook 两层存储，持续生命周期 / 每批资源限额、队列恢复、模型身份与原始结构化响应哈希、来源请求记录、失败暂停、反例/过期知识排除与知识版本链。
- `benchmarks/evaluate_memory.py` 作为**不进入产品生成器**的只读、成对四臂对照计算器。可记录机制多样性/验证效率/样本量，但缺真实对照时只输出 INCOMPLETE。
- 新增 QA 与源码硬编码依赖审计，以及本文件、架构说明、实验报告和下一轮 BRAIN 计划。

## 2. 实际工程证据

- 原完整源码基线 P0/P1：392/392 安全与功能回归（由用户之前任务提供的可执行源码）。
- 本轮 v0.8：**26 个独立测试模块，总计 410 个用例全部通过**。为防旧单进程测试运行器停在解释器退出阶段，验收脚本将每个测试模块放入独立进程，禁用 socket 网络并移除 BRAIN/LLM 环境凭据。每个模块返回零退出码和可解析 unittest test case 数字；日志/聚合 JSON 一并交付。
- 在合并文档后的最终发布阶段，`python scripts/release.py --check` 验证发布源码清单；官方无历史数据库源码 ZIP 与清单一致。
- 新的协议工程测试：LLM 不可用零候选、动态授权字段、合法 Worker 提案、非法 AST 阻断、5 角色来源归档、跨批次记忆/修订、父子谱系、矛盾排除、未知批次故障暂停、冻结计划、独立旧库隔离、正式 Live/Submit 阻断、A/B 资源相等性及无效果冒领。
- 原有安全测试：Submit、原历史安全门禁/收益契约、协议校验、平台模拟预约及错误恢复仍通过（部分历史固定参数证据由 tests-only 旧协议夹具构造，不是产品生成）。

## 3. 不要误解的验证边界

| 验收目标 | 当前状态 | 理由 |
| --- | --- | --- |
| v0.8 LLM-only 自主研究工程协议 | **离线脚本模型验证通过** | 角色工具、结构校验、队列、反馈闭环、无模板回退 |
| Playbook 记忆工程能力 | **离线脚本模型验证通过** | 真实 SQLite 事务、evidence ID、不可变修订、内存检索过滤 |
| 真正由**真实 LLM**自主多轮 Alpha 发现 | **未验证** | 无授权本地模型服务器、无 `LLM_API_KEY`；无真实 LLM 调用 |
| Playbook 实际提高 LLM 研究效果 | **未验证** | 无四臂冻结真实对照记录；独立评测工具返回 INCOMPLETE |
| 真实 BRAIN Alpha 独立市场有效性 | **未验证** | 本次未授权真实 BRAIN Simulation，未产生实际收益证据 |
| 正式 Live BRAIN 研究 | **禁用** | 等待账户授权、配额审查、真实结果验证、独立审批 |

**不能因为 Demo/脚本化模型测试通过，就宣称满足“真实模型在完全无固定种子下完成多轮研究”的最终最强判定。**

## 4. 源码审计和依赖许可

硬编码审计见 `docs/v08-hardcoded-audit.md`；Playbook 逻辑见 `docs/v08-architecture-playbook.md`；本地真实模型实验和四臂评测状态见 `docs/v08-experiments-ab.md`；迁移/破坏性 API 变化见 `docs/v08-compatibility.md`；后续真实 BRAIN 计划见 `docs/v08-next-brain-validation.md`。

参考来源仅借鉴架构：Morgan Stanley MSML AlphaLab Apache-2.0、Microsoft RD-Agent MIT、FactorMiner MIT；QuantaAlpha 许可证仍需独立确认，本次没有复制任何该仓库源码。没有引入外部交易、交易所连接、实盘执行系统或大型基础设施。

## 5. 旧数据与可复现交付

- 独立本地开发分支，不覆盖旧冻结计划/SQLite、旧 Submit 安全门禁或收益契约。新 v0.8 source manifest 不应替代历史 Run 使用的原始代码快照。
- 执行 `python scripts/release.py --check` 和 `python verification/v08_module_matrix.py --repo . --report-dir /tmp/v08-qa`；若系统终止单次命令，可用 `--start 0 --end 13` 和 `--start 13 --end 26` 分段运行，合并两个已验证报告。
- Git diff 由本地合成基线提交产生，不能推断此分支已推送 GitHub。
- 使用新独立 v0.8 DB，真实本地 LLM 需外部明确授权模型及 loopback endpoint，旧数据库无法直接作为新项目库写入。
