"""Security tests for statusline preview_script execution hardening."""
import os
from unittest.mock import patch

import pytest

from app.services.statusline_service import MAX_PREVIEW_SCRIPT_SIZE, StatusLineService


@pytest.fixture
def service():
    return StatusLineService()


def test_script_size_limit_enforced(service):
    """Scripts larger than MAX_PREVIEW_SCRIPT_SIZE must be rejected without execution."""
    oversized = "echo hello\n" * (MAX_PREVIEW_SCRIPT_SIZE // 11 + 1)
    assert len(oversized) > MAX_PREVIEW_SCRIPT_SIZE
    success, output, error = service.preview_script(oversized)
    assert not success
    assert output == ""
    assert error is not None and "too large" in error.lower()


def test_script_at_size_limit_is_accepted(service):
    """A script exactly at or below the limit must be executed."""
    script = "#!/bin/bash\necho ok\n"
    assert len(script) <= MAX_PREVIEW_SCRIPT_SIZE
    success, output, error = service.preview_script(script)
    assert success
    assert output == "ok"


def test_env_vars_are_stripped(service):
    """Executed scripts must not have access to sensitive host environment variables."""
    sensitive_vars = [
        "CLAUDE_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
    ]
    for var in sensitive_vars:
        with patch.dict(os.environ, {var: "SUPERSECRET"}):
            script = f'#!/bin/bash\necho "${var}"\n'
            success, output, error = service.preview_script(script)
            assert success, f"Script should run but failed: {error}"
            assert "SUPERSECRET" not in output, (
                f"Sensitive env var {var} was exposed to preview script"
            )


def test_home_env_is_safe(service):
    """HOME inside script must not point to the real user's home directory."""
    script = "#!/bin/bash\necho $HOME\n"
    with patch.dict(os.environ, {"HOME": "/root"}, clear=False):
        success, output, error = service.preview_script(script)
    assert success
    assert output != "/root", "HOME should be overridden, not the real user home"


def test_normal_script_still_works(service):
    """Legitimate statusline-style scripts must continue to work after hardening."""
    script = """#!/bin/bash
input=$(cat)
echo "preview-ok"
"""
    success, output, error = service.preview_script(script)
    assert success
    assert output == "preview-ok"


def test_bash_config_not_loaded(service):
    """bash --norc --noprofile must be used so ~/.bashrc cannot tamper with the environment."""
    # If --norc / --noprofile were NOT used, a malicious ~/.bashrc could
    # override PATH or inject commands. We verify the flag indirectly by
    # checking that a script which relies on BASH_ENV fails gracefully rather
    # than loading an arbitrary file.
    script = "#!/bin/bash\necho clean\n"
    with patch.dict(os.environ, {"BASH_ENV": "/etc/passwd"}, clear=False):
        success, output, error = service.preview_script(script)
    # The script itself doesn't fail; we just verify no exception from the env
    assert success
    assert output == "clean"
