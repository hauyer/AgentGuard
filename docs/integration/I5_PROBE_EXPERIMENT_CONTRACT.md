# WARDEN 白盒 Probe 实验合同

**状态：** AgentGuard 配置合同已对齐；真实模型运行 `NOT_RUN`，Live 执行 `BLOCKED`。本地候选版本 `1.1.0rc3`，基于 `63fe1b5662df93fbf06750ecf09c14ee9e6bd5cc` 且包含未提交更改。  
**WARDEN 源码：** `381fc08170386a2dbd4c2ded3f0f724f871e9636`，版本 `0.3.0`。  
**上游 JSON Schema SHA-256：** `cee132eed53bf81d64da68e68bcda881f0aa31f246bc33d6a96a86ccdc1fb0ae`。

## 合同来源

AgentGuard 的 `WardenWhiteboxProbeExperimentConfig` 与固定版本的
`whitebox-probe-experiment.schema.json` 对齐。上游原始 Schema 的只读快照保存在
[`warden-whitebox-probe-experiment-1.upstream.schema.json`](../../schemas/warden-whitebox-probe-experiment-1.upstream.schema.json)，AgentGuard 导出的 Pydantic 合同为
[`warden-whitebox-probe-experiment.schema.json`](../../schemas/warden-whitebox-probe-experiment.schema.json)。

上游正式配置字段包括：实验/模型 ID、模型路径与 revision、tokenizer、device/dtype、量化、
数据集路径、输出目录、Agent ID、层/module/component/token 归约目标以及 Probe 正则、迭代数
和随机种子。AgentGuard 只验证这个配置形状并计算配置指纹；不会读取该路径、加载模型、采集
Activation、训练 Probe 或运行 WARDEN CLI。

来源：[WARDEN 白盒工作流说明](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/docs/whitebox-workflow.md)、
[WARDEN 配置 Schema](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/schemas/whitebox-probe-experiment.schema.json)、
[WARDEN CLI](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/warden/interface/cli.py)。

## 数据隔离与输出要求

- JSONL 数据按 `sample_id`、`family_id`、`split`、`label`、`inputs_by_agent` 和 metadata 组织。
- WARDEN loader 要求同一家族及完全相同的主体输入不能跨 train/validation/test；运行时不随机
  重切分；三个 split 都需要同时含两类标签。
- `label=1` 只表示预注册风险条件，不是模型共谋真值。
- 单 Agent Probe 与联合 Probe 使用同一预处理；只在 train 拟合，在 validation 选层/阈值，test
  只作最终报告。AgentGuard 不复制这段数据加载或 Probe 训练实现。
- 正式结果必须核对固定模型 revision、tokenizer、量化配置、数据 fingerprint、激活/权重哈希、
  收集和训练日志、Probe 报告及其工件引用。缺任何关键引用时不能记为可复现完成。
- Probe 输出最高为 observational/readout evidence。它不单独证明秘密传播路径，也不能提升为
  `causal_validated`。

## 执行门控

固定 WARDEN 仅提供 `warden whitebox-preflight` 与 `warden whitebox-run --phase collect|train|all`
本机 CLI；未提供等价白盒 REST 提交、任务状态、取消或受控工件下载合同。AgentGuard readiness
将 `whitebox_workflow_api_available` 报为 `unsupported`，不能从 CLI 存在推断远程可执行。

当前 AgentGuard/WARDEN 在线环境未配置，本轮没有运行 CLI、真实模型、GPU 或 Probe 训练；这些项
都是 `NOT_RUN`。本合同测试只证明本地字段校验与上游 Schema 快照一致，不作为 Live 或模型测试
通过证据。恢复 Live 门控需要 WARDEN 提供版本化、可从 OpenAPI 验证的白盒任务 API 和真实服务
集成测试，见 [`WARDEN_CAPABILITY_MATRIX.md`](WARDEN_CAPABILITY_MATRIX.md)。
