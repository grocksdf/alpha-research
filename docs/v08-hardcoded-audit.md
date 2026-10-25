# AlphaResearch v0.8 — 硬编码 Alpha 来源与依赖审计

## 基线、可复现范围

- 原始基线：用户提供的 `alpha-research-P0P1-source-fixed-20261025.zip`，ZIP SHA256 `5c10563d98101209c14483001c64aa5283e0df19d54b323317691c2f28b7f923`。
- 本地新建 Git 导入提交：`bf232d2f85452615b4d163de9245ddda5855f44e`；分支 `v0.8-autonomous-research`。这**不是**远端 GitHub HEAD；当前运行环境无法访问远端 Git 历史。
- 扫描：`rg -n 'DemoDiscovery|CampaignDemoDiscovery|_demo_fixtures|seed|preset|template|baseline|family|neighbors\(|ts_mean\(|ts_corr\(' alpharesearch tests examples`，并人工区分是否构成候选来源。

## 删除/迁移的生产候选来源

| 原路径/入口 | 风险 | v0.8 处置 |
| --- | --- | --- |
| `alpharesearch/_demo_fixtures.py` | 内置固定公式；不应成为研究发现来源 | 移入 `tests/fixtures/generators.py`，生产包不能导入 |
| `alpharesearch/discovery.py` 中 `DemoDiscovery` | 离线生成器被产品发现路径复用 | 生产定义已撤出，离线夹具显式注入才可运行；失败时不回退 |
| `alpharesearch/campaign_runner.py` 中 Demo 生成器 | 旧 Campaign 生成预制候选 | 生成类撤出；默认生成器 LLM；只在测试中显式注入 Demo fixture |
| `alpharesearch/directed.py` | 旧六公式定向候选池 | 移入 `tests/fixtures/legacy_directed.py`，保留旧审查数据的合法性回归 |
| `alpharesearch/evolution.py` | 固定窗口/参数变体生成 | 移入 `tests/fixtures/legacy_evolution.py`，生产入口移除 |
| `examples/baseline.json` | 默认候选文件 | 移入 `tests/fixtures/baseline.json`，CLI 移除默认 baseline 分支 |
| `alpha-research evolution-plan / evolution-preview / directed-*` | 旧生产候选生成 CLI | 从正式 CLI 子命令移除；旧结果事件和哈希不删除 |
| `Engine.propose()`、`Engine.ensure_sensitivity()`、`CampaignRunner._run_depth()` | 基础模拟后自动创建参数变体 | v0.8 运行代码不再创建任何公式变体；历史敏感性关联仅只读 |
| `DiscoveryAgent` / `research.tag_family` 固定标签枚举 | 生成空间局限于已知经济家族 | 经济标签允许模型提出自由文本；新 Explorer 不要求家族枚举 |
| `autonomy-run` 旧 Live 入口 | 旧协议受限于固定领域和敏感性预算假设 | Live 正式 CLI 入口关闭；保留只读历史恢复/审计和离线测试 |

## 不得误删的确定性安全检查

- `validator.py` 中 AST、合法算子、PIT/Delay、字段时点检查，属于**验证**而非生成。
- `formula_quality.py` 的结构指纹、单位/语义预检，属于**去重/质量约束**而非经济配方。
- `Engine.neighbor_ids_for()` 的 `validator.neighbors()` 仅计算**历史冻结敏感性证据的匹配指纹**，不新建 Alpha、试验、POST 或资金占用；旧历史安全检查有独立测试。
- `probe.py` 中固定 `rank(close)` 属于独立的**账户授权冒烟验证**，不进入自主研究队列；真正 Live BRAIN Probe 不在本次授权范围，当前任务不执行。
- `robustness.py` 中 `seed` 是 bootstrap 随机数重现参数，而不是 Alpha 公式来源。
- `evaluator.py` 家族上限与相关性约束是**后验选择/防过拟合规则**；v0.8 Explorer 没有预置家族标签。
- `tests/fixtures/legacy_engine_auto_variants.py` 仅供**原有历史安全回归测试**构造旧版证据，生产包从不导入；历史兼容测试与新 v0.8 协议测试在验收中分别列出，不能混为真实模型发现效果。

## 静态扫描结果与限制

新增的 v0.8 研究产品模块 `autoresearch_v08.py` 不包含公式常量、算子示例表达式、研究家族枚举或导入测试 fixture；任何由它创建的表达式都必须关联已审核的 Worker/Critic 模型工具输出。旧版维护性模块仍保留纯验证算法和只读历史协议的固定参数规则，**不**作为 v0.8 正式候选来源。

该静态审计不能代替真实模型供应商身份认证；模型在本机返回的名称/Tool Call ID 不构成密码学签名。也不能把独立测试中的固定输入视为真实 Alpha。
