"""Packaged YAML safety contracts; not collector-binary or hosted validation."""

from pathlib import Path

import pytest
import yaml

from rove.models.config import ObservabilityConfig

PROFILES = Path(__file__).resolve().parents[1] / "examples" / "observability"


@pytest.mark.parametrize("name", ["local", "grafana", "azure-monitor"])
def test_packaged_collectors_expose_only_explicit_loopback_trace_receiver(name):
    config = yaml.safe_load((PROFILES / f"collector-{name}.yaml").read_text())
    assert config["receivers"] == {"otlp": {"protocols": {"http": {"endpoint": "127.0.0.1:4318"}}}}
    assert config["service"]["telemetry"]["metrics"] == {"level": "none"}
    assert set(config["service"]["pipelines"]) == {"traces"}
    pipeline = config["service"]["pipelines"]["traces"]
    assert pipeline["receivers"] == ["otlp"]
    assert set(pipeline["exporters"]) == set(config["exporters"])
    assert len(pipeline["exporters"]) == 1
    for exporter in config["exporters"].values():
        if "auth" in exporter:
            identity = exporter["auth"]["authenticator"]
            assert identity in config["service"]["extensions"]
            assert identity in config["extensions"]


def test_default_profiles_do_not_start_cloud_forwarding_or_capture_full_payloads():
    config = yaml.safe_load((PROFILES / "collector-local.yaml").read_text())
    assert config["exporters"] == {"debug": {"verbosity": "basic"}}
    fragment = yaml.safe_load((PROFILES / "rove-observability.yaml").read_text())
    profile = ObservabilityConfig.model_validate(fragment["defaults"]["observability"])
    assert not profile.enabled and profile.endpoint == "http://127.0.0.1:4318"


def test_hosted_templates_require_external_configuration_and_explicit_auth_scope():
    grafana = yaml.safe_load((PROFILES / "collector-grafana.yaml").read_text())
    assert grafana["extensions"]["basicauth/grafana"]["client_auth"] == {
        "username": "${env:GRAFANA_CLOUD_INSTANCE_ID}",
        "password": "${env:GRAFANA_CLOUD_API_KEY}",
    }
    assert grafana["exporters"]["otlp_http/grafana"]["endpoint"] == (
        "${env:GRAFANA_CLOUD_OTLP_ENDPOINT}"
    )
    azure = yaml.safe_load((PROFILES / "collector-azure-monitor.yaml").read_text())
    assert azure["exporters"]["azure_monitor"]["connection_string"] == (
        "${env:APPLICATIONINSIGHTS_CONNECTION_STRING}"
    )
    assert azure["extensions"]["azure_auth/monitor"] == {
        "managed_identity": {},
        "scopes": ["https://monitor.azure.com/.default"],
    }
