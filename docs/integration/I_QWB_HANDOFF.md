# I7: QWB-App 应用交接文档

## 固定版本

- AgentGuard 分支: `feature/i-reliability-and-live-validation`
- 最新提交: 见 git log
- Python: 3.12
- OpenAPI schema: 见 `/openapi.json`

## 公共接口清单

### 调查相关
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/investigations/{request_id}/risk-analysis` | 风险分析 |
| GET | `/v1/investigations/{request_id}/interaction-graph` | 交互图谱 |
| GET | `/v1/investigations/{request_id}/timeline` | 调查时间线 |

### 调查循环
| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/v1/investigation-loops` | 列表 | 只读 Token |
| GET | `/v1/investigation-loops/{loop_id}` | 详情 | 只读 Token |
| GET | `/v1/investigation-loops/{loop_id}/decisions` | 决策记录 | 只读 Token |
| GET | `/v1/investigation-loops/{loop_id}/evidence-changes` | 证据变化 | 只读 Token |
| GET | `/v1/investigation-loops/{loop_id}/budget` | 预算 | 只读 Token |
| POST | `/v1/investigation-loops` | 创建 | Operator Token + 幂等键 |

### 策略评价
| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/v1/research/strategy-evaluations` | 评价列表 | 只读 Token |
| GET | `/v1/research/strategy-evaluations/{evaluation_id}` | 评价详情 | 只读 Token |

## 关键参数

### POST 创建 Loop 必需头
- `X-AgentGuard-Operator-Token`: Operator 凭证
- `X-Loop-Idempotency-Key`: 幂等键（缺失返回 422）

### Loop 列表查询
- `GET /v1/investigation-loops?base_request_id=...`

## 安全边界

- 普通 API Token 不能创建调查循环
- 所有 GET 接口不触发新实验或模型调用
- 不暴露系统提示词、隐藏真值和秘密字段
- 不接受任意文件路径参数
- 损坏工件返回 410，不产生 500 堆栈
- 详情返回的 `report_artifact_id` 与索引一致

## 数据来源

| 数据类型 | 来源 | 说明 |
|----------|------|------|
| 调查记录 | Recorded Observational | 历史 WARDEN 观察记录 |
| 策略评价 | Synthetic / Recorded | 取决于执行环境 |
| 真实 Live 实验 | ❌ 未执行 | 需 WARDEN 环境 |

## 已知阻塞

- 无真实 WARDEN 环境，策略评价数据为 synthetic/recorded
- 公共循环观察动作默认关闭
- 经验策略无真实经验库数据
