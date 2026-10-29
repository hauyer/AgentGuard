# Study 与 WARDEN 执行映射

新增兼容操作：

- POST /v1/studies/{study_id}/runs，正文仅 arm_id，必需 Idempotency-Key 和已有研究写权限。
- GET /v1/studies/{study_id}/runs，读取已有持久工作流映射。
- GET /v1/studies/{study_id}/runs/{workflow_id}，查询、对账并校验授权公共摘要。

提交复用 ResearchRegistry 与 WardenLifecycleService。Study/Arm 不是 Plan/Run：
稳定的工作流键及 journal 保存关联。相同 Study 与幂等键改变实验臂返回 409。
所有执行字段来自已冻结的注册记录，客户端不能传入代码或模型路径。
结果校验内容哈希、run/plan/study/arm/protocol/contract、模型和分词器 revision、切分、种子与轨道。
数据版本采用 SHA-256 时同时校验数据摘要。工件导入复用现有内容寻址存储。

local_real_model 仅说明真实本地模型。当前 M 轨道采集没有可核验的配对干预结果，返回
analysis_status=incomplete 与 NO_VERIFIED_PAIRED_OUTCOMES；现有 measured 写入门控继续拒绝
未验证的客户端数字。不得据此宣称 BER、通信成功或防御有效。

就绪状态反映已配置能力，不代表实验完成；UTC 使用 wall-clock，TTL 独立使用 monotonic。
RC1 历史 fixture 保留；新增 RC2 fixture 与对比测试确认请求 Schema 相同、工件下载媒体类型修复。
原有 QWB 操作固定 fixture 不变，新增操作进入更新的 OpenAPI 快照。

部署沿用 AGENTGUARD_DATA_DIR、AGENTGUARD_WARDEN_URL、AGENTGUARD_WARDEN_RESEARCH_TOKEN、
AGENTGUARD_API_TOKEN 与 AGENTGUARD_OPERATOR_API_TOKEN。BFF 需要同时配置读取和操作者凭据。
启动：python -m uvicorn agentguard.api.app:create_app --factory --host 127.0.0.1 --port 18081 --ws none

本次联合验收详见 App 的 docs/integration/THREE_REPO_E2E_ACCEPTANCE.md。

本次真实浏览器 Run：run-48fec68756304a73a8025388e5d35975；24 条 M 条件；SHA-256 c54c53ff4db8aeca41e4048521a937d18da8b715906eefabe7a4193102c9040b。配对分析 incomplete，四类干预效果 NOT_RUN。
