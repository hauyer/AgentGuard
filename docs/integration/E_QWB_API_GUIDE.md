# E5: QWB 应用 API 集成指南

## SDK 方法清单

AgentGuard Python SDK 提供以下公共方法，对应 REST 端点一一对应：

### 风险分析（E1）

```python
client.get_risk_analysis(request_id: str, *, run_id: str | None = None) -> dict
```

对应：`GET /v1/investigations/{request_id}/risk-analysis`

返回结构：
- `schema_version`: "1.0"
- `request_id`: 调查请求 ID
- `available`: 是否有可用数据
- `data_source`: 数据来源（warden_observation / synthetic_report）
- `evidence_level`: 证据级别（observational）
- `findings`: 风险发现列表
- `hypotheses`: 可证伪假设列表
- `limitations`: 局限性说明

### 调查循环（E2）

```python
client.create_investigation_loop(
    base_request_id: str,
    strategy: str = "information_value",
    max_actions: int = 8,
    max_seconds: float = 300.0,
    max_model_calls: int = 4,
    no_new_evidence_rounds: int = 2,
    data_partition: str = "held_out_family",
    idempotency_key: str | None = None,
) -> dict
```

对应：`POST /v1/investigation-loops`

需要 operator 或 approval 凭证。幂等键通过 `X-Loop-Idempotency-Key` 头传递。

```python
client.list_investigation_loops(
    base_request_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict
```

对应：`GET /v1/investigation-loops`

```python
client.get_investigation_loop(loop_id: str) -> dict
```

对应：`GET /v1/investigation-loops/{loop_id}`

```python
client.list_investigation_decisions(loop_id: str) -> dict
```

对应：`GET /v1/investigation-loops/{loop_id}/decisions`

```python
client.get_investigation_evidence_changes(loop_id: str) -> dict
```

对应：`GET /v1/investigation-loops/{loop_id}/evidence-changes`

```python
client.get_investigation_loop_budget(loop_id: str) -> dict
```

对应：`GET /v1/investigation-loops/{loop_id}/budget`

## 权限模型

| 操作 | 所需凭证 |
|------|---------|
| 读取调查列表/详情 | 普通 API Token |
| 读取风险分析 | 普通 API Token |
| 读取调查循环 | 普通 API Token |
| 创建调查循环 | Operator Token 或 Approval Token |
| 执行防御动作 | Approval Token |

## 数据隔离

- 调查 ID 严格隔离，跨请求 ID 查询返回 404
- 不返回系统提示词、隐藏真值、私有工具参数
- 不读取 WARDEN 的 file:// 路径
- 证据不足时返回 `available=false` 或 `insufficient_evidence`

## QWB 兼容测试覆盖

以下数据结构已建立转换测试：
- 风险 ID（finding_id）
- 假设 ID（hypothesis_id）
- 图谱边 ID（graph_edge_refs）
- 调查循环 ID（loop_id）
- 证据引用（evidence_refs）
- 预算结构（budget / remaining）
- 状态枚举（phase / stop_reason / execution_status）
- 数据来源标记（data_source / evidence_level）
