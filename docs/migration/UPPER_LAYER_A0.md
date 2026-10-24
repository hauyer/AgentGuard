# 上层平台重构 A0 阶段完成报告

## 一、完成内容

以当前 AgentGuard 为起点建立 `feature/agentguard-upper-layer-v1`，继承已有
修复；更新基线、重复能力清单、模块依赖图和 A0–A8 迁移顺序。新增可执行
AST 架构审计工具，持续检测新生产模块对底层包的直接依赖与依赖环。

## 二、代码变化

新增 `scripts/audit_architecture.py`、`tests/test_architecture_audit.py`，以及
`DEPENDENCY_GRAPH.md`、本报告和 `docs/DEPENDENCY_BLOCKERS.md`。更新 README、
CHANGELOG、BASELINE、DUPLICATION_MAP、MIGRATION_PLAN 和 IMPLEMENTATION_STATUS。
本阶段未删除业务模块。

## 三、架构变化

新指令收紧开发授权：只修改 AgentGuard；底层三库从此只读。原有底层未提交
草稿保持原样，不算作完成能力。上层真实审计继续只经统一 WARDEN 客户端。
新工具只分析本仓库源码，不导入底层、读取真值或复制底层实现。

## 四、测试结果

- `python -m pytest -o addopts='' --disable-warnings --tb=no`：156 通过、2 跳过。
- `python -m compileall -q src tests scripts`：通过。
- 新增脚本及测试 Ruff：通过；全仓 Ruff：171 项现存问题，未通过。
- 架构检查：67 模块、431 导入，3 条旧兼容底层导入；无新生产直接越界和静态直接依赖环。

## 五、真实集成状态

本阶段是静态审计、软件测试与基线验证。没有启动真实 WARDEN 或模型运行。
以前真实 HTTP 和 7B 观察记录继续有效，但不因此宣称真实因果干预或 A8 通过。

## 六、已知问题

旧 Probe 路由仍暴露本地实现，白盒和两个直接底层适配器仍有旧 Python
调用者。MCP 目前主要是合成研究工具；SDK、统一业务状态机和 Evidence
Gateway 尚待完善。全仓质量问题与底层缺失能力有明确后续阶段。

## 七、Git 信息

分支：`feature/agentguard-upper-layer-v1`。起点：`321614c`；本阶段提交使用
`Audit upper-layer boundaries and establish A0 baseline`，提交哈希可由分支日志追溯。

## 八、下一阶段准备

A0 的清单与可复现检查具备，允许继续 A1。A1 只增强现有客户端与能力发现；
不调整 WARDEN API。A8 全量验收与独立比赛应用 B0 尚未达到启动条件。
