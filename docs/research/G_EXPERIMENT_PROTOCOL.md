# G5: 调查策略评价实验协议

## 实验目标

验证不同调查策略在预算约束下的行为差异，测量信息增益和证据收集效率。

## 五种策略

| 策略 | 描述 |
|------|------|
| fixed_order | 按固定顺序执行候选动作 |
| risk_score | 按风险分数优先级选择动作 |
| information_value | 启发式信息价值排序 |
| experience | 基于历史经验库选择 |
| llm | 大模型驱动决策（需配置提供商） |

## 公平性条件

所有策略必须：
- 使用相同基础案例
- 使用相同候选动作集合
- 使用相同预算（max_actions, max_seconds, max_model_calls）
- 使用相同初始证据
- 使用相同数据分区
- 不共享测试后更新的经验
- 不筛掉失败样本

## 核心指标

| 指标 | 定义 |
|------|------|
| Steps-to-First-Evidence | 首次发现有效证据的步数 |
| Evidence-Refs-Collected | 收集到的独立证据引用数量 |
| Effective-Action-Ratio | 有效动作占总动作比例 |
| Redundant-Action-Count | 重复读取同一证据的次数 |
| Blocked-Ratio | 被阻塞的动作比例 |
| Unsupported-Ratio | 不支持的动作比例 |
| Model-Calls-Used | 模型调用次数 |
| Elapsed-Seconds | 墙钟时间 |
| Degradation-Rate | 策略降级比例 |
| Stop-Reason | 停止原因 |

## 注意事项

- 首证据步数 ≠ 首证据耗时
- 启发式分数 ≠ 信息熵下降
- 证据引用数量 ≠ 攻击检测准确率
- 不能因为策略执行完成就认为检测成功

## 数据隔离

- training：用于经验策略训练
- validation：用于调参
- test：用于最终评价
- held_out_family：独立家族测试

经验策略不能在当前测试任务执行后，将其结果作为当前任务选择时已经存在的经验。

## LLM 策略说明

LLM 策略只有真正使用模型时才计入真实 LLM 结果。
没有模型服务时发生降级，不得记为"LLM 策略完成真实实验"。

## 当前执行状态

- ✅ 实验协议已设计
- ✅ 评价框架已实现
- ✅ 多案例 CLI 已支持
- ❌ 多案例真实实验未执行（无真实 WARDEN 环境）
- ❌ 真实 LLM 实验未执行（无模型提供商配置）
