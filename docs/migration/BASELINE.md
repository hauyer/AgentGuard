# AgentGuard 0.9.0 迁移基线

记录日期：2026-10-23（Asia/Shanghai）。本阶段只做审计与文档更新；没有修改业务代码，也没有把合成测试视为真实模型验证。

## 来源与版本

| 项目 | 固定信息 |
| --- | --- |
| AgentGuard 来源 | `AgentGuard-0.9.0-WARDEN.zip`，SHA-256 `6594bfbebd4d8d76c5429472a2bcafb02cc9c829eb69f8387189660075cf3dee` |
| 原始 Git 历史 | 压缩包中无 `.git`，无法恢复原始上游 commit |
| 本地导入基线 | `3bc9584c60854961396eaf5581b178a31df79df7`（新建本地仓库的首个提交） |
| 审计分支 | `feature/warden-kernel-migration` |
| WARDEN 来源 | `https://github.com/Xinyuan20061/WARDEN`，只读克隆，commit `3116af8eb3a7fc3cc9d14ecfa4e5b469f2356e4a`，包版本 0.2.0 |
| Python | 项目虚拟环境 3.12；nanogpt Miniconda 环境 3.12.14、PyTorch 2.11.0+cu128 |
| 项目主要依赖 | agentguard 0.9.0、FastAPI 0.161.1、Pydantic 2.14.1、Starlette 1.9.0、httpx 0.28.1、pytest 9.1.1、Ruff 0.17.2、Uvicorn 0.54.0 |

`pip install -e ".[dev]"` 首次因机器默认清华镜像的 TLS 连接失败；改用 PyPI 官方索引后安装成功。nanogpt 环境未安装 AgentGuard 的测试依赖；白盒测试通过临时 `PYTHONPATH` 读取本项目虚拟环境依赖，没有改动该 Conda 环境。

## 原样验证结果

| 验证 | 实际结果 | 边界 |
| --- | --- | --- |
| 项目虚拟环境 `python -m pytest -ra` | 收集阶段 1 个错误；`tests/test_whitebox_activation.py:15` 在可选 PyTorch 缺失时仍定义 `torch.nn.Module`，未进入测试执行 | 属于现存可选依赖测试问题 |
| 项目虚拟环境 `pytest -ra --ignore=tests/test_whitebox_activation.py` | 130 通过、1 失败 | 失败为 Windows 删除仍被占用的 `registry.sqlite3`，见 `tests/test_study_analysis.py:68`、`src/agentguard/research_registry.py:36-46`；`with sqlite3.Connection` 不关闭连接 |
| nanogpt Python + PyTorch，白盒测试 | 5 通过 | 仅 toy model，不是实际 LLM 审计 |
| nanogpt Python + PyTorch，完整 pytest | 135 通过、1 失败 | 同一 SQLite 文件占用问题 |
| `ruff check src tests scripts` | 173 个现存问题 | 本阶段不做批量格式修复 |
| `python -m compileall -q src tests` | 通过 | 仅语法/字节码检查 |
| `agentguard doctor` | `backend_mode=synthetic`；WARDEN `not_configured`；FRACTAL-Bench、StegoPot 不可导入 | 不证明真实内核或模型可用 |
| `agentguard adapters check` | `validated=false` | 仅包可导入性检查 |
| AgentGuard FastAPI `TestClient` | `/health` 与 `/v1/kernel/status` 均 HTTP 200 | 进程内 API 烟测；未连接远端 WARDEN |
| `agentguard demo --no-auto-intervene` | 合成演示完成，并标为 `synthetic` | 没有执行防御或真实模型 |
| WARDEN 默认 FastAPI `TestClient` | health/capabilities/resources/contracts 均 HTTP 200；不存在合同的建计划请求 HTTP 400 | 真正运行 WARDEN 代码的进程内烟测，不是 AgentGuard→WARDEN HTTP 联调 |

WARDEN 默认能力结果明确显示 `agent.runtime` 与 `activation.backend` 不可用；StegoPot/FRACTAL-Bench 包未安装，资源与威胁契约列表为空。`warden/interface/factory.py:16-61` 未注入 `RunDispatcher`；即使计划通过，`warden/application/service.py:98-110` 也会返回 `blocked` 运行。`GET /v1/health` 返回 `ok` 只说明服务在运行。

另用测试哨兵值构造 WARDEN 威胁合同，进程内 `POST /v1/audit-plans` 的 blocked 计划仍原样返回 `threat_contract.truth`。AgentGuard 当前转发完整计划并写入工作流上下文，构成需要在阶段 1 处理的真值访问边界风险；见 `API_COMPATIBILITY.md`。

## CLI、API、SQLite 与示例核查

- CLI 入口在 `src/agentguard/cli.py:91`，`doctor`、`demo`、`adapters check` 可运行；`kernel status` 在未配置 URL 时明确返回 `not_configured`。
- FastAPI 在 `src/agentguard/api/app.py:92-151` 默认注入 MockBackend，同时创建工作流 SQLite；`/v1/kernel/*` 为独立 WARDEN 路径，未配置时不会静默使用 Mock。
- `WardenWorkflowStore` 的幂等、事件日志、恢复由 `tests/test_warden_lifecycle.py` 中替身测试覆盖；本阶段没有真实远端断线/重启测试。
- `examples/warden_audit.example.json` 的合同与模型 ID 是占位值，`start_run=false`；不能作为端到端真实运行配置。
- `pyproject.toml:7` 和 `src/agentguard/api/app.py:107` 为 0.9.0，但 `src/agentguard/api/app.py:249` 的 `/health` 仍报告 0.8.0；这是现存版本漂移。

## 当前结论

软件层面的 Mock 闭环、WARDEN 客户端与工作流存储已有实现。真实 WARDEN 联调、FRACTAL-Bench 合同、StegoPot 运行、模型工件和可验证的干预证据仍未完成验收。阶段 1 应先处理本报告记录的接口差异和 WARDEN 默认组合缺口；阶段 0 不改变完成度标记。
