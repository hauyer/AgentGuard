# I 阶段失败、阻塞与恢复分析

## 记录约定

这里区分研究运行与软件验证。测试使用隔离目录、synthetic fixture 或 Stub；即使
通过，也只能证明相应软件路径，不构成真实 WARDEN、真实模型、真实证据或策略效果
结果。研究运行必须有输入清单、来源、完整 source SHA、evaluation ID 和报告工件
ID。当前只有一项 synthetic control，身份信息见下表与 `I_RESULTS.md`；没有真实观察
或干预运行。

## 已验证的软件路径与 synthetic control

| 项目 | 输入与来源 | 完整代码 SHA | 验证结果 | evaluation ID | 报告工件 |
| --- | --- | --- | --- | --- | --- |
| Loop 注册/中断/重试、旧 SQLite schema、工件写入后索引失败 | pytest 隔离数据库与注入故障；synthetic software test | `aa46f4c0461ba18b1a8b8d7728f9ebe4fa4f92a9` | 定向四组测试合计 91 passed；远端调用未执行 | N/A（软件测试） | N/A（pytest 输出；无研究报告工件） |
| 新 WARDEN 证据进入下一轮动作选择 | HTTP transport Stub；伪造的 `artifact://stub/...` 引用 | `aa46f4c0461ba18b1a8b8d7728f9ebe4fa4f92a9` | Stub 闭环测试通过；引用带入下一轮；不代表真实证据 | N/A（软件测试） | N/A（pytest 输出；无研究报告工件） |
| 五策略 × 2 次 CLI/API 对照 | manifest SHA `7b2919dba61deb3dae36f29cb8e0b2e6d9a31bd80e1d11fb8fd1a1b3ec5968f7`；`synthetic` | `ecd1cc3a74ee2ff5ee3e3595c382c6bb8f9affcf` | 10 次完成；新 API App 列表/详情均 HTTP 200；Gateway 发出 0 条 WARDEN evidence；不含真实 WARDEN/模型 | `eval-eval-2e181b7fb8` | [`5bf54f35…4fb234.json`](artifacts/i6-synthetic-control-v1/5bf54f35c6364a14f67682d9e09f9ac5c18a9a01dda68c507026c4995a4fb234.json) |

表中软件测试使用 synthetic fixture/Stub；主比较的输入、来源与指标均见
`I_RESULTS.md`。完整 source SHA 是运行代码快照标识，不替代输入 manifest 哈希。

## 恢复语义与剩余风险

- 幂等注册先写入 SQLite；`not_started` 可安全启动。旧 schema 行迁移为 `unknown`，
  防止把旧的 `CREATED/pending` 错当成从未运行。
- 在调用调查循环之前持久化 `running`。进程崩溃后，同一幂等键返回
  `BLOCKED / reconciliation_required`，不盲目再次提交外部动作。
- 无报告工件的预算字段返回 `null`。发生不确定中断时，不猜测已消耗预算，不把未知
  成功率或动作数填成零。
- 工件写入后数据库更新失败时，登记仍保留 `running`，重启后的同键请求不会重放。
  工件可能存在但当前登记无法证明其与完整 Loop 结果一致；需要人工对账。
- 本地进程锁与文件锁阻止同键并发执行；跨进程锁竞争时只返回当前持久化状态，不
  启动第二个循环。
- WARDEN 的公开接口虽有能力、合同、计划、运行和证据读取路径，但预检发现其计划/
  运行状态为内存状态，缺少足以证明远端重启恢复与提交幂等的合同。故公共 Loop 的
  observation 仍默认关闭。超时或未知远端状态不能自动标为完成，也不能自动重新
  POST；Live 阶段保持 **BLOCKED**。

## 数据与执行真实性

- 本轮使用的 WARDEN HTTP 测试 transport 是 Stub；引用明确带有 `stub` 标记。
- I6 主比较使用单个人工生成的 synthetic 工作流；其五策略聚合结果相同，不能用于
  真实策略排序或泛化结论。
- 未连接或执行真实 WARDEN 调查；未产生可报告的真实新增证据。
- 未配置或调用真实 LLM；没有真实模型输出。
- 本文软件测试不是独立研究案例，也不进入任何策略均值或统计分母。

## 后续解除条件

1. WARDEN 为审计计划与运行提供可查询的持久化状态、稳定请求标识和明确的幂等/对账
   语义，并通过重启和响应丢失测试。
2. Operator 授权、预算预留、证据引用来源以及运行终态可由公开 API 验证。
3. 真实观察前先通过相同合同的 Stub 与故障注入测试，再在获授权环境中逐项验证；
   实际结果与 Stub 分开登记。
4. 后续研究报告补齐输入哈希、数据来源、完整 source SHA、evaluation ID 和报告工件
   ID 后才能加入 `I_RESULTS.md`；真实观察与 synthetic control 分开登记。
