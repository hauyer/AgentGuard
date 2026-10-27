# I8: QWB-App API 交接合同

## 固定接口快照

| 项目 | 固定值 |
| --- | --- |
| AgentGuard 分支 | `feature/i-reliability-and-live-validation` |
| API 实现快照 SHA | `9a8ee1f5e78a0b254c17ea66ac5b140aa7e1e89a` |
| 包版本 | `1.1.0rc3` |
| CI | [GitHub Actions `40336215846`](https://github.com/hauyer/AgentGuard/actions/runs/40336215846)，Python 3.11/3.12/3.13 全通过 |
| Python 支持范围 | `>=3.11`；CI 矩阵为 3.11、3.12、3.13 |
| OpenAPI 文件 | [`openapi-1.1.0rc3.json`](openapi-1.1.0rc3.json) |
| rc2→rc3 差异 | [`openapi-1.1.0rc3.diff.md`](openapi-1.1.0rc3.diff.md) |
| OpenAPI 文件 SHA-256（UTF-8、LF 换行） | `fadab4d701d23fabda785413cf0c4536fcffec521ea09cbe6ad73a417c15de1e` |
| 规范化运行时 OpenAPI SHA-256 | `52ad0d5dc175e58759196588d1c4ab5b45e82751f882c82ed8540cb963d942f6` |
| OpenAPI 版本 | `3.1.0` |
| OpenAPI 路径数 | `86` |

API 实现快照 SHA 标识候选实现；文件 SHA 按 UTF-8 编码和 LF 换行计算，以便不同操作系统得到相同结果。发布构建应通过
`AGENTGUARD_BUILD_COMMIT` 注入实际 40 位 commit SHA；本地
开发构建会明确返回 `unknown`。`GET /v1/system/identity` 返回软件/API 版本、提交身份、
能力状态和规范化 OpenAPI SHA。规范化算法为 UTF-8 JSON、键排序、紧凑分隔符、不转义
Unicode，再计算 SHA-256。部署应分别核验快照文件 SHA 与运行时身份返回值。

本版本没有增加路由；I5/I8 快照仍为 86 条路径。公开 Schema 的 `CausalValidationCriteria`
升级至 0.3，加入预注册方案、独立案例家族、sham/无关位置负对照、随机/置换基线、前后输出、
执行日志、干预参数、评价器、统计结果和内容寻址工件引用。`PairedExperimentDesign` 增加
真实执行所需的预注册条件（paired schema 兼容旧 0.1 数据并默认使用 0.2）。GovernedEvidence
增加 provenance 字段。结构完整只使报告通过合同检查，不能替代对真实工件内容、执行来源和独立审查的验证；
具体阻塞见 [`I5_PAIRED_CAUSAL_GATE.md`](I5_PAIRED_CAUSAL_GATE.md)。

## 路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/investigation-loops` | 创建或按幂等键读取已有 Loop |
| `GET` | `/v1/investigation-loops` | Loop 列表 |
| `GET` | `/v1/investigation-loops/{loop_id}` | Loop 摘要 |
| `GET` | `/v1/investigation-loops/{loop_id}/decisions` | 每轮决策 |
| `GET` | `/v1/investigation-loops/{loop_id}/evidence-changes` | 发现新增证据的轮次 |
| `GET` | `/v1/investigation-loops/{loop_id}/budget` | 预算、使用量和剩余额度 |
| `POST` | `/v1/investigation-loops/{loop_id}/reconcile` | Operator 专用查询式对账；只查询已知 WARDEN 操作并尝试恢复本地报告工件 |
| `GET` | `/v1/research/strategy-evaluations` | 策略评价索引 |
| `GET` | `/v1/research/strategy-evaluations/{evaluation_id}` | 策略评价报告工件 |
| `GET` | `/v1/system/identity` | 软件、API、构建和 OpenAPI 合同身份 |

以上 GET 路由只读持久化索引或工件，不启动调查、WARDEN 任务或模型调用。这里不新增
QWB 专用别名；以 OpenAPI 文件为完整路由合同。

`POST .../{loop_id}/reconcile` 需要独立配置的
`X-AgentGuard-Operator-Token`。API token 或审批 token 不能替代。该接口可以查询已登记
WARDEN 操作 ID，或从本地工件存储恢复与 Loop 登记 ID 绑定的有效报告；会更新可由事实
证明的本地状态，但不会 POST 新计划或运行。没有可证明的报告时，Loop 保持
`BLOCKED` / `reconciliation_required`，即使
WARDEN 单独返回 `completed` 也不会伪造整轮成功。

## Loop 创建请求

`POST /v1/investigation-loops` 使用 `application/json`，请求体为
`InvestigationLoopCreateRequest`：

| 字段 | 类型与约束 | 默认值 |
| --- | --- | --- |
| `schema_version` | 常量字符串 `"1.0"` | `"1.0"` |
| `base_request_id` | 非空字符串，最多 256 字符；必须是已完成的基础调查 | 必填 |
| `strategy` | `fixed_order`、`risk_score`、`information_value`、`experience`、`llm` | `information_value` |
| `max_actions` | 整数 1–100 | 8 |
| `max_seconds` | 数值，大于 0，最多 3600 | 300 |
| `max_model_calls` | 整数 0–64 | 4 |
| `no_new_evidence_rounds` | 整数 1–10 | 2 |
| `data_partition` | `training`、`validation`、`test`、`held_out_family` | `held_out_family` |
| `authorization_ref` | 字符串或 `null`，最多 256 字符；参与幂等参数比较 | `null` |

当 `max_seconds > 600` 且 `max_actions > 20` 时，服务端会拒绝超出服务预算包络的
请求。请求体中没有额外属性；未知字段会触发 422 校验错误。

`max_seconds` 是循环的墙钟停止预算：服务会在每个动作开始前检查剩余时间，并将剩余
时间传给 LLM 选择和 WARDEN 轮询。同步 preflight、网络清理和本地处理无法被强制中断，
因此实际耗时可能略超预算；这不是精确的进程级取消保证。
`max_model_calls` 计数已发起的模型请求，包括失败或无效响应；未配置模型或在发出请求前
因预算不足而回退时不计数。

请求头：

| Header | 要求 |
| --- | --- |
| `X-Loop-Idempotency-Key` | 必需；同键同参数返回已有 Loop，同键不同参数返回冲突 |
| `X-AgentGuard-Operator-Token` | Operator 凭证；或使用有效 `X-Approval-Token` |
| `X-Approval-Token` | 可替代 Operator 凭证的审批凭证 |
| `X-AgentGuard-Token` | 配置全局 API 鉴权时用于 API 访问；本身不授予 Loop 创建权限 |

`X-Loop-Idempotency-Key` 在运行时和 OpenAPI 中均为必需。缺失或仅含空白字符时返回
422 `MISSING_IDEMPOTENCY_KEY`。同键、同参数返回已登记 Loop；同键、不同参数返回
409 `LOOP_IDEMPOTENCY_CONFLICT`。所有会影响执行的请求字段（含非空
`authorization_ref`）参与参数指纹；省略或 `null` 的 `authorization_ref` 保持兼容。

`authorization_ref` 只是调用方提供的审计引用，不构成授权凭据。Loop 创建的路由级
权限只接受已配置的 `AGENTGUARD_OPERATOR_API_TOKEN` 对应的
`X-AgentGuard-Operator-Token`，或已配置的 `AGENTGUARD_APPROVAL_TOKEN` 对应的
`X-Approval-Token`。API token 本身不会授予 Loop 创建权限；当前实现没有“未配置独立
Operator token 时回退到 API token 值”的路由级规则。

全局 API 鉴权与路由级 Loop 权限是两层独立检查。若设置
`AGENTGUARD_API_TOKEN`，全局门禁接受有效 `X-AgentGuard-Token`；若另行设置
`AGENTGUARD_OPERATOR_API_TOKEN`，有效 `X-AgentGuard-Operator-Token` 也可通过全局
门禁。只有全局 API token 时，Operator 头不能替代 `X-AgentGuard-Token` 通过全局门禁；
但若配置了全局 API token 且路由凭据是有效审批 token，请求仍需提供有效
`X-AgentGuard-Token`。若未配置全局 API token，则不执行该层 token 检查。部署应按
环境要求配置认证。跨域浏览器请求的 CORS allow-list 包含以上认证头与
`X-Loop-Idempotency-Key`；部署还需显式设置允许的 `AGENTGUARD_CORS_ORIGINS`，不允许
通配符。

## Loop 响应

创建成功和详情使用 `InvestigationLoopView`：

```json
{
  "schema_version": "1.0",
  "loop_id": "loop-…",
  "base_request_id": "…",
  "strategy": "information_value",
  "phase": "COMPLETED",
  "stop_reason": "…",
  "report_artifact_id": "…",
  "created_at": "…",
  "updated_at": "…",
  "rounds_executed": 3,
  "created_audit_request_ids": [],
  "budget": {},
  "metrics": {},
  "limitations": []
}
```

`phase` 和 `stop_reason` 来自 Loop 报告/登记。报告不可用时，摘要仍可返回登记字段，
`rounds_executed` 为 `null`，`budget`/`metrics` 为空对象，并通过 `limitations` 说明
缺少报告或需要对账；登记的 `report_artifact_id` 可能是 `pending` 或 `failed` 哨兵值。
调用方不得将此类空对象解释成零预算消耗或零动作。

其他响应结构：

- `GET .../decisions`：`{schema_version, loop_id, items, count}`。每个 item 含
  `decision_id`、`round_index`、`phase`、`strategy`、`candidate_actions`、
  `selected_action_id`、`selection_reason`、证据前后引用、`new_evidence_count`、
  成本、剩余预算、`result_status`、`new_evidence` 和 `stop_reason`。
- `GET .../evidence-changes`：同一决策 item 结构，但只返回 `new_evidence=true` 的
  轮次，并返回 `limitations`。完整逐轮列表以 decisions 路由为准。
- `GET .../budget`：`{schema_version, loop_id, budget, metrics, remaining}`。若没有
  可用报告，`remaining.actions`、`remaining.model_calls` 和 `remaining.seconds`
  为 `null`，表示未知；有报告时秒数剩余额度仍为 `null`，因为该值只在执行中有意义。
- 所有模型定义详见固定 OpenAPI 文件的 `InvestigationLoop*` 与 `LoopDecisionView`
  components，以及 `src/agentguard/contracts/loop_public.py`。

## 策略评价响应

列表请求支持可选 `data_partition`、`limit`、`offset`。响应为：

```json
{
  "schema_version": "1.0",
  "items": [{
    "evaluation_id": "…",
    "comparison_id": "…",
    "report_artifact_id": "…",
    "data_partition": "held_out_family",
    "source_commit": "…",
    "executed_at": "…",
    "status": "available"
  }],
  "count": 1,
  "offset": 0,
  "limit": 50
}
```

详情返回报告工件 `StrategyComparisonReport`，包含 `comparison_id`、基础调查 ID、
策略、重复数、分区、逐次 `runs`、`aggregates`、`source_commit`、执行时间、限制、
`report_artifact_id`，以及保持 `null` 的风险识别和防御效果指标。详情的
`report_artifact_id` 来自索引并与工件索引值一致。完整字段以固定 OpenAPI 文件的
`StrategyComparisonReport`、`StrategyRunRecord` 与 `StrategyAggregate` schemas 为准。

## 分页、空值与错误

- Loop 列表默认 `limit=50`、`offset=0`，`limit` 范围 1–500；支持
  `base_request_id`。按创建时间倒序。响应的 `count` 是当前页数量，不是总数；当页
  满时 `next_offset` 为下页偏移，否则为 `null`。
- 评价列表默认 `limit=50`、`offset=0`，范围相同；支持 `data_partition`，按
  `executed_at` 倒序。`count` 是当前页数量，无总记录数字段或 `next_offset` 字段。
  客户端可以在本页 `count < limit` 时停止翻页。
- Decisions/evidence-changes 当前不分页，每次最多 100 条；超过上限时以完整报告工件
  和 OpenAPI 合同为准，客户端不要自行推断截断项。
- JSON `null` 表示未知/不可用，不等于 0；空数组表示没有可返回的条目；空对象表示
  对应详细数据不可用或尚未生成。不能把未知预算、指标或策略结果转换成零。

| HTTP | `detail.error` | 含义 |
| ---: | --- | --- |
| 401 | `API_TOKEN_REQUIRED` | 已启用全局 token 鉴权但凭证缺失/无效 |
| 403 | `LOOP_AUTHORIZATION_REQUIRED` | 创建 Loop 缺少 Operator/审批凭证 |
| 400 | `LOOP_REGISTRATION_LOST` | Loop 登记在执行前不可读取；请求未启动调查 |
| 404 | `BASE_INVESTIGATION_NOT_FOUND`、`LOOP_NOT_FOUND`、`EVALUATION_NOT_FOUND` | 对应记录不存在 |
| 409 | `BASE_INVESTIGATION_NOT_FINISHED`、`LOOP_IDEMPOTENCY_CONFLICT` | 基础调查未完成或幂等键参数冲突 |
| 410 | `EVALUATION_ARTIFACT_UNAVAILABLE` | 评价索引存在，但报告工件缺失或无效 |
| 422 | `MISSING_IDEMPOTENCY_KEY`、`HTTPValidationError` | 缺幂等头或请求参数/请求体校验失败 |
| 429 | `RATE_LIMIT_EXCEEDED` | 超出配置的每分钟请求数；可能带 `Retry-After` |
| 502 | `LOOP_EXECUTION_FAILED` | Loop 执行失败 |
| 503 | `API_AUDIT_UNAVAILABLE` | API 审计事件无法持久化；请求失败关闭 |
| 500 | `INTERNAL_SERVER_ERROR` | 未预期服务错误 |

错误响应通常形如 `{"detail":{"error":"…","request_id":"…"}}`；FastAPI 参数校验
错误为 `detail` 数组。客户端应按 HTTP 状态和 `detail.error` 处理，不解析错误消息文本。

## 真实模型与 WARDEN 状态

- Evidence Gateway 的公开证据视图保留 `source_type`、`production_phase`、run/experiment ID、
  model ID/revision、data version、工件引用和 `artifact_hashes`。缺失信息显式返回 `unknown`；
  `artifact_hashes` 只包含本地内容寻址存储中已验证字节的哈希。`source_type=warden_api`
  表示数据经 WARDEN API 到达，不代表 source authenticity 已验证。
- Synthetic/stub 证据不会被标作真实 WARDEN 观察；上游因果自称仍降级。证据完整性、来源真实性、
  执行状态和方法审查状态分开呈现，缺少真实结果或报告工件不能补写为成功。
- 配对干预需预注册、可信方案与输出工件验证器，并在完成报告中保留五臂记录；详情见
  [`I5_PAIRED_CAUSAL_GATE.md`](I5_PAIRED_CAUSAL_GATE.md)。

- AgentGuard 内置研究后端为 synthetic。真实审计通过 WARDEN 公共接口，不直接注入
  AgentGuard 底层模型运行时。
- 默认 Loop observation 仍为关闭状态。WARDEN 的计划/运行状态缺少可证明的远端重启
  恢复与提交幂等语义；真实 Live Loop 保持 **BLOCKED**，未知 POST 结果不能盲目重试。
- 本轮 I5 完成公开 API/Schema 合同核对、read-only 对账保护、Probe 配置合同和配对因果门控；
  HTTP Stub 仅作为合同测试。没有真实 WARDEN Loop、真实模型观察或本轮产生的真实新增证据。
  Stub 引用不会作为真实证据展示。
- WARDEN 的远端提交幂等、重启后持久状态、可验证授权与证据来源能力尚不能从当前公开
  API 合同中确认；Live 保持 BLOCKED。逐项审查和验收状态见
  [`I5_WARDEN_LIVE_GATE.md`](I5_WARDEN_LIVE_GATE.md)。
- 没有独立真值时不提供检测准确率；没有真实干预和独立评价时不提供防御成功率。

## 已知限制

- Loop 崩溃后若状态为 `running` 或旧状态为 `unknown`，API 会返回
  `BLOCKED / reconciliation_required`；不自动继续执行。若报告工件已写入但索引更新
  失败，需人工对账。
- 未知预算以 `null` 返回。Loop 列表与评价列表的 `count` 都表示页长度，不表示全量
  总数。
- 评价详情依赖本地内容寻址报告工件。索引有记录而工件缺失时返回 410；调用方应报告
  工件不可用，不得静默丢弃来源或自行重建字段。
- CORS 需显式配置允许域名。使用的 headers、token 环境变量和幂等语义见本页。
- I6 synthetic control 已登记 comparison `eval-2e181b7fb8`、evaluation
  `eval-eval-2e181b7fb8` 和可核验报告工件，详见
  [`I_RESULTS.md`](../research/I_RESULTS.md)。该组结果只验证合成输入上的软件行为；没有
  真实 WARDEN 执行、真实模型调用、真实新增证据或独立真值，不得解释为真实实验结果。
