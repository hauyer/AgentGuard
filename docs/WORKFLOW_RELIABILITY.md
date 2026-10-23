# AgentGuard 0.9.0：WARDEN 工作流可靠性与恢复

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
AgentGuard 先读取 WARDEN 计划，核对 `request_id/contract_id/model_id/runtime_id/mode`，
再列出远端运行。只有**恰好一个**运行属于该计划且有 `run_id`，才读取其终态并
继续原工作流；零个、多个、身份不一致或内核不可达时继续返回
`reconciliation_required`，不会再次执行 `POST /v1/runs`。真实 HTTP 演练从
现存观察运行恢复到 SQLite `finished`，没有再次调用模型。
本次全量测试 146 通过、2 跳过，真实 HTTP 专项 1 通过。
