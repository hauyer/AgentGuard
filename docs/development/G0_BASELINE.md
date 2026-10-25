# G0: 开发基线审计

## Git 信息

- **分支**: feature/g-live-evaluation-and-loop
- **基线提交**: c42f9d650a9f6a7f9a1b89645b0b171bf2fe76a9
- **基线分支**: feature/f-loop-hardening-and-evaluation
- **工作区状态**: 干净（仅 3 个未跟踪日志文件）

## 软件版本

- **Python**: 3.12.10
- **Pytest**: 9.1.1
- **Ruff**: 已安装

## 测试结果

| 检查项 | 结果 |
|--------|------|
| pytest -q | ✅ 全部通过 |
| ruff check src tests scripts | ✅ All checks passed |
| compileall src scripts | ✅ 通过 |
| audit_architecture.py --check | ✅ 无边界违规，无依赖循环 |

## 当前公共 API

### 调查相关
- `/v1/investigations` (GET/POST)
- `/v1/investigations/{request_id}` (GET)
- `/v1/investigations/{request_id}/risk-analysis` (GET)
- `/v1/investigations/{request_id}/interaction-graph` (GET)
- `/v1/investigations/{request_id}/timeline` (GET)
- `/v1/investigations/options` (GET)

### 调查循环
- `/v1/investigation-loops` (GET/POST)
- `/v1/investigation-loops/{loop_id}` (GET)
- `/v1/investigation-loops/{loop_id}/decisions` (GET)
- `/v1/investigation-loops/{loop_id}/evidence-changes` (GET)
- `/v1/investigation-loops/{loop_id}/budget` (GET)

### 实验与报告
- `/v1/experiments` (GET)
- `/v1/experiments/{experiment_id}` (GET)
- `/v1/experiments/compare` (POST)
- `/v1/reports` (GET)
- `/v1/reports/{run_id}` (GET)

### 系统
- `/v1/system/health` (GET)
- `/v1/system/capabilities` (GET)
- `/v1/system/readiness` (GET)
- `/v1/defense-policies/{request_id}/preview` (POST)
- `/v1/defense-policies/{request_id}/execute` (POST)

## 真正尚未实现的能力

1. **策略评价公共 API**：无 `/v1/research/strategy-evaluations` 端点
2. **多案例评价 CLI**：当前 `run_investigation_eval.py` 仅支持单一案例
3. **公共循环观察动作**：`observation=None`，公共 API 不发起新观察
4. **真实 WARDEN 动态观察验收**：未执行
5. **真实 LLM 策略实验**：无配置，全部降级
6. **经验策略非降级实验**：无经验库数据
7. **策略优劣证明**：仅证明行为不同，未证明优劣

## 已验证的真实模型实验

- **无**：当前环境无真实 WARDEN 配置，所有测试均为 synthetic/recorded

## 未配置的底层资源

- **WARDEN_URL**: 未配置
- **模型运行时**: 未配置
- **经验库**: 空
- **真实 Threat Contract**: 未配置

## 本轮开发依赖

- G1：策略评价只读 API（依赖现有 InvestigationEvaluationService + LocalArtifactStore）
- G2：多案例评价 CLI（依赖 G1 索引）
- G3：循环恢复强化（依赖现有 loop_registry）
- G4：观察动作门控（依赖现有 ObservationRunService）
- G5：五策略对照（依赖 G1-G4）
- G6：应用合同验证（依赖 G1-G5）
- G7：综合验收
