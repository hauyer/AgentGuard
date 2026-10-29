# Stego Sandbox 公共研究视图 v1

本地增量合同：93 paths / 107 operations。历史 `openapi-1.1.0rc5.json` 保留；新快照为 `openapi-stego-sandbox-v1.json`。旧 102 个操作及其所有组件 Schema 有逐字段相等回归，不靠路径存在性替代兼容证明。API 仍使用现有 RC5 版本字符串，精确实现由 App 锁定的实际 Git commit 与快照 SHA 标识。

新增五个操作：

| 操作 | 行为 |
|---|---|
| GET /v1/studies/{study_id}/runs/{workflow_id}/visualization | 校验摘要和事件的哈希、身份及分母，再投影确定性图谱；可用 through_seq 回放 |
| GET .../timeline | after_seq >= 0、limit 1..200；游标越界 422；返回 next_seq/last_seq/has_more/status |
| GET /v1/studies/{study_id}/comparison | 固定模型/权重/训练/执行器/协议/数据/分割/种子和 test 家族的配对观察，沿用原 Bootstrap/置换/多重比较 |
| POST /v1/studies/{study_id}/run-batches | 先写不可变 stego.batch/1 清单，按冻结 Study 的所有 arm 依次调用原提交服务；每臂独立稳定幂等键 |
| GET /v1/studies/{study_id}/run-batches | 按 Study 过滤并校验已保存清单；limit 1..100、offset >= 0；响应丢失时只读对账 |

读操作沿用全局读鉴权，写操作沿用 research operator 鉴权与审计；POST 批次必须带 Idempotency-Key。App BFF 进一步验证项目角色、Study 关联、CSRF。批次提交没有模型线程；真实执行仍在 WARDEN 独立 Worker。部分提交或传输错误不在服务中自动重试；同密钥显式对账可复用已有工作流。

事件只允许公开标量白名单：消息长度/摘要、行为读出、坐标、Probe 语义和来源。不接受秘密正文、激活、任意路径。事件必须 UTC、单调 seq/时间、唯一 event_id 和一致 study/run/arm。消息事件要求完整消息/源/目标/信道身份。公开摘要各行为指标必须从逐家族结果重算；未执行、未解码和 BER 缺失分别保留。

通信边仅来自消息事实；Probe 只属于独立证据层，不能造边。隔离、擦除和 Patch 覆盖仅在执行事件证明 applied 后出现；图谱不声明 effective。M 轨道没有 S 消息时返回空图和明确原因。

比较是受控词表的小样本观察。缺少 baseline 或任一冻结实验臂时 incomplete；不填零、不选择最佳方法、不生成 causal_validated。正常任务效用来自独立未干预读出；非共享生成路径上的任务保持证明。Path Patching 未支持。
