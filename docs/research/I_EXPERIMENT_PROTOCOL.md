# I6: 五策略对照实验与消融

## 实验目的

检验证据驱动的动态调查是否能够在有限预算下，比固定顺序或简单风险排序更有效地发现证据。

## 对照策略

| 策略 | 描述 |
|------|------|
| Fixed Order | 按固定顺序执行候选动作 |
| Risk Score | 按风险分数优先级选择动作 |
| Information Value | 启发式信息价值排序 |
| Experience Based | 基于历史经验库选择 |
| LLM Assisted | 大模型驱动决策 |

## 控制变量

- 同一案例
- 相同初始证据
- 相同候选动作
- 相同预算
- 相同数据分区
- 相同可用 WARDEN 能力

## 主要评价指标

| 指标 | 含义 |
|------|------|
| Steps-to-First-Evidence | 首次发现有效证据所需轮数 |
| Evidence-Refs-Collected | 获取的独立证据引用数 |
| Effective-Action-Ratio | 有效动作比例 |
| Redundant-Action-Count | 重复动作数量 |
| Blocked-Ratio | 被阻塞动作比例 |
| Degradation-Rate | 策略降级率 |
| Model-Calls-Used | 实际模型调用数 |
| Elapsed-Seconds | 实际耗时 |
| Stop-Reason | 调查停止原因 |

## 注意事项

- 首证据轮数 ≠ 真实耗时
- 证据数量 ≠ 攻击检测准确率
- 没有独立真值时不能报告 Precision、Recall、F1
- 没有真实干预时不能报告真实防御效果

## 数据来源分类

| 来源 | 说明 |
|------|------|
| Synthetic Control | 人工构造的受控测试案例 |
| Recorded Observational | 真实观察运行的历史回放 |
| Live Observational | 本次真正发生的模型观察执行 |
| Interventional | 实际执行受控干预并取得结果 |

**注意**：不同来源的数据不能在没有明确方法说明的情况下直接混合汇总。

## 当前执行状态

- ✅ 实验协议已设计
- ✅ 评价框架已实现
- ✅ 多案例 CLI 已支持持久化
- ✅ CLI/API 存储路径已统一
- ❌ 多案例真实实验未执行（无真实 WARDEN 环境）
- ❌ 真实 LLM 实验未执行（无模型提供商配置）
- ❌ 经验策略无真实数据
