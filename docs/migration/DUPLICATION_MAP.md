# 重复能力与调用点清单

> 2026-10-24 上层重构 A0 复核：后续只修改 AgentGuard。下面对 WARDEN
> 能力缺口的判断用于登记依赖阻塞，不再授权开发底层等价实现。
> 当前调用者图可通过 `scripts/audit_architecture.py` 重新生成。

| 模块 | A3 最终处理方向 | 兼容与删除条件 |
| --- | --- | --- |
| `compat/linear_probe.py` | 独立兼容命名空间中的本地离线基线；旧 `core.linear_probe` 保留导入垫片，API 仅提供弃用离线路由 | 1.0 兼容，1.1 候选移除前须有 WARDEN 公共训练/预测接口和旧客户端迁移说明；DEP-005 |
| `core/attribution.py` | 保留上层统计 | 输入来源与证据等级明确，不自行升级因果结论 |
| `compat/paired_runner.py` | 合成研究兼容；旧 `core.paired_runner` 保留导入垫片；真实执行仍不开放 | API/MCP 只允许合成后端，真实路径交 WARDEN；1.1 移除前迁移公开调用者 |
| `compat/whitebox_activation.py` | 旧 Python 离线兼容；`whitebox.activation` 与包级导出保留导入垫片，禁止作为生产审计入口 | 1.0 兼容，1.1 候选移除前须有 WARDEN 等价公开能力和迁移说明；DEP-001/005 |
| `compat/fractal_bench.py` | 旧场景展示/离线评价兼容；`adapters.fractal_bench` 保留垫片 | 不由新应用服务实例化，不向调查 Agent 输出真值；1.1 移除前评估正式兼容包和调用者迁移 |
| `compat/stegopot.py` | 旧 Python 离线兼容；`adapters.stegopot` 保留垫片，生产真实运行入口已拒绝 | 1.0 兼容；1.1 候选移除需有 WARDEN 正式替代与迁移说明 |

这里的 1.1 是评估移除窗口，不代表无条件删除。A0 不删除业务源码。
当前兼容调用者见 `DEPENDENCY_GRAPH.md` 和 `COMPATIBILITY.md`。五份低层/直连
实现已集中在 `agentguard.compat`；旧 Python 路径仍作为导入垫片，API 的
Probe 路由继续标记 deprecated。由于 WARDEN 尚无 Probe 训练/预测、模型白盒
运行和配对干预的等价公开 REST 接口，本阶段实际删除文件为 **无**。归因统计
属于 AgentGuard 上层分析，继续留在 `core.attribution`。

依据 AgentGuard 本地导入基线 `3bc9584`、WARDEN `3116af8`，并于 2026-10-24 对照 WARDEN 观察运行提交 `e9aa65d` 复核。分类：A = WARDEN 已提供等价能力、迁移后可删除；B = 只部分覆盖，需保留适配/兼容；C = AgentGuard 上层职责；D = 确认无调用者的死代码。以下是**现状分类**，不是删除授权。

