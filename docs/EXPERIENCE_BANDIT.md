# Experience Bandit：基于已验证经验的动作排序

## 目的

0.6.0 为经验库增加一个有边界的 Thompson Sampling 排序器。它帮助研究者从历史验证记录中挑选值得进一步验证的动作，不是自治防御器，也不会直接执行任何动作。

## 运行方式

```bash
agentguard experience recommend --scenario-id demo_cloud_security --risk-type unauthorized_information_flow --seed 7 --limit 5
```

REST API：

```http
GET /v1/experiences/bandit-recommendations?scenario_id=demo_cloud_security&risk_type=unauthorized_information_flow&seed=7&limit=5
```

CLI/API 均只返回推荐排序和证据引用，不会调用底层 runtime。

## 证据准入规则

记录必须同时满足：

1. `verification_status == "verified"`；
2. `allow_auto_recommendation == true`；
3. `evidence_grade` 为 `interventional` 或 `causal_validated`；
4. 结果为 `success`、`partial` 或 `failure`；`inconclusive` 不作为奖励观测；
5. 符合请求指定的场景和风险类型过滤条件。

Verified 记录的其他合同校验（重复次数、证据引用、验证说明）仍然生效。合成或观察性记录不会被升级为可推荐经验。

## 算法

对每个动作类型，以 Jeffreys 先验 Beta(0.5, 0.5) 建模。每条合格记录贡献一个 outcome observation：

- success：alpha 加 1；
- failure：beta 加 1；
- partial：alpha 和 beta 各加 0.5；
- inconclusive：排除。

每个动作从后验分布中抽取一次样本，按样本值排序。`seed` 使同一输入得到可复现的排序。虽然记录中保存 `repetitions`，但目前**不把一个记录中的多次重复当成独立观测**，因为记录合同没有保存每一次重复的独立结果；重复数仅用于报告。

## 如何解释输出

- `posterior_mean_reward` 是模型假设下的后验均值，不是经过校准的真实成功概率。
- `thompson_sample` 是该次后验抽样用于探索/排序的分数，不是置信度。
- `verified_record_count` 是记录数，不是独立案例家族数。
- `evidence_refs` 方便研究者追溯底层材料，但系统不自动判断引用的原始数据是否真实、完整或独立。

## 安全边界与研究限制

- 只推荐动作类型，不创建 `InterventionRequest`，不持有审批 token，不执行干预。
- 真正执行动作仍需要 Capability Manifest、策略闸门、隔离范围和必要审批。
- 记录可能存在发表偏倚、选择偏倚、场景漂移和重复案例；上线前应使用前瞻性留出集验证。
- Thompson Sampling 只在“给定历史记录”的条件下对动作排序，不能单独证明动作的因果效果。
- 该实现不是强化学习训练算法；它是带证据准入规则的轻量级上下文过滤 + Beta-Bernoulli 后验排序。当前上下文由场景和风险类型的精确过滤构成，不学习嵌入表示。
