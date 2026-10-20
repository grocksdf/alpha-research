# AlphaResearch V0.1

一个轻量、可审计的 WorldQuant BRAIN Alpha 研究闭环：

1. **Research Agent**：读取 `program.md`、字段目录和实验记忆，使用工具调用提出候选。
2. **Expression Validator**：自定义 FASTEXPR lexer/Pratt parser + AST，确定性检查字段、算子、参数、前视风险、复杂度和重复结构。
3. **BRAIN Simulator**：登录、提交 Simulation、按 `Location` / `Retry-After` 轮询、读取 Alpha 和 `GET /alphas/{id}/check`。
4. **Evaluation Engine**：固定规则综合 Sharpe、Fitness、Turnover、Drawdown、逐年稳定性、相关性、平台检查和家族新颖性。
5. **Research Memory**：SQLite 保存 run、公式、规范化 AST、公式家族、谱系、原始平台响应、评价和失败原因。

## 安全边界

- 不承诺能产生可提交 Alpha，也不承诺达到 Researcher 标准。
- 默认每次 run 的 Simulation 上限为 15，可在配置中进一步降低。
- Agent 不能修改校验器、评价器或历史数据。
- 系统不会自动提交。`submit` 命令要求显式 `--approve`，并会再次调用平台检查。
- API 端点可能变化、受账户权限或验证码影响；平台响应始终是最终事实。
- 请遵守 WorldQuant BRAIN 的账户条款、自动化规则和速率限制。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alpha-research init
```

编辑 `.env`：

```dotenv
BRAIN_EMAIL=you@example.com
BRAIN_PASSWORD=...
OPENAI_API_KEY=...
ALPHARESEARCH_MODEL=gpt-5-mini
```

## 使用

先同步当前账户可用字段和算子：

```powershell
alpha-research sync-catalog
```

本地验证公式：

```powershell
alpha-research validate "rank(ts_delta(close, 5))"
```

只生成和校验，不消耗 BRAIN Simulation：

```powershell
alpha-research run --count 5 --focus "short-term reversal with liquidity control" --no-simulate
```

运行完整研究闭环：

```powershell
alpha-research run --count 5 --focus "volume-price divergence"
```

向 Agent 提供论文笔记：

```powershell
alpha-research run --count 5 --notes .\notes\paper.md
```

查看通过本地门槛和已存平台检查的候选：

```powershell
alpha-research ready
```

人工确认后提交：

```powershell
alpha-research submit 42 --approve
```

## 目录

```text
src/alpharesearch/
  agent.py        # 单 Research Agent + tool loop
  expression.py   # DSL parser、AST、规范化、约束检查
  brain.py        # BRAIN HTTP adapter + async polling
  evaluation.py   # 固定评价器
  memory.py       # SQLite schema / lineage / results
  pipeline.py     # 研究闭环
  cli.py          # 运维入口
program.md        # Agent 研究宪法
config.yaml       # 本地研究范围和固定阈值
```

## 评价逻辑

资格判断和排序分离：

- **硬拒绝**：语法、未知字段/算子、前视、非法窗口、复杂度。
- **资格门槛**：配置中的 Sharpe/Fitness/Turnover/Drawdown、平台 checks、相关性、逐年稳定性。
- **排序分数**：只在固定权重下综合质量、稳定性和家族新颖性，不允许 Agent 改权重。

`config.example.yaml` 中阈值是本地研究门槛，不代表 WorldQuant 当前官方提交条件。实际提交前以账户当前返回的 checks 为准。

## 当前限制

- FASTEXPR 很大；V0.1 parser 覆盖函数调用、算术、比较、逻辑和条件表达式。同步 operator catalog 可放宽“未知算子”检查，但部分算子的参数语义仍需逐步补充。
- BRAIN 返回结构可能因账户和产品更新而不同。原始响应会完整存档，便于增加解析适配。
- “逐年表现”只有在 Alpha 响应中可识别到逐年数值时才参与硬门槛，否则以中性分参与排序。
- 目前没有本地收益序列，因此不能可靠计算 PBO、Deflated Sharpe、参数扰动收益相关性或真正 walk-forward；这些属于 V0.2。

## 测试

```powershell
pytest
ruff check .
```

## 借鉴而非直接 Fork

- FactorMiner：实验记忆、成功/失败方向、家族与谱系。
- Microsoft RD-Agent：金融因子研究循环和知识积累。
- q3yi/worldquant 等 BRAIN 工具：异步 Simulation 和本地持久化实践。
- autoresearch：固定 evaluator、研究预算、可复现实验循环。

本仓库代码为独立实现，不复制这些项目的实现代码。

