# 上层平台重构 A3：隔离 WARDEN 重复能力

日期：2026-10-24。范围仅限 AgentGuard；WARDEN、FRACTAL-Bench 与 StegoPot
保持只读。

## 处理结果

- 把本地线性 Probe、合成配对 runner、PyTorch Hook、FRACTAL-Bench 目录适配、
  StegoPot 文件级适配五份实现迁入 `src/agentguard/compat/`。
- 原 `core.linear_probe`、`core.paired_runner`、`whitebox.activation`、
  `adapters.fractal_bench`、`adapters.stegopot` 路径保留为兼容导入垫片；旧
  Python 用户继续得到相同对象和行为。
- API/MCP 内部调用明确指向 `agentguard.compat`。Probe HTTP 操作继续保留请求/
  响应格式并标记 deprecated，OpenAPI 描述明确写出它不调用 WARDEN，且当前
  WARDEN 无等价训练/预测 endpoint。
- 普通 `agentguard.adapters` 初始化不再主动加载 FRACTAL-Bench 与 StegoPot
  适配器；只有显式取用旧兼容导出时才导入它们。
- `core.attribution` 保留为 AgentGuard 上层分析，不自行确认数组来源或提升
  证据等级。配对研究保留 synthetic 标记，真实审计仍只经 WARDEN 客户端。
- 将架构静态检查的兼容白名单移至新命名空间，并新增垫片同一实现、Probe
  OpenAPI 弃用状态及低层导入位置回归检查。

## 删除清单

**实际删除文件：无。** 五份实现是迁移到兼容目录，不是复制；五个旧路径文件现
为小型导入垫片。WARDEN 0.3.0 虽增加白盒 CLI 和 Probe Suite，但仍未提供与
AgentGuard 旧训练/预测、白盒模型运行、配对重放等价的公开 REST 接口。删除
这些能力会造成未经替代的可见破坏。

## 前后依赖图

- 重构前：`api.app`、`mcp_server` 和包级导出直接依赖 `core`/`whitebox`/
  `adapters` 内的重复实现；公开兼容导入与内部实现同处一层。
- 重构后：生产 WARDEN 工作流依赖 `WardenLifecycleService` →
  `WardenKernelClient` → WARDEN `/v1`。只保留的离线旧能力统一位于
  `agentguard.compat`，原导入路径只是兼容边。归因仍由上层 `core` 提供。
- 图示与调用清单见 [`DEPENDENCY_GRAPH.md`](DEPENDENCY_GRAPH.md)；逐入口迁移条
  件见 [`COMPATIBILITY.md`](COMPATIBILITY.md)。

## 验收结果

- 定向兼容回归：4 passed。
- 全量 pytest：199 passed、2 skipped、1 warning；跳过项依赖未配置的可选 PyTorch
  或真实 WARDEN HTTP 服务。
- `compileall -q src tests scripts`：通过。
- A3 涉及的源码、架构审计脚本与发布测试 Ruff：通过。
- 全仓 Ruff：140 项存量诊断，A0 基线记录 171 项；全仓 lint 尚未通过，留在 A8
  统一收敛。
- 架构审计：74 个模块、449 条导入；3 条底层导入分别来自
  `compat.fractal_bench`、`compat.stegopot` 和 `compat.whitebox_activation`；
  `boundary_violations=[]`、`dependency_cycle=[]`。
- `git diff --check`：通过。

A3 通过只表示兼容边界及软件行为得到检查，不表示真实模型运行、Probe 工件
兼容、白盒干预或因果有效性已经通过。
