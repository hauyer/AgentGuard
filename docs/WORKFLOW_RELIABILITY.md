# AgentGuard 0.9.0：WARDEN 工作流可靠性与恢复

## 当前上层 A2（2026-10-24）

生产 API 的 `/v1/kernel/audits` 与 `/v1/kernel/workflows`、CLI 的 submit/run
现在共用 `WardenLifecycleService` 和同一个 SQLite 日志。兼容提交入口新增
预检、幂等和当前审批校验，不再绕过工作流直接提交。

同一 SQLite 路径和 request_id 使用本地操作系统文件锁协调处理；未取得锁
的并发请求返回 `in_progress` 与已有业务状态，不再次提交。进程异常退出由
操作系统释放锁，远端引用仍在 SQLite 中。锁文件只存一个无敏感信息的字节，
不能在服务运行时删除。此保证针对同机本地文件系统，不支持多主机网络盘
部署；需要跨主机队列时必须另外评估部署方案。

旧数据库 `status` 保持兼容，新的 `workflow_status` 将其映射到统一业务
状态；例如日志 `finished` + 计划结果 `planned` 仍显示 planned，不会显示
模型 completed。状态映射及合法迁移见 `services/workflow_state.py`。终态
不能倒退或改写结果；本地 `timed_out` 是跟踪期限，不是远端已终止，仍能恢复。

每次实际处理、提交和恢复写入追加事件，`statistics` 从同一事件表计算重试与
恢复计数。旧版本没有记录处理次数的历史通过 `legacy_attempt_history` 标记，
不能把缺失的历史当成零成本。轮询保存每次实际观察到的远端状态变化。

任务默认不等待模型完成，以 WARDEN run ID 跟踪；需要等待时使用有界轮询。
未新增不可恢复的模型后台线程或第二套任务数据库。相同请求再次提交可恢复
已知外部运行；GET 工作流详情只读取本地最近快照，不自动联系 WARDEN。

新增取消入口只允许取消未提交的本地 created/preflighting 工作。已有计划、
运行或并发处理时不会改成 cancelled，外部取消返回 unsupported。没有向
WARDEN 猜测或新增取消路由。

验证：195 测试通过、2 跳过；真实 WARDEN blocked 工作流重放 1 通过。
详细边界与兼容变化见 `migration/UPPER_LAYER_A2.md`。

## 本版本解决的问题

0.8.0 已具备 WARDEN 生命周期调用，但进程在远端计划/运行提交期间退出时，单靠内存状态无法区分“请求没有到达内核”和“内核已接收但响应丢失”。如果客户端直接重试，可能创建重复任务。

0.9.0 增加 SQLite 工作流日志 `warden_workflows.sqlite3`，将上层请求、规范化请求哈希、状态、计划/运行快照、最终响应和追加式状态事件持久化。

## 幂等行为

- `request_id` 是 AgentGuard 层幂等键。
- 同一 `request_id` 和同一规范化请求会返回已有结果，不再次提交 WARDEN 请求。
- 同一 `request_id` 携带不同请求体会返回 HTTP 409 `IDEMPOTENCY_KEY_CONFLICT`。
- 已知 `run_id` 的任务可以通过 `GET /v1/runs/{run_id}` 重新对账；需要等待的请求会继续进行有界轮询。
- 已知 `plan_id` 且还没有提交运行的任务可以从该计划继续，不重新创建计划。
- 在 `plan_submitting` 或 `run_submitting` 阶段发生传输异常时，系统记录 `submission_uncertain`，不会自动重复提交。已知计划的运行提交可按下述唯一匹配规则向 WARDEN 查询；其余情况需操作员核对。

该设计提供的是**防止 AgentGuard 自己盲目重复提交**，不是对 WARDEN 的远端 exactly-once 保证。若 WARDEN 本身不支持幂等键，网络中断后无法从客户端单方面证明远端操作只执行了一次。

## 查询工作流

```bash
agentguard kernel workflow-status --request-id req-2026-001
```

REST 接口：

- `GET /v1/kernel/workflows`：按状态与条数列出工作流摘要，便于运维对账。
- `GET /v1/kernel/workflows/{request_id}`：读取工作流当前状态、版本号、计划/运行 ID、结果和最近错误。
- `GET /v1/kernel/workflows/{request_id}/events`：读取追加式状态转换记录。

这些接口只读取 AgentGuard 的本地 SQLite 日志，不会在后台自动重放审计任务。

## 状态解释

| 状态 | 说明 | 自动重试策略 |
|---|---|---|
| `created` / `preflighting` | 尚处于预检阶段 | 可以安全重新开始预检 |
| `plan_created` | 计划 ID 已持久化，运行尚未提交 | 可以使用现有计划继续 |
| `run_submitted` / `polling` / `timed_out` | 运行 ID 已知 | 通过 WARDEN 查询同一运行并恢复等待 |
| `submission_uncertain` | 请求可能已被 WARDEN 接收，但本地没有可靠的远端 ID/响应 | 禁止自动重复提交，需人工或外部查询对账 |
| `finished` | AgentGuard 已保存可返回的工作流响应 | 相同请求返回保存的结果 |
| `interrupted` | 预检或提交前阶段出现异常 | 仅当日志表明未进入外部提交阶段时才重新开始 |

## 存储和隐私

工作流日志默认存放在 `AGENTGUARD_DATA_DIR/warden_workflows.sqlite3`。它保存请求合同、状态快照、响应快照和状态事件，不应该保存 API 密钥、完整模型提示词或原始激活张量。生产部署应限制该目录访问权限并纳入备份策略。

## 验证边界

本版本的持久化、幂等、恢复分支和 API 行为通过本地测试替身验证。它没有证明实际 WARDEN 部署一定支持所有状态名称，也没有验证私有 FRACTAL-Bench、StegoPot 模型执行或真实模型的因果干预。部署前仍需针对实际 WARDEN commit 执行 `docs/INTEGRATION_GUIDE.md` 中的现场检查。
# 丢失运行提交响应后的唯一匹配恢复（2026-10-24）

当 SQLite 记录处于 `submission_uncertain` 且已知 WARDEN `plan_id` 时，
AgentGuard 先读取 WARDEN 计划，严格校验完整内核请求合同，并比较所有字段
（包括 Probe、SAE、capture_targets 和 metadata），
再列出远端运行。只有**恰好一个**运行属于该计划且有 `run_id`，才读取其终态并
继续原工作流；零个、多个、身份不一致或内核不可达时继续返回
`reconciliation_required`，不会再次执行 `POST /v1/runs`。真实 HTTP 演练从
现存观察运行恢复到 SQLite `finished`，没有再次调用模型。
本次全量测试 146 通过、2 跳过，真实 HTTP 专项 1 通过。

进一步加固后，已知 `run_id` 的网络或轮询异常会保留 `run_submitted` 状态；
兼容旧版本留下的 `interrupted` 记录时，也会先恢复原运行。查询失败不再触发
新的计划或模型运行。新增完整合同差异、未知字段、默认值规范化、网络故障和
旧记录恢复测试；当前全量测试 154 通过、2 跳过。
