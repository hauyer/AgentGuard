# AgentGuard 1.1.0-rc1 API 参考

完整路径清单与 Python SDK 示例见 [`../PUBLIC_API.md`](../PUBLIC_API.md)。运行时
OpenAPI JSON 位于 `/openapi.json`，交互文档位于 `/docs` 与 `/redoc`。

## 稳定业务 API

| 资源 | 方法 | 说明 |
| --- | --- | --- |
| `/v1/system/health`、`/v1/system/capabilities`、`/v1/kernel/status` | GET | AgentGuard 服务状态、能力和 WARDEN 配置状态；health 不代表模型可用 |
| `/v1/system/readiness` | GET | 分层 readiness（连接性、API 兼容、契约、模型运行时、Probe/SAE、审计与干预执行、证据访问），每层带独立 blockers 与缓存来源；WARDEN health 不升级为可执行 |
| `/v1/investigations/options` | GET | 调查表单可选项：审计模式（可提交/仅计划/不支持）、威胁契约、Probe/SAE 资源、CaptureTarget 结构与缺失依赖；只报告真实发现的能力 |
| `/v1/investigations`、`/v1/workflows` | GET、POST | 创建经过 WARDEN 合同校验的工作流并分页查询 |
| `/v1/investigations/{request_id}`、`/v1/workflows/{request_id}` | GET | 查询本地持久化状态和安全摘要 |
| `/v1/investigations/{request_id}/timeline` | GET | 工作流日志投影的调查时间线；支持 `after_event_id` 增量拉取；不合成未记录的步骤 |
| `/v1/investigations/{request_id}/interaction-graph` | GET | 消息级交互图谱；仅由 WARDEN run 元数据构造，缺失时 `available=false`（GRAPH_SOURCE_UNAVAILABLE）；显式 `run_id` 渲染合成引擎报告且恒标 synthetic |
| `/v1/investigations/{request_id}/evidence` | GET | 经过 Evidence Gateway 的摘要，不返回原始工件内容；条目带工件能力字段（content_access_supported/integrity_verified/access_restriction） |
| `/v1/defense-policies/{request_id}` | GET | 读取候选策略与能力描述（审批要求、执行/回滚支持、验证与效用数据可得性） |
| `/v1/defense-policies/{request_id}/preview` | POST | 保存只提议、不执行的候选预览 |
| `/v1/defense-policies/{request_id}/execute` | POST | 兼容入口；要求审批凭证，但执行器缺失时如实返回 unsupported |
| `/v1/reports` | GET | 持久化运行报告的分页索引（仅 artifact 摘要 ID，不含路径） |
| `/v1/reports/{run_id}`、`/v1/scenarios` | GET | 读取 AgentGuard 报告（服务重启后从内容寻址工件重建）或公开情景 |
| `/v1/experiments`、`/v1/experiments/{experiment_id}` | GET | 只读实验索引：warden_workflow 与 synthetic_report 双来源、状态/场景/来源筛选；缺失指标为 null 不为零 |
| `/v1/experiments/compare` | POST | 对两份已存在报告做可比性检查后的描述性对比；不可比返回 not_comparable 与原因，不生成防御提升百分比 |
| `/v1/system/audit-events` | GET | Operator-only 的 API 访问审计分页 |

列表接口接受 `limit` 与 `offset`，受接口上限约束。工作流创建用 `request_id` 实现
本地幂等；同一 ID 携带不同请求体返回冲突。远程提交状态不确定时保持 ambiguous
并要求对账，不自动重提。

## 兼容路由与字段

- 旧 `/v1/kernel/*`、`/v1/runs/*` 仍公开，保留既有 SDK 调用方迁移空间；新集成优先
  使用业务路由。两类路径共享相同生命周期服务和 SQLite 日志。
- `/v1/probes/train` 与 `/v1/probes/predict` 保留为 deprecated 兼容端点；它们不是
  WARDEN 代理，也不进入新生产审计流程。
- A8 OpenAPI 回归测试锁定业务路径、核心旧路径、共享请求 schema、分页参数和 Probe
  弃用标志。API 路由拆分位于 `agentguard.api.public_routes`，行为由原 app factory
  提供的同一服务回调承载。
- 1.1.0-rc1 的新增路径全部为加法式（C0 基线 68 paths → 76 paths）；既有路径字段
  语义零改动，`tests/test_qwb_contract_compat.py` 固化了 C0→当前 OpenAPI 的
  加法性回归。

## 错误、身份和真值边界

- `X-Request-ID` 可由调用方提供有效值，否则由服务生成；响应始终包含该 header。
- 配置 `AGENTGUARD_API_TOKEN` 后，除 health、OpenAPI/docs 和 console 等显式公共路由
  外，其他路径需 `X-AgentGuard-Token` 或 operator token。
- 422 校验响应只包含错误类型、字段位置和通用消息，不返回原始输入。服务访问日志
  不保存查询字符串、请求体或凭据；未预期异常日志只保存 request ID 和异常类型。
- 隐藏真值字段（如 oracle/ground-truth/label 类字段）在模型合同中递归拒绝；API、
  SQLite、证据和输出不得因此产生可被查询的真值记录。合成、观察、干预与已验证因果
  证据使用不同成熟度状态。
- SDK 和 MCP 只消费 AgentGuard 公共 API。MCP 不读取数据库、不调用 WARDEN、不启动
  run，也不提供审批和防御执行工具。

安全控制和环境变量见 [`DEPLOYMENT.md`](DEPLOYMENT.md)，已知限制见
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md)。
