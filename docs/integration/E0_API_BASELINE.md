# E0 API 基线（真实调查服务化）

日期：2026-10-25。分支 `feature/e-investigation-service-integration`
（自 D7 `3c34931` 创建）。快照：`openapi-1.1.0rc1-e0.json`（76 paths，
`info.version=1.1.0rc1`）。

## 1. 基线核验结果

| 检查 | 结果 |
| --- | --- |
| Git | 工作区干净，HEAD=`3c34931`（与指令基线一致，无未合并新工作） |
| pytest | 397 收集 / 0 失败 / 6 跳过（torch 1 + live WARDEN 类 5；本机未配置 live 栈，**live 测试本轮未运行**，如实记录） |
| Ruff / compileall / 架构审计 | 全部通过 |
| build + wheel | sdist/wheel 构建成功；`scripts/verify_wheel_install.py` 干净安装验证通过（版本 1.1.0rc1 一致，CLI/SDK 从 venv 导入） |
| 版本 | pyproject `1.1.0rc1`（E 轮不变更版本，E6 视验收结果决定） |

## 2. 既有公共合同清单（E 轮消费方）

全部既有端点要求：`X-AgentGuard-Token`（未配置 token 时鉴权关闭）、
逐响应 `X-Request-ID`、422 脱敏（不回显输入）、分页
`items/count/offset/limit/next_offset`、错误体 `{"detail":{"error":CODE}}`。

| 端点 | 方法 | 关键参数 | 返回核心字段 | E 轮关系 |
| --- | --- | --- | --- | --- |
| `/v1/investigations` | POST,GET | status, limit, offset | 工作流摘要分页 | 不变（base_request_id 的来源） |
| `/v1/investigations/{request_id}` | GET | — | 状态/计划/运行 ID/结果摘要 | 不变 |
| `/v1/investigations/{request_id}/interaction-graph` | GET | run_id, include_findings | nodes/edges/candidate_paths + D2 finding_ids/hypothesis_ids 挂载 | 不变（E1 与其共享发现 ID 空间） |
| `/v1/investigations/{request_id}/timeline` | GET | after_event_id | journal 事件投影 | 不变 |
| `/v1/evidence/investigations/{request_id}` | GET | — | GovernedEvidence 条目 + 工件能力字段 | 不变 |
| `/v1/experiments` | GET,POST(compare) | status, scenario_id, data_source… | 实验索引/描述性对比 | 不变 |
| `/v1/reports` | GET,{run_id} | status, scenario_id… | 报告索引/详情 | 不变 |
| `/v1/system/readiness` | GET | force_refresh | 11 层检查 | 不变 |
| `/v1/investigations/options` | GET | force_refresh | 模式/契约/资源/运行时标识 | 不变（E2 创建循环的预检来源） |

D 轮新能力（风险发现、调查循环、防御规划、策略对比）**零路由**：数据在
Application Service 与内容寻址工件中。E1/E2 的职责就是把其中两块服务化。

## 3. 向后兼容承诺

E 轮新增路径全部为加法（E1 risk-analysis、E2 investigation-loops 只读族）；
既有 76 paths 字段语义零改动，C0 基线加法性回归
（`tests/test_qwb_contract_compat.py`）持续守护。
