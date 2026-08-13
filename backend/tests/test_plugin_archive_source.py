"""Tests for archive:// plugin source (CC 2.1.224): zip-over-HTTPS install
with optional SHA-256 pinning.

The archive scheme lives next to the existing marketplace syntax:
`name@archive://https://...zip[?sha256=<hex>]`. The plugin installer
parses the scheme, downloads + verifies, extracts into the user
plugin dir, and registers `archive://<url>` as the source verbatim
so the registry parses it back the same way.
"""

import hashlib
import io
import json
import zipfile

from app.models.schemas import PluginInstallRequest
from app.services.plugin_installer_service import PluginInstaller


def _build_plugin_zip(plugin_name: str = "archive-foo") -> bytes:
    """Build a minimal plugin zip with .claude-plugin/plugin.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "name": plugin_name,
            "version": "0.1.0",
            "description": "fixture for archive:// tests",
        }
        zf.writestr(f"{plugin_name}/.claude-plugin/plugin.json", json.dumps(manifest))
        zf.writestr(f"{plugin_name}/README.md", "# fixture\n")
    return buf.getvalue()


# -- Parser -----------------------------------------------------------------


def test_parse_archive_source_extracts_url_and_optional_sha256():
    installer = PluginInstaller()
    pinned = "a" * 64
    parsed = installer._parse_archive_source(
        f"foo@archive://https://example.com/plugin.zip?sha256={pinned}"
    )
    assert parsed == {
        "plugin_name": "foo",
        "url": "https://example.com/plugin.zip",
        "sha256": pinned,
    }


def test_parse_archive_source_without_sha256_pinning():
    installer = PluginInstaller()
    parsed = installer._parse_archive_source(
        "foo@archive://https://example.com/plugin.zip"
    )
    assert parsed == {
        "plugin_name": "foo",
        "url": "https://example.com/plugin.zip",
        "sha256": None,
    }


def test_parse_archive_source_rejects_non_archive_schemes():
    installer = PluginInstaller()
    assert installer._parse_archive_source("foo@anthropic-marketplace") is None
    assert installer._parse_archive_source("plain-name") is None


def test_parse_archive_source_rejects_non_https():
    installer = PluginInstaller()
    assert (
        installer._parse_archive_source("foo@archive://http://example.com/plugin.zip")
        is None
    )


def test_parse_archive_source_rejects_bad_sha256_length():
    installer = PluginInstaller()
    parsed = installer._parse_archive_source(
        "foo@archive://https://example.com/plugin.zip?sha256=abc"
    )
    assert parsed is None


# -- SHA-256 verification ---------------------------------------------------


def test_verify_sha256_accepts_matching_digest():
    installer = PluginInstaller()
    payload = b"hello world"
    expected = hashlib.sha256(payload).hexdigest()
    assert installer._verify_sha256(payload, expected) is True


def test_verify_sha256_rejects_mismatch():
    installer = PluginInstaller()
    assert installer._verify_sha256(b"hello world", "0" * 64) is False


def test_verify_sha256_rejects_garbage_hex():
    installer = PluginInstaller()
    assert installer._verify_sha256(b"x", "not-hex") is False


# -- End-to-end install (mocked HTTP) --------------------------------------


def test_install_from_archive_success_registers_and_extracts(
    tmp_path, monkeypatch
):
    """HTTPS fetch → sha256 verify → extract → registered with archive:// source."""
    # Redirect user-config dir into tmp so we don't touch real ~/.claude.
    fake_plugins_dir = tmp_path / "plugins"
    fake_plugins_dir.mkdir()
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({"enabledPlugins": {}}))
    monkeypatch.setattr(
        "app.services.plugin_installer_service.get_claude_user_plugins_dir",
        lambda: fake_plugins_dir,
    )
    monkeypatch.setattr(
        "app.services.plugin_installer_service.get_claude_user_settings_file",
        lambda: fake_settings,
    )

    payload = _build_plugin_zip("archive-foo")
    digest = hashlib.sha256(payload).hexdigest()

    # Stub sync httpx.get via the module-level import in plugin_installer_service.
    class _Resp:
        def __init__(self, content: bytes):
            self.content = content
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, *args, **kwargs):
        return _Resp(payload)

    monkeypatch.setattr(
        "app.services.plugin_installer_service.httpx.get", fake_get
    )

    installer = PluginInstaller()
    request = PluginInstallRequest(
        name=f"archive-foo@archive://https://example.com/p.zip?sha256={digest}"
    )
    resp = installer.install_plugin(request)

    assert resp.success is True, resp.message
    extracted = fake_plugins_dir / "archive-foo" / ".claude-plugin" / "plugin.json"
    assert extracted.exists()

    # Registry shape: enabledPlugins uses name@<source>, source is verbatim archive:// URL.
    settings = json.loads(fake_settings.read_text())
    enabled = settings["enabledPlugins"]
    keys = [k for k in enabled if k.startswith("archive-foo@")]
    assert len(keys) == 1
    assert enabled[keys[0]] is True
    assert keys[0] == f"archive-foo@archive://https://example.com/p.zip?sha256={digest}"


def test_install_from_archive_rejects_sha256_mismatch(tmp_path, monkeypatch):
    """Tampered payload + pinning → success=False, nothing on disk."""
    fake_plugins_dir = tmp_path / "plugins"
    fake_plugins_dir.mkdir()
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({"enabledPlugins": {}}))
    monkeypatch.setattr(
        "app.services.plugin_installer_service.get_claude_user_plugins_dir",
        lambda: fake_plugins_dir,
    )
    monkeypatch.setattr(
        "app.services.plugin_installer_service.get_claude_user_settings_file",
        lambda: fake_settings,
    )

    payload = _build_plugin_zip("evil")
    # Pin to a deliberately-wrong hash.
    wrong_digest = "0" * 64

    class _Resp:
        status_code = 200

        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.services.plugin_installer_service.httpx.get",
        lambda url, *a, **kw: _Resp(payload),
    )

    installer = PluginInstaller()
    request = PluginInstallRequest(
        name=f"evil@archive://https://example.com/p.zip?sha256={wrong_digest}"
    )
    resp = installer.install_plugin(request)

    assert resp.success is False
    assert "sha256" in (resp.stderr or "").lower()
    assert not (fake_plugins_dir / "evil").exists()


def test_install_without_archive_scheme_keeps_existing_cli_path(monkeypatch):
    """Plain `name@marketplace` must NOT route through archive parsing."""
    installer = PluginInstaller()

    called = {"archive": False}

    def fake_archive(*a, **kw):
        called["archive"] = True
        raise AssertionError("archive path must not run for non-archive inputs")

    monkeypatch.setattr(installer, "_install_from_archive", fake_archive)

    def fake_cli_execute(*args, **kwargs):
        from app.services.cli_executor import CLIResult
        return CLIResult(stdout="installed", stderr="", exit_code=0)

    monkeypatch.setattr(
        installer.cli_executor, "execute", fake_cli_execute
    )

    resp = installer.install_plugin(PluginInstallRequest(name="plain@marketplace"))
    assert resp.success is True
    assert called["archive"] is False
