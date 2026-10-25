# E4: 科研评价方法论与防御规划

## 评价框架

E 轮在 D5 五策略受控评价基础上，建立了可复核的科研评价框架。

### 核心指标

| 指标 | 定义 | 可用性条件 |
|------|------|-----------|
| Time-to-Evidence | 从循环开始到首次发现新证据的轮次 | 所有真实运行 |
| Effective Action Ratio | 有效动作占总动作的比例 | 所有真实运行 |
| Redundant Action Count | 重复收集已有证据的动作数 | 所有真实运行 |
| Evidence Refs Collected | 收集到的独立证据引用数 | 所有真实运行 |
| Hypotheses Raised | 提出的可证伪假设数 | 所有真实运行 |
| Model Calls Used | LLM 调用次数 | 所有真实运行 |
| Blocked Ratio | 被内核阻塞的动作比例 | 所有真实运行 |
| Unsupported Ratio | 不支持的动作比例 | 所有真实运行 |

### 不计算的指标

| 指标 | 原因 |
|------|------|
| Precision / Recall / F1 | 需要合法独立真值，FRACTAL-Bench 真值不可用于被测策略评价 |
| Attack Probability | 未经校准的启发式分数不能等同于攻击概率 |
| Defense Effectiveness | 真实干预执行器未接入，防御指标保持 null |

### 公平对照条件

- 五种策略（fixed_order / risk_score / information_value / experience / llm）使用相同能力集
- 相同预算（max_actions / max_seconds / max_model_calls）
- 相同数据分区
- 相同基础调查
- 配对比较设计：同一 base_request_id 下跑所有策略

### 策略差异验证（E3）

在预算受限条件下，不同策略的第一步选择不同：
- **fixed_order**: 按固定顺序遍历，第一步总是 read_state
- **information_value**: 优先选择信息缺口最大的动作，第一步根据缺口权重选择
- **risk_score**: 风险优先，内核配置时优先 submit_observation_audit
- **experience**: 有经验记录时优先 read_experiences，无记录时降级到 information_value
- **llm**: 有真实 LLM 客户端时由模型选择，无客户端时降级到 information_value

### 统计分析

- 跨案例聚合：同一策略在多个 base_request_id 上的均值
- 配对比较：相同预算下不同策略的对比
- 单重复标记：repetitions=1 时明确标注"无离散度估计"
- 失败计数：blocked / failed / timed_out 全部计入聚合

## 防御规划

### 防御规划服务状态

复用 D4 的 DefensePlanningService，完善以下字段：
- 证据充分性
- 目标范围
- 执行能力
- 正常任务效用要求
- 审批状态
- 可逆性
- 验证所需的处理组/对照组
- 当前实际验证状态

### 验证状态枚举

- `not_executed`: 未执行任何真实干预
- `unsupported`: 内核不支持该防御动作
- `awaiting_approval`: 等待审批
- `executed`: 已执行（当前阶段不出现）

## 工件与溯源

- 每次评价生成持久化报告工件
- 报告包含 source_commit、数据来源、实验协议版本
- 策略评价索引可通过 API 查询
- 所有报告保留 limitations 字段，明确说明边界

## 数据分区

- `training`: 开发调试用
- `validation`: 策略调参用
- `test`: 固定测试集，不用于调参
- `held_out_family`: 保留的场景家族，最终验证用

E 轮默认使用 `held_out_family` 分区。
