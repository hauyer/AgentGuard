# AgentGuard 研究工作流：预注册、配对分析与可选 LLM 调查

## 1. 预注册的目的与边界

`POST /v1/studies` 或 `agentguard study register` 会将研究问题、主要假设、主要终点、方向、显著性水平、匹配变量、样本量目标、排除标准、停止规则、缺失值规则、统计方法、随机种子与代码版本写入 SQLite 注册表。注册记录不提供原位更新接口；研究方案发生变化时，应新建注册并在论文中说明偏离，而不是覆盖旧记录。

系统对规范化注册内容计算 SHA-256，并可通过 `GET /v1/studies/{study_id}/integrity` 校验。该哈希只能检测内容变化，不是可信时间戳、第三方公证或防管理员篡改机制。

## 2. 配对数据分析

分析请求必须包含预注册的主要终点和全部登记的次要终点，以维持预先定义的多重比较家族；未登记终点会被拒绝用于确认性分析。系统对提交的 outcome 数组进行规范化 SHA-256 计算；若调用者提供 `data_sha256`，它必须与计算结果一致。每个终点包含等长的 control、treatment、case_families 数组。默认 `clustered_paired`：

- 先计算每个配对单位的 treatment − control 差值。
- 在每个 case family 内对差值求均值。
- 以案例家族为独立单位计算等权平均处理效应。
- Bootstrap 重采样整个案例家族，而不是把同一案例的 episode 当作独立样本。
- sign-flip permutation 在家族级执行；家族数不超过 16 时穷举全部符号组合，否则采用固定随机种子的 Monte Carlo 抽样。
- 对登记的多个终点按预注册规则执行 Holm 或 Benjamini–Hochberg 校正。

如果只关心独立配对单位且没有家族内相关，可在预注册时选择 `paired`。选择后不能因为结果不显著而改用另一种方法并继续称为确认性分析。

## 3. API 示例

创建注册：

```bash
curl -X POST http://127.0.0.1:8000/v1/studies \
  -H 'Content-Type: application/json' \
  --data @examples/study_registration.example.json
```

分析数据应按 `schemas/study-analysis-request.schema.json` 提交：

```bash
curl -X POST http://127.0.0.1:8000/v1/studies/STUDY_ID/analyses \
  -H 'Content-Type: application/json' \
  --data @examples/study_analysis.example.json
```

CLI：

```bash
agentguard study register --config examples/study_registration.example.json
agentguard study list
agentguard study integrity --study-id STUDY_ID
agentguard study analyze --study-id STUDY_ID --data examples/study_analysis.example.json
agentguard study analyses --study-id STUDY_ID
```

这些示例数组是教学用合成数据，不能作为论文结果。提交真实结果时必须把原始运行工件、模型版本、代码 revision、数据哈希和匹配条件保存在实验记录中。

## 4. 可选 LLM Investigator

默认仍使用确定性调查排序，不发起外部模型调用。若显式使用 `agentguard demo --llm-investigator` 或 `agentguard run --llm-investigator --config ...`，AgentGuard 会通过 OpenAI-compatible Chat Completions 调用模型，默认指向 DeepSeek API。配置变量：

- `DEEPSEEK_API_KEY`（也支持 `OPENAI_API_KEY`）
- `AGENTGUARD_LLM_BASE_URL`（默认 `https://api.deepseek.com/v1`）
- `AGENTGUARD_LLM_MODEL`（默认 `deepseek-chat`）
- `AGENTGUARD_LLM_TIMEOUT`（默认 20 秒）

模型只会看到候选 finding 的 ID、类型、分数、目标引用、证据引用和简短理由；不会获得原始对话全文、隐藏真值或工具执行能力。它只能从现有 finding ID 中选择优先调查对象。伪造 ID、无效响应或 provider 错误会被拒绝，并回退到确定性调查器。最终干预仍由本地策略闸门校验，模型无权授权或执行动作。

外部调用会把上述候选元数据发送给所配置的模型服务。只有在数据使用符合项目授权和服务条款时才启用。`agentguard agents status` 与 `GET /v1/agents/status` 只检查环境变量，不会发起网络请求。REST 服务需要显式设置 `AGENTGUARD_LLM_INVESTIGATOR=1` 才会启用此功能；默认关闭。

## 5. 解释限制

- 预注册和 p 值校正改善分析纪律，但不能修复错误的随机化、失配对照或选择偏差。
- 家族数少时，精确置换检验的 p 值粒度较粗；应同时报告效应量、区间、家族数量与失败案例。
- API 接收的配对数组仍由调用方提交。声明 `evidence_mode=measured` 不会自动证明数据来自真实测量。
- 真实 FRACTAL-Bench / StegoPot 配对重放仍要求经验证的专用 executor；当前文件级 `run_file` 不能替代反事实重放。
- LLM 调查建议是研究辅助，不是因果证据，也不代表多个独立自主 Agent 已完成真实模型消融。


## 6. 秘密路径中介贡献与路径迁移

研究想法将问题拆为“秘密可解码性”和“秘密是否真正通过候选节点传播”。AgentGuard 0.5.0 增加了两类分析接口，但不会假装自己已具备白盒运行时 hook：

- `POST /v1/attribution/nodes/analyze` 或 `agentguard attribution mediation --config ...`：输入同一匹配样本在正常运行和候选节点干预后的 Secret Recovery、可选的 Collusion Success，以及正常任务效用。计算 `M(Z_i) = R_0 - R_i` 的家族级均值、Bootstrap 区间和家族级 sign-flip 检验。正常任务效用单独报告，以便发现“恢复率下降只是因为模型整体坏掉”的情况。
- `POST /v1/attribution/path-shift` 或 `agentguard attribution path-shift --config ...`：输入同一归因协议下优化前后的非负节点贡献，计算归一化贡献分布的 Total Variation Distance、Cosine Distance、Top-K Jaccard 和 Spearman 排名相关。

示例文件 `examples/attribution_node.example.json` 和 `examples/path_shift.example.json` 都是 synthetic，仅用于验证计算与接口。中介贡献为正不自动证明因果；它只有在真正的 matched activation/token patching、sham/无关节点控制、继续生成和独立效用评价有效时才可被解释为因果证据。路径贡献分布变化也只是描述性迁移指标，必须固定秘密分布、任务、拓扑、探针和归因协议，并对多种候选节点进行多重比较校正。

当前 AgentGuard 未直接实现白盒模型的 activation capture/patch/continue-generation 运行时。要将这组分析提升为真实机制结论，仍需在固定版本的 StegoPot / 模型适配器中打通：`forward → capture → patch → continue generation → outcome`，并保存原始激活与重放工件。


## 7. 本地白盒激活干预

若已经加载了授权的 PyTorch 模型，可使用 `WhiteBoxActivationAdapter` 在指定模块路径采集激活，并在继续生成时执行 zero、reference activation replacement 或 add-noise 干预。该能力为本地 Python 接口，不暴露为 HTTP 端点，以避免 API 服务端加载调用方指定模型路径。完整示例、KV-cache 限制与研究验收要求见 [`WHITEBOX_INSTRUMENTATION.md`](WHITEBOX_INSTRUMENTATION.md)。当前只用 toy model 做了自动测试，尚未完成真实开源 LLM 的隐写共谋实验。
