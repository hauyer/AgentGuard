# GitHub 上游更新核查（2026-10-24）

## 核查范围与结论

通过 GitHub 连接器只读核查四个仓库的默认分支及已知 AgentGuard 开发分支。
未向只读仓库写入文件、执行 fetch/pull、合并或调整 submodule。

| 仓库 | GitHub main 当前状态 | 相对原固定上游基线 |
| --- | --- | --- |
| `hauyer/AgentGuard` | `3bc9584` | main 无新增；远端 `feature/warden-kernel-migration` 仍为 `c4564bd` |
| `Xinyuan20061/WARDEN` | `381fc08170386a2dbd4c2ded3f0f724f871e9636`，0.3.0 | 原 `3116af8` 之后新增 2 提交 |
| `Xinyuan20061/FRACTAL-Bench` | `8081bc6` | main 无新增 |
| `Xinyuan20061/stegopot` | `f374a39` | main 无新增 |

以上比较的是远端分支；不包含本地 AgentGuard 的未推送开发提交，也不把本地
底层实验分支当作远端 main。实际代码尚未升级到 WARDEN 0.3.0，本机真实 HTTP
烟测使用的仍是先前本地 0.2.0 构建。

## WARDEN 0.3.0 新内容

- `9bc7934`：GPU-ready white-box Probe workflow，提交时间为北京时间
  2026-10-24 09:24:38。
- `381fc08`：white-box workflow lint 修复，09:27:33。
- 新增带家族/输入隔离的 train/validation/test 数据合同、StegoPot Activation
  特征提取与 SHA-256 Corpus、逐层单主体和联合主体 Probe Suite。
- 新增 Probe 风险方向与分数加权 Activation 干预算子。
- 新增 `warden whitebox-preflight`、`warden whitebox-run --phase collect|train|all`
  和 Qwen2.5-1.5B 参考配置。
- 最新提交对应的两个 GitHub CI 运行均 completed/success；没有在本机执行
  这批新代码或真实模型，因此不能标为本机 real_model_validated。

## 对 AgentGuard 的实际影响

1. `/v1` 路由和请求主体没有随本次更新变化；默认 `factory` 仍不注入真实
   Agent Runtime、Activation Backend 或 RunDispatcher。新增白盒 CLI 不等于
   AgentGuard 可以经公开 REST 执行完整白盒/因果工作流。
2. 上游 `AuditMode` 仍只有 capture_only、detection、causal_audit、
   defense_evaluation；不包含本地实验分支的 observation。不能把两个构建
   视为完全等价。使用 observation 的请求要以目标部署实际支持为准。
3. 上游 `WardenService.capabilities()` 尚无默认 `run.dispatcher` 组件报告；
   AgentGuard doctor 必须保持 unknown/blocked，不能由版本号推断可执行。
4. 上游 API 对计划直接调用 `to_primitive(plan)`，仍可能将内部
   `threat_contract.truth` 返回给客户端。AgentGuard 的 `_public_plan` 投影会
   丢弃这些字段；该保护不能移除。后续 Evidence Gateway 仍需保证应用和
   Agent 不接触隐藏真值。
5. Probe 参考实现和 CLI 的能力有所增强，但没有已核实的公开训练/预测或
   干预执行 REST 入口。DEP-001、DEP-004、DEP-005 不能因此自动解除。

后续继续 AgentGuard 上层 A2–A8；只通过公开接口消费已实际返回的能力。
不在 AgentGuard 内调用 WARDEN 私有模块或补写白盒执行器。

## 来源

- [两次上游提交比较](https://github.com/Xinyuan20061/WARDEN/compare/3116af8eb3a7fc3cc9d14ecfa4e5b469f2356e4a...381fc08170386a2dbd4c2ded3f0f724f871e9636)
- [0.3.0 变更记录](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/CHANGELOG.md)
- [白盒工作流文档](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/docs/whitebox-workflow.md)
- [公开 API](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/warden/interface/api/app.py)
- [默认组合](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/warden/interface/factory.py)
- [最新提交 CI](https://github.com/Xinyuan20061/WARDEN/actions/runs/40050294985)
