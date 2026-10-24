# Experience Bandit：证据门控的上下文动作排序

## 目的

Experience Bandit 复用既有 SQLite `ExperienceStore`，按场景、风险和版本筛选历史记录，用 Beta-Bernoulli Thompson Sampling 排序值得继续验证的动作。它不执行动作，不产生因果结论，也不把后验分数解释成校准成功概率。

## 记录与验证门控

经验合同 0.2 记录审计运行 ID、来源系统和运行终态、假设、选中动作、证据等级/引用、风险结果、正常任务效用、执行成本、模型/策略版本、可复现摘要、数据分区、验证时间、到期时间和限制。

新的成功奖励必须同时满足：

1. 来源为完成的 WARDEN 运行，证据引用经过可信流程核验；
2. 属于 training 分区且案例家族已固定在该分区；
3. 至少两次重复、有评价风险指标和正常任务效用、有执行成本及验证记录；
4. 有模型/策略版本、审计运行 ID 和 SHA-256 可复现摘要；
5. 证据为 `interventional` 或 `causal_validated`，显式允许推荐，结果不是 `inconclusive`；
6. 经验未过期，也没有与同一运行和同一工件上下文冲突的结果。

合成或 Mock、观察性、失败/blocked/cancelled/未知运行、只含风险分、缺工件或效用、旧格式、验证过期、冲突或不可复现的内容不会贡献奖励。公共 `POST /v1/experiences` 拒绝调用方自称 `verified`。当前 WARDEN 没有安全的外部工件完整性读取接口，因此自动记录保持 observational/unverified。

## 数据分区与防泄漏

`dataset_partition` 为 `training`、`validation`、`test` 或 `held_out_family`。同一 `case_family` 只能属于一个分区；旧记录因原合同没有分区信息，迁移时进入 `held_out_family`。公共 API 和 CLI 不返回 test/held-out 经验。

推荐只从 training 记录取值，并排除当前调查的案例家族。Validation、test 和 held-out 经验不会被用作奖励；test 查询返回空结果。执行 test/held-out 评估时，训练经验可以跨不同案例家族提供先验动作排序，但系统不会读取目标家族的隐藏答案或事后评价。

## 检索与运行方式

API 与 CLI 支持按场景、风险、动作、模型版本、记录策略版本、证据等级、最近验证时间和允许的分区检索。调查请求可以指定 `case_family`、`dataset_partition`、`model_version`、`experience_record_policy_version` 和 `recommendation_policy_version`。

```bash
agentguard experience recommend --scenario-id demo_cloud_security --risk-type unauthorized_information_flow --case-family heldout-family --dataset-partition test --seed 7 --limit 5
agentguard experience policy-list
agentguard experience policy-rollback --policy-version experience-thompson-v1 --reason validation_regression
```

```http
GET /v1/experiences/bandit-recommendations?scenario_id=demo_cloud_security&risk_type=unauthorized_information_flow&case_family=heldout-family&target_partition=test&seed=7&limit=5
GET /v1/experiences/recommendation-policies
POST /v1/experiences/recommendation-policies?activate=true
POST /v1/experiences/recommendation-policies/experience-thompson-v1/rollback?reason=validation_regression
```

REST 策略创建和回滚要求部署时配置 `AGENTGUARD_API_TOKEN`，并在请求头中提供 `X-AgentGuard-Token`。策略版本不可变；所有激活和回滚写入现有经验库的事件表。

## 算法和输出解释

对每种动作使用 Jeffreys Beta(0.5, 0.5) 先验。每条 eligible 记录只贡献一个结果：success 增加 alpha，failure 增加 beta，partial 对两侧各增加 0.5。`repetitions` 仅报告，不被伪作独立样本。

报告中提供后验均值、抽样值、后验标准差、平均执行成本和成本调整后的样本分。策略配置确定 cost penalty 与 cost scale；固定 `seed` 和策略版本使同一输入可复现。成本调整只改变建议排序，不代表真实净收益。

- `posterior_mean_reward` 是当前 Beta 模型的描述性均值，不是校准概率。
- `reward_uncertainty` 描述当前有限记录下的后验离散程度，不替代置信区间或独立验证。
- `mean_execution_cost` 取自满足奖励门控的记录；缺成本的记录不会进入计算。
- `cost_adjusted_sample` 是策略版本下用于动作排序的抽样分数，不是风险下降估计。
- `execution_authorized` 始终为 false；运行仍需独立的审批、能力和策略检查。

## 策略限制

- 重复统计相关、选择偏倚、模型/场景漂移和未覆盖风险仍可能影响排序；冲突记录被隔离而不由多数票决定。
- 真实 WARDEN 工件核验和独立经验审核尚未接通，因此本阶段没有新的真实经验奖励或真实策略收益结论。
- 本实现不是 DQN/PPO 或强化学习训练；不充足的真实数据不应用复杂模型掩盖数据缺口。
