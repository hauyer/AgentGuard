# 消融与预算敏感性计划（D5）

## 已内建且已执行的消融

| 消融 | 机制 | 结果载体 |
| --- | --- | --- |
| 无历史经验 | experience 策略在空经验库时按合同降级到 information_value | RESULTS.md 降级率列（1.0 = 全部触发降级路径） |
| 无 LLM 提供商 | llm 策略无客户端/预算耗尽时确定性降级 | 同上；`model_calls_used=0` 佐证未发起调用 |
| 预算上限 | max_actions 硬停（action_budget_reached） | LoopDecisionRecord.stop_reason |

## 待执行（依赖解除条件）

| 项 | 设计 | 阻塞 |
| --- | --- | --- |
| 预算敏感性 max_actions ∈ {3,5,8} | 同协议重跑，比较 evidence_refs_collected 与首证据步数曲线 | 无阻塞，可在下一次 live 会话执行（本轮单预算已跑） |
| 无证据反馈变体 | 策略调用时不传 evidence_summary，仅按静态顺序选择 | 需要策略接口加开关；建议 D6 后实现 |
| 单 Agent 一次性读取基线 | 无循环、单轮全读对照 | 需实现 + 独立标注才有解释力 |
| 因果消融（去干预/去对照） | D4 验证计划的配对执行 | DEP-006/007（无干预执行器与独立评价） |

## 统计规则

同 EXPERIMENT_PROTOCOL.md §5：小样本报告不确定性与效应量（均值差），
不使用显著性措辞；未执行项不得预填数值。
