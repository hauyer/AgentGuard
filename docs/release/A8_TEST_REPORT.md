# AgentGuard 1.0.0-rc1 候选验收报告

日期：2026-10-24。范围：仅 AgentGuard 上层 A8。WARDEN、FRACTAL-Bench 和
StegoPot 均保持只读。本报告不把 Mock、合成数据、历史观察记录或合同测试描述为
真实模型因果验证。

## 当前验收结果

| 检查 | 结果 | 证据/边界 |
| --- | --- | --- |
| Ruff | 通过 | `ruff check src tests scripts`，Ruff 0.17.2；未删测试或排除目录 |
| Python 编译 | 通过 | `python -m compileall -q src scripts`，Python 3.12 |
| 完整 pytest | 通过 | 临时干净开发环境 Python 3.12：255 passed、2 skipped、1 warning |
| 重复测试收集 | 已核对 | 256 tests collected；另有 PyTorch 可选模块级跳过和 WARDEN 实时服务测试跳过；参数化测试保留两种输入变体 |
| 架构审计 | 通过 | `scripts/audit_architecture.py --check`：无越界、无环；低层导入仅位于 `agentguard.compat` |
| SQLite 生命周期 | 软件测试通过 | 幂等、冲突、追加审计、恢复/不重放、并发 begin 等测试；不等于多节点/生产压力验证 |
| API/OpenAPI 合同 | 软件测试通过 | 业务路由与旧路径的 OpenAPI 回归；Probe 旧路径标记 deprecated |
| 包构建 | 通过 | 构建 `agentguard-1.0.0rc1.tar.gz` 与纯 Python wheel |
| 干净环境安装 | 通过 | 从 wheel 在独立临时运行环境安装，版本为 `1.0.0rc1`，`agentguard --help` 成功 |
| PyTorch 可选测试 | 通过 | `D:\flask\miniconda\envs\nanogpt`（Python 3.12.14、torch 2.11.0+cu128），`tests/test_whitebox_activation.py`：5 passed |
| Python 3.11/3.12/3.13 | 通过 | GitHub Actions run `40074638267` 三个矩阵作业全绿；每个版本均构建并安装 wheel |
| WARDEN 真实 HTTP | 阻塞 | `AGENTGUARD_WARDEN_URL`、`AGENTGUARD_LIVE_WARDEN_URL` 未配置 |
| 真实模型/因果防御 | 阻塞 | 无实时 3B/7B WARDEN 模型运行与经验证的防御执行器 |

## 已修复 CI 问题

GitHub 目标分支之前的 Actions run `40058130736` 在 Python 3.11、3.12、3.13
三个作业中均停在 Ruff 步骤，pytest 尚未运行。修复包括未使用导入、异常处理和
重复测试名称；没有放宽规则或删测试。重复定义被覆盖的用例已改为参数化或唯一名称，
收集从此前基线 246 增至 256。两项 skip 分别是未配置 live WARDEN 的测试，以及
基础环境没有 PyTorch 时整模块的可选跳过；随后在 nanogpt 环境运行该模块得到 5 passed。

修复后的首个 GitHub Actions run `40074520778` 中，三种 Python 的安装、编译和 Ruff
均通过；pytest console entry point 在 Linux 上未将项目根目录放入 `sys.path`，导致
三个收集错误无法导入 `scripts`。CI 已改用 `python -m pytest` 与已验证的本地调用一致，
后续 run `40074638267`（提交 `24285ab03cf81860fb8364f2c7b5185e80f22a05`）在三种
Python 上全部通过：每个作业均为 255 passed、2 skipped、1 warning，Ruff 全绿，
sdist/wheel 构建、隔离 wheel 安装和 CLI 启动均成功。两项 skip 是未配置真实 WARDEN
服务，以及 Linux CI 环境未安装可选 PyTorch 模块。

## 安全与架构核对

- 新生产审计路径只能通过 WARDEN HTTP 客户端访问低层审计；API 禁止注入真实本地
  runtime，API/MCP 不直接调用底层仓库。
- 隐藏真值字段在请求合同中递归拒绝；422 响应不返回输入值；SQLite、响应和验证
  日志使用 sentinel 回归测试核对。API 未预期异常日志仅记录 request ID 与异常类型，
  不写异常文本。
- SDK 固定到 AgentGuard API origin、限制重定向与响应大小；MCP 仅委托 SDK；证据
  网关不打开本地文件 URI，并过滤原始 WARDEN 工件内容。
- 旧 Probe 和 `compat` 实现只服务兼容/离线入口，不能成为新的生产低层审计路径。

## 待完成门槛

最终文档更新提交仍须保留三版本矩阵全绿。软件 RC 验收的真实 WARDEN/model 项按
依赖阻塞公开记录，不影响软件包构建与上层 API 合同验收。
