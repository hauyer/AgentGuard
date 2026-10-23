import json
from agentguard.cli import main


def test_cli_run_from_json_config_saves_reproducible_report(tmp_path, monkeypatch, capsys):
    config = tmp_path / "run.json"
    config.write_text(json.dumps({"scenario_id": "demo_cloud_security", "runtime_backend": "mock",
                                  "auto_intervene": False, "seed": 22}), encoding="utf-8")
    monkeypatch.setenv("AGENTGUARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("sys.argv", ["agentguard", "run", "--config", str(config)])
    main()
    captured = capsys.readouterr()
    assert "Report saved to:" in captured.err
    files = list((tmp_path / "data" / "runs").glob("*.json"))
    assert len(files) == 1
    report = json.loads(files[0].read_text(encoding="utf-8"))
    assert report["backend_mode"] == "synthetic"
    assert report["experiment_designs"]
