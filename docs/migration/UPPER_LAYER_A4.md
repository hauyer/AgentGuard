# 上层平台重构 A4：多 Agent 调查编排

日期：2026-10-24。代码范围为 AgentGuard；底层仓库仍只读。

## 实际实现

- `ExperimentRequest.investigation_strategy` 支持 `fixed_order`、`risk_score`、
  `information_value`、`experience`、`llm`。原默认流程保持为确定性的信息价值
  排序。CLI JSON/YAML 与 REST 均可显式切换；`--llm-investigator` 在没有配置
  其他策略时选择 LLM 策略。
- 风险排序使用严重等级和未校准筛查分；信息价值排序用现有候选动作的启发式
  utility 与去重证据引用数；固定排序保持检测器输入次序。这些值均为排序启发
  式，不是概率或收益估计。
- 经验策略只使用匹配场景和风险类型，且为 `verified`、明确允许自动推荐、
  `interventional` 或 `causal_validated` 的历史记录。没有合格记录时记录原因并
  明确退回风险排序。经验只影响候选优先级，不授权执行。
- 各候选的排名、排序依据、证据引用、实际选择原因和确定性回退原因进入类型化
  `AgentMessage`。候选上限由 `max_agent_steps` 控制，超出部分会在轨迹中记录
  被截断数量和限制值。
- `ROLE_TOOL_ALLOWLIST` 与消息 allowlist 限定每个角色的内部操作与通信。协调器
  在检测、候选排名、防御建议、策略检查、已授权执行和评价等节点校验角色权限。
  LLM 提示只携带有限发现摘要，没有 tools 字段；输出合同禁止额外字段，且只能
  返回候选 ID。无论由哪种策略选中，写操作仍通过现有 Policy Engine。
- LLM 的调用数受 `max_model_calls` 剩余额度限制，生成上限取请求输出 token 上限、
  剩余模型 token 预算和系统上限中的较小值。运行时用量未知时不调用 LLM；总
  token 未由模型供应方报告时保留为 unknown 并写明限制。外部工具调用数仍受
  `max_tool_calls` 与 runtime 声明的用量追踪，LLM 本身无工具调用能力。
- 配对实验设计现在使用策略最终选择的发现及其动作，不再沿用另一候选的设计。
- 内部包导出改为惰性加载，避免导入顺序形成循环依赖。
- 更新 `experiment`、`experiment-budget`、`budget-usage`、`run-report` Schema。

## 验收与限制

- 定向策略/API/LLM/Schema 回归：45 passed；新增 API 经验读取用例已纳入完整套件。
- 全量 pytest：208 passed、2 skipped、1 warning。跳过项依赖可选 PyTorch 和未配置
  的真实 WARDEN HTTP 服务。
- `compileall -q src tests scripts`：通过；48 份 Schema parity 测试通过。
- A4 涉及的模块、合同、API/CLI 与测试 Ruff：通过。
- 全仓 Ruff：124 项仍未通过，A0 基线为 171 项；剩余质量债务留在 A8。
- AST 架构审计：76 个模块、463 条导入；3 条外部底层导入全部留在兼容包；
  `boundary_violations=[]`、`dependency_cycle=[]`。
- `git diff --check`：通过。

完整测试只证明策略和接口行为；协调器仍由确定性角色组件构成，LLM 只做候选
优先级建议。本阶段没有执行真实模型多 Agent 消融，也没有把合成/观察数据提升
为因果证据。
