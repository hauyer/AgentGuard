# 调查策略基线（D5）

## 已实现基线（本轮全部可执行）

| 基线 | 实现位置 | 说明 |
| --- | --- | --- |
| Fixed-order | `services/investigation_strategies.py::_fixed_order` | 固定顺序遍历只读动作，最后考虑审计；无信息自适应 |
| Risk-score | `_risk_score` | 透明启发式权重（审计>图谱>假设>证据>经验>实验）；权重非概率 |
| Information-value | `_information_value` | 按信息缺口（图谱未知/证据未知/有发现无假设）加权选择 |
| Experience-based | `_experience` | 先检索历史已验证经验；无适用记录按合同降级到 information_value 并记录 |
| LLM-assisted | `_llm` | OpenAI-compatible 提供商推荐动作 id；schema 校验 + 候选集约束；任何失败确定性降级 |

共同约束：所有策略共享同一动作集合、预算、停止条件与证据判定；
LLM 只能推荐，执行仍走规则与权限门（`docs/SECURITY_MODEL.md`）。

## 资源允许时的扩展基线（未执行）

- 单 Agent 调查基线（无多轮循环，一次性读取）：需要专门实现 + 独立标注才有意义。
- 无预算限制对照：受限于真实内核单冻结观察，当前不产生额外信息，暂缓。
- 无证据反馈变体（策略不看 evidence_summary）：见 ABLATION_PLAN 待执行项。

## 与模型/内核的关系

基线比较的是**上层调查行为**，不是模型能力：基础证据来自同一真实观察运行，
策略差异只体现在动作顺序、降级路径与预算占用上。
