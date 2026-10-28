# AG-0 静态合同与复用基线

日期：2026-10-28。此门只证明 AgentGuard 本地合同和静态复用审计；不证明 WARDEN 在线或真实模型执行。

## 仓库命令

| 命令 | 退出码 | 结果 |
|---|---:|---|
| `git status --porcelain=v1` | 0 | 空输出；开始时工作树干净 |
| `git rev-parse HEAD` | 0 | `a5d5603cc653e17025038f3eb0606bc28d8bbd70` |
| `git branch -avv` | 0 | 当前为 `feature/i-reliability-and-live-validation`；与 `origin/` 同步 |
| `git log --oneline --decorate -30` | 0 | HEAD 后一提交为代码快照 `9a8ee1f`；未回退或重置 |
| `.venv/Scripts/python.exe -m pytest -o addopts='' -q -rs` | 0 | **508 passed, 6 skipped, 1 warning**，103.57s |
| `.venv/Scripts/ruff.exe check .` | 0 | All checks passed |
| `.venv/Scripts/python.exe scripts/audit_architecture.py` | 0 | 107 modules / 710 imports；boundary violations 0；dependency cycles 0；3 条旧 compat 下层导入均留在 compat |

## OpenAPI 与 QWB-App 合同

- AgentGuard rc3 OpenAPI 3.1.0，86 paths；原始 SHA-256 `fadab4d701d23fabda785413cf0c4536fcffec521ea09cbe6ad73a417c15de1e`，规范化 SHA-256 `52ad0d5dc175e58759196588d1c4ab5b45e82751f882c82ed8540cb963d942f6`。
- QWB-App 固定 pin 为 AgentGuard `63fe1b5662df93fbf06750ecf09c14ee9e6bd5cc` / rc2；App 清单的 OpenAPI 86 paths，文件 SHA-256 `2ce83660a67111a346a2de9772fcaec1938a4a5de713439de34a83e372b9a5e5`，规范化 SHA-256 `430a8115f4dfb8c865060d45345a58bf267f60657b1ba8ab1c0faa2ba3acf41c`。
- App 固定 33 个操作：`GET /v1/system/health`、`GET /v1/system/readiness`、`GET /v1/system/capabilities`、`GET /v1/kernel/status`、`GET /v1/investigations/options`、`GET /v1/scenarios`、`POST /v1/investigations`、`GET /v1/investigations`、`GET /v1/investigations/{request_id}`、`GET /v1/investigations/{request_id}/timeline`、`GET /v1/investigations/{request_id}/interaction-graph`、`GET /v1/runs/{run_id}/findings`、`GET /v1/runs/{run_id}/hypotheses`、`GET /v1/runs/{run_id}/investigation`、`GET /v1/evidence/investigations/{request_id}`、`GET /v1/defense-policies/{request_id}`、`POST /v1/defense-policies/{request_id}/preview`、`GET /v1/reports`、`GET /v1/reports/{run_id}`、`GET /v1/experiences`、`GET /v1/experiments`、`GET /v1/experiments/{experiment_id}`、`POST /v1/experiments/compare`、`POST /v1/investigation-loops`、`GET /v1/investigation-loops`、`GET /v1/investigation-loops/{loop_id}`、`GET /v1/investigation-loops/{loop_id}/decisions`、`GET /v1/investigation-loops/{loop_id}/evidence-changes`、`GET /v1/investigation-loops/{loop_id}/budget`、`GET /v1/research/strategy-evaluations`、`GET /v1/research/strategy-evaluations/{evaluation_id}`、`GET /v1/investigations/{request_id}/risk-analysis`、`GET /v1/system/audit-events`。
- 执行了 33 个 `path+method` 的全 operation JSON 结构比较；匹配 33，差异 0。Golden fixture：`tests/fixtures/qwb_app_supported_operations_rc2.json`。这包括参数、请求体、响应、授权声明和错误结构；rc3 内本地兼容测试将继续逐项检查。

## 基线跳过项和外部阻塞

| 跳过项 | 结果 | 含义 |
|---|---|---|
| `tests/test_whitebox_activation.py` | SKIPPED：可选 PyTorch 未安装 | 离线数值测试未运行 |
| `tests/test_live_investigation_loop.py` | SKIPPED：`AGENTGUARD_LIVE_WARDEN_URL` 未配置 | Live Loop 未运行 |
| `tests/test_live_observation_run.py` | SKIPPED：同上 | Live observation 未运行 |
| `tests/test_live_warden_c_round.py` | SKIPPED（2 项）：同上 | WARDEN C-round 未运行 |
| `tests/test_live_warden_http.py` | SKIPPED：同上 | 真实 HTTP 未运行 |

WARDEN 源码固定为 `381fc08170386a2dbd4c2ded3f0f724f871e9636` / v0.3.0。在线服务与在线 OpenAPI 未运行。其公开 REST 没有白盒实验提交/查询、干预执行、远端 POST 幂等、进程重启后的任务查询、受控工件内容下载或来源签名合同。AG-1/AG-2/AG-3/AG-5 的真实后端验收因此保持 `BLOCKED / NOT_RUN`；本地合同测试不得替代 Live 通过。

完整文件/符号/调用者与复用决定见 [`RESEARCH_REUSE_MATRIX.md`](RESEARCH_REUSE_MATRIX.md)。

## 本轮最终候选验收

- AgentGuard rc4 实现快照：`9d1c1dc9331f4ce168aab29b860c94b9d65a8321`；分支：`feature/i-reliability-and-live-validation`。
- 最终本地回归：`python -m pytest -q`，退出码 0，**524 passed, 6 skipped, 1 warning**。6 个跳过项仍是可选 PyTorch 测试和 5 个未配置 `AGENTGUARD_LIVE_WARDEN_URL` 的 Live 测试。
- `ruff check .`、`python -m compileall -q src tests scripts`、`python scripts/audit_architecture.py --check` 和 `git diff --check` 均退出码 0。架构审计为 109 modules / 722 imports，0 boundary violations、0 dependency cycles；3 个既有 compat 下层导入仍限制在 compat。
- 隔离 `python -m build` 因配置的 Tsinghua PyPI 镜像 TLS EOF 未能下载 hatchling；`python -m build --no-isolation` 使用本机已安装 hatchling 成功生成 sdist 与 wheel。推送后的 GitHub Actions 另在干净环境完成 build 与 wheel 安装验证。
- GitHub Actions [run 40665395993](https://github.com/hauyer/AgentGuard/actions/runs/40665395993) 对该实现快照的 Python 3.11、3.12、3.13 全部成功，包括测试、构建、隔离 wheel 安装和 synthetic demo 检查。
- WARDEN URL 未配置；上述任何本地或 CI 通过都不代表真实 WARDEN、真实模型或 Live 研究执行。AG-5 保持 `BLOCKED / NOT_RUN`。
