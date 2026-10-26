# I5：WARDEN Live 接入门控审查

## 结论

**Live 状态：BLOCKED。真实 WARDEN 执行：NOT_RUN。** 本审查只读取 AgentGuard 与
WARDEN 源码并运行本地软件/Stub 测试，没有向真实 WARDEN 提交计划或运行，没有真实模型
调用，也没有产生真实新增证据。

本文件对应的 AgentGuard API 实现快照为
`66dae883fb75cf90ef735b94d2f5c9b565ba5ad3`；纯文档提交不改变该实现。核验的 WARDEN
仓库快照为 `077f5405619261d6c4c8f989f8cfc996cc2391a6`。审查范围包括
`warden/interface/api/app.py`、`warden/interface/api/schemas.py`、
`warden/application/service.py`、`warden/application/compiler.py`、
`warden/infrastructure/execution/in_process.py` 和 `warden/core/domain/models.py`。
AgentGuard 的当前 API 实现快照与本文件对应；后续纯文档提交不改变该代码快照。

## WARDEN v1 公共接口

实际 FastAPI 路由定义于 `warden/interface/api/app.py`：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `GET` | `/v1/health` | 返回 `status`、版本和 `api_version` |
| `GET` | `/v1/capabilities` | 返回组件能力报告 |
| `GET` | `/v1/resources` | 返回 Probe/SAE 资源清单 |
| `GET` | `/v1/contracts` | 返回可用契约 ID |
| `POST` | `/v1/audit-plans` | 按 `AuditRequestSchema` 创建计划 |
| `GET` | `/v1/audit-plans` | 列出当前进程内计划 |
| `GET` | `/v1/audit-plans/{plan_id}` | 查询当前进程内单个计划 |
| `POST` | `/v1/runs` | 按 `RunRequestSchema.plan_id` 提交运行 |
| `GET` | `/v1/runs` | 列出当前进程可见运行 |
| `GET` | `/v1/runs/{run_id}` | 查询当前进程可见的运行状态和结果 |

`AuditRequestSchema` 包含 `request_id`、`contract_id`、`model_id`、`runtime_id`、
`mode`、可选 `probe_id`/`sae_id`、`capture_targets` 和 `metadata`。运行提交体仅有
`plan_id`。这两个请求体都没有远端幂等键字段；路由也没有声明幂等请求头。

## 能力核验

| 能力 | 结论 | 源码依据与影响 |
| --- | --- | --- |
| 稳定远端操作 ID | 不满足 | 编译器用 `uuid4()` 生成 `plan_id`；运行调度器生成新的 `run_id`。调用方没有传入可重复使用的操作 ID。`request_id` 仅是计划请求字段，不会使创建幂等。 |
| 远端提交幂等 | 不满足 | `POST /v1/audit-plans` 每次调用都编译并登记一个新计划；`POST /v1/runs` 调用 `start_run()`，没有按调用方键去重。重复提交不能安全地假定返回原操作。 |
| 按操作 ID 查询状态 | 部分满足 | `GET /v1/audit-plans/{plan_id}` 和 `GET /v1/runs/{run_id}` 可查当前 Service/Dispatcher 中的记录；运行状态值为 `planned`、`blocked`、`queued`、`running`、`completed`、`failed`、`cancelled`。 |
| 重启后查询既有任务 | 不满足 | `WardenService` 的 `_plans`/`_local_runs`、`InProcessRunDispatcher` 的 `_runs` 都是内存字典。异步本地调度器文档也明确说明重启会丢失排队和运行状态。当前公共接口没有持久化任务查询合同。 |
| 读取实际结果和证据引用 | 部分满足 | 可见的 `AuditRun` 可包含 `EvidenceBundle`，其中有 artifacts、risk_scores、findings、metrics 和 limitations。公开 API 没有单独下载 artifact 的路由；记录不可见时按 ID 查询返回 404。 |
| 可验证来源和权限 | 不满足 | `ArtifactRef` 提供 URI、content hash 与 metadata，但 API 没有签名/来源证明校验合同。当前 WARDEN FastAPI 路由未配置认证依赖或认证中间件；AgentGuard 无法从该接口验证操作者权限或证据来源真实性。 |

因此，已收到 POST 响应时 AgentGuard 可记录 WARDEN 返回的 ID 并查询当前可见任务；若
请求超时且未收到 ID，或 WARDEN 重启使任务消失，现有公开合同不能证明远端是否已执行，
也不能安全地重新提交。

## AgentGuard 防重与证据隔离

- `create_app` 当前将 Loop observation 设为 `None`。公开 Loop 不会因 GET、readiness
  查询或默认配置而启动 WARDEN 任务。
- `WardenLifecycleService` 先在本地持久日志登记请求指纹与提交状态。已知远端 ID 可
  继续查询；提交结果不确定且没有 ID 时进入 `reconciliation_required`，不会自动重发。
  该日志能防止 AgentGuard 自身盲目重试，但无法恢复 WARDEN 进程丢失的远端记录。
- WARDEN 返回未识别的计划或运行状态时，日志保留 `unknown`，而非写成终态；重复请求
  只会查询已知计划/运行 ID。没有可查询 ID 时仍需人工或外部对账，不会再次提交。
