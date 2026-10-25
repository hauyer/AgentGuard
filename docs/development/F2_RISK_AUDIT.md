# F2: 风险分析公共接口审计报告

## 审计结论

**RiskFinding 合同满足全部安全要求，无需修改代码。**

## 字段完整性核对

| 要求字段 | 状态 | 说明 |
|---------|------|------|
| finding_id | ✅ | 主键，min_length=1 |
| investigation_id | ✅ | 关联调查 ID |
| risk_type | ✅ | 风险类型 |
| detection_method | ✅ | 检测方法枚举（6 种） |
| supporting_evidence_refs | ✅ | 支持证据引用 |
| contradicting_evidence_refs | ✅ | 反对证据引用 |
| severity | ✅ | 严重度分级（low/medium/high/unknown） |
| score | ✅ | 可 null，范围 [0, 1] |
| score_calibration_status | ✅ | 默认 uncalibrated |
| graph_node_refs | ✅ | 图谱节点引用 |
| graph_edge_refs | ✅ | 图谱边引用 |
| verification_status | ✅ | 默认 unverified |
| limitations | ✅ | 局限性说明 |

## 安全语义核对

| 安全要求 | 状态 | 说明 |
|---------|------|------|
| score 允许 null | ✅ | `float | None = None` |
| 未校准分数不解释为攻击概率 | ✅ | `score_calibration_status` 默认 `uncalibrated` |
| 证据不足不生成虚假风险 | ✅ | `status` 可为 `insufficient_evidence` |
| 假设必须可证伪 | ✅ | `RiskHypothesis` 含 `statement` + `expected_effect` + `stop_condition` |
| 图谱节点和边真实存在 | ✅ | 仅从已记录的图谱元数据生成 |
| 不泄漏隐藏真值 | ✅ | 无 ground_truth 字段 |
| 不返回敏感原始模型正文 | ✅ | 仅结构元数据 |
| synthetic 数据不标记为真实观测 | ✅ | `evidence_level` 区分 `synthetic` / `observational` |
| candidate 不标记为 causal_validated | ✅ | `status` 仅 `candidate` / `insufficient_evidence` |
| 跨 request_id 不串数据 | ✅ | 每个 risk-analysis 绑定 investigation_id |

## 测试覆盖

- 12 个测试全部通过
- 覆盖：正常通信、风险候选、证据缺失、只有图结构、无消息正文、重复记录、跨请求隔离、隐藏真值注入、错误清洗、API 鉴权

## 结论

E1 风险分析接口已具备完整的安全边界，无需额外修改。
