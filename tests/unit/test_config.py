import pytest

from wsr_evidence.config import RuntimeSettings


def test_runtime_defaults_to_otlp_http_on_ipv4_loopback() -> None:
    settings = RuntimeSettings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 4318


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "example.test"])
def test_runtime_rejects_non_loopback_bindings(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        RuntimeSettings(host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.4.3.2", "::1"])
def test_runtime_accepts_ip_loopback_bindings(host: str) -> None:
    assert RuntimeSettings(host=host).host == host


def test_container_scope_explicitly_allows_internal_wildcard_binding() -> None:
    settings = RuntimeSettings(host="0.0.0.0", bind_scope="container")

    assert settings.host == "0.0.0.0"
    assert settings.bind_scope == "container"
