# I6 策略消融与预算敏感性

## 当前状态

本文件定义可复现的消融方案；本轮没有生成可验证的策略评价记录或报告工件，所有
实验项均为 **未执行**。不把单元测试、合成后端或 Stub 行为记作研究效应。

## 消融矩阵

| 实验项 | 唯一变化 | 固定条件 | 主要观测 | 当前状态 |
| --- | --- | --- | --- | --- |
| 无新增证据反馈 | 后续策略决策不接收上一轮新增 evidence refs | 起点、动作集、策略版本、预算、seed、WARDEN 能力 | `steps_to_first_evidence`、证据数、冗余动作、阻塞率 | 未执行；需提供可配置的对照开关 |
| 无经验数据 | `experience` 使用运行前冻结的空经验集 | 同一输入、候选动作、预算和能力 | 降级率、逐轮选择、有效动作比例 | 未执行 |
| 经验数据隔离 | `experience` 只读取 preregistration 中声明的训练 family 快照 | 其他设置相同 | 运行状态、降级率、逐轮动作 | 未执行；需要经验快照与访问审计 |
| 无模型提供商 | `llm` 不配置 provider | 其余请求和预算相同 | `model_calls_used`、降级原因、停止原因 | 未执行；必须标记无真实模型调用 |
| 预算敏感性 | `max_actions` 分别取 3、5、8；每个预算独立运行 | 同一输入/策略/seed，其他预算上限不变 | 证据数、首证据步数、耗时、停止原因 | 未执行 |
| 只读动作基线 | 固定的一次性只读动作顺序，不使用下一轮反馈 | 同一证据快照及动作集合 | 证据数、冗余动作、步骤、耗时 | 未执行；需确认实现与计量等价 |

## 运行规则

1. 在查看实验结果之前登记输入快照、案例 family、数据分区、seed、策略版本、预算、
   底层能力、唯一消融变量和停止规则。
2. 按配对组运行；每次保存状态、失败/阻塞、降级、完整逐轮决策和报告工件。
3. `experience` 不得访问被评估案例或其 family 的后验结果。经验快照需写入输入哈希。
4. Stub 与 synthetic 结果单独存放，不计入 live 样本。真实观察要求 WARDEN 运行 ID
   和证据引用；干预结果还需成对基线和独立评估。
5. evaluation ID 必须唯一映射到一次 comparison 与报告工件；若缺少输入哈希、完整
   source SHA、evaluation ID 或工件，结果不进入汇总。
6. 同一历史观察上的多次重复是技术重复，只用于检查稳定性；统计独立样本数按案例
   family 计。

## 报告字段

逐次记录合同已有的 `steps_to_first_evidence`、`evidence_refs_collected`、
`effective_action_ratio`、`blocked_ratio`、`redundant_action_count`、
`degradation_rate`、`model_calls_used`、`elapsed_seconds` 和 `stop_reason`。
未知值保留 `null`。不计算无独立真值支撑的 Precision/Recall/F1，也不报告无真实
配对干预支撑的防御效果。

## 结果登记

| 消融 ID | 输入 / 来源 | 完整 source SHA | evaluation ID | 报告工件 ID | 结果 |
| --- | --- | --- | --- | --- | --- |
| 无 | 尚无运行 | N/A | N/A | N/A | 未执行 |
