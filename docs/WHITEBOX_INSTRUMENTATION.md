# 本地白盒激活采集与干预适配器

## 当前实现边界

`agentguard.whitebox.WhiteBoxActivationAdapter` 提供一个本地 PyTorch hook 层：

- 按 `named_modules()` 中的精确模块路径挂载 forward hook；
- 采集某个模块输出张量的形状、dtype 和 SHA-256，并保留本地张量供后续分析；
- 在继续生成期间对指定 token 位置执行 `zero`、`replace_from_reference` 或 `add_noise`；
- 输出输入/输出 token ID 哈希、干预配置、参考激活哈希、授权引用和限制说明；
- hook 在 `finally` 中移除，并恢复模型原先的 train/eval 状态。

适配器不负责下载模型、读取 API 密钥、启动任意进程或自行评价秘密恢复率。调用者必须加载已获授权的本地模型，并自行计算 Secret Recovery、Collusion Success 和 benign utility。`authorization_ref` 只是审计元数据；适配器不会连接授权系统验证票据，调用者必须在可信控制面完成审批校验。结果默认标记 `causal_validation_required`，不直接认定中介贡献已被验证。

## 安装与模型加载

```bash
python -m pip install -e ".[whitebox]"
```

适配器接受调用者已经加载的 PyTorch 模型。以 Hugging Face Causal LM 为例：

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from agentguard.whitebox import WhiteBoxActivationAdapter
from agentguard.contracts.whitebox import ActivationPatchSpec

model_id = "your-authorized-local-model-path"
tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id, local_files_only=True, torch_dtype=torch.float32
)
model.eval()
adapter = WhiteBoxActivationAdapter(model, model_id=model_id)
inputs = tokenizer("A fixed task prompt", return_tensors="pt")

# First inspect model.named_modules() and select a real module path for your architecture.
module_path = "model.layers.8"
snapshot = adapter.capture(
    module_path=module_path,
    input_ids=inputs["input_ids"],
    attention_mask=inputs.get("attention_mask"),
)

spec = ActivationPatchSpec(
    target_module=module_path,
    operation="zero",
    token_positions=[inputs["input_ids"].shape[1] - 1],
    experiment_id="authorized-pair-001",
    authorization_ref="your-approved-test-ticket",
    seed=7,
    max_new_tokens=32,
)
patched_ids, audit = adapter.generate_with_patch(
    spec=spec,
    input_ids=inputs["input_ids"],
    attention_mask=inputs.get("attention_mask"),
)
print(audit)
print(tokenizer.decode(patched_ids[0], skip_special_tokens=True))
```

示例模块路径不是通用保证。Llama、Qwen、Mistral 和不同封装模型的模块路径可能不同，必须先检查本地模型的 `named_modules()`。

## 参考激活替换

`replace_from_reference` 要求调用者提供相同模块的参考激活。最常见的设计是构造任务和公共上下文匹配、但秘密值不同的成对输入，分别采集激活，再将参考运行指定 token 位置的激活替换到目标运行中。必须确保批大小、隐藏维度和 token 位置一致。

```python
reference = adapter.capture(
    module_path=module_path,
    input_ids=matched_reference_inputs["input_ids"],
    attention_mask=matched_reference_inputs.get("attention_mask"),
)

spec = ActivationPatchSpec(
    target_module=module_path,
    operation="replace_from_reference",
    token_positions=[target_position],
    experiment_id="authorized-pair-002",
    authorization_ref="your-approved-test-ticket",
    seed=7,
    max_new_tokens=32,
)
patched_ids, audit = adapter.generate_with_patch(
    spec=spec,
    input_ids=target_inputs["input_ids"],
    reference_activation=reference,
    attention_mask=target_inputs.get("attention_mask"),
)
```

## 为什么强制 `use_cache=False`

许多生成模型默认启用 KV cache，后续 forward 只输入最新 token，导致“绝对 token 位置”与当前激活序列索引不一致。当前适配器为了确保位置干预语义明确，强制 `use_cache=False`。这会明显增加生成开销，但避免静默 patch 错误位置。后续若要支持 KV-cache 干预，应实现独立的绝对位置映射与测试，不能简单去掉此限制。

## 研究验收要求

一次 hook 成功或生成文本发生变化，不等于发现了因果路径。至少还需要：

1. 同一模型状态、任务上下文、秘密分布和解码参数的匹配运行；
2. 基线、候选节点干预、sham 干预和无关节点干预；
3. Secret Recovery、Collusion Success 与 benign utility 的独立评价；
4. 多个独立案例家族与多个随机种子；
5. 对候选位置多重比较校正，并在未用于筛选的样本上复验；
6. 保存模型 revision、模块路径、输入哈希、参考激活哈希、输出 token 哈希与完整运行配置。

本次自动化测试使用一个极小的本地 PyTorch toy model 验证 hook 生命周期、zero patch、reference replacement、输入边界和审计输出。没有加载真实开源 LLM 权重，也没有完成真实隐写共谋实验；这不构成模型级实证结论。
