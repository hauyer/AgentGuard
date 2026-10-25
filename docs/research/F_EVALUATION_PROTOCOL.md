# F5: 调查策略真实评价协议

## 实验设计

### 五种策略（保留）

1. `fixed_order` — 固定顺序遍历
2. `risk_score` — 风险优先排序
3. `information_value` — 信息缺口最大优先
4. `experience` — 经验库优先，无记录降级
5. `llm` — LLM 选择，无客户端降级

### 公平对照条件

- 同一任务集合
- 相同可用动作集
- 相同初始证据
- 相同预算（max_actions / max_seconds / max_model_calls）
- 相同模型版本
- 相同数据分区

### 有区分度的任务条件

- 预算不足以执行全部动作
- 不同动作具有不同成本
- 某些动作不产生新证据
- 某些动作产生重复证据
- 风险分数与证据价值不一致
- 信息缺口随新证据动态变化

## 核心指标

| 指标 | 定义 |
|------|------|
| Time-to-Evidence | 首次发现新证据的轮次 |
| Effective Action Ratio | 有效动作占总动作比例 |
| Redundant Action Count | 重复收集已有证据的动作数 |
| Evidence Refs Collected | 收集到的独立证据引用数 |
| Hypotheses Raised | 提出的可证伪假设数 |
| Mean Cost | 平均动作成本 |
| Degradation Rate | 策略降级比例 |
| Blocked Ratio | 被内核阻塞的动作比例 |
| Stop Reason | 调查停止原因分布 |

### 不计算的指标

- Precision / Recall / F1 — 需要合法独立真值
- Attack Probability — 未经校准的启发式分数
- Defense Effectiveness — 真实干预执行器未接入

## 数据隔离

- `training` — 开发调试用
- `validation` — 策略调参用
- `test` — 固定测试集，不用于调参
- `held_out_family` — 保留的场景家族，最终验证用

### 经验策略隔离

- 经验策略不能在当前测试任务执行之后，将其结果作为当前任务选择时已经存在的经验
- 经验库必须是预先验证的历史记录

### LLM 策略标记

- 有真实模型提供商：标记为真实 LLM 实验
- 无配置：降级为 information_value，标记为降级，不视为 LLM 策略实验结果

## 输出

- 实验配置文档
- 原始结果索引（持久化工件）
- 聚合统计
- 消融实验（可选）
- 局限性说明

## 当前状态

- E3 已验证策略行为在预算受限时存在差异
- F5 目标：建立可复现的受控实验，量化不同策略的实际表现差异
- 真实 WARDEN 环境缺失，当前仅可进行合成实验
