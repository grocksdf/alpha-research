# 参考资料与实现取舍

核查日期：2026-10-20。以下公开来源的默认分支会变化。本项目为独立实现，未复制或安装第三方框架，也未执行第三方挖矿/自动提交脚本。

| 项目或来源 | 采用的设计线索 | 适用边界 |
|---|---|---|
| [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) | `fin_factor` 的假设—实现—评价反馈循环 | Qlib 场景不能直接当成 BRAIN Fast Expression 或 BRAIN 回测结果 |
| [FactorMiner 论文，2602.14670](https://arxiv.org/abs/2602.14670) | 模块化评价、结构化成功/失败记忆、减少相关性冗余 | 学术实验不证明 BRAIN 中有效或能提交 |
| [minihellboy/factorminer](https://github.com/minihellboy/factorminer) | 可运行开源实现中的谱系、实验记录、恢复与复现契约 | 仓库自称基于论文，不在此宣称为论文作者唯一官方仓库；默认金融数据与接口不同 |
| [karpathy/autoresearch](https://github.com/karpathy/autoresearch) | `program.md`、固定评价器、受约束实验循环 | 没有引入模型训练代码或单一 Sharpe 优化目标 |
| [wh0amibjm/brainapi-go-sdk](https://github.com/wh0amibjm/brainapi-go-sdk) | 文档中的 BRAIN 响应陷阱与测试 fixture：GET check、浮点 Retry-After、空体长轮询、提交 503/303 | 第三方按其账户观察的协议；不是 WorldQuant 官方 API 承诺 |
| [zeron-G/worldquant-alpha-research-agent API_RESEARCH](https://github.com/zeron-G/worldquant-alpha-research-agent/blob/main/docs/API_RESEARCH.md) | API 流程与需实测的端点清单 | 仅辅助交叉核查，不视为账号授权或稳定接口保证 |
| [WorldQuant 官方 Consultant 页面](https://worldquantbrain.com/consultant) | 10,000 积分与 Gold 后可能获邀；高级功能以资格为准 | 不保证邀请或资格；账户实际要求优先 |

## 已确认的公开代码约定与未验证项

实现中的默认路径包括 `POST /authentication`、`POST /simulations`、按 Location GET、`GET /alphas/{id}`、`GET /alphas/{id}/check`、`GET /alphas/{id}/recordsets/pnl`、`GET /operators`、`GET /data-fields`、`POST + GET /alphas/{id}/submit`。第三方源码和 fixture 支持这些约定，**本交付没有用用户账户实测它们**。

`/alphas/{id}/recordsets/yearly-stats`、`/correlations/self`、`/correlations/prod` 及具体字段列仍需根据账户响应逐项核实。尤其年度统计 schema 本实现仅支持带明确列名的记录，不猜测位置列。没有所需证据会显示 `evidence_incomplete`，不会编造年度指标来放行。

提交阶段部分账户的 `SELF_CORRELATION` 只在提交后完成。本实现允许在独立预提交相关性证据存在且通过阈值时，对唯一的该项 PENDING 采用有文档说明的例外；其他 PENDING/未知检查仍阻止提交。所有阈值由负责人冻结，不由 LLM 更改。平台是否接受只以提交后实际状态确认。

本地 mock 覆盖协议行为，不能证明真实端点兼容性、账户权限、积分计算或因子经济效力。生产接入应先用最小预算验证，再放大研究规模。

## V0.1.1 additional third-party observations

- [FilbertNg/worldquant](https://github.com/FilbertNg/worldquant) describes a *Pre-Consultant* workflow and shows a historical example of an accepted `POST /simulations` (201). This is anecdotal repository evidence from 2025, not proof of current account access, licensing, or permitted automation. Its aggressive concurrent and infinite-retry patterns are **not** reproduced here.
- [zeron-G worldquant-alpha-research-agent](https://github.com/zeron-G/worldquant-alpha-research-agent/blob/main/docs/API_RESEARCH.md) records frontend `OPTIONS /simulations`, `POST /simulations`, accepted version headers and variant single-alpha response shapes. The V0.1.1 probe tests only its own account endpoint responses.
- [WorldQuant consultant page](https://worldquantbrain.com/consultant) describes API + Multi-Simulation as advanced consultant features. Client wrappers do not override account restrictions.

## V0.2 / V0.3 方法与接口

- [arXiv 官方 API 用户手册](https://info.arxiv.org/help/api/user-manual.html)：Atom 元数据、查询参数、版本信息。
- [arXiv API 使用条款](https://info.arxiv.org/help/api/tou.html)：本实现限制并发、请求节奏和缓存；部署者仍需协调所有机器的总请求。
- [Microsoft Qlib rolling task generator](https://github.com/microsoft/qlib/blob/main/qlib/workflow/task/gen.py)：滚动训练/测试窗口的参考背景。本地代码独立实现，不复制 Qlib 数据或数值结果。
- [Bailey & López de Prado, The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)：多次搜索、选择偏差与非正态性问题。本版本没有以简化公式假冒实现 DSR/PBO。

核查日期 2026-10-20。Walk-Forward 诊断、预注册和一次性留出是不同证据层次；重复观察折外结果会削弱其独立性。最终数据独立性还依赖部署者的预先访问记录和数据时点审查，不能只靠本地文件哈希证明。
