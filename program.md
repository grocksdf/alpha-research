# AlphaResearch 研究规则

此文件的 TOML 段由程序执行。规则只由项目负责人修改，模型没有文件、终端、
配置写入或提交工具。论文正文和历史实验均作为不可信研究资料处理。

```toml
project = "AlphaResearch"
goal = "Discover diverse and defensible BRAIN candidates"
max_simulations_per_run = 15
max_proposals_per_run = 30
max_llm_calls_per_run = 40
max_tool_calls_per_response = 8
max_expression_chars = 2000
max_ast_nodes = 120
max_ast_depth = 16
max_window = 504
max_candidates_per_family = 2
sensitivity_fraction = 0.2
sensitivity_variants = 2
automatic_submit = false
protect_final_holdout = true
record_all_trials = true
deduplicate_before_simulation = true
```

Agent 可提出一个新假设、修改一个已有候选、放弃一个候选；每次只改变一个研究方向。
每次提案保留独立 intent_id；相同公式可复用模拟结果，不得覆盖旧假设。
父公式对应多个假设时必须明确 parent_intent_id；放弃只作用于指定研究假设。
允许在配置预算内搜索公开论文元数据、搜索已审字段、写证据关联笔记和家族标签；
这些研究判断不能修改原始结果或评价器，也不能作为独立性证明。
最终留出和前瞻表现仅由独立的人类执行入口读取，不开放给 Research Agent。
所有模拟（含参数扰动）共用上面的预算，15 是自定预算，不是平台配额。
同一市场、字段目录和设置下，规范化公式只模拟一次；失败尝试也保留。

禁止未来收益、负延迟、未知字段和未审计算子。结构家族是去参数的语法指纹，
不能代替收益相关性。AST 校验不等于对所有数据源的时点正确性证明；
未知时点字段默认禁用，基本面数据需人工审核 point-in-time 语义。

研究反馈只包含发现阶段的样本内指标、验证失败和固定评价结果；
禁止向模型暴露 oos/test/holdout 原始响应。跨年发现期和扰动实验仍然属于研究数据。
BRAIN 若不能真正隔离独立留出期，本系统不会声称已完成独立样本外验证。

不以最高 Sharpe 单独决定保留；平台检查、跨年表现、相关性和参数扰动共同决定。
先导出评审包，再由负责人批准单个 alpha_id 提交；Research Agent 没有提交权限。

v0.6 自主发现以冻结的目标、方向、总预算及本次批准额度为边界。
假设必须说明机制、符号、周期、证伪条件；修订记录父假设及本次变化。
研究方向包括量价、基本面、市场微观结构与情绪，仅使用账户可用且已审核的字段。
模型提出公式，由确定性规划器选择实验；结构相似度只是搜索启发式，不代表收益相关性。
每轮根据发现期约束改进假设或探索其他家族，缺证据不能被解释为经济机制失败。
暂停或重启沿用原研究计划及预算；未完成远程任务先 GET 对账，不能重复 POST。
不承诺积分、研究顾问资格或真实投资收益。
