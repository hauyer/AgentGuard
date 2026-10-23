# AgentGuard ↔ WARDEN 0.2.0 接口核查

核查源：WARDEN commit `3116af8eb3a7fc3cc9d14ecfa4e5b469f2356e4a` 的 `warden/interface/api/`、`sdk.py`、`application/`、`core/domain/` 和默认 FastAPI 进程内烟测。未启动独立 HTTP 服务，也未执行真实模型。

| WARDEN `/v1` 接口 | 源码与实际返回 | AgentGuard 当前实现 | 判定 |
| --- | --- | --- | --- |
| `GET /health` | `interface/api/app.py:30-34` 返回 `status=ok, version=0.2.0, api_version=v1` | `adapters/warden.py:103-104,200-231` 只检查 `status`；生命周期 `services/warden_lifecycle.py:294-303` 未核 `api_version` | 形状兼容，缺版本门槛 |
| `GET /capabilities` | `api/app.py:36-40` 返回 `CapabilityReport[]`，字段 `component_id, available, capabilities, reason, metadata`（`core/domain/models.py:36-59`） | 客户端只验证对象列表；预检保存列表但不据此检查请求需要的运行能力 | 需运行能力诊断 |
| `GET /resources?kind=probe|sae` | `api/app.py:42-46` 返回 `ResourceManifest[]`，含 `resource_id, kind, version, state, artifact` 等（`models.py:216-243`） | 预检核指定 ID 和 `state=ready`（`warden_lifecycle.py:307-327`） | 基本兼容；需验证实际资源清单 |
| `GET /contracts` | `api/app.py:48-52` 返回字符串 ID 数组 | 预检核 `contract_id` 在列表中 | 兼容；默认服务列表为空 |
| `POST /audit-plans`、`GET /audit-plans/{id}` | `api/app.py:54-77` 使用严格 `AuditRequestSchema`，返回 `AuditPlan`：`plan_id, request, threat_contract, required_capabilities, stages, status, blockers, created_at` | `contracts/warden.py:26-56` 的 `to_warden_payload()` 剥离上层审批、标签、轮询字段；客户端和生命周期按 `status/plan_id/blockers` 处理 | 请求主体基本匹配；需真实合同与响应契约测试 |
| `POST /runs`、`GET /runs/{id}` | `api/app.py:79-103` 的请求仅 `{plan_id}`；返回 `AuditRun`：`run_id, plan_id, status, blockers, error, evidence, created_at, updated_at, metadata`（`models.py:334-363`） | 客户端提交/查询路径正确；生命周期只浅层校验 ID/status，未完整解析证据 | 关键证据映射缺口 |

`AuditRequestSchema` 的 9 个字段是 `request_id, contract_id, model_id, runtime_id, mode, probe_id, sae_id, capture_targets, metadata`（`warden/interface/api/schemas.py:32-60`）。AgentGuard 的 `authorization_ref, scenario_id, risk_type, start_run, wait_for_completion, timeout_seconds, poll_interval_seconds` 仅供本地使用，`to_warden_payload()` 已排除它们。`CaptureTarget` 字段基本一致；AgentGuard 对 `token_policy` 取值更严格。没有发现需要向 WARDEN 提交这些上层字段的理由。

## 实际差异与风险

1. **证据引用结构不匹配。** WARDEN 的真实 `AuditRun.evidence` 是 `EvidenceBundle`；工件位于 `evidence.artifacts[]`，每项是 `ArtifactRef`（`models.py:62-80,307-363`）。AgentGuard `_evidence_refs()`（`services/warden_lifecycle.py:403-431`）识别顶层 `evidence_refs` 或 `evidence.artifact_refs`，却不遍历 `evidence.artifacts[]`。`tests/test_warden_lifecycle.py:22-23,235-241` 使用合成的顶层 `evidence_refs`。实际完成运行即使带工件，现有代码仍可能给出 `not_assessed` 且不入经验库；使用 WARDEN 形状的最小响应核查也得到空引用列表。需按 WARDEN 的 `artifact_id/uri/content_hash` 公共结构映射，并增合同测试。
2. **状态词表不同。** WARDEN `core/domain/enums.py:13-21` 只有 `planned, blocked, running, completed, failed, cancelled`。AgentGuard 还处理 `queued, unknown, timed_out`；后两者可作为上层归一化状态，但不能宣称是 WARDEN 原始状态。未知状态必须失败关闭。
3. **默认服务无法执行审计。** `interface/factory.py:16-61` 标记 `agent.runtime`、`activation.backend` 不可用，未注入 `RunDispatcher`；`application/compiler.py:24-96` 会阻止缺能力/Probe/采集目标的计划；`application/service.py:98-110` 在无 dispatcher 时返回 `blocked`。`PipelineAuditExecutor`（`application/executor.py:207-291`）是可注册阶段框架，不证明默认部署已有真实阶段。
4. **计划和运行快照只在进程内。** `application/service.py:42-49,83-125` 保存计划与本地阻塞任务于内存，默认服务没有持久化调度器。AgentGuard 的 SQLite 幂等只保护上层；WARDEN 重启后旧 plan/run ID 可能不可查询，必须在阶段 1 的重启/对账测试中验证。
5. **版本诊断不足。** WARDEN health 提供 `api_version=v1`，AgentGuard 当前不核对；AgentGuard 自身 `/health` 还返回 0.8.0（`api/app.py:249`）。
6. **错误结构。** WARDEN 对缺威胁源/无效计划使用 HTTP 400 与字符串 `detail`（`api/app.py:54-87`）；AgentGuard 能归一化 HTTP 错误，但应在真实服务测试中记录机器可诊断的错误码和关联请求 ID。
7. **计划响应含隐藏真值。** `ThreatContract.truth` 是 WARDEN 领域字段（`core/domain/models.py:115-143`）；FRACTAL-Bench 适配器会填入目标和反事实信息（`infrastructure/adapters/fractal_bench.py:69-93`）。WARDEN `POST /v1/audit-plans`/`GET /v1/audit-plans/{id}` 使用 `to_primitive(plan)` 原样序列化（`interface/api/app.py:54-77`）；AgentGuard `api/app.py:310-351,366-395` 又返回完整 plan，并将其写入工作流上下文。用只含测试哨兵真值的自定义合同进行进程内请求，HTTP 200 的 blocked 计划仍包含该 `truth` 值。这证明普通响应存在泄漏通道，**并不证明被测 Agent 已经读取**。阶段 1 需在 WARDEN 公共响应或 AgentGuard 边界定义明确的真值剔除/授权视图，并测试调查工具、LLM、日志与工作流查询不可见隐藏真值。

## 现场烟测

WARDEN `create_app()` 默认服务在进程内返回：health 200 `{status: ok, version: 0.2.0, api_version: v1}`；capabilities 200 且四个组件均不可用；resources 200 `[]`；contracts 200 `[]`；以不存在合同调用 `POST /v1/audit-plans` 得到 400（未配置 ThreatContractSource）。这仅核实真实 API 代码和默认配置行为，不是 AgentGuard→远端服务的端到端联调。
