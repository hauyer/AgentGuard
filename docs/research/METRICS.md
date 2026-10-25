# 调查评价指标定义（D5）

四类指标按证据来源严格分组；无合法来源的组在合同中恒为 null。

## 1. 风险识别（当前全部 null）

Precision / Recall / F1 / False Positive Rate —— 仅当存在**独立、合法取得**的
真值标注时可计算。当前场景真值由 FRACTAL-Bench 中央评价器持有，按
`docs/INTERFACE_DECISIONS.md` 决策 2 不进入 AgentGuard 任何可查询输出。
**禁止**以工具调用成功率、任务完成率或结构信号命中数替代。

## 2. 调查效率（已实现，`InvestigationLoopMetrics`）

| 指标 | 定义 |
| --- | --- |
| steps_to_first_evidence | 首个产生新证据引用的轮次序号 |
| rounds_executed | 实际执行轮数（含 blocked/failed 轮） |
| effective_action_ratio | result_status=completed 的轮占比 |
| redundant_action_count | 重复类型且无新证据的轮数 |
| evidence_refs_collected | 循环累计发现的不同证据引用数 |
| model_calls_used | LLM 策略实际调用次数（预算内） |
| elapsed_seconds | 循环墙钟时长 |

## 3. 系统可靠性（已实现，`StrategyRunRecord`/`StrategyAggregate`）

completed/blocked/failed/timed_out 计数与完成率、阻塞率、降级率
（degradation_rate：触发确定性降级的运行占比，降级≠失败，单独列示）。
超时率与重复提交率由循环停止原因与 journal 幂等统计承载（本轮协议未触发，
记录为 0 次事件而非未定义）。

## 4. 防御效果（当前全部 null）

风险变化、正常任务效用变化、防御成本、干预局部性 —— 需要真实干预执行器、
配对重放与独立评价（DEP-006/007）。D4 的验证计划处于 `not_executed` 状态，
其存在不构成任何效果声明。

## 统计呈现规则

- 小样本只报告逐次记录 + 均值 + 不确定性声明；重复数=1 时显式标注无离散估计。
- 不使用显著性措辞宣称策略优越性；差异一律称"指示性"（indicative）。
