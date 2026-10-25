# I6 多策略对照实验结果

## 结果等级

本文件记录一项 **synthetic control** 比较。它通过真实 AgentGuard CLI 执行五种策略、
写入 SQLite 和内容寻址报告工件，再由全新 API App 查询索引与报告。基础调查是人工
生成的 WARDEN-journal 形状 fixture；没有运行 WARDEN、LLM 或真实模型，也没有产生
真实证据。synthetic/stub workflow 不会被 `EvidenceGateway` 输出为 WARDEN 来源的
证据条目。本结果只说明当前软件在此受控合成输入上的行为，不代表真实调查效果。

## 运行身份与来源

| 字段 | 值 |
| --- | --- |
| 数据集 | `i6-synthetic-control-v1` |
| 输入 manifest | [`i6_synthetic_case_manifest.json`](fixtures/i6_synthetic_case_manifest.json) |
| manifest SHA-256 | `7b2919dba61deb3dae36f29cb8e0b2e6d9a31bd80e1d11fb8fd1a1b3ec5968f7` |
| 来源类别 | `synthetic`（manifest 声明；由 [`seed_i6_synthetic_case.py`](../../scripts/seed_i6_synthetic_case.py) 生成人工日志） |
| 案例 / 基础调查 | `i6-synthetic-control-001` / `i6-synthetic-control-001` |
| 分区 | `test`（仅本合成 fixture 的合同值，不是外部测试集） |
| 完整 source commit | `ecd1cc3a74ee2ff5ee3e3595c382c6bb8f9affcf` |
| comparison ID | `eval-2e181b7fb8` |
| evaluation ID | `eval-eval-2e181b7fb8` |
| 执行时间 | `2026-10-25T15:09:30.885820Z` |
| 配置 | 5 策略 × 2 次技术重复；`max_actions=6`、`max_seconds=60`、`max_model_calls=4`、`no_new_evidence_rounds=3` |
| SQLite → 重启 API | 新 API App 的列表和详情响应均为 HTTP 200；索引、详情和比较工件字段一致 |
| 比较报告工件 | [`5bf54f35…4fb234.json`](artifacts/i6-synthetic-control-v1/5bf54f35c6364a14f67682d9e09f9ac5c18a9a01dda68c507026c4995a4fb234.json) |

比较报告 artifact ID 为 `5bf54f35c6364a14f67682d9e09f9ac5c18a9a01dda68c507026c4995a4fb234`；文件 SHA-256 与 ID 一致。每个运行的逐轮 Loop 报告也已保存并按 artifact ID 命名：

- `fixed_order`：[`9b9cc167…3c029.json`](artifacts/i6-synthetic-control-v1/9b9cc16797e4848ee7c2018a9fc84ccf87b41a32e053970cd268ba318ce3c029.json)、[`9b782ff1…8bda9.json`](artifacts/i6-synthetic-control-v1/9b782ff1ab0d66db4b989123423adf112ab0ab97662299d18770a69ae528bda9.json)
- `risk_score`：[`ca5241b7…aad51.json`](artifacts/i6-synthetic-control-v1/ca5241b769ebe2a3390678a3e03fb9c3c54912bf48450c6195914e6abbfaad51.json)、[`08c2110f…5d4a.json`](artifacts/i6-synthetic-control-v1/08c2110fd9b6170385aeafa2c86a14492ce2e6ea977cad1358e8d1575ad75d4a.json)
- `information_value`：[`4e3bfd5d…8585.json`](artifacts/i6-synthetic-control-v1/4e3bfd5d7641f10bc7e207ad6cb8f71a1b109432b8e9909392da3605b8fd8585.json)、[`0e9bc04b…af86.json`](artifacts/i6-synthetic-control-v1/0e9bc04b51bd3b123e09674c841ea41a9f7dced2b382d295a86a09065b4daf86.json)
- `experience`：[`d433a561…10bd7.json`](artifacts/i6-synthetic-control-v1/d433a561df6ae72394966cd3ee8bb99c240c29b1eefd07375d516355f8710bd7.json)、[`317cf5c9…11194.json`](artifacts/i6-synthetic-control-v1/317cf5c978b66e99d4b20c2112420470cac7aaf727a108d27709df5b80811194.json)
- `llm`：[`7b0db431…d9210.json`](artifacts/i6-synthetic-control-v1/7b0db4318ba41499e968c184809933b1bffdeb90e34a54e1157ec9f59efd9210.json)、[`32c19d6a…f85fd8.json`](artifacts/i6-synthetic-control-v1/32c19d6a3790febfa5bde4607205ee04bd23bf936b264c5835670dfbd7f85fd8.json)

