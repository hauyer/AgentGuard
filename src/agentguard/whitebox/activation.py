"""Hook-based activation capture and controlled patching for caller-loaded PyTorch models.

This adapter never downloads or loads model weights itself. The caller is responsible for
loading an authorized local model/tokenizer and for evaluating secret recovery and benign
utility on the returned token IDs. It deliberately forces ``use_cache=False`` during
patched generation so absolute prompt token positions remain addressable on every forward.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from agentguard.contracts.whitebox import ActivationPatchSpec


class WhiteBoxAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivationSnapshot:
    module_path: str
    shape: tuple[int, ...]
    dtype: str
    sha256: str
    tensor: Any


@dataclass(frozen=True)
class ActivationInterventionResult:
    experiment_id: str
    model_id: str
    target_module: str
    operation: str
    token_positions: tuple[int, ...]
    input_token_ids_sha256: str
    output_token_ids_sha256: str
    output_shape: tuple[int, ...]
    reference_activation_sha256: str | None
    seed: int
    max_new_tokens: int
    use_cache: bool
    authorization_ref: str
    scope: str
    status: str = "completed"
    causal_claim_status: str = "causal_validation_required"
    limitations: tuple[str, ...] = (
        "This result records a local activation intervention, not a complete causal analysis.",
        "Evaluate secret recovery, collusion success, and benign utility on matched runs before interpreting mediation.",
        "Use sham/irrelevant-node controls and independent case-family repeats.",
        "authorization_ref is audit metadata only; the caller must verify the approval in its trusted control plane.",
    )


def _torch():
    try:
        import torch
    except ImportError as exc:
        raise WhiteBoxAdapterError(
            "White-box activation experiments require PyTorch; install the optional [whitebox] dependencies."
        ) from exc
    return torch


def _tensor_from_output(output: Any, torch):
    if torch.is_tensor(output):
        return output
    if isinstance(output, (tuple, list)) and output and torch.is_tensor(output[0]):
        return output[0]
    if isinstance(output, dict):
        for key in ("hidden_states", "last_hidden_state", "logits"):
            if torch.is_tensor(output.get(key)):
                return output[key]
    raise WhiteBoxAdapterError("target module output is not a supported tensor or tensor-first container")


def _replace_output(output: Any, tensor: Any, torch):
    if torch.is_tensor(output):
        return tensor
    if isinstance(output, tuple) and output and torch.is_tensor(output[0]):
        return (tensor, *output[1:])
    if isinstance(output, list) and output and torch.is_tensor(output[0]):
        return [tensor, *output[1:]]
    if isinstance(output, dict):
        for key in ("hidden_states", "last_hidden_state", "logits"):
            if torch.is_tensor(output.get(key)):
                copied = dict(output)
                copied[key] = tensor
                return copied
    raise WhiteBoxAdapterError("cannot replace tensor in target module output container")


def _bytes_sha256(tensor: Any, torch) -> str:
    value = tensor.detach().to("cpu").contiguous()
    raw = bytes(value.view(torch.uint8).reshape(-1).tolist())
    return hashlib.sha256(raw).hexdigest()


class WhiteBoxActivationAdapter:
    """Capture activations and run bounded interventions on an already-loaded PyTorch model."""

    def __init__(self, model: Any, *, model_id: str = "caller-loaded-model") -> None:
        _torch()
        self.model = model
        self.model_id = model_id

    def _module(self, module_path: str):
        modules = dict(self.model.named_modules())
        if module_path not in modules:
            nearby = [name for name in modules if module_path.lower() in name.lower()][:8]
            suffix = f" Nearby matches: {nearby}" if nearby else ""
            raise WhiteBoxAdapterError(f"target module path not found: {module_path}.{suffix}")
        return modules[module_path]

    @staticmethod
    def _input_hash(input_ids: Any, torch) -> str:
        return _bytes_sha256(input_ids, torch)

    def capture(
        self,
        *,
        module_path: str,
        input_ids: Any,
        attention_mask: Any | None = None,
        forward_kwargs: dict[str, Any] | None = None,
    ) -> ActivationSnapshot:
        torch = _torch()
        module = self._module(module_path)
        captured: list[Any] = []

        def hook(_module, _inputs, output):
            tensor = _tensor_from_output(output, torch)
            captured.append(tensor.detach().to("cpu").clone())
            return None

        was_training = bool(getattr(self.model, "training", False))
        handle = module.register_forward_hook(hook)
        try:
            self.model.eval()
            kwargs = dict(forward_kwargs or {})
            if "input_ids" in kwargs or "attention_mask" in kwargs:
                raise WhiteBoxAdapterError("forward_kwargs cannot override input_ids or attention_mask")
            kwargs["input_ids"] = input_ids
            if attention_mask is not None:
                kwargs["attention_mask"] = attention_mask
            with torch.no_grad():
                self.model(**kwargs)
        finally:
            handle.remove()
            self.model.train(was_training)
        if not captured:
            raise WhiteBoxAdapterError("target module was not executed during the forward pass")
        tensor = captured[-1]
        return ActivationSnapshot(
            module_path=module_path,
            shape=tuple(int(value) for value in tensor.shape),
            dtype=str(tensor.dtype),
            sha256=_bytes_sha256(tensor, torch),
            tensor=tensor,
        )

    def generate_with_patch(
        self,
        *,
        spec: ActivationPatchSpec,
        input_ids: Any,
        reference_activation: ActivationSnapshot | Any | None = None,
        attention_mask: Any | None = None,
        generation_kwargs: dict[str, Any] | None = None,
    ) -> tuple[Any, ActivationInterventionResult]:
        torch = _torch()
        module = self._module(spec.target_module)
        if spec.operation == "replace_from_reference":
            if reference_activation is None:
                raise WhiteBoxAdapterError("replace_from_reference requires a reference activation")
            if isinstance(reference_activation, ActivationSnapshot):
                if reference_activation.module_path != spec.target_module:
                    raise WhiteBoxAdapterError("reference activation module_path does not match target_module")
                reference_tensor = reference_activation.tensor
                reference_hash = reference_activation.sha256
            else:
                reference_tensor = reference_activation
                reference_hash = _bytes_sha256(reference_tensor, torch)
        else:
            reference_tensor = None
            reference_hash = None

        forbidden_kwargs = {"input_ids", "attention_mask", "use_cache", "max_new_tokens"}
        kwargs = dict(generation_kwargs or {})
        overlap = forbidden_kwargs & set(kwargs)
        if overlap:
            raise WhiteBoxAdapterError(f"generation_kwargs cannot override controlled fields: {sorted(overlap)}")
        positions = tuple(spec.token_positions)

        def patch_hook(_module, _inputs, output):
            tensor = _tensor_from_output(output, torch)
            if tensor.ndim < 2:
                raise WhiteBoxAdapterError("activation tensor must have a sequence dimension")
            seq_len = int(tensor.shape[-2])
            if any(position >= seq_len for position in positions):
                # With use_cache=False, prompt positions remain addressable. Fail closed if not.
                raise WhiteBoxAdapterError("a requested token position is outside the current activation sequence")
            patched = tensor.clone()
            for position in positions:
                if spec.operation == "zero":
                    patched[..., position, :] = 0
                elif spec.operation == "add_noise":
                    generator = torch.Generator(device=patched.device)
                    generator.manual_seed(spec.seed + position)
                    noise = torch.randn(
                        patched[..., position, :].shape,
                        generator=generator,
                        device=patched.device,
                        dtype=patched.dtype,
                    ) * spec.noise_std
                    patched[..., position, :] = patched[..., position, :] + noise
                elif spec.operation == "replace_from_reference":
                    assert reference_tensor is not None
                    reference = reference_tensor.to(device=patched.device, dtype=patched.dtype)
                    if reference.ndim != patched.ndim or reference.shape[0] != patched.shape[0] or reference.shape[-1] != patched.shape[-1]:
                        raise WhiteBoxAdapterError("reference activation shape is incompatible with target activation")
                    if position >= reference.shape[-2]:
                        raise WhiteBoxAdapterError("token position is outside the reference activation sequence")
                    patched[..., position, :] = reference[..., position, :]
                else:  # defensive; the Pydantic contract should already reject this.
                    raise WhiteBoxAdapterError(f"unsupported patch operation: {spec.operation}")
            return _replace_output(output, patched, torch)

        was_training = bool(getattr(self.model, "training", False))
        handle = module.register_forward_hook(patch_hook)
        try:
            self.model.eval()
            generation = {
                "input_ids": input_ids,
                "max_new_tokens": spec.max_new_tokens,
                "use_cache": False,
                **kwargs,
            }
            if attention_mask is not None:
                generation["attention_mask"] = attention_mask
            with torch.no_grad():
                output_ids = self.model.generate(**generation)
        finally:
            handle.remove()
            self.model.train(was_training)
        if not torch.is_tensor(output_ids):
            raise WhiteBoxAdapterError("model.generate must return a token ID tensor")
        result = ActivationInterventionResult(
            experiment_id=spec.experiment_id,
            model_id=self.model_id,
            target_module=spec.target_module,
            operation=spec.operation,
            token_positions=positions,
            input_token_ids_sha256=self._input_hash(input_ids, torch),
            output_token_ids_sha256=self._input_hash(output_ids, torch),
            output_shape=tuple(int(value) for value in output_ids.shape),
            reference_activation_sha256=reference_hash,
            seed=spec.seed,
            max_new_tokens=spec.max_new_tokens,
            use_cache=False,
            authorization_ref=spec.authorization_ref,
            scope=spec.scope,
        )
        return output_ids, result
