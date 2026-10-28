# AG-5：真实 WARDEN Live 门

日期：2026-10-28。结论：**BLOCKED / NOT_RUN。没有把 Stub 或 synthetic 结果记作 Live 通过。**

AgentGuard 实现快照 `9d1c1dc9331f4ce168aab29b860c94b9d65a8321` 的 GitHub Actions
[run 40665395993](https://github.com/hauyer/AgentGuard/actions/runs/40665395993) 已在
Python 3.11、3.12、3.13 全部通过。这是软件与合同 CI 结果，不是 WARDEN Live 验证，
不解除本 Gate 的任何远端能力阻塞。

| Live 验收 | 状态 | 原因 |
|---|---|---|
| WARDEN URL / 在线 OpenAPI | `NOT_RUN` | `AGENTGUARD_WARDEN_URL` 与 `AGENTGUARD_LIVE_WARDEN_URL` 未配置 |
| Operator / 远端授权 | `NOT_RUN` | 无真实部署与授权环境；固定源码路由无已核实认证合同 |
| 计划和运行稳定幂等键 | `BLOCKED` | WARDEN 0.3.0 两个 POST 无客户端幂等键 |
| 远端提交超时后查询已有操作 | `BLOCKED` | 无可重用操作 ID 合同；AgentGuard 不重发未知 POST |
| 服务重启后查询既有任务 | `BLOCKED` | 计划/运行存于 WARDEN 进程内存 |
| 四类 Probe/干预真实执行 | `BLOCKED / NOT_RUN` | 没有白盒任务 REST、message/activation/residual patch REST；CLI 不作为绕行接口 |
| 实际输出与 evidence refs | `NOT_RUN` | 未执行真实模型运行 |
| 工件内容下载/哈希重算/来源签名 | `BLOCKED` | WARDEN 公共 API 没有受控工件读取和来源证明合同 |
| baseline + treatment 真实配对复算 | `NOT_RUN` | 没有真实运行、可信分区数据和独立真值 |
| Live Loop 完成状态 | `BLOCKED` | 缺远端幂等、持久状态、真实结果和可验证证据；即使上游声明 completed 也不足以完成 AgentGuard Loop |

本地已经通过的故障注入证明：未配置服务不会提交；提交结果未知时不盲目重发；有已知计划/运行 ID 时只读对账；重启后找不到远端状态会保留 unknown/reconciliation_required；stub evidence 与 synthetic evidence 不会进入真实 WARDEN 证据或 causal 状态。对应测试仍是本地合同测试。

解除 AG-5 需要 WARDEN 版本化公开合同及真实环境证据：远端幂等/稳定 ID、跨重启查询、真实权限、结果与工件字节读取/验证、完整 baseline+treatment 原始记录。届时应通过现有 `WardenLifecycleService`、`WardenWorkflowStore` 和 `EvidenceGateway` 接入，并先通过 AG-1 到 AG-4；不得在 AgentGuard 内重写 WARDEN/StegoPot。
