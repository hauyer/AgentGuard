# 重复能力与调用点清单

依据 AgentGuard 本地导入基线 `3bc9584`、WARDEN `3116af8`。分类：A = WARDEN 已提供等价能力、迁移后可删除；B = 只部分覆盖，需保留适配/兼容；C = AgentGuard 上层职责；D = 确认无调用者的死代码。以下是**现状分类**，不是删除授权。

| AgentGuard 模块 | 具体功能与现有调用者 | WARDEN 对应位置与差异 | 分类、处理 |
| --- | --- | --- | --- |
| `src/agentguard/core/linear_probe.py:21-119,148-205` | 本地逻辑回归 Probe、分组切分和阈值。`api/app.py:37,971-1088` 的已弃用 `/v1/probes/train|predict` 仍调用；`tests/test_linear_probe.py`、`tests/test_api_expanded.py`、`tests/test_package_release.py` 依赖。 | `warden/infrastructure/methods/probe/linear.py` 有 Probe 参考实现与资源清单；公开 REST 目前只列资源、创建审计计划，没有等价训练/预测端点（`warden/interface/api/app.py:42-96`）。两边序列化合同也未证明兼容。 | **B**。保留离线基线与旧 API；先核对工件格式、调用者迁移和弃用期限。 |
| `src/agentguard/core/attribution.py:21-92,128-181` | 对调用方提供的处理/对照数组做中介贡献摘要与路径变化统计。CLI `cli.py:24-25,157-160,335-347`、API `api/app.py:52-56,1132-1164`、`tests/test_attribution.py`、`tests/test_research_api.py` 调用。 | WARDEN `application/compiler.py:53-64` 规划 `causal_attribution` 阶段，`application/executor.py:207-291` 可注册阶段，但默认组合尚未执行真实归因。AgentGuard 的描述性聚合不等同于 WARDEN 的底层因果实验。 | **C**（上层研究统计），可保留；真实因果结论必须引用 WARDEN 干预证据。 |
| `src/agentguard/core/paired_runner.py:24-190` | Mock 下执行基线、处理、负对照；真实后端需注入 `paired_executor`，否则拒绝。API `api/app.py:38,151,807-947`、MCP `mcp_server.py:15,104-136`、`tests/test_paired_runner.py` 调用。 | WARDEN 有 `AuditPlan`、`PipelineAuditExecutor` 与 `RunDispatcher` 端口，但默认无真实配对重放阶段/调度器。 | **B/C**。保留合成研究工具；真实路径将来只提交 WARDEN 审计。不能删掉 API/MCP 所依赖的兼容功能。 |
| `src/agentguard/whitebox/activation.py:100-231` | 本地 PyTorch Hook、激活采集与 zero/reference/noise patch。`whitebox/__init__.py:2-12` 导出，`tests/test_whitebox_activation.py` 与文档示例直接使用；目前未被 WARDEN 生产工作流调用。 | `warden/infrastructure/adapters/stegopot.py:20-176` 有 StegoPot Activation Backend、采集与干预适配；默认服务 `warden/interface/factory.py:16-61` 未注入白盒模型。 | **B**。属于重复底层能力，但现在只能作为已声明的旧 Python 接口保留；等真实 WARDEN 路径、工件迁移与弃用测试具备后移除。 |
| `src/agentguard/adapters/fractal_bench.py:19-105` | 场景目录和中心评价回调；`adapters/__init__.py:2` 导出，`tests/test_adapters.py`、`tests/test_real_adapters.py`、`docs/INTEGRATION_GUIDE.md` 引用。当前默认 API/CLI 不实例化该类。 | `warden/infrastructure/adapters/fractal_bench.py:14-126` 把冻结数据集映射为 `ThreatContract`，并可建立 StegoPot 实验计划；不提供 AgentGuard `ScenarioSpec` 的同形公开接口。 | **B**。场景展示可能仍需薄上层映射；真实威胁合同需经 WARDEN。 |
| `src/agentguard/adapters/stegopot.py:30-488` | 直接调用 StegoPot `run_file` 并转换 Observation/Evaluation；`adapters/__init__.py:3` 导出，`tests/test_adapters.py`、`tests/test_real_adapters.py`、集成指南引用。可通过外部注入的 `AgentGuardEngine` 进入 API 路径（`api/app.py:92-151`）。 | WARDEN `infrastructure/adapters/stegopot.py:20-176` 处理模型内采集/干预，但默认服务尚未组合 Agent Runtime/Dispatcher；目前没有可替代 `run_file` 上层路径的真实 API 工作流。 | **B**。生产审计最终不得绕过 WARDEN；先补 WARDEN 运行链并迁移调用方，不立即删。 |

## 其他保留能力

`agents/coordinator.py`、`core/investigation.py`、`core/planner.py`、`core/policy.py`、`core/defense.py`、`memory/store.py`、`core/experience_bandit.py`、`research_registry.py`、`core/statistics.py`、API/CLI 与 `services/warden_workflow_store.py` 属于 AgentGuard 的调查、决策、研究管理和持久化职责，分类 **C**。`adapters/warden.py` 是必要边界，非重复内核。

**可立即删除的业务模块：无。** 六个重点模块均有直接测试、公开导出、API/CLI/MCP 调用或文档用户。未确认任何 **D** 类模块。未来删除前须检查源码、测试、文档、JSON Schema、发布打包断言和外部用户迁移情况；不能因当前默认 API 未实例化某类就认定无调用者。

## 依赖方向

当前并存路径：

```text
AgentGuard API/CLI/MCP -> 本地 Probe / Attribution / Paired / WhiteBox
AgentGuard 可注入 Runtime -> 直接 StegoPot / FRACTAL-Bench 适配器
AgentGuard /v1/kernel 与 kernel CLI -> WardenLifecycleService -> WardenKernelClient -> WARDEN REST
```

目标生产审计路径：

```text
AgentGuard API/CLI -> AgentGuard 应用服务/策略/工作流存储
                   -> WardenKernelClient -> WARDEN /v1 API
                   -> WARDEN 组合的 StegoPot Runtime 与 FRACTAL-Bench 合同
AgentGuard 离线统计工具 <- 已标明来源与等级的 WARDEN 证据/实验数据
```

生产流程不得直接导入 `warden.infrastructure`、StegoPot 内部运行模块或 FRACTAL-Bench 隐藏真值。现有 `StegoPotAdapter`、`FractalBenchAdapter` 是历史接入路径，需要以兼容性/研究用途明确隔离。