- `EvidenceGateway` 丢弃标记为 `synthetic` 或 `stub` 的来源。可读的真实 WARDEN 工件
 仍须经过本地哈希/审核元数据检查，未验证的引用不会升级为已验证证据。
- Loop 只有读取到实际结果并取得新增证据引用后才记录新增证据。WARDEN 回报
  `completed`、但缺少可查询操作 ID 或结果证据引用时，Loop 返回 BLOCKED，不能报告成功
  完成。

## Readiness 阻塞诊断

`GET /v1/system/readiness` 的 `live_loop_execution` 检查固定为 `blocked`，来源标记为
`agentguard:warden_live_gate`。诊断将缺口分别列为：

- `AGENTGUARD_LOOP_OBSERVATION_DISABLED`
- `WARDEN_REMOTE_SUBMISSION_IDEMPOTENCY_UNSUPPORTED`
- `WARDEN_REMOTE_STATE_NOT_DURABLE`
- `WARDEN_REMOTE_AUTHORIZATION_UNVERIFIED`
- `WARDEN_EVIDENCE_PROVENANCE_UNVERIFIED`
- 另视环境加入 `WARDEN_URL_NOT_CONFIGURED`、连接/健康错误或
  `WARDEN_API_VERSION_INCOMPATIBLE`

诊断字段还会显示 `observation_adapter=disabled`、远端幂等/重启状态为
`unsupported`、授权和证据来源为 `unverified`。WARDEN 健康检查成功不会覆盖这些
阻塞项，也不会使 Live Loop 变绿。

## 验收覆盖与执行状态

下表中的自动化测试是 AgentGuard 本地软件测试；带 HTTP Stub/替身的测试验证合同、失败
处理和证据反馈，不代表真实 WARDEN 通过。所有真实环境场景均为 **NOT_RUN**。

| 场景 | 本地覆盖 | 真实 WARDEN |
| --- | --- | --- |
| 未配置 WARDEN | `test_unconfigured_warden_reports_unavailable_with_blocker`；readiness 保留 Live 阻塞 | NOT_RUN |
| Operator 凭证缺失 | `test_post_requires_operator_or_approval_credential` | NOT_RUN |
| 远端计划/运行提交超时或结果不确定 | `test_uncertain_plan_submission_is_never_retried_automatically`、`test_uncertain_run_submission_is_never_retried_automatically` | NOT_RUN |
| WARDEN 响应缺少运行 ID | `test_malformed_run_response_missing_id_is_rejected`、`test_bad_submission_response_preserves_uncertainty_without_resubmit` | NOT_RUN |
| 计划/运行状态未识别或无远端 ID | `test_journaled_unknown_plan_status_is_reconciled_without_resubmission`、`test_journaled_unknown_run_status_is_reconciled_by_id_without_resubmission`、`test_unknown_journal_without_remote_id_never_resubmits` | NOT_RUN |
| 重启后远端状态未知 | `test_restart_with_unknown_remote_run_never_resubmits` | NOT_RUN |
| 同键重复请求 | `test_idempotent_retries_return_the_same_loop`、`test_g3_idempotent_repeat_does_not_create_second_loop`；覆盖 AgentGuard Loop 登记，不代表 WARDEN 远端幂等 | NOT_RUN |
| 已存在操作状态对账 | `test_workflow_journal_resumes_known_run_without_starting_duplicate`、`test_uncertain_run_submission_reconciles_one_matching_warden_run` | NOT_RUN |
| 多个匹配/未知运行不重复提交 | `test_uncertain_run_submission_stays_ambiguous_with_multiple_matches`、`test_restart_with_unknown_remote_run_never_resubmits` | NOT_RUN |
| Stub 证据不进入真实 WARDEN 证据集合 | `test_synthetic_or_stub_workflow_is_not_emitted_as_warden_evidence`、`test_http_stub_observation_feedback_informs_the_next_decision` | NOT_RUN；Stub 结果不计 Live |
| synthetic 不标记为真实观察或因果验证 | `test_multi_round_read_only_loop_records_adaptive_decisions`、`test_causal_validated_contract_requires_provenance_and_all_method_checks` 与 `I_RESULTS.md` 的来源/真值限制 | NOT_RUN；现有 I6 结果仍标为 synthetic |
| 缺少真实结果时不成功完成 | `test_completed_run_without_evidence_refs_blocks_loop_completion` | NOT_RUN |

## 解除门控所需的 WARDEN 合同

在接通真实 Loop observation 前，WARDEN 应提供并测试：

1. 客户端可指定或可稳定重用的操作幂等键，并定义重复提交的响应语义；
2. 持久化操作 ID，以及跨服务重启查询计划、运行和最终结果的接口；
3. 可校验的认证/授权方案；
4. 可验证的证据来源与 artifact 获取合同（含内容哈希或签名校验方式）；
5. 明确的超时、未知状态和终态协议，并用真实服务集成测试验证对账及不重复执行。

在这些能力进入正式实现/合同并经验证前，不能通过本地假 ID、Stub 成功响应、readiness
条件放宽或手工改写状态启用 Live。当前没有真实环境执行，也没有新增 Recorded
Observational 或 Live Observational 研究结果。
