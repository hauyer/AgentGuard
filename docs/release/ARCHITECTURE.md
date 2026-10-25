# AgentGuard 1.1.0-rc1 架构

```mermaid
flowchart LR
    App[应用 / 用户] --> API[FastAPI 公共 API]
    SDK[Python SDK] --> API
    MCP[MCP facade] --> SDK
    API --> Edge[鉴权 · 限流 · API 审计 · 合同校验]
    Edge --> Public[稳定业务路由]
    Public --> Lifecycle[WARDEN 生命周期服务]
    Lifecycle --> Journal[(SQLite 工作流日志)]
    Lifecycle --> Warden[WARDEN HTTP API\n唯一生产低层审计内核]
    Public --> Evidence[证据治理网关]
    Evidence --> Journal
    Public --> Graph[交互图谱投影 C1/D2]
    Graph --> RiskFind[风险发现与假设 D2]
    Public --> ObsRun[标准化观察工作流 D1]
    ObsRun --> Lifecycle
    Public --> Loop[多轮调查循环 D3]
    Loop --> Graph & Evidence & RiskFind
    Loop --> ObsRun
    Loop --> DefPlan[证据约束防御规划 D4]
    Loop --> Eval[策略受控对比 D5]
    Public --> Synthetic[合成研究 / Demo]
    Synthetic --> Local[(本地报告、工件、经验与研究 SQLite)]
    Loop --> Local
    Legacy[旧兼容 API] -. deprecated / offline .-> Compat[agentguard.compat]
    Compat -. compatibility only .-> Lower[旧 Probe / Activation / lower integrations]
    Warden -. public HTTP dependency .-> LowerRepos[WARDEN runtime / lower repositories]
```

## 边界说明

- WARDEN 是生产低层审计内核。AgentGuard 对其进行能力预检、请求验证、审批/预算
  门控、持久化生命周期协调和证据摘要；它不复制 WARDEN 的因果、Activation、Probe
  或 SAE 实现。
- 新业务路由通过公共 API 服务和 WARDEN 生命周期服务工作。`agentguard.compat` 仍被
  明确的旧/离线路由用于迁移兼容，但架构审计确认低层直接依赖仅位于该命名空间，
  没有生产边界越权或模块环。
- SQLite 保存本地 API 审计事件、工作流幂等/恢复状态、经验和研究登记；它不是远程
  WARDEN 的事务数据库，也不提供跨系统 exactly-once。
- Evidence Gateway 只投影有治理的摘要与可引用工件，不读取任意 URI 或本地路径。
- Demo/Mock 与真实 WARDEN 流程分离。合成或观察性结果不能升级为因果成功。
- **D 轮层（1.1.0-rc1）**：标准化观察工作流（D1）复用生命周期服务并校验内核
  声明的运行时标识；风险发现（D2）只消费图谱投影的结构元数据；多轮调查循环
  （D3）组合只读服务与 D1，受四重预算约束，LLM 仅推荐；防御规划（D4）与策略
  对比（D5）为纯上层投影/记录，不触碰内核。D 轮未新增 REST 路由，报告经
  内容寻址工件持久化。

## 静态边界审计

`scripts/audit_architecture.py --check` 通过：81 个 AgentGuard 模块、517 条导入，
0 个边界违规、0 个依赖环。仅 `agentguard.compat.fractal_bench`、
`agentguard.compat.stegopot` 与 `agentguard.compat.whitebox_activation` 导入低层或
PyTorch 依赖；这些不是生产 WARDEN 审计入口。
