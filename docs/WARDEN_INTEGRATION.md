# AgentGuard ↔ WARDEN 集成与迁移说明

版本：AgentGuard 0.9.0  
内核：[`Xinyuan20061/WARDEN`](https://github.com/Xinyuan20061/WARDEN)  
接口版本：WARDEN REST `/v1`

## 1. 目标边界

AgentGuard 是上层安全决策、审批、经验管理和工作流编排系统；WARDEN 是低层因果审计内核。集成必须保持这个方向，不把两个项目拼成一个重复实现的“大包”。

- **WARDEN 唯一负责**：Probe/SAE 资源清单、能力发现、审计计划编译、运行调度、底层因果审计与低层证据引用。
- **AgentGuard 唯一负责**：多 Agent 工作流、候选风险筛查、审批与预算策略、经验库、策略排序、研究登记、统计汇总和面向用户的 REST/CLI。
- **StegoPot** 提供模型运行时、白盒前向与 Activation/干预能力；WARDEN 通过其 Adapter 调用，不要求 AgentGuard 再实现模型 Hook。
- **FRACTAL-Bench** 提供威胁场景/契约与基准评估。它不是 AgentGuard 自己的标签库，也不能由 AgentGuard 根据观测推断隐藏真值。

## 2. 请求流

```text
用户 / 比赛演示 / 测试脚本
          │
          ▼
AgentGuard API / CLI
          │  校验请求、审批范围、预算、保留审计记录
          ▼
WardenKernelClient (本项目唯一内核适配入口)
          │  REST /v1，显式传递字段
          ▼
WARDEN API
  ├─ capabilities / resources / contracts
  ├─ compile audit plan
  ├─ blocked reasons / resource checks
  └─ run status / evidence references
          │
          ▼
AgentGuard 汇总状态、门控策略、经验更新和报告
```

AgentGuard 不访问 WARDEN 的 `artifacts/` 目录，不读取 Activation 原始张量，不导入 `warden.infrastructure`，也不根据本地可用性猜测 WARDEN 能力。原始张量由内核侧受控保存；AgentGuard 消费状态、元数据和证据引用。

## 3. 配置

```dotenv
AGENTGUARD_WARDEN_URL=http://127.0.0.1:8765
AGENTGUARD_WARDEN_TIMEOUT=15
```

未配置时 `/v1/kernel/status` 返回 `not_configured`；无法连接时返回 `unreachable`。这两种状态都不是“没有风险”。审计端点在内核未配置时返回 HTTP 503，不会悄悄退回本地 Probe 或模拟审计。

## 4. API 对接

| AgentGuard 端点 | WARDEN 端点 | 作用 |
| --- | --- | --- |
| `GET /v1/kernel/status` | `GET /v1/health` | 检查内核连通性 |
| `GET /v1/kernel/capabilities` | `GET /v1/capabilities` | 能力与阻塞原因 |
| `GET /v1/kernel/resources` | `GET /v1/resources` | Probe/SAE 清单 |
| `GET /v1/kernel/contracts` | `GET /v1/contracts` | 威胁契约 ID |
| `POST /v1/kernel/audits` | `POST /v1/audit-plans`，可选 `POST /v1/runs` | 兼容式计划提交入口 |
| `POST /v1/kernel/workflows` | health/capabilities/resources/contracts → plan → run → bounded polling | 完整生命周期编排和证据门控经验记录 |
| `GET /v1/kernel/audits/{plan_id}` | `GET /v1/audit-plans/{plan_id}` | 查计划和 blockers |
| `GET /v1/kernel/runs/{run_id}` | `GET /v1/runs/{run_id}` | 查运行状态与证据引用 |

AgentGuard 的请求契约在 `schemas/warden-audit-request.schema.json`，示例在 `examples/warden_audit.example.json`。AgentGuard 的 `start_run` 和 `authorization_ref` 是上层控制字段，发送到 WARDEN `/v1/audit-plans` 前会被剥离，避免违反 WARDEN 的 `additionalProperties: false` 契约。`defense_evaluation` 还必须通过 AgentGuard 的 `AGENTGUARD_APPROVAL_TOKEN` 校验；未通过审批时不会联系 WARDEN。

## 5. 状态语义

- `not_configured`：没有设置内核地址。
- `unreachable`：已配置地址但网络/服务不可用。
- `blocked`：WARDEN 明确指出能力、Probe、SAE、威胁契约或执行器缺失。
- `planned`：计划已生成，实验尚未必执行。
- `queued` / `running`：任务已提交或运行中。
- `completed`：仅表示 WARDEN 运行状态完成；因果结论仍须检查证据、对照组、干预效果和效用指标。
- `failed`：运行失败，不可折叠成阴性检测结果。

## 6. 0.8.0 生命周期编排

`WardenLifecycleService` 是上层完整审计生命周期的唯一编排服务：先检查 WARDEN 健康状态、威胁契约和请求中明确指定的 Probe/SAE 资源，再创建计划；只有 WARDEN 返回 `planned` 才提交运行。可选轮询有 1–120 秒的硬上限，并使用有界退避。未知状态、超时、blocked、failed、cancelled 均不会变成阴性检测。

当且仅当运行状态为 `completed`、响应中包含明确的证据/工件引用，并且请求提供 `scenario_id` 与 `risk_type` 时，AgentGuard 才写入一条经验记录。该记录固定为 `observational`、`unverified`、`inconclusive` 且 `allow_auto_recommendation=false`；它只是可追溯的审计记忆，不代表防御成功或因果结论。相同 `run_id` 使用确定性经验 ID，避免重试重复写入。

新增 `POST /v1/kernel/workflows` 和 `agentguard kernel run`。API 请求中的 `scenario_id`、`risk_type`、审批引用和轮询选项均由 AgentGuard 消费，不会转发到 WARDEN 的严格请求合同。

## 7. 重复实现清理策略

0.6.x 中包含一些本地研究辅助模块：`core/linear_probe.py`、`core/attribution.py` 和 `whitebox/activation.py`。它们目前被旧 API、研究脚本或测试直接调用，贸然删除会造成不兼容。因此 0.7.x–0.8.0 采取分阶段迁移：

1. 新增唯一生产内核入口 `adapters/warden.py`，新的低层审计流程走 WARDEN REST。
2. 旧模块不再作为生产因果审计的权威来源；它们只用于离线分析、兼容性和方法对照。
3. 对外报告必须区分本地基线统计与 WARDEN 实际返回的内核证据。
4. 后续主版本可按调用情况移除本地 Probe/Activation 重复入口；在确认迁移完成前保留兼容层。

本次没有删除尚被现有脚本调用的模块；这是有意的兼容性措施，不代表它们与 WARDEN 具有同等权威性。

## 7. 验证

```bash
python -m pip install -e '.[dev]'
pytest
agentguard kernel status
agentguard kernel capabilities
agentguard kernel resources --kind probe
agentguard kernel submit --config examples/warden_audit.example.json --plan-only
```

测试使用可控 HTTP 传输替身验证接口契约、HTTP 错误处理和 blocked 状态传播；这些测试只证明适配层行为，不证明真实模型、Probe/SAE 工件或 WARDEN Runtime 已部署。


## 8. 0.9.0 持久化工作流与故障恢复

工作流记录保存在 `AGENTGUARD_DATA_DIR/warden_workflows.sqlite3`。同一 `request_id` 的相同请求会返回已保存结果；同一 ID 对应不同请求体会返回 HTTP 409。已保存的 `plan_id` 和 `run_id` 可用于恢复，不会在不确定的提交阶段盲目创建第二个远端任务。

查看状态：

```bash
agentguard kernel workflow-status --request-id <request-id>
```

也可以使用 `GET /v1/kernel/workflows?status=run_submitted&limit=50` 列出待对账工作流，或使用 `GET /v1/kernel/workflows/{request_id}` 与 `GET /v1/kernel/workflows/{request_id}/events` 查询单条记录和状态转换历史。详见 [`WORKFLOW_RELIABILITY.md`](WORKFLOW_RELIABILITY.md)。
