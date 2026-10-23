# 本地 7B 多智能体观察链路

日期：2026-10-24。运行配置位于工作区 `integration-data/`，真实模型与私有
StegoPot 轨迹不纳入 Git。模型为用户本地 Ollama
`qwen2.5:7b-instruct-q4_K_M`；PyTorch 白盒开发使用 `nanogpt` Miniconda。

WARDEN 通过 FRACTAL-Bench 冻结合同比对和 StegoPot `core.llm` 运行生成
真实模型轨迹。AgentGuard 只访问 WARDEN HTTP；没有直接调用 StegoPot 或
读取基准隐藏真值。提交指定条件后，异步接口约 0.3 秒返回 `queued` 和
`run-3cd6eef3886b4660bc671bf902901562`。StegoPot 运行
`20261023T162227Z-9d9ca3dc` 完成该条件的 3 条消息、4 次模型调用；
WARDEN 给出 `observational` 证据工件，AgentGuard 重查并将结果写入 SQLite
（`finished`、8 条状态事件，因果状态 `not_assessed`）。

第二次真实运行 `run-3ee7cf5d82fe4f498a11e3015719004f` 验证了观察证据
的通信边元数据：指定条件公开 3 条消息，每条仅有消息 ID、发送/接收节点和
轮次，不含正文或基准真值。AgentGuard 按原工作流恢复并持久化终态；这仍是
观察性证据，不构成风险或因果结论。

**实验总体并未成功**：8 条件中 6 个完成、2 个失败，StegoPot 汇总状态
`failed`；总体 `benign_accuracy=0.0`、`coordinated_target_rate=0.0`。
因此这次运行只验证真实观察链路，不能证明风险识别、因果归因或定向防御有效。
WARDEN 仍未配置 Activation Backend、Probe/SAE 工件和受控干预。

先前同步调度时 WARDEN 虽完成同类条件，AgentGuard 却因 HTTP 超时将提交
标记为不确定。WARDEN 已改用有容量限制的本地异步调度器；它不具备重启恢复，
生产部署仍需持久化队列。AgentGuard 原有 SQLite 幂等和恢复逻辑保留。
