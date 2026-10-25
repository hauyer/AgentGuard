# E0 QWB-App 需求映射（只读核对）

日期：2026-10-25。核对对象：AgentGuard-QWB-App（只读；B0–B19 已完成）。

## 1. App 侧访问架构（决定 E1/E2 的权限设计）

- BFF 是 AgentGuard 唯一访问点（`backend/app/integrations/agentguard_client.py`），
  前端不直接持有上游地址/令牌。
- B19 授权模型（`backend/app/services/resource_access.py`）：
  LIVE 模式下调查按**项目成员制**鉴权（`project_investigations` 关联 +
  `project_members` 角色），未关联项目仅 admin 可见；`run_id` 寻址资源经本地
  run bridge 解析，未知 run_id 对非 admin 拒绝（不泄露存在性）。
  Demo 命名空间登录即用、免项目。
- 结论：AgentGuard 的 request_id 级授权由 **App 侧执行**；AgentGuard 侧继续
  用三 token 模型（API/operator/approval）。E2 的 POST 创建循环因此必须走
  **operator 或独立审批凭证**，普通 API token 不自动获得创建权。

## 2. 页面 → 数据需求 → E 轮供给

| App 页面/服务 | 现有数据源 | 缺口 | E 轮供给 |
| --- | --- | --- | --- |
| InvestigationWorkspacePage | investigations 系列 | 风险候选/假设无接口 | **E1 risk-analysis**（挂载在既有 request_id 授权下） |
| InteractionGraphPage | graph_service → interaction-graph | 边上已可带 finding_ids（C 轮 include_findings），但 App 未启用 | 无新端点需求；App 升级 pin 后开参数即可 |
| EvidencePage | evidence_service | 不变 | 无 |
| ExperimentsPage | experiments_service | 不变 | 无 |
| ReportsPage | reports_service | 不变 | 无 |
| 调查过程可视化（新需求） | 无 | 多轮循环的决策/证据变化不可查 | **E2 investigation-loops 只读族** |
| B19 resource_access | 项目成员制 | 循环与 base_request_id 同键，可复用既有项目关联授权 | E2 文档中写明映射规则 |

## 3. App 侧升级路径（其仓库自行执行）

1. 重 pin AgentGuard 至 E 轮提交 + 刷新 OpenAPI 快照；
2. BFF 增加客户端方法（SDK 同名映射，见 E5）；
3. InvestigationWorkspacePage 接 risk-analysis；
4. 新增调查过程视图消费 investigation-loops 只读族；
5. resource_access 对 loop_id 的授权规则：**loop 继承 base_request_id 的项目
   关联**（AgentGuard 返回的 loop 记录携带 base_request_id，App 端零新增授权表）。

## 4. 明确不做

- 不在 AgentGuard 侧复制项目/成员模型（App 的 B19 是权威）。
- 不为 Demo 命名空间提供循环创建（POST 需授权审计，Demo 数据不进真实链路）。
- 不改 App 仓库任何文件。
