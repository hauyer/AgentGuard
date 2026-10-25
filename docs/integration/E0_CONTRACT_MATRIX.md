# E0 合同矩阵（现有 → E 轮计划）

日期：2026-10-25。状态标记：`existing`=已发布且 E 轮不变；`new-E1`/`new-E2`=E 轮计划新增；
`blocked`=明确阻塞不开放。

## 1. 端点矩阵

| 端点 | 状态 | 鉴权 | 数据来源 | 备注 |
| --- | --- | --- | --- | --- |
| `/v1/investigations`(POST,GET) `/{request_id}` | existing | API token | journal | 不变 |
| `/v1/investigations/{id}/interaction-graph` | existing | API token | journal→投影 | `include_findings` 已有（C/D 轮） |
| `/v1/investigations/{id}/risk-analysis` | **new-E1** | API token | journal→RiskFindingService | 只读；复用 D2 服务，零新检测逻辑 |
| `/v1/investigation-loops`(POST) | **new-E2（受限）** | **operator/approval**（普通 API token 不够） | loop 服务 | 创建会提交新 WARDEN 审计 → 独立授权审计后才开；无安全方案则保持只读记录开放、创建明确 blocked |
| `/v1/investigation-loops`(GET) `/{loop_id}` `/decisions` `/evidence-changes` `/budget` | **new-E2** | API token | 内容寻址 loop 报告工件索引 | 只读；不读工件内部路径，响应为投影 |
| `/v1/strategy-evaluations`(GET 索引) | **new-E4（候选，待定）** | API token | 对比报告工件 | 只在有消费需求时评审上线（同 E0 原则） |
| 其余 C 轮端点 | existing | 同 C 轮 | — | 零改动 |

## 2. ID 语义矩阵（E2 必须区分，不混用）

| ID | 权威产生处 | 出现在 |
| --- | --- | --- |
| `request_id`（base_request_id） | App 生成 → journal | 全部调查端点 |
| `investigation_loop_id` | E2 创建/重放 | loop 只读族 |
| `decision_id`（round 序号 + loop_id 构成） | 循环执行 | decisions |
| `action_id` | 循环服务候选集 | decisions.candidate_actions/selected |
| `run_id` | WARDEN | journal/实验索引/审计记录 |
| `artifact_id` | 本地内容寻址存储 | 各报告字段（opaque，非路径） |

## 3. 权限/隐私边界（全 E 轮不变量）

- 三 token 模型不变；POST 创建循环需要 operator 级凭证 + 幂等键 + 参数一致性校验。
- 响应不出现：消息正文、sender/recipient 明细、系统提示词、隐藏真值、
  file:///本地路径、未校准分数的概率化措辞。
- 无数据时返回 `available=false` + `insufficient_evidence`/unavailable 语义，
  不造数；422 不回显输入；`X-Request-ID` 逐响应回传。

## 4. 兼容性守护

- `tests/test_qwb_contract_compat.py` 的 C0→当前纯加法回归持续运行；
  E1/E2 合并前把新路径加入该测试的 expected 新增清单。
- OpenAPI 快照随 E6 再导出一次（`openapi-1.1.0rc1-e0.json` 保留为 E 轮起点）。
