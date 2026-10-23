# AgentGuard 角色协调基线

## 当前实现

0.5.0 保留并扩展了**确定性、角色分离的协调基线**，用于验证角色间消息契约、证据引用和审计顺序；它不是多个独立 LLM 的自主协作系统。0.5.0 新增了可选的 LLM Investigator 建议器，但只允许其从现有 Finding ID 中选择优先级，不提供工具或执行权限。

当前角色：

| 角色 | 责任 | 输入 | 输出 |
|---|---|---|---|
| Supervisor | 控制流程并跟踪状态 | 实验请求、角色结果 | 阶段消息、终止状态 |
| Trace Analyst | 分析可观察事件 | Observation | DetectionFinding |
| Causal Investigator | 选择优先调查对象 | Findings、预算 | 候选目标与证据引用 |
| Defense Planner | 生成防御候选 | 选中 Finding | InterventionRequest |
| Policy Engine | 执行前权限检查 | 请求、场景、能力声明 | PolicyDecision |
| Runtime | 执行获准动作 | InterventionRequest | InterventionResult |
| Evaluator | 评价实验 | 实际运行报告或 Mock 数据 | EvaluationReport |
| Verifier | 检查证据状态 | 干预结果、评价结果 | 验证摘要 |

所有角色通信都通过 `AgentMessage` 记录，并包含 `run_id`、发送角色、接收角色、消息类型、payload schema 版本、受限 payload、证据引用、置信度、deadline、policy context 和时间戳。每次运行创建独立协调器，避免并发运行之间共享消息状态。主动调查排序会影响候选 Finding 和操作类型选择；该排序仍是可解释启发式，而非自主 LLM 规划。

## 后续升级为 LLM Agent

将确定性角色替换为 LLM Agent 前，需要为每个角色定义：

- 明确工具白名单。
- 只读/可执行权限差异。
- 输入证据视图和数据敏感级别。
- 每次调用的模型与 Token 预算。
- 结构化输出 Schema。
- 超时、重试和终止条件。
- Prompt injection 与不可信工具结果的处理。
- 相对确定性基线的消融实验。

不能仅因接入 LLM 就宣称系统具备自主调查能力。需要证明动态规划相对固定工作流带来可测量收益，例如减少干预次数、降低调查成本或提高关键边定位率。
