import pytest

try:
    import torch
except ImportError:
    torch = None

from agentguard.contracts.whitebox import ActivationPatchSpec
from agentguard.whitebox.activation import WhiteBoxActivationAdapter, WhiteBoxAdapterError


pytestmark = pytest.mark.skipif(torch is None, reason="PyTorch is optional")


class TinyCausalToy(torch.nn.Module):
    """Small deterministic model with a module hook and a minimal generate API."""
    def __init__(self):
        super().__init__()
        self.layers = torch.nn.ModuleList([torch.nn.Linear(4, 4, bias=False)])
        with torch.no_grad():
            self.layers[0].weight.copy_(torch.eye(4))

    def forward(self, input_ids, attention_mask=None, **kwargs):
        x = torch.nn.functional.one_hot(input_ids, num_classes=4).float()
        return {"logits": self.layers[0](x)}

    def generate(self, input_ids, max_new_tokens=1, use_cache=False, attention_mask=None, **kwargs):
        if use_cache:
            raise AssertionError("white-box adapter must disable cache for absolute token-position patching")
        output = input_ids
        for _ in range(max_new_tokens):
            logits = self.forward(output, attention_mask=attention_mask)["logits"]
            next_token = logits[:, -1, :].argmax(dim=-1, keepdim=True)
            output = torch.cat([output, next_token], dim=1)
        return output


def patch_spec(operation="zero", positions=None):
    return ActivationPatchSpec(
        target_module="layers.0",
        operation=operation,
        token_positions=positions or [2],
        seed=19,
        noise_std=0.1 if operation == "add_noise" else 0.01,
        max_new_tokens=3,
        experiment_id="whitebox-test-run",
        authorization_ref="local-test-authorization",
    )


def test_capture_activation_records_shape_and_restores_model_mode():
    model = TinyCausalToy()
    model.train()
    adapter = WhiteBoxActivationAdapter(model, model_id="tiny-test-model")
    input_ids = torch.tensor([[1, 2, 3]])
    snapshot = adapter.capture(module_path="layers.0", input_ids=input_ids)
    assert snapshot.shape == (1, 3, 4)
    assert len(snapshot.sha256) == 64
    assert snapshot.tensor.shape == (1, 3, 4)
    assert model.training is True


def test_zero_patch_changes_generation_and_emits_auditable_result():
    model = TinyCausalToy()
    adapter = WhiteBoxActivationAdapter(model)
    input_ids = torch.tensor([[1, 2, 3]])
    baseline = model.generate(input_ids, max_new_tokens=3, use_cache=False)
    patched, record = adapter.generate_with_patch(spec=patch_spec("zero"), input_ids=input_ids)
    assert patched.shape == baseline.shape
    assert not torch.equal(patched, baseline)
    assert record.status == "completed"
    assert record.model_id == "caller-loaded-model"
    assert record.causal_claim_status == "causal_validation_required"
    assert record.input_token_ids_sha256 != record.output_token_ids_sha256
    assert record.use_cache is False
    assert model.training is True


def test_reference_activation_patch_uses_matching_module_and_is_deterministic():
    model = TinyCausalToy()
    adapter = WhiteBoxActivationAdapter(model)
    reference = adapter.capture(module_path="layers.0", input_ids=torch.tensor([[0, 1, 0]]))
    spec = patch_spec("replace_from_reference")
    first, record1 = adapter.generate_with_patch(spec=spec, input_ids=torch.tensor([[1, 2, 3]]), reference_activation=reference)
    second, record2 = adapter.generate_with_patch(spec=spec, input_ids=torch.tensor([[1, 2, 3]]), reference_activation=reference)
    assert torch.equal(first, second)
    assert record1.output_token_ids_sha256 == record2.output_token_ids_sha256
    assert record1.reference_activation_sha256 == reference.sha256


def test_whitebox_adapter_rejects_unknown_modules_positions_and_untrusted_kwargs():
    model = TinyCausalToy()
    adapter = WhiteBoxActivationAdapter(model)
    with pytest.raises(WhiteBoxAdapterError, match="target module path not found"):
        adapter.capture(module_path="layers.99", input_ids=torch.tensor([[1, 2, 3]]))
    with pytest.raises(WhiteBoxAdapterError, match="outside the current activation sequence"):
        adapter.generate_with_patch(spec=patch_spec("zero", [10]), input_ids=torch.tensor([[1, 2, 3]]))
    with pytest.raises(WhiteBoxAdapterError, match="cannot override controlled fields"):
        adapter.generate_with_patch(spec=patch_spec("zero"), input_ids=torch.tensor([[1, 2, 3]]), generation_kwargs={"use_cache": True})


def test_replace_patch_requires_reference_activation():
    adapter = WhiteBoxActivationAdapter(TinyCausalToy())
    with pytest.raises(WhiteBoxAdapterError, match="requires a reference activation"):
        adapter.generate_with_patch(spec=patch_spec("replace_from_reference"), input_ids=torch.tensor([[1, 2, 3]]))
