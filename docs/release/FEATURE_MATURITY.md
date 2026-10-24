# 1.1.0-rc1 功能成熟度矩阵

成熟度标签按证据来源区分：`implemented` 表示代码存在；`unit_tested` 表示单元/合同
测试通过；`integration_tested` 表示 AgentGuard 内部组件或 API 端到端测试通过；
`real_backend_validated` 和 `real_model_validated` 仅在真实服务/模型运行成功并可复核
时使用；`blocked` 表示缺少真实外部依赖或必要条件。测试替身不会填充真实验证列。

| 功能 | implemented | unit_tested | integration_tested | real_backend_validated | real_model_validated | 状态说明 |
| --- | --- | --- | --- | --- | --- | --- |
| AgentGuard REST API、旧路径兼容与 OpenAPI 合同 | 是 | 是 | 是（TestClient + C0 基线加法性回归） | 不适用 | 不适用 | 业务 alias 与 legacy route 共享服务/生命周期；Probe route deprecated；C0 68 paths → 76 paths 纯加法 |
| Python SDK | 是 | 是 | 是（假 HTTP transport 与 API 合同，含 C1–C6 新方法） | 否 | 不适用 | 未对部署中的服务做现场互通验收 |
| MCP API facade | 是 | 是 | 是（假 MCP + 公共 SDK 边界） | 否 | 不适用 | 不持有运行时、数据库或 WARDEN 客户端 |
| 消息级交互图谱 API（C1） | 是 | 是 | 是（TestClient + uvicorn 真实 HTTP） | 部分（真实 7B 观察载荷结构验证；未在 live WARDEN 会话回放） | 否 | 真实边仅来自 WARDEN run 元数据；合成报告恒标 synthetic；无来源返回 unavailable |
| 只读实验索引与对比（C2） | 是 | 是 | 是（重启恢复、旧日志行、筛选分页） | 否 | 否 | 只聚合既有持久化记录；缺指标为 null；compare 仅描述性 |
| 分层 readiness 与调查可选项（C3） | 是 | 是 | 是（伪内核 11 层检查 + 缓存来源） | 否 | 不适用 | health 不升级为可执行；intervention 无显式声明即 unsupported |
| 调查时间线（C4） | 是 | 是 | 是（journal 投影、增量拉取、重启） | 不适用 | 不适用 | 仅投影真实事件，不合成步骤 |
| 证据工件能力字段与报告索引（C5） | 是 | 是 | 是（本地核验/外部引用、重启重建报告） | 否 | 不适用 | integrity_verified 仅对本地哈希复验工件为 true；外部工件仍引用-only |
| 防御能力描述字段（C6） | 是 | 是 | 是（审批后仍 unsupported、verified 候选标志） | **blocked** | **blocked** | 执行器/回滚 API 缺失（DEP-007/003）；审批不改变执行支持 |
| WARDEN 唯一生产审计边界 | 是 | 是 | 是（请求/状态/失败合同） | **blocked** | **blocked** | 当前未配置 WARDEN HTTP 服务；真实烟测不可执行 |
| SQLite 工作流幂等、审计、恢复 | 是 | 是 | 是（本地 SQLite 并发/重放测试） | 不适用 | 不适用 | 不承诺跨主机 exactly-once；未知远端提交不会盲目重放 |
| SQLite 经验库迁移与隔离 | 是 | 是 | 是（本地数据库迁移测试） | 不适用 | 不适用 | 旧记录进入 quarantine，自动推荐需显式验证门控 |
| 工件与证据治理 | 是 | 是 | 是（本地/模拟响应） | 否 | 不适用 | 本地路径被拒绝；真实 WARDEN 工件下载/完整性接口仍 blocked |
| 合成 Demo 与研究工作流 | 是 | 是 | 是 | 不适用 | 不适用 | 必须保留 synthetic 标记，不代表真实模型效果 |
| 配对统计、研究注册与分析 | 是 | 是 | 是（固定测试数据） | 不适用 | 不适用 | 统计显著性本身不构成因果有效性证明 |
| 防御候选、审批与效果评估合同 | 是 | 是 | 是（状态机/模拟数据） | **blocked** | **blocked** | 真实执行器与经验证的成对 WARDEN 重放未提供；审批后也可返回 unsupported |
| 可选白盒/Activation 兼容代码 | 是 | 是（nanogpt：5 passed） | 否 | **blocked** | **blocked** | 仅旧兼容/离线工具，不属于 AgentGuard 生产审计内核 |
| LLM 调查建议器 | 是 | 是（schema、fallback、transport） | 否 | 否 | **blocked** | 未验证真实本地 3B/7B 模型与端到端安全行为 |
| API 鉴权、审计、CORS、进程内限流 | 是 | 是 | 是（请求合同 + 新路径自动纳入审计） | 否 | 不适用 | 真实代理/集群拓扑未做部署验收；详见部署限制 |
| Python 3.11/3.12/3.13 兼容 | 是 | 是 | 是（GitHub Actions run 40074638267，1.0.0-rc1 轮） | 不适用 | 不适用 | 1.1.0-rc1 的三版本矩阵在候选推送后按流程执行 |

历史 7B 观察记录包含 6/8 条件完成、2 条失败，汇总状态为 failed。它既不是
`real_model_validated` 的成功证据，也不是已验证因果防御结论；细节见
[`migration/OBSERVATION_RUN.md`](../migration/OBSERVATION_RUN.md)。
