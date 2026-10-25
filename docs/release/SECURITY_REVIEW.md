# 安全评审（D 轮：真实调查，2026-10-25）

范围：`feature/d-real-investigation` 分支 D0–D7 引入的全部新面
（运行配置、风险发现、调查循环、防御规划、策略评价）及其与既有边界的交互。
既有 C/A 轮安全结论不变；本文只列 D 轮增量与复核结论。

## 1. 鉴权与越权

- D 轮未新增 REST 路由（决策记录见 §6）；新读端点（readiness/options/
  timeline/graph 等，C 轮引入）在配置 `AGENTGUARD_API_TOKEN` 后全部要求令牌
  （`tests/test_security_d6.py`）。
- 伪造角色头（`X-Role: admin`、用 API token 冒充 operator token）一律 401/403；
  Operator 权限仅由服务端配置的 token 常数时间比较决定。

## 2. LLM 边界（D3 重点复核）

- LLM 只能从**服务端构造的候选集**中推荐动作 id；集合外 id 一律作废并降级
  （`test_llm_strategy_rejects_out_of_set_ids`、D6 注入测试）。
- **注入文本不持久化**：D6 评审发现模型 rationale 原文曾被写入决策记录
  （注入持久化通道），已修复——selection_reason 改为服务端中性描述，模型
  原文不进入任何工件或日志（`test_llm_prompt_injection_cannot_redirect_selection`）。
- 提示上下文只含服务端构造的结构化摘要（计数/布尔），不含消息正文、
  系统提示词或隐藏真值；不可信数据被明确声明为不可执行内容。
- 模型调用受独立预算（`max_model_calls`），无配置/超限/失败均确定性降级并
  记录，LLM 无法通过输出突破预算或触发干预。

## 3. 干预与审批边界

- 调查循环中 `plan_intervention` 是**计划生成**动作：产出仅含计划性文字与
  所需能力清单，结果状态为 completed 仅表示"计划已生成"，不表示任何执行；
  全局限 brave `execution_supported=false`（DEP-007）。
- D4 防御规划的验证计划恒为 `not_executed`；`expected_effect` 来自候选自身
  声明并在 unknown_fields 中显式标注未测量项；审批要求/可逆性照抄候选合同。
- 审计提交复用既有生命周期服务：幂等键、不确定提交不重试、审批 token
  独立校验，全部未改动。

## 4. 数据与工件卫生

- 运行配置/记录/循环报告/对比工件均不含消息正文、sender/recipient 明细、
  凭证或隐藏真值（各测试逐一断言；真值字段在合同层递归拒绝）。
- 证据引用沿用 C5 安全规则：file:// 与本地路径丢弃不打开，仅保留 opaque id。
- 工件为内容寻址（SHA-256），循环报告按 base+strategy 命名，可追溯可复验。

## 5. 资源与可靠性

- 循环受 max_actions / max_seconds / max_model_calls / no_new_evidence 四重
  硬预算约束；对抗性策略（每次请求更多）测试证明无法突破（D6）。
- 同配置重跑决策序列确定且无重复副作用（journal 单行、审计经幂等键去重）。
- 模型调用与真实审计分别计量（`model_calls_used` vs `created_audit_request_ids`）。

## 6. 路由决策记录

D3–D5 的新数据（循环报告、风险发现、防御规划、策略对比）**未新增公共路由**：
应用层当前不消费它们，且图谱挂载（`include_findings`）已承载发现关联；
按"禁止创建只有 Mock 返回的新路由"原则保持为服务层 + 内容寻址工件，待
应用层提出明确需求后再按 D6 流程评审上线路由。

## 7. 遗留风险

- 多进程部署下 readiness 缓存与限流仍为进程内（1.0 已知限制，未变化）。
- LLM 提供商可用性不在本评审范围；降级路径已验证。
- 真实干预执行器出现后，D4 验证计划的执行路径需一轮独立安全评审。
