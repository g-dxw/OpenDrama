"""A fresh open-source installation must never send credentials to JJTV."""
import argparse
from pathlib import Path
import runpy
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def cli(monkeypatch, tmp_path):
    monkeypatch.setenv("MINITV_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("MINITV_API_BASE", raising=False)
    return runpy.run_path(str(ROOT / "cli" / "minitv"), run_name="minitv_test")


def test_unconfigured_cli_fails_before_any_network_request(cli):
    with pytest.raises(cli["CliError"]) as raised:
        cli["resolve_settings"](argparse.Namespace(), require_key=False)
    assert raised.value.code == "instance_required"


def test_cli_uses_explicit_instance_and_command_line_precedence(cli, monkeypatch):
    monkeypatch.setenv("MINITV_API_BASE", "https://studio.example.test")
    assert cli["resolve_settings"](argparse.Namespace(), require_key=False).base_url == "https://studio.example.test"
    settings = cli["resolve_settings"](argparse.Namespace(base_url="https://other.example.test"), require_key=False)
    assert settings.base_url == "https://other.example.test"


def test_archive_and_docker_preserve_operational_boundaries():
    attributes = (ROOT / ".gitattributes").read_text()
    assert "/deploy/video/** text eol=lf" in attributes
    assert "data export-ignore" in attributes
    docker_ignore = (ROOT / ".dockerignore").read_text()
    assert "**/.env*" in docker_ignore
    compose = (ROOT / "deploy/selfhost/compose.yml").read_text()
    assert "dastaan-staging_default" not in compose
    assert "INDIA_JJTV_DELEGATED_SECRET_FILE" not in compose
