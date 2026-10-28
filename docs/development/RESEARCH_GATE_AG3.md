# AG-3：研究指标与证据治理门

日期：2026-10-28。结论：**本地统计与拒绝不完整输入门通过；真实因果门 BLOCKED。**

## 实现和测试

- RQ1 使用现有 paired / family-clustered 估计、family bootstrap、family sign-flip 与多重比较校正；指标名通过冻结 `metric_definitions` 对应 `SRR_ITT`、`SRR_executed`、`BER`、正常任务 utility、载体送达率、误报代价、latency、GPU 成本等。AgentGuard 汇总 WARDEN 的 metric 输入，不代替 WARDEN 计算 BER 或解码语义。
- RQ2 仅接收和预注册 model revision、label semantics、Probe scope/target/layer/token 相同且 `split=test` 的分数/真实干预效应对应项；复用 `core/statistics.py` 输出 family bootstrap Spearman 区间。恒定排名时结果为 undefined/null。
- RQ3 按 Arm 分别报告 `E=SRR_baseline−SRR_arm` 和 `L=U_baseline−U_arm`，以 safety/utility 两轴输出描述性 Pareto frontier；不生成加权总分。
- 未运行/失败 Arm 保留状态、null 和稳定原因码；family 不匹配的 Arm 标为 `not_comparable`。隐藏 truth keys 被递归拒绝；本地路径不接受为数据/目标引用。
- caller-supplied arrays、artifact URI 和声明 hash 不证明内容/来源真实。`source_validation` 对 synthetic 返回 `synthetic_only`，其他输入为 `unverified`；`causal_claim_status` 为 `synthetic_only` 或 `not_assessed`。

| 本地命令 | 结果 |
|---|---|
| `pytest tests/test_study_analysis.py tests/test_research_registry.py` | 退出码 0；family bootstrap、RQ2 Spearman、RQ3 Pareto、丢失 Arm、历史 hash、隐藏真值测试通过 |
| `pytest tests/test_paired_runner.py tests/test_evidence_gateway.py` | 最终数量见整套验收；既有负对照、工件归属和 synthetic 隔离门继续保留 |

## 真实证据阻塞

WARDEN 没有受控 artifact bytes API、SHA 复算、来源签名或授权范围合同。任何 URI/hash 都不会被本轮标记 `integrity_verified`，也不提升到 `causal_validated`。没有可信 Recorded/Live 分区数据和独立真值，因此真实准确率、Precision/Recall/F1、防御成功率和因果效应均未报告；真实研究结论 `NOT_RUN`。