逐轮报告各自的 SHA-256 与文件名中的 artifact ID 一致；每份 Loop 报告也带有
`source_kind=synthetic` 限制说明。manifest 的 `source_kind` 是调用方声明，不是
独立验证；此处的真实性由已提交的合成输入与 seed 脚本限定。

## 合同指标

| 策略 | 完成 | 平均轮数 | 首证据步数 | 有效动作比 | 阻塞比 | 冗余动作 | 证据引用数 | 模型调用 | 降级率 | 平均耗时（秒） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fixed_order` | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 0.0 | 8.0 | 0 | 0.0 | 0.047 |
| `risk_score` | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 0.0 | 8.0 | 0 | 0.0 | 0.054 |
| `information_value` | 2/2 | 6.0 | 2.0 | 1.0 | 0.0 | 0.0 | 8.0 | 0 | 0.0 | 0.054 |
| `experience` | 2/2 | 6.0 | 2.0 | 1.0 | 0.0 | 0.0 | 8.0 | 0 | 1.0 | 0.093 |
| `llm` | 2/2 | 6.0 | 2.0 | 1.0 | 0.0 | 0.0 | 8.0 | 0 | 1.0 | 0.079 |

10 次运行均以 `action_budget_reached` 停止，无阻塞或失败。`experience` 因空经验库
降级；`llm` 因没有 provider 降级，二者的 `model_calls_used` 均为 0。平均耗时是本机
合成执行记录，仅供还原本次运行。

## 解释边界

- 五种策略在这一个合成案例、两次技术重复中的汇总结果相近；这不能证明策略等价，也
  不能说明任何策略优于其他策略。该案例没有独立案例 family，重复不是独立样本。
- `steps_to_first_evidence` 是循环合同按新增引用记录的步数；本 synthetic fixture 的
  引用不是真实证据，不能解释为首次真实证据发现。
- 没有独立真值标注，风险识别指标保持 `null`；没有真实干预，防御效果指标保持
  `null`。不报告 Precision、Recall、F1 或防御成功率。
- 本轮没有真实 WARDEN 执行、真实模型调用或真实新增证据。I5 Live 阶段仍为
  **BLOCKED**；详见 `I_FAILURE_ANALYSIS.md`。

## 可复现命令

在新的临时目录中依次运行：

```powershell
python scripts/seed_i6_synthetic_case.py --data-dir .tmp/i6-synthetic
python scripts/run_investigation_eval.py `
  --data-dir .tmp/i6-synthetic `
  --dataset-manifest docs/research/fixtures/i6_synthetic_case_manifest.json `
  --strategies fixed_order,risk_score,information_value,experience,llm `
  --repetitions 2 --max-actions 6 --max-seconds 60 `
  --max-model-calls 4 --partition test `
  --out .tmp/i6-synthetic/report.md
```

该命令生成新的 comparison/evaluation ID 和 artifact IDs；上表 ID 对应的是
`ecd1cc3a74ee2ff5ee3e3595c382c6bb8f9affcf` 上保存的这次执行，不会被复现命令覆盖。

## 旧 D5 摘要

旧 `docs/research/RESULTS.md` 的 D5 摘要未提供 evaluation ID；所列 artifact 当前
不可读取，source SHA 只有短值。它不纳入上述 synthetic 结果或任何 live 结果。
