# 上层平台 A1 阶段完成报告

## 一、完成内容

在现有 `WardenKernelClient` 上实现 URL、重定向、响应大小、JSON 合同、版本
和只读重试检查。`diagnose_kernel` 新增统一的服务/执行能力字段；没有收到
实际响应时保留 `unknown`。现有 CLI 的 doctor、capabilities、resources、
contracts、status 已具备，无需新增第二套客户端或命令。

## 二、代码变化

修改 `adapters/warden.py`、`services/warden_diagnostics.py`、相关合同测试与
诊断测试；新增 `tests/test_warden_transport.py`。刷新
`schemas/warden-audit-request.schema.json` 的 observation 枚举，新增所有已
发布 Schema 与当前 Pydantic 合同的一致性检查。本阶段未删除业务模块。

## 三、架构变化

所有真实审计仍经统一客户端。拒绝 URL 中的用户信息、查询、片段、相对路径
和重定向；远端错误正文不进入用户错误消息，响应限 2 MiB。健康/API v1
核对在首次写请求前完成，工作流每次预检刷新；客户端其余写操作复用最近
核对的版本。没有向 WARDEN 增加路由或修改底层文件。

只读重试通过 `AGENTGUARD_WARDEN_READ_RETRIES=0..3` 显式启用，默认 0。
仅 GET 的临时网络故障或 408/429/502/503/504 可重试；POST 始终一次。
单次连接和读取共用 `AGENTGUARD_WARDEN_TIMEOUT`（1–120 秒）；最大等待受
尝试次数与超时共同约束。合同错误和无效 JSON 不重试。

诊断提供 `kernel_status/api_compatible/runtime_available/activation_available/
contract_source_available/available_contracts/ready_probes/ready_saes/
execution_available/blockers`，保留旧响应字段供兼容。执行可用性描述已报告
的组件前提，实际计划是否可执行仍以 WARDEN 编译结果为准。

## 四、测试结果

全量 pytest 184 通过、2 跳过；真实 WARDEN HTTP 专项 1 通过；Python 编译
通过。新增/主要修改客户端、诊断与安全测试 Ruff 通过。测试覆盖实际 HTTP
重定向拒绝、只读重试、提交不重试、未知/重复/非有限 JSON、响应大小、
错误脱敏、缺失字段、API 不兼容和不可用能力。全仓旧质量问题仍留待 A8。

## 五、真实集成状态

使用现有 WARDEN 源码只读启动本地服务，禁用字节码写入，日志只写入
AgentGuard 的 `.agentguard/`。响应为 `version=0.2.0/api_version=v1`，有
8 个真实加载的合同。运行时、激活后端和调度器均 false，Probe/SAE 为空。
诊断与 SQLite 工作流测试正确保留 blocked。没有配置或执行模型，本次
不算真实模型或因果干预验收。先前底层未提交草稿保持不变。

## 六、已知问题

DEP-001–006 仍存在。公共诊断目前仍以兼容字典返回，完整业务 API/SDK 类型
输出在 A7 统一；远端资源的真实执行能力不能由健康检查或替身测试推断。

## 七、Git 信息

分支 `feature/agentguard-upper-layer-v1`，阶段提交
`Harden WARDEN client and unify capability diagnostics`。

## 八、下一阶段准备

A1 的客户端、诊断和真实 blocked HTTP 验证通过，继续 A2 的工作流状态机、
并发幂等及恢复。无法执行的底层能力保持依赖阻塞。
