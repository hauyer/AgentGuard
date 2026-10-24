# QWB 合同差异分析（C0 接口审计）

日期：2026-10-24。基线：AgentGuard `7d42809`（= QWB App `contracts/agentguard-version.json` pin）。
本文档记录：应用层固定/期望的合同与 AgentGuard 当前公共合同之间的全部差异，
以及 C1–C8 新合同的版本化决策。

## 1. 已对齐、无差异的部分

App 固定的 13 个端点（`contracts/agentguard-version.json` supported_endpoints）在当前
OpenAPI（68 paths）中全部存在且语义一致：health、capabilities、kernel/status、scenarios、
investigations（POST/GET/GET {id}）、evidence、defense-policies（GET/preview）、
reports/{run_id}、experiences、system/audit-events。

一致的关键语义（App known_semantics 与 AgentGuard 实现逐条核对通过）：

- `WARDEN_NOT_CONFIGURED` → 503（app.py:582、investigations 路径同源）。
- `X-Request-ID` 逐响应回传，合法调用方值保留（app.py:244-247、333）。
- request_id 幂等 + 异体冲突 409（WardenWorkflowStore.begin / WorkflowConflictError）。
- 提交不确定保持 ambiguous，只读对账，不自动重提（warden_lifecycle.py:103-181；
  App 侧 `_reconcile_after_timeout` 与之配套）。
- 隐藏真值元数据递归拒绝 422（WardenAuditRequest.reject_hidden_truth_metadata）。
- 分页/证据等级/verification_status 枚举与 App 期望一致。

## 2. 差异清单（App 期望 vs 当前实现）

| # | 差异 | App 侧证据 | AgentGuard 现状 | 处理 |
| --- | --- | --- | --- | --- |
| D-1 | 消息级图谱端点不存在 | graph.ts 全套类型；graph_service.py Live 503 | GET /v1/runs/{run_id}/graph 只读内存合成报告；无 request_id 键、不消费 WARDEN 消息元数据 | C1 新增独立端点（§3.1），不改动旧 /v1/runs 路径 |
| D-2 | 实验列表/汇总端点不存在 | reports.ts `Experiment`；reports_service Live 503 | 只有单报告 GET /v1/reports/{run_id} 与内存 GET /v1/runs | C2 新增（§3.2） |
| D-3 | 分层 readiness 不存在 | system.ts `FeatureAvailability`；status_service.py 硬编码 features 可用性 | kernel/status 只有 WARDEN 连接态 | C3 新增（§3.3） |
| D-4 | 调查可选项发现不存在 | investigations.ts `AuditMode`/`CaptureTarget`；表单只能猜 | scenarios 端点与 WARDEN 契约/资源互不关联 | C3 新增（§3.3） |
| D-5 | 调查时间线由 App 推导 | investigations_service._derive_timeline | warden_workflow_events 已持久化，legacy GET /v1/kernel/workflows/{id}/events 存在但字段是内部 journal 视图（from_status/to_status/detail），无 phase/status 语义层 | C4 新增公共时间线（§3.4）；不动 legacy events 端点 |
| D-6 | 证据索引/工件能力字段不存在 | evidence.ts `EvidenceItem` 无 content_access 概念；QWB-INT-003 | GovernedEvidence 有引用与核验态，无工件"可读性"能力声明；无跨调查证据索引 | C5 增量扩展（加法字段） |
| D-7 | 报告索引不存在 | 报告中心需先知道 run_id | GET /v1/reports/{run_id} 单读 | C5 新增 GET /v1/reports（§3.5） |
| D-8 | 防御候选缺审批/验证能力描述字段 | evidence.ts `DefenseCandidate.execution_supported`（App 已按 false 处理） | DefenseCandidate 有 execution_supported/execution_provider，但审批态在 lifecycle（ DefenseLifecycleRecord）中，App 看不到 per-candidate 审批/提交/验证进度的统一投影 | C6 以加法字段补齐（§3.6），不改已发布枚举 |

## 3. 新合同版本化决策（v1.0）

所有新端点响应带 `schema_version: "1.0"`（既有合同用 "0.1"，新公开业务合同升级为
1.0 以示稳定承诺起点；两者并存互不影响）。字段命名沿用 snake_case、错误码沿用
SCREAMING_SNAKE、`available/data_status` 双字段模式与分页四件套。

