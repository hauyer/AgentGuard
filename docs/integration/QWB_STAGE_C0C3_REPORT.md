# C0–C3 阶段验收报告（Application Integration 前三阶段）

日期：2026-10-24。分支 `feature/qwb-public-api-integration`。
基线 `7d42809`（1.0.0rc1）→ 本报告时点 `0e02547` + 本报告提交。

## 1. 完成的实际功能

| 阶段 | 功能 | 提交 |
| --- | --- | --- |
| C0 | QWB App 接口审计：环境/版本/基线核对、OpenAPI 基线导出（68 paths）、覆盖矩阵、合同差异、阻塞映射、开发计划 | `dbb8be6` |
| C1 | `GET /v1/investigations/{request_id}/interaction-graph`：消息级交互图谱。真实来源 = 工作流日志中 WARDEN run 载荷的 `findings[].messages`（sender/recipient/message_id/round_index，与 integration-data 真实 7B 观察运行结构一致）；合成来源 = 本地引擎 Observation 事件，恒标 `agentguard_synthetic_report`。无合规元数据 → `available=false` + `GRAPH_SOURCE_UNAVAILABLE`，绝不编造边 | `c5028ac` |
| C2 | `GET /v1/experiments`、`GET /v1/experiments/{experiment_id}`、`POST /v1/experiments/compare`：只读实验索引（warden_workflow + synthetic_report 双来源、分页/筛选/排序、指标缺失为 null 不为零、无分组如实 experiment_id=null）与可比性检查（场景/模型版本/数据源等级/共享指标，不可比返回 not_comparable + 原因，仅描述性对比，不生成"防御提升百分比"） | `907aa19` |
| C3 | `GET /v1/system/readiness`（11 层检查，每层独立 status/source/blockers/diagnostics/capability_version，短 TTL 缓存带 provenance）与 `GET /v1/investigations/options`（5 种审计模式的 plan/run/submittable 三段能力、威胁契约、Probe/SAE 资源、CaptureTarget 结构、缺失依赖）。WARDEN health 绝不推断为模型运行时/审计/干预可执行；intervention_execution 无显式声明一律 unsupported（DEP-007） | `0e02547` |

## 2. 新增/修改文件

新增：`contracts/interaction_graph.py`、`contracts/experiment_index.py`、
`contracts/system_readiness.py`、`services/interaction_graph.py`、
`services/experiment_index.py`、`services/system_readiness.py`、
`tests/test_interaction_graph.py`（17）、`tests/test_experiment_index.py`（12）、
`tests/test_system_readiness.py`（11）、`tests/test_live_agentguard_http.py`（1，
uvicorn 子进程真实 HTTP 集成）、`docs/integration/`（4 份审计文档 + OpenAPI 基线）。
修改：`api/app.py`（状态与处理器接线）、`api/public_routes.py`（4 个新业务路由）、
`services/evidence_gateway.py`（+`reference_is_safe` 公共安全引用规则）。
注意 `/v1/investigations/options` 注册在 `{request_id}` 之前以避免路径参数吞没。

## 3. 对外接口变化

加法式新增 4 个业务路径（OpenAPI 68 → 72 paths），既有 68 路径零改动；
新合同 `schema_version="1.0"`；全部沿用 items/count/offset/limit/next_offset 分页、
`{"detail":{"error":CODE}}` 错误体、X-Request-ID、三 token 鉴权与审计中间件
（新路径自动进入 api_audit_events）。

## 4. 真实执行的测试及结果

- pytest 全量：**298 通过 / 0 失败 / 2 跳过**（torch whitebench、live WARDEN HTTP，
  跳过原因与 A8 相同），64.4s。
- Ruff 0.17.2 全绿；`compileall` 通过；`audit_architecture.py --check` 无越界无环。
- 真实服务集成：uvicorn 子进程 + 原生 HTTP 客户端串行验证
  readiness→options→experiments→compare→POST /v1/runs→索引→interaction-graph
  （含 404/不可用分支与 X-Request-ID 回传）。
- 未运行：真实 WARDEN 三类验证（`AGENTGUARD_WARDEN_URL` 未配置，维持 A8 阻塞状态，
  不冒充通过）。

## 5. 新增安全防护

- 图谱/实验合同元数据经 truth_guard 递归拒绝隐藏真值字段（测试覆盖）。
- 图谱 evidence_refs 与 run artifact 引用复用 EvidenceGateway 安全规则：
  file://、绝对路径、带凭证 URL 一律丢弃且不打开（真实 7B 载荷中的
  file:///D:/... URI 在测试中被验证为"仅保留 artifact_id、URI 不出现"）。
- 合成数据隔离：合成图谱/实验条目必带 synthetic 标记与 limitation，不冒充真实。
- compare 拒绝未记录模型版本的对照（MODEL_VERSION_NOT_RECORDED），描述性对比
  明确声明不能支持因果防御结论。
- readiness 不提升 health 语义；options 不发布未证实运行时标识（空列表 + 说明）。

## 6. 仍然存在的底层阻塞

QWB-INT-002（防御执行器，DEP-007）、QWB-INT-003（工件下载，DEP-004）、
QWB-INT-004（远端取消/恢复，DEP-002/003）、QWB-INT-005 的底层部分（Probe/SAE/白盒
REST，DEP-001/005）、QWB-INT-006 的真实验证数据（DEP-006）。上层已按诚实语义
（unsupported/blocked/unknown/null）处理，详见 QWB_DEPENDENCY_BLOCKERS.md。

## 7. 与 QWB App 的兼容性

既有 13 个被固定端点零变化（App pin `7d42809` 无需变更即可继续运行）。
新端点可供 App BFF 增量接入：graph.ts/experiments 需要的字段按
QWB_CONTRACT_DIFF.md §3 映射（`path_id`↔`id` 等键名差异由 App 适配层处理）；
readiness/options 直接回答 status_service.py 硬编码的 features 判定来源。

## 8. Git 提交

`dbb8be6`（C0）→ `c5028ac`（C1）→ `907aa19`（C2）→ `0e02547`（C3）→ 本报告提交。
未推送远程、未合并 main（未获授权）。

## 9. 下一阶段（C4–C8）前置条件

无阻塞：C4 时间线的数据源（warden_workflow_events）已验证可投影；
C5/C6 在既有 EvidenceGateway 与防御状态机上做加法；C7 SDK 方法依赖的路由已全部
就绪。按计划继续。
