# AgentGuard 上层平台依赖图

日期：2026-10-24；分支 `feature/agentguard-upper-layer-v1`。

```mermaid
flowchart TD
  App[未来 AgentGuard-QWB-App] --> Public[AgentGuard Public API / SDK]
  Public --> API[api.app]
  CLI[cli] --> Lifecycle[services.warden_lifecycle]
  API --> Lifecycle
  Lifecycle --> Client[adapters.warden]
  Lifecycle --> Journal[services.warden_workflow_store / SQLite]
  Lifecycle --> Memory[memory.store]
  Client --> WARDEN[只读依赖 WARDEN /v1 API]
  API --> Coordinator[agents.coordinator / llm_advisor]
  Coordinator --> Strategies[固定 / 风险 / 信息价值 / 经验 / LLM 排序]
  Strategies --> Trace[类型化决策与证据引用轨迹]
  Coordinator --> RolePolicy[角色消息和工具白名单]
  Coordinator --> Budget[候选步数 / 模型调用 / token 预算]
  API --> Policy[core.policy / core.defense]
  API --> Research[core.attribution / statistics / study_analysis]
  API -. 弃用离线兼容路由 .-> CompatProbe[compat.linear_probe]
  API -. 仅合成研究 .-> CompatPaired[compat.paired_runner]
  MCP[mcp_server] -. 仅合成研究 .-> CompatPaired
  Legacy[0.9 Python 导入垫片] -.-> Compatibility[agentguard.compat]
  Compatibility -. 旧离线/研究能力 .-> OldAdapters[FRACTAL-Bench / StegoPot 适配器]
  Compatibility -. 旧本地 Python 能力 .-> Hooks[PyTorch Hook / Probe]
```

复现静态模块图：

```powershell
python scripts/audit_architecture.py --check --output .agentguard/architecture-a4.json
```

源码调用者按兼容边界划分：

| 兼容能力 | 生产/公开调用者 | 旧导入路径 |
| --- | --- | --- |
| `compat.linear_probe` | `api.app` 中 OpenAPI 标记 deprecated 的离线训练/预测端点 | `core.linear_probe` |
| `compat.paired_runner` | `api.app` 和 MCP 的合成研究工作流 | `core.paired_runner` |
| `compat.whitebox_activation` | 旧 Python 导出 | `whitebox.activation` 和 `whitebox` 包级导出 |
| `compat.fractal_bench` | 按需调用的旧 Python 场景适配 | `adapters.fractal_bench` 和包级惰性导出 |
| `compat.stegopot` | 按需调用的旧 Python 文件级实验适配 | `adapters.stegopot` 和包级惰性导出 |
| `core.attribution` | CLI、API 的上层描述性统计 | 同一上层模块 |

五份重复低层/直连实现位于 `agentguard.compat`。旧路径保留为兼容垫片；新
WARDEN 工作流与诊断不依赖这些实现，所有底层 API 调用经过
`WardenKernelClient`。`adapters` 包现在按需导入旧 FRACTAL-Bench/StegoPot
适配器，普通 WARDEN/Mock 导入不会初始化它们。

静态检查当前为 76 个模块、463 条导入；旧兼容路径和 `agentguard.compat` 单独列为兼容债务；白盒和
StegoPot/FRACTAL-Bench 的三个底层导入只允许出现在兼容实现中。AST 检查包括
函数内导入和字面动态导入，不判断代码运行时可达性，也不扫描 Python 隐式
包初始化以外的打包行为。
