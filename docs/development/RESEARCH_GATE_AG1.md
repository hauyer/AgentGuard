# AG-1：WARDEN 合同与能力发现门

日期：2026-10-28。结论：**静态合同门通过；Live 合同和服务门 BLOCKED / NOT_RUN。**

## 实现

- 扩展只读 `GET /v1/research/capabilities`，底层沿用 `WardenKernelClient` 与 `diagnose_kernel` 的健康、OpenAPI、capabilities、resources、contracts 查询；没有第二个 HTTP 客户端，也没有 WARDEN POST。
- 对每个候选键返回状态、blockers、源版本、WARDEN 是否精确声明该键和观察时间：`SUPPORTED_AND_READY`、`SUPPORTED_BUT_BLOCKED`、`UNSUPPORTED`、`UNAVAILABLE`、`UNKNOWN`。
- 没配置服务时返回 `WARDEN_URL_NOT_CONFIGURED`；错误配置脱敏为 `WARDEN_CONFIGURATION_ERROR`；符合已核验合同但不声明候选能力时为 `WARDEN_RESEARCH_CAPABILITY_NOT_DECLARED`；只声明能力而缺 AgentGuard 已审查操作映射时为 `WARDEN_RESEARCH_OPERATION_CONTRACT_UNREVIEWED`。
- 当前固定 WARDEN 源码 0.3.0 没有研究执行能力端点。在线 WARDEN 地址未配置，因此本机服务状态仍为 `UNAVAILABLE`，线上真实状态 `NOT_RUN`。不会根据 `health=ok` 设置 ready。

## 证据和阻塞

| 检查 | 结果 |
|---|---|
| `pytest tests/test_research_capability.py` | 退出码 0；状态映射、固定 0.3.0、未配置、误配置脱敏均通过 |
| `pytest tests/test_research_api.py` | 退出码 0；只读端点不改写 Study 记录 |
| `pytest tests/test_qwb_pinned_openapi_compat.py` | 退出码 0；旧 33 个 App operation object 不变 |
| Live WARDEN GET `/openapi.json` / `/v1/capabilities` / `/v1/resources` | `NOT_RUN`：没有授权环境 URL |
| Live research plan/run | `BLOCKED / NOT_RUN`：WARDEN 没有版本化的 Probe、干预、研究任务 API |

解除阻塞需要 WARDEN 正式发布具体能力键、版本化 request/response schema、提交幂等、持久任务状态、工件读取/来源合同，再通过真实服务集成验收。CLI、Stub 或 OpenAPI 中的候选字符串不能单独解除门控。
