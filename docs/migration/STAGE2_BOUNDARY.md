# 阶段 2：真实运行入口边界与保留模块

日期：2026-10-24。对照 AgentGuard `22b6114` 与 WARDEN `e9aa65d`。

## 本次变更

`create_app` 现在拒绝注入 `backend_mode=real` 的研究运行时或直接配对
执行器。AgentGuard 生产 API 的真实审计只能经
`WardenLifecycleService → WardenKernelClient → WARDEN /v1`。
默认合成研究引擎、审批、经验、工作流持久化和旧离线 Python 工具继续可用。

## 删除检查

| 旧模块 | 现有调用者 | WARDEN 对等能力 | 处理 |
| --- | --- | --- | --- |
| `core/linear_probe.py` | 已弃用的 `/v1/probes/train|predict`、测试 | 有 Probe 方法和资源清单，无同形公开训练/预测 API | 保留离线兼容 |
| `core/attribution.py` | API、CLI、研究测试 | 上层描述统计，不是模型干预执行器 | 保留 |
| `core/paired_runner.py` | API、MCP、测试 | 无真实配对重放 | 仅合成研究；真实 API 注入已关闭 |
| `whitebox/activation.py` | 公开 Python 导出、测试 | StegoPot Adapter 存在，但默认 WARDEN 无已配置激活后端 | 保留离线兼容 |
| `adapters/fractal_bench.py` | 公开导出、测试、文档 | WARDEN 有冻结威胁合同源，无 AgentGuard 同形场景展示接口 | 保留离线兼容 |
| `adapters/stegopot.py` | 公开导出、测试、文档 | WARDEN 已有观察运行，无配对干预与评价等价入口 | 保留离线兼容；不能进入 AgentGuard API |

本次**删除文件：无**。直接删除任一模块会破坏现有公开调用者或丢失尚无
WARDEN 等价能力的功能。旧 Probe 路由仍标记 deprecated，并需在后续迁移
过程中移出默认生产 API；它产生的离线结果不能当作 WARDEN 证据。

变更前：

```text
AgentGuard API ── 可注入真实 AgentGuardEngine ── 直接 StegoPot
               └─ /v1/kernel ── WARDEN
```

变更后：

```text
AgentGuard API ── 合成研究引擎
               └─ /v1/kernel ── WARDEN ── StegoPot / FRACTAL-Bench
旧 Python/弃用 Probe 接口 ── 离线兼容研究（不能升级因果证据）
```

## 验收与下一门槛

新增两个边界测试；AgentGuard 全量测试 **142 通过、2 跳过**，真实 HTTP
专项测试 **1 通过**，Python 编译和 Git 差异检查通过。旧调用者仍须逐项
迁移；要删除 Probe、白盒和配对模块，WARDEN
必须先提供已实测的对等功能，且相关 API、CLI、MCP 和打包测试改用新接口。
回滚本边界变更可恢复 `create_app` 直接注入，但不应把其输出标记为 WARDEN
审计。阶段 2 尚未整体验收，阶段 3 的因果结论门槛也未达到。
