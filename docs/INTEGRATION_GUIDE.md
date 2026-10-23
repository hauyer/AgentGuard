# FRACTAL-Bench / StegoPot 集成指南

本文保留 FRACTAL-Bench 与 StegoPot 的基准运行时接入细节。自 AgentGuard 0.7.0 起，低层 Probe/SAE 管理与因果审计的权威入口是 WARDEN；本指南中的 AgentGuard 直连 StegoPot 适配器只用于已有基准运行与兼容流程，不应作为第二套低层因果内核。集成应固定仓库 commit 并在目标环境验证；不要把“能 import”当成完整实验验证。

## 1. 安装本地仓库

建议目录如下：

```text
research/
├── AgentGuard/
├── FRACTAL-Bench/
└── stegopot/
```

在同一个虚拟环境内安装：

```bash
python -m pip install -e ../stegopot
python -m pip install -e ../FRACTAL-Bench
python -m pip install -e ".[dev]"
```

FRACTAL-Bench 的 `pyproject.toml` 声明了 `stegopot.plugins` entry point：

```text
fractal_bench = fractal_bench.adapters.stegopot.plugin:get_plugin
```

其 StegoPot 插件包含 `fractal_bench.fractal` 场景提供器与 `fractal_bench.evaluator` 中央评价器。场景提供器构建 `ExperimentPlan`，中央评价器根据 trial truth 重算指标。隐藏真值只属于中央评价器与研究审计视图，不应进入 AgentGuard 的 `Observation`。

参考源码：

- [FRACTAL-Bench StegoPot provider](https://github.com/Xinyuan20061/FRACTAL-Bench/blob/main/fractal_bench/adapters/stegopot/provider.py)
- [FRACTAL-Bench central evaluator](https://github.com/Xinyuan20061/FRACTAL-Bench/blob/main/fractal_bench/adapters/stegopot/evaluator.py)
- [FRACTAL-Bench StegoPot integration contract](https://github.com/Xinyuan20061/FRACTAL-Bench/blob/main/docs/STEGOPOT_INTEGRATION.md)
- [StegoPot file-level experiment API](https://github.com/Xinyuan20061/stegopot/blob/main/stegopot/bootstrap/experiments/api.py)
- [StegoPot run lifecycle and audit output](https://github.com/Xinyuan20061/stegopot/blob/main/stegopot/bootstrap/experiments/run.py)


> **版本核对提醒：** 当前 FRACTAL-Bench 项目元数据标注为 `0.4.0`，但其 StegoPot 插件源码中的插件对象仍标注 `0.3.2`。在提交论文或竞赛实验前，应固定并记录 Git commit、包版本与插件版本，避免仅凭版本字符串判断兼容性。

## 2. 配置模型客户端

复制 `examples/stegopot/fractal_bench.template.yaml`，按自己的模型服务调整 `base_url` 和 `model`。模板采用 StegoPot `core.chat_completions` 资源；凭证字段只填写环境变量名，不填写真实密钥。

Linux/macOS：

```bash
export DEEPSEEK_API_KEY="your-key"
```

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
```

先用 StegoPot 自身命令验证配置，不要一开始就通过 AgentGuard 排错：

```bash
stegopot validate experiment --workspace .
stegopot doctor experiment --workspace .
```

如果工作区中没有名为 `experiment` 的配置，请将模板复制到 StegoPot 工作区的 `configs/experiment.yaml`，再执行上述命令。

## 3. 通过 AgentGuard 调用

```python
from agentguard.adapters.fractal_bench import FractalBenchAdapter
from agentguard.adapters.stegopot import StegoPotAdapter
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine

scenarios = FractalBenchAdapter.from_installed()
runtime = StegoPotAdapter.from_config(
    "configs/experiment.yaml",
    workspace=".",
    output_dir=".agentguard/runs",
)

engine = AgentGuardEngine(
    scenarios=scenarios,
    runtime=runtime,
    evaluator=runtime,
)

report = engine.run(
    ExperimentRequest(
        scenario_id="release_review",
        runtime_backend="stegopot",
        seed=7,
        families=1,
        max_interventions=1,
    )
)
print(report.model_dump_json(indent=2))
```

适配器会基于配置模板生成本次运行配置，覆盖 `seed`、`scenario_id` 和 `families`，然后调用 StegoPot 的 `run_file` API。生成的配置保存在 `.agentguard/generated/`，StegoPot 审计目录保存在指定的输出目录下。

**注意：** `families=1` 在 FRACTAL-Bench 中表示一个案例家族，完整家族包含多个因子条件；这不等于只运行一个 episode。

## 4. 当前真实集成能做什么

当依赖与配置正确时，适配器可以：

- 从已安装 FRACTAL-Bench 场景目录读取场景元数据。
- 通过 StegoPot 运行 FRACTAL-Bench 的声明式实验计划。
- 从运行报告中抽取消息、最终决策与运行状态作为观测。
- 读取 FRACTAL-Bench 中央评价器产生的 ITT 与 per-protocol 指标。
- 将原始运行目录作为证据来源，并在 AgentGuard 报告中区分测量结果和合成结果。

适配器从 `record["result"]` 中抽取可观察消息和最终回答，不读取 `trial["truth"]`，也不会把真值字段写入 `Observation`。

## 5. 当前尚未自动完成的部分

StegoPot 的文件级 API 运行的是一份完整实验计划。已完成运行之后，AgentGuard 不能通过通用 `intervene()` 方法安全地回头修改其中一条消息。因此：

- 未提供 `intervention_executor` 时，StegoPot 适配器会明确声明不支持直接干预；提供回调后还必须配置显式 `CapabilityManifest` 才能授权特定操作。
- 仅提供回调仍不足以开放执行权限；必须同时传入显式 `CapabilityManifest`，声明允许的目标类型和操作。
- 真实干预需要实现基于 FRACTAL-Bench / StegoPot 实验合同的配对重放或干预回调，并保存 `MessageEnvelope`、`InterventionRecord`、对照条件与运行工件。
- 仅配置真实模型并跑完基准，不等于已完成 AgentGuard 的因果归因实验。
- 当前消息特征只作为透明的筛查基线，不能证明隐蔽通信；正常文本中的长编码样式也可能是良性内容。
- AgentGuard 的 `max_runtime_seconds` 目前主要用于记录是否超预算；同步 `run_file` 调用不能被它强制中断。StegoPot 自身的运行预算和取消机制仍需在配置中正确设置。

## 6. 版本与可复现性

在真实实验记录中保存：

- FRACTAL-Bench commit。
- StegoPot commit。
- AgentGuard commit。
- 运行配置文件哈希。
- 模型 ID / revision 与推理参数。
- 环境依赖锁文件。
- 运行目录、审计封印和报告。
- 数据集 seed 与案例家族数。

不要只记录分支名，因为分支会继续变化。
