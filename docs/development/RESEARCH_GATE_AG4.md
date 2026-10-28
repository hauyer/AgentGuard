# AG-4：公开 API、SDK/MCP 与兼容门

日期：2026-10-28。结论：**本地 rc4 合同/SDK/MCP 验收通过；GitHub CI 通过；外部 App smoke `NOT_RUN`。**

## OpenAPI 冻结

- 包/API：`1.1.0rc4`；OpenAPI `3.1.0`；87 paths。
- rc3→rc4 新增仅 `GET /v1/research/capabilities`；没有移除 path 或 method。
- 变更既有 operation：Study 注册与分析 POST；其余旧 operation object 不变。新 Header/error 参数仅在 Study 写操作；Study Schema 只扩展 optional metadata/analysis fields。
- App 固定 33 个支持 operation 来自 AgentGuard `63fe1b5662df93fbf06750ecf09c14ee9e6bd5cc` / rc2；当前运行时 operation object 对 golden snapshot 逐项相等，33/33。
- rc4 快照：`openapi-1.1.0rc4.json`；文件 SHA-256 `f2b802fd7928bf2701bfbc65e4cf655c8f801a465fe6dd853f938e9d8f75b978`；规范化 SHA-256 `09b21ab3cd1e95dd3073142e4274b387ffb43689437be3fa60ad0939403f7894`。

## SDK/MCP 权限

- SDK 新方法只调用 `/v1/research/capabilities` 与已存在 Study REST，写入带 API/Operator/可选 Approval header。
- MCP 仅提供能力与 Study list/get 的只读工具；没有 Study write、运行启动或复制统计逻辑。
- Study write 通过现有 API audit journal；全局门和路由 Operator/approval 门分层运行。

## 测试

| 命令 | 结果 |
|---|---|
| `pytest tests/test_qwb_pinned_openapi_compat.py` | 退出码 0；快照等于运行时 OpenAPI，87 paths，旧 App 33/33 相等，Study header/error schema 在位 |
| `pytest tests/test_sdk_c7.py tests/test_mcp_boundary_a7.py` | 退出码 0；SDK 固定 URL/header，MCP 仍只走公开 API |
| QWB-App 实时服务 smoke / App pin 更新 | `NOT_RUN`；App 仓库没有修改，旧 pin 不自动升级 |
| rc4 GitHub Actions Python 3.11/3.12/3.13 | [run 40665395993](https://github.com/hauyer/AgentGuard/actions/runs/40665395993)；3.11、3.12、3.13 全部成功，含测试、构建、隔离 wheel 安装和 synthetic demo 检查 |
| 本地 `python -m build` | 隔离构建被配置的 Tsinghua PyPI 镜像 TLS EOF 阻断；`python -m build --no-isolation` 使用已安装 hatchling 成功生成 sdist 和 wheel |
