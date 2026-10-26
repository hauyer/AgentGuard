# AgentGuard 1.1.0-rc3 部署指南

本指南支持受控的私有部署。A8 未在公网或生产环境部署；真实 WARDEN 和模型能力
仍需按 [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) 验收。

## 安装

在支持 Python 3.11–3.13 的目标系统上先构建/取得发布 wheel，再用锁文件安装：

```bash
python -m pip install -c requirements/runtime.lock dist/agentguard-1.1.0rc3-py3-none-any.whl
agentguard --help
```

源码验收和 RC wheel/sdist 构建使用 `requirements/dev.lock`。不要把其他仓库的依赖
或 editable install 混入 AgentGuard 发布环境。MCP 与白盒依赖是可选 extras，不属于
默认 runtime 安装。

## 运行前配置

至少将 `AGENTGUARD_DATA_DIR` 指向具有持久写权限的专用目录。建议为 API、operator 和
审批分别配置独立随机凭证，并在可信 TLS 反向代理后运行：

```bash
export AGENTGUARD_DATA_DIR=/var/lib/agentguard
export AGENTGUARD_API_TOKEN='<secret-from-secret-manager>'
export AGENTGUARD_OPERATOR_API_TOKEN='<separate-operator-secret>'
export AGENTGUARD_APPROVAL_TOKEN='<separate-approval-secret>'
export AGENTGUARD_CORS_ORIGINS='https://console.example.org'
export AGENTGUARD_RATE_LIMIT_PER_MINUTE=300
export AGENTGUARD_WARDEN_URL='https://warden.internal.example'
export AGENTGUARD_BUILD_COMMIT='<release-git-commit-sha>'
agentguard serve --host 127.0.0.1 --port 8000
```

| 变量 | 默认/要求 | 用途 |
| --- | --- | --- |
| `AGENTGUARD_DATA_DIR` | `.agentguard` | 本地报告、工件、工作流、经验与研究 SQLite 存储 |
| `AGENTGUARD_API_TOKEN` | 空（鉴权关闭） | 通用 API 访问令牌；对外暴露前必须配置 |
| `AGENTGUARD_OPERATOR_API_TOKEN` | 空 | 激活策略、审计事件等 operator 操作的独立凭证 |
| `AGENTGUARD_APPROVAL_TOKEN` | 空 | 需要审批的防御生命周期凭证；不代表执行器可用 |
| `AGENTGUARD_CORS_ORIGINS` | 空（CORS 关闭） | 逗号分隔的精确来源白名单；禁止 `*` |
| `AGENTGUARD_RATE_LIMIT_PER_MINUTE` | `300` | 单进程、按直接 peer IP 的请求上限 |
| `AGENTGUARD_WARDEN_URL` | 未设置 | WARDEN 公共 HTTP API 地址；未配置时真实工作流返回 unavailable/blocked |
| `AGENTGUARD_WARDEN_TIMEOUT` | `15` 秒 | WARDEN HTTP 超时 |
| `AGENTGUARD_WARDEN_READ_RETRIES` | `0` | GET 临时错误最多重试 0–3 次；写请求不重试 |
| `AGENTGUARD_BUILD_COMMIT` | 未设置时为 `unknown`；发布流水线应注入 40 位 Git SHA | `/v1/system/identity` 的构建身份；无效值不接受且不泄露其他环境变量 |
| `AGENTGUARD_BASE_URL` | `http://127.0.0.1:8000` | SDK/MCP 使用的 AgentGuard API origin |
| `AGENTGUARD_API_TIMEOUT` | `15` 秒，最大 120 | SDK API 超时 |

Health、OpenAPI、Swagger/ReDoc 与 console 路由按当前实现保持公开。若这些界面不适合
部署环境，应在受信任的反向代理层进一步限制访问。不得信任来自任意客户端的
`X-Forwarded-For`；本应用限流按直连 peer 地址计数。

## 数据、恢复与升级

- SQLite schema 在存储对象初始化时创建并执行兼容迁移；旧经验会进入 quarantine，
  不会被静默提升为已验证奖励。升级前备份 `AGENTGUARD_DATA_DIR`，并保留可回滚的
  发布 wheel。
- 受支持的存储拓扑为单主机：SQLite 与本地文件锁可协调共享同一数据目录的本机进程，
  但不是跨主机分布式锁。多主机 active-active 与网络文件系统部署不受支持，readiness
  会以 `MULTI_HOST_STORAGE_UNSUPPORTED` 明示此限制。单节点仍建议单 Uvicorn worker，因
  后台任务、取消标记与限流窗口含进程内状态。
- SQLite 工作流日志支持幂等登记、并发请求门控和已知远端 ID 对账；提交不确定且没有
  可查询 ID 时进入待对账状态，不会盲目重提，也不承诺跨系统 exactly-once。
- 对不确定的远端计划/运行提交，AgentGuard 保留 ambiguous 状态，不自动重复提交；按
  WARDEN 远端状态人工对账后再恢复。
- API 审计事件只保留 request ID、方法、路由模板、状态、角色和时间，不保存 body、
  query string 或凭据。其保留窗口约为最近 100,000 个事件。

## 代理与可观测性

在可信反向代理终止 TLS、执行网络访问控制和跨 worker 共享限流。对外暴露前配置
独立 API/operator/approval secret、精确 CORS 来源、持久化数据卷和备份。监控
`/health`、`X-Request-ID`、API audit events 和明确的 `WARDEN_NOT_CONFIGURED` /
`API_AUDIT_UNAVAILABLE` / `unsupported` 状态。API health 只表示 AgentGuard HTTP
进程存活，不代表 WARDEN、Probe 或模型就绪。