### 3.1 C1 交互图谱（GET /v1/investigations/{request_id}/interaction-graph）

AgentGuard 合同（权威，App BFF 负责映射到其 TS 类型）：

```text
{schema_version, request_id, available, data_status(complete|partial|unavailable),
 source(warden_observation|agentguard_synthetic_report), run_id, scenario_id,
 nodes[], edges[], candidate_paths[], limitations[]}

node:  {id, type(agent|tool|model|invocation|external_resource), label,
        risk(none|low|medium|high|unknown), timestamp, source_id, metadata}
edge:  {id, source, target, type(message|invocation|result), message_id,
        risk, timestamp, source_id, evidence_refs[], metadata}
path:  {path_id, label, node_ids[], edge_ids[], evidence_refs[],
        risk_level(none|low|medium|high|unknown), rationale,
        verification_status(unverified|verified|unsupported)}
```

与 App `graph.ts` 的映射：`risk: string` ↔ 分级枚举（兼容）；`CandidatePath.id` ↔
`path_id`、`CandidatePath.risk` ↔ `risk_level`（App 适配层改键名，属 App 仓库的
后续工作，本仓库不改字段名迁就）。合成引擎报告作来源时 `source=
agentguard_synthetic_report` 且 limitations 强制带 synthetic 声明，绝不标记为
真实 Live 图谱。

### 3.2 C2 实验索引（GET /v1/experiments、GET /v1/experiments/{experiment_id}、POST /v1/experiments/compare）

条目字段：`experiment_id, run_id, request_id, scenario_id, model_id, runtime_id,
mode, execution_status, data_source(warden_workflow|synthetic_report),
evidence_level, created_at, updated_at, metrics_available`；指标字段
（risk_indicators、benign_utility、model_tokens、execution_cost、time_seconds）
仅真实存在并经验证才出现，否则 `null` + limitations。对外状态枚举：
`created|planned|queued|running|completed|blocked|failed|cancelled|timed_out|unknown`
+ 保留 `raw_status` 追溯。compare 仅比较既有报告，不可比返回 `not_comparable` + reasons。

### 3.3 C3 readiness 与 options（GET /v1/system/readiness、GET /v1/investigations/options）

readiness 每项：`{name, status(available|unavailable|unknown|unsupported|blocked),
last_checked_at, source, blockers[], diagnostics, capability_version}`；覆盖
agentguard_api、warden_connectivity、warden_api_compatibility、contract_source、
available_contracts、model_runtime、probe_resources、sae_resources、
audit_execution、intervention_execution、evidence_access。options 返回真实发现的
审计模式（可提交/仅计划/不支持三态）、威胁契约、Probe/SAE 资源、运行时标识、
CaptureTarget 类型与缺失依赖；带短 TTL 缓存元数据（generated_at/is_stale/source）。

### 3.4 C4 时间线（GET /v1/investigations/{request_id}/timeline）

事件：`{event_id, request_id, event_type, phase, status, occurred_at, source,
related_plan_id, related_run_id, reason_code, message}`；仅由
`warden_workflow_events` 真实持久化事件投影，无事件返回空数组 + `pending`
workflow 状态说明；支持 `after_event_id` 增量拉取。

### 3.5 C5 证据与报告

GovernedEvidence 加法字段（默认值保持向后兼容）：`metadata_available`、
`content_access_supported`、`integrity_verified`、`access_restriction`；
GET /v1/reports 分页索引（items/count/offset/limit/next_offset + status/scenario
筛选）。`integrity_verified` 仅在 AgentGuard 本地内容寻址工件哈希复验通过时为 true。

### 3.6 C6 防御能力描述

DefensePreview 响应加法字段：`approval_required_per_candidate`、
`execution_supported_overall`、`verification_evidence_available`；状态机沿用已发布
枚举（proposed→awaiting_approval→approved→submitted→executed→verification_pending→
verified / rejected / unsupported / failed / rolled_back），不重排不重命名。

## 4. 兼容性结论

全部差异以**新路径 + 旧路径加法字段**方式解决；既有 68 paths 的字段语义零改动。
唯一接近行为变化的是 D-6/D-8 的加法字段（默认值填充，不影响旧消费方）。
无需版本化迁移机制（v1 路径继续承载全部公共接口），App pin 升级只涉及新端点采纳。