| AgentGuard 模块 | 具体功能与现有调用者 | WARDEN 对应位置与差异 | 分类、处理 |
| --- | --- | --- | --- |
| `src/agentguard/compat/linear_probe.py`（旧垫片：`core/linear_probe.py`） | 本地逻辑回归 Probe、分组切分和阈值。`api/app.py` 的弃用 `/v1/probes/train|predict` 调用兼容实现；旧 Python 导入与测试保持可用。 | WARDEN 有 Probe 参考实现与资源清单；公开 REST 没有等价训练/预测端点，工件合同也未证明兼容。 | **B**。仅离线兼容；待 DEP-005 解除、迁移调用者并结束 1.1 候选弃用窗口后再评估删除。 |
| `src/agentguard/core/attribution.py:21-92,128-181` | 对调用方提供的处理/对照数组做中介贡献摘要与路径变化统计。CLI `cli.py:24-25,157-160,335-347`、API `api/app.py:52-56,1132-1164`、`tests/test_attribution.py`、`tests/test_research_api.py` 调用。 | WARDEN `application/compiler.py:53-64` 规划 `causal_attribution` 阶段，`application/executor.py:207-291` 可注册阶段，但默认组合尚未执行真实归因。AgentGuard 的描述性聚合不等同于 WARDEN 的底层因果实验。 | **C**（上层研究统计），可保留；真实因果结论必须引用 WARDEN 干预证据。 |
| `src/agentguard/compat/paired_runner.py`（旧垫片：`core/paired_runner.py`） | 合成基线、处理、负对照；API 与 MCP 通过兼容命名空间调用。真实 AgentGuard runtime 注入和 paired executor 均被拒绝。 | WARDEN 有计划/执行器端口，但当前默认组合无真实配对重放阶段/调度器。 | **B/C**。保留合成研究，真实路径只提交 WARDEN 审计；不宣称实际因果验证。 |
| `src/agentguard/compat/whitebox_activation.py`（旧垫片：`whitebox/activation.py`） | 本地 PyTorch Hook 与激活 patch；旧包级 Python 导出保持。当前不进入 WARDEN 生产工作流。 | WARDEN/StegoPot 有 Activation Backend 与白盒 CLI，但其默认 REST 尚无等价模型运行接口。 | **B**。只作为旧本地 Python 兼容；等 WARDEN 正式等价 API、工件迁移和弃用窗口满足后评估删除。 |
| `src/agentguard/compat/fractal_bench.py`（旧垫片：`adapters/fractal_bench.py`） | 映射公开场景元数据与离线评价回调；包级导出保留，默认 API/CLI 不实例化。 | WARDEN 能映射冻结数据集至 `ThreatContract`，但不提供 AgentGuard `ScenarioSpec` 同形接口。 | **B**。保留薄兼容映射；隐藏真值留在基准评价端，不进入 Agent/应用。 |
| `src/agentguard/compat/stegopot.py`（旧垫片：`adapters/stegopot.py`） | 旧直接 `run_file` 适配与 Observation/Evaluation 转换；默认 API/CLI 不实例化，真实 runtime 直接注入已拒绝。 | WARDEN 有 StegoPot Activation Backend 与白盒 CLI，但默认 REST 运行组合未就绪，不能替代旧文件级实验适配。 | **B**。限旧本地兼容；生产审计只能走 WARDEN，未来删除须有正式接口及调用者迁移。 |

## 其他保留能力

`agents/coordinator.py`、`core/investigation.py`、`core/planner.py`、`core/policy.py`、`core/defense.py`、`memory/store.py`、`core/experience_bandit.py`、`research_registry.py`、`core/statistics.py`、API/CLI 与 `services/warden_workflow_store.py` 属于 AgentGuard 的调查、决策、研究管理和持久化职责，分类 **C**。`adapters/warden.py` 是必要边界，非重复内核。

WARDEN 上游 `381fc08` 增加白盒 CLI 和 Probe Suite，但仍没有 Probe 训练/预测、白盒模型运行或配对因果重放的等价公开 REST 入口。A3 将五份低层实现收进 `agentguard.compat`，保留原路径垫片；归因统计继续作为上层分析。API 已拒绝直接注入真实 runtime 或 paired executor。兼容 API 与调用者迁移条件见 `COMPATIBILITY.md`。

**A3 实际删除文件：无。** 五个重复底层/直连实现已物理迁入兼容命名空间，旧模块路径只是重新导出的垫片；归因模块仍承担上层统计职责。当前六项均未满足业务实现整体删除条件，WARDEN 等价公开能力尚缺。未来移除垫片或兼容实现前，须检查源码、测试、文档、Schema、发布包和外部调用者迁移；不能因默认 API 未实例化适配器就认定无用户。

## 依赖方向

A3 后的生产审计路径：

```text
AgentGuard API/CLI -> AgentGuard 编排/策略/工作流存储
                   -> WardenLifecycleService -> WardenKernelClient -> WARDEN REST
                   -> WARDEN 组合的 StegoPot Runtime 与 FRACTAL-Bench 合同
AgentGuard 离线统计工具 <- 已标明来源、等级与限制的证据摘要
```

隔离的兼容路径：

```text
旧 Python 导入垫片 -> agentguard.compat
API Probe deprecated 路由 -> compat.linear_probe（本地调用方向量离线基线）
API/MCP 配对研究 -> compat.paired_runner（仅 synthetic）
归因摘要 -> core.attribution（上层统计，不验证输入来源）
```

生产流程不得直接导入 `warden.infrastructure`、StegoPot 内部运行模块或
FRACTAL-Bench 隐藏真值。旧直连适配器只在显式兼容路径保留；WARDEN 不可用时
不得回退到它们。新应用调用 AgentGuard Public API / SDK，不导入该兼容包。
