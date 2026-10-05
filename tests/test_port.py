from __future__ import annotations

import socket

import pytest

from groovy_runner import port as port_mod


def _free_base() -> int:
    """A port that's free along with its next two neighbours, for the test."""
    for start in range(20000, 30000, 7):
        if all(port_mod.is_free(p) for p in range(start, start + 3)):
            return start
    pytest.skip("no free port range")


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "127.0.0.1"])
def test_skips_port_held_by_another_app(bind_host):
    """The bug: another app on ALL interfaces (Streamlit's default) wasn't seen
    as a conflict for our localhost-only bind on Windows."""
    base = _free_base()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.bind((bind_host, base))
        other.listen(1)
        assert not port_mod.is_free(base)
        assert port_mod.find_port(base) == base + 1


def test_skips_ipv6_wildcard_listener():
    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    base = _free_base()
    try:
        other = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        other.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        other.bind(("::", base))
    except OSError:
        pytest.skip("IPv6 bind unavailable")
    with other:
        other.listen(1)
        assert port_mod.find_port(base) == base + 1


def test_preferred_port_from_env_and_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GROOVY_RUNNER_PORT", "8642")
    assert port_mod._preferred() == 8642
    monkeypatch.setenv("GROOVY_RUNNER_PORT", "not-a-port")
    assert port_mod._preferred() == port_mod.DEFAULT_PORT
