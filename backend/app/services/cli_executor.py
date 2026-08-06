"""Agentic-CLI-aware CLI executor service."""

import logging
import re
import shutil
import subprocess

from ..models.schemas import CLIResult
from .agentic_cli import get_agentic_cli
from .agentic_cli.base import AgenticCli

logger = logging.getLogger(__name__)

class AgenticCliExecutor:
    """Execute whitelisted agentic CLI commands with security constraints."""

    DEFAULT_CLI = "claude-code"
    SAFE_ARG_PATTERN = re.compile(r"^[A-Za-z0-9_./@:+,=%?#&-]{0,4096}$")

    def __init__(self, cli_id: str = DEFAULT_CLI):
        self.cli = get_agentic_cli(cli_id)
        self.cli_id = self.cli.id
        self.binary_path = self._find_binary(self.cli)
        self.ALLOWED_COMMANDS = self.cli.get_allowed_cli_commands()
        # Compatibility for existing callers that check claude_binary directly.
        self.claude_binary = self.binary_path if self.cli_id == "claude-code" else None

    def _find_binary(self, cli: AgenticCli) -> str | None:
        return shutil.which(cli.binary_name)

    def validate_command(self, command: str) -> bool:
        """
        Validate that the command is in the whitelist

        Args:
            command: The Claude CLI subcommand to validate

        Returns:
            True if command is allowed, False otherwise
        """
        return command in self.cli.get_allowed_cli_commands()

    def _validate_args(self, args: list[str]) -> list[str]:
        safe_args = []
        for arg in args:
            if not isinstance(arg, str):
                raise ValueError("CLI arguments must be strings")
            if "\x00" in arg or any(ord(char) < 32 for char in arg):
                raise ValueError("CLI arguments cannot contain control characters")
            if not self.SAFE_ARG_PATTERN.fullmatch(arg):
                raise ValueError("CLI argument contains unsupported characters")
            safe_args.append(arg)
        return safe_args

    def execute(
        self,
        command: str,
        args: list[str],
        timeout: int = 30,
        env: dict[str, str] | None = None
    ) -> CLIResult:
        """
        Execute an agentic CLI command

        Args:
            command: The CLI subcommand (must be whitelisted)
            args: List of arguments to pass to the command
            timeout: Maximum execution time in seconds (default: 30)
            env: Optional environment variables to pass to the command

        Returns:
            CLIResult containing stdout, stderr, and exit code

        Raises:
            ValueError: If command is not whitelisted or CLI binary not found
            subprocess.TimeoutExpired: If command execution exceeds timeout
        """
        if not self.validate_command(command):
            raise ValueError(
                f"Command '{command}' is not allowed. "
                f"Allowed commands for {self.cli.display_name}: "
                f"{', '.join(self.cli.get_allowed_cli_commands())}"
            )

        if not self.binary_path:
            raise ValueError(
                f"{self.cli.display_name} binary not found in PATH. "
                f"Please ensure {self.cli.display_name} is installed and accessible."
            )

        safe_args = self._validate_args(args)
        full_command = [self.binary_path, command] + safe_args

        try:
            # CLI commands use a resolved fixed binary, a CLI-owned subcommand
            # whitelist, shell=False, and validation for user-controlled arguments.
            # Confirmed by-design in docs/cockpit/security-scanning-decision.md
            # §2.1 (card beace361…). `# lgtm[...]` was the legacy LGTM syntax
            # and suppressed nothing — CodeQL only reads `# codeql[...]`.
            # codeql[py/command-line-injection]
            result = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )

            return CLIResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode
            )
        except subprocess.TimeoutExpired as e:
            return CLIResult(
                stdout=e.stdout.decode() if e.stdout else "",
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1
            )
        except Exception as e:
            return CLIResult(
                stdout="",
                stderr=f"Failed to execute command: {str(e)}",
                exit_code=-1
            )


class CLIExecutor(AgenticCliExecutor):
    """Backward-compatible Claude Code executor."""

    def __init__(self):
        super().__init__("claude-code")
