"""CLI commands for OpenCane."""

import asyncio
import contextlib
import json
import os
import re
import select
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from opencane import __logo__, __version__
from opencane.utils.helpers import get_data_path

app = typer.Typer(
    name="opencane",
    help=f"{__logo__} opencane - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = get_data_path() / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} opencane[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} opencane v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """opencane - Personal AI Assistant."""
    pass


# ============================================================================
# Config Commands
# ============================================================================


config_app = typer.Typer(help="Manage opencane config")
app.add_typer(config_app, name="config")

config_profile_app = typer.Typer(help="Manage deployment profile templates")
config_app.add_typer(config_profile_app, name="profile")


@config_app.command("check")
def config_check(
    config: Path | None = typer.Option(None, "--config", help="Config path to validate"),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail when unknown keys are detected (possible typos)",
    ),
):
    """Validate config JSON structure and schema."""
    from opencane.config.loader import _migrate_config, convert_keys, get_config_path
    from opencane.config.profile_merge import (
        find_unknown_paths,
        load_json_file,
        normalize_config_data,
    )
    from opencane.config.schema import Config

    config_path = (config or get_config_path()).expanduser()
    if not config_path.exists():
        console.print(f"[red]Config file not found:[/red] {config_path}")
        raise typer.Exit(2)

    try:
        raw = load_json_file(config_path)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/red] {exc}")
        raise typer.Exit(2) from exc
    except Exception as exc:
        console.print(f"[red]Failed to read config:[/red] {exc}")
        raise typer.Exit(2) from exc

    migrated = _migrate_config(raw)

    try:
        normalized = normalize_config_data(migrated)
        cfg = Config.model_validate(convert_keys(normalized))
    except Exception as exc:
        console.print(f"[red]Schema validation failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    unknown_paths = find_unknown_paths(migrated, normalized)
    if unknown_paths:
        console.print(
            f"[yellow]Unknown config keys detected ({len(unknown_paths)}):[/yellow]"
        )
        for item in unknown_paths[:10]:
            console.print(f"  - {item}")
        if len(unknown_paths) > 10:
            console.print(f"  - ... ({len(unknown_paths) - 10} more)")
        if strict:
            raise typer.Exit(1)

    console.print("[green]✓[/green] Config validation passed")
    console.print(f"path={config_path}")
    console.print(f"model={cfg.agents.defaults.model}")
    console.print(
        "hardware="
        f"{'on' if cfg.hardware.enabled else 'off'} "
        f"adapter={cfg.hardware.adapter} "
        f"control={cfg.hardware.control_host}:{cfg.hardware.control_port}"
    )
    console.print(
        "features="
        f"vision={'on' if cfg.vision.enabled else 'off'} "
        f"lifelog={'on' if cfg.lifelog.enabled else 'off'} "
        f"digital_task={'on' if cfg.digital_task.enabled else 'off'} "
        f"safety={'on' if cfg.safety.enabled else 'off'}"
    )


@config_profile_app.command("apply")
def config_profile_apply(
    profile: Path = typer.Option(..., "--profile", "-p", help="Profile JSON file path"),
    config: Path | None = typer.Option(None, "--config", help="Target config path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate merge only; do not write file"),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Do not create backup when target config already exists",
    ),
):
    """Merge profile JSON into config and validate schema."""
    from opencane.config.loader import get_config_path
    from opencane.config.profile_merge import (
        backup_config_file,
        load_json_file,
        merge_profile_data,
        write_json_file,
    )

    profile_path = profile.expanduser()
    config_path = (config or get_config_path()).expanduser()

    if not profile_path.exists():
        console.print(f"[red]Profile not found:[/red] {profile_path}")
        raise typer.Exit(2)

    try:
        existing_config = load_json_file(config_path) if config_path.exists() else {}
        profile_data = load_json_file(profile_path)
        merged_config = merge_profile_data(existing_config, profile_data)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/red] {exc}")
        raise typer.Exit(2) from exc
    except Exception as exc:
        console.print(f"[red]Failed to merge profile:[/red] {exc}")
        raise typer.Exit(2) from exc

    if dry_run:
        console.print("[green]✓[/green] Merge validation passed (dry-run)")
        console.print(f"profile={profile_path}")
        console.print(f"target={config_path}")
        console.print(f"top-level keys={', '.join(sorted(merged_config.keys()))}")
        return

    backup_path: Path | None = None
    if config_path.exists() and not no_backup:
        backup_path = backup_config_file(config_path)

    write_json_file(config_path, merged_config)

    if backup_path:
        console.print(f"[green]✓[/green] Backup created: {backup_path}")
    console.print(f"[green]✓[/green] Merged config written: {config_path}")


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize opencane configuration and workspace."""
    from opencane.config.loader import get_config_path, load_config, save_config
    from opencane.config.schema import Config
    from opencane.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    # Create default bootstrap files
    _create_workspace_templates(workspace)

    console.print(f"\n{__logo__} opencane is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.opencane/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]opencane agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/iflabx/opencane[/dim]")




def _create_workspace_templates(workspace: Path):
    """Create default workspace template files."""
    templates = {
        "AGENTS.md": """# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in memory/MEMORY.md; past events are logged in memory/HISTORY.md
""",
        "SOUL.md": """# Soul

I am OpenCane, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
""",
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
    }

    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)
            console.print(f"  [dim]Created {filename}[/dim]")

    # Create memory directory and MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")

    history_file = memory_dir / "HISTORY.md"
    if not history_file.exists():
        history_file.write_text("")
        console.print("  [dim]Created memory/HISTORY.md[/dim]")

    # Create skills directory for custom user skills
    skills_dir = workspace / "skills"
    skills_dir.mkdir(exist_ok=True)


def _make_provider(config):
    """Create LiteLLMProvider from config. Exits if no API key found."""
    from opencane.providers.litellm_provider import LiteLLMProvider
    p = config.get_provider()
    model = config.agents.defaults.model
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.opencane/config.json under providers section")
        raise typer.Exit(1)
    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(),
    )


def _apply_control_plane_runtime_overrides(
    runtime: object,
    safety_policy: object,
    cp_result: dict[str, object],
) -> tuple[str, str]:
    cp_data = cp_result.get("data")
    cp_map = cp_data if isinstance(cp_data, dict) else {}
    override_tts_mode = str(cp_map.get("tts_mode") or "").strip().lower()
    if override_tts_mode in {"device_text", "server_audio"} and hasattr(runtime, "tts_mode"):
        setattr(runtime, "tts_mode", override_tts_mode)

    try:
        override_timeout = int(cp_map.get("no_heartbeat_timeout_s") or 0)
    except (TypeError, ValueError):
        override_timeout = 0
    if override_timeout > 0 and hasattr(runtime, "no_heartbeat_timeout_s"):
        setattr(runtime, "no_heartbeat_timeout_s", max(10, override_timeout))

    safety_cfg = cp_map.get("safety")
    if isinstance(safety_cfg, dict):
        if "low_confidence_threshold" in safety_cfg and hasattr(safety_policy, "low_confidence_threshold"):
            with contextlib.suppress(TypeError, ValueError):
                setattr(safety_policy, "low_confidence_threshold", float(safety_cfg.get("low_confidence_threshold")))
        if "max_output_chars" in safety_cfg and hasattr(safety_policy, "max_output_chars"):
            with contextlib.suppress(TypeError, ValueError):
                setattr(safety_policy, "max_output_chars", max(64, int(safety_cfg.get("max_output_chars"))))

    source = str(cp_result.get("source") or "unknown")
    warning = str(cp_result.get("warning") or "")
    return source, warning


def _extract_control_plane_metadata(cp_result: dict[str, object]) -> dict[str, object]:
    cp_map = cp_result.get("data")
    data = cp_map if isinstance(cp_map, dict) else {}
    meta = cp_result.get("meta")
    if not isinstance(meta, dict):
        meta = cp_result.get("metadata")
    meta_map = dict(meta) if isinstance(meta, dict) else {}
    for key in (
        "config_version",
        "rollout_id",
        "issued_at",
        "issued_at_ms",
        "expires_at",
        "expires_at_ms",
        "rollback",
    ):
        if key in cp_result and key not in meta_map:
            meta_map[key] = cp_result.get(key)
        if key in data and key not in meta_map:
            meta_map[key] = data.get(key)

    config_version = str(
        meta_map.get("config_version")
        or meta_map.get("version")
        or ""
    ).strip()
    rollout_id = str(meta_map.get("rollout_id") or meta_map.get("rolloutId") or "").strip()
    issued_at_ms = _parse_cp_timestamp_ms(meta_map.get("issued_at_ms"), assume_ms=True)
    if issued_at_ms <= 0:
        issued_at_ms = _parse_cp_timestamp_ms(meta_map.get("issued_at"), assume_ms=False)
    expires_at_ms = _parse_cp_timestamp_ms(meta_map.get("expires_at_ms"), assume_ms=True)
    if expires_at_ms <= 0:
        expires_at_ms = _parse_cp_timestamp_ms(meta_map.get("expires_at"), assume_ms=False)
    rollback = _to_bool_value(meta_map.get("rollback"), default=False)
    return {
        "config_version": config_version,
        "rollout_id": rollout_id,
        "issued_at_ms": int(issued_at_ms),
        "expires_at_ms": int(expires_at_ms),
        "rollback": bool(rollback),
    }


def _should_apply_control_plane_config(
    current_meta: dict[str, object],
    incoming_meta: dict[str, object],
    *,
    now_ms: int,
) -> tuple[bool, str]:
    rollback = bool(incoming_meta.get("rollback"))
    expires_at_ms = _extract_int(incoming_meta.get("expires_at_ms"), default=0)
    if expires_at_ms > 0 and now_ms > expires_at_ms:
        return False, "expired_runtime_config"

    current_version = str(current_meta.get("config_version") or "").strip()
    incoming_version = str(incoming_meta.get("config_version") or "").strip()
    if not rollback and current_version and incoming_version:
        if _compare_version_token(incoming_version, current_version) < 0:
            return False, f"non_regressive_version_rejected:{incoming_version}<{current_version}"

    current_issued_ms = _extract_int(current_meta.get("issued_at_ms"), default=0)
    incoming_issued_ms = _extract_int(incoming_meta.get("issued_at_ms"), default=0)
    if not rollback and current_issued_ms > 0 and incoming_issued_ms > 0 and incoming_issued_ms < current_issued_ms:
        return False, "stale_issued_at_rejected"

    return True, ""


def _parse_cp_timestamp_ms(value: object, *, assume_ms: bool) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
        if not assume_ms and 0 < parsed < 10_000_000_000:
            parsed *= 1000
        return max(0, parsed)
    except (TypeError, ValueError):
        pass
    text = str(value or "").strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _compare_version_token(a: str, b: str) -> int:
    av = _version_token_parts(a)
    bv = _version_token_parts(b)
    if av == bv:
        return 0
    return 1 if av > bv else -1


def _version_token_parts(value: str) -> list[tuple[int, object]]:
    text = str(value or "").strip().lower()
    if not text:
        return [(0, 0)]
    parts = [p for p in re.split(r"[._-]+", text) if p]
    output: list[tuple[int, object]] = []
    for part in parts:
        if part.isdigit():
            output.append((0, int(part)))
        else:
            output.append((1, part))
    if not output:
        return [(0, 0)]
    return output


def _extract_int(value: object, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_bool_value(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


class _ControlPlaneRuntimeRefresher:
    """Applies runtime config from control-plane on startup and periodic refresh."""

    def __init__(
        self,
        *,
        client: object,
        runtime: object,
        safety_policy: object,
        refresh_seconds: float,
        now_fn: Callable[[], float] | None = None,
        now_ms_fn: Callable[[], int] | None = None,
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.safety_policy = safety_policy
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        self._now_fn = now_fn or time.monotonic
        self._now_ms_fn = now_ms_fn or (lambda: int(time.time() * 1000))
        self.next_refresh_at = float(self._now_fn()) + self.refresh_seconds
        self.last_source = ""
        self.last_warning = ""
        self.last_metadata: dict[str, object] = {}

    async def load_initial(self) -> tuple[str, str]:
        cp = await getattr(self.client, "fetch_runtime_config")()
        source, warning, _ = self._apply_if_allowed(cp)
        self.last_source = source
        self.last_warning = warning
        self.next_refresh_at = float(self._now_fn()) + self.refresh_seconds
        return source, warning

    async def refresh_if_due(self) -> dict[str, object] | None:
        now = float(self._now_fn())
        if now < self.next_refresh_at:
            return None
        cp = await getattr(self.client, "fetch_runtime_config")(force_refresh=True)
        source, warning, applied = self._apply_if_allowed(cp)
        changed_source = source != self.last_source
        changed_warning = warning != self.last_warning
        self.last_source = source
        self.last_warning = warning
        self.next_refresh_at = now + self.refresh_seconds
        return {
            "source": source,
            "warning": warning,
            "source_changed": changed_source,
            "warning_changed": changed_warning,
            "applied": applied,
            "metadata": dict(self.last_metadata),
        }

    def _apply_if_allowed(self, cp: dict[str, object]) -> tuple[str, str, bool]:
        metadata = _extract_control_plane_metadata(cp)
        allowed, reason = _should_apply_control_plane_config(
            self.last_metadata,
            metadata,
            now_ms=int(self._now_ms_fn()),
        )
        source = str(cp.get("source") or "unknown")
        warning = str(cp.get("warning") or "")
        if not allowed:
            final_warning = reason if not warning else f"{warning}; {reason}"
            return source, final_warning, False

        source, warning = _apply_control_plane_runtime_overrides(self.runtime, self.safety_policy, cp)
        if metadata:
            if (
                str(metadata.get("config_version") or "").strip()
                or _extract_int(metadata.get("issued_at_ms"), default=0) > 0
                or _extract_int(metadata.get("expires_at_ms"), default=0) > 0
                or bool(metadata.get("rollback"))
                or str(metadata.get("rollout_id") or "").strip()
            ):
                self.last_metadata = dict(metadata)
        return source, warning, True


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the opencane gateway."""
    from opencane.agent.loop import AgentLoop
    from opencane.api.lifelog_service import LifelogService
    from opencane.bus.queue import MessageBus
    from opencane.channels.manager import ChannelManager
    from opencane.config.loader import get_data_dir, load_config
    from opencane.cron.service import CronService
    from opencane.cron.types import CronJob
    from opencane.heartbeat.service import HeartbeatService
    from opencane.safety.policy import SafetyPolicy
    from opencane.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{__logo__} Starting opencane gateway on port {port}...")

    config = load_config()
    safety_policy = SafetyPolicy.from_config(config)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    lifelog_service = None
    if config.lifelog.enabled:
        try:
            lifelog_service = LifelogService.from_config(config, analyzer=None)
            console.print("[green]✓[/green] Lifelog service enabled")
        except Exception as e:
            console.print(f"[yellow]Lifelog service init failed, disabled: {e}[/yellow]")

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        safety_policy=safety_policy,
        lifelog_service=lifelog_service,
        max_subagents=config.agents.defaults.max_subagents,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        if job.payload.deliver and job.payload.to:
            from opencane.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response or ""
            ))
        return response
    cron.on_job = on_cron_job

    # Create heartbeat service
    async def on_heartbeat(prompt: str) -> str:
        """Execute heartbeat through the agent."""
        return await agent.process_direct(prompt, session_key="heartbeat")

    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 minutes
        enabled=True
    )

    # Create channel manager
    channels = ChannelManager(config, bus)

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print("[green]✓[/green] Heartbeat: every 30m")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()
            if lifelog_service:
                await lifelog_service.shutdown()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show opencane runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from opencane.agent.loop import AgentLoop
    from opencane.api.lifelog_service import LifelogService
    from opencane.bus.queue import MessageBus
    from opencane.config.loader import load_config
    from opencane.safety.policy import SafetyPolicy

    config = load_config()
    safety_policy = SafetyPolicy.from_config(config)

    bus = MessageBus()
    provider = _make_provider(config)
    lifelog_service = None
    if config.lifelog.enabled:
        try:
            lifelog_service = LifelogService.from_config(config, analyzer=None)
        except Exception as e:
            console.print(f"[yellow]Lifelog service init failed, disabled: {e}[/yellow]")

    if logs:
        logger.enable("opencane")
    else:
        logger.disable("opencane")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        safety_policy=safety_policy,
        lifelog_service=lifelog_service,
        max_subagents=config.agents.defaults.max_subagents,
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]opencane is thinking...[/dim]", spinner="dots")

    if message:
        # Single message mode
        async def run_once():
            try:
                with _thinking_ctx():
                    response = await agent_loop.process_direct(message, session_id)
                _print_agent_response(response, render_markdown=markdown)
            finally:
                await agent_loop.close_mcp()
                if lifelog_service:
                    await lifelog_service.shutdown()

        asyncio.run(run_once())
    else:
        # Interactive mode
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        with _thinking_ctx():
                            response = await agent_loop.process_direct(user_input, session_id)
                        _print_agent_response(response, render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                await agent_loop.close_mcp()
                if lifelog_service:
                    await lifelog_service.shutdown()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from opencane.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "✓" if dc.enabled else "✗",
        dc.gateway_url
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "✓" if mc.enabled else "✗",
        mc_base
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "✓" if slack.enabled else "✗",
        slack_config
    )

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    table.add_row(
        "DingTalk",
        "✓" if dt.enabled else "✗",
        dt_config
    )

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "QQ",
        "✓" if qq.enabled else "✗",
        qq_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "✓" if em.enabled else "✗",
        em_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    user_bridge = get_data_path() / "bridge"

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # opencane/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall opencane-ai")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from opencane.config.loader import load_config

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from opencane.config.loader import get_data_dir
    from opencane.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")

    import time
    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"

        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000))
            next_run = next_time

        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """Add a scheduled job."""
    from opencane.config.loader import get_data_dir
    from opencane.cron.service import CronService
    from opencane.cron.types import CronSchedule

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr)
    elif at:
        import datetime
        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.add_job(
        name=name,
        schedule=schedule,
        message=message,
        deliver=deliver,
        to=to,
        channel=channel,
    )

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from opencane.config.loader import get_data_dir
    from opencane.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from opencane.config.loader import get_data_dir
    from opencane.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from opencane.config.loader import get_data_dir
    from opencane.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    async def run():
        return await service.run_job(job_id, force=force)

    if asyncio.run(run()):
        console.print("[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show opencane status."""
    from opencane.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} opencane Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from opencane.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# Hardware Commands
# ============================================================================

hardware_app = typer.Typer(help="Run hardware runtime services")
app.add_typer(hardware_app, name="hardware")


@hardware_app.command("serve")
def hardware_serve(
    adapter: str | None = typer.Option(
        None,
        "--adapter",
        help="Adapter override: websocket/mock/ec600/generic_mqtt",
    ),
    host: str | None = typer.Option(None, "--host", help="Hardware adapter host override"),
    port: int | None = typer.Option(None, "--port", help="Hardware adapter port override"),
    mqtt_host: str | None = typer.Option(
        None,
        "--mqtt-host",
        help="MQTT broker host override (ec600/generic_mqtt)",
    ),
    mqtt_port: int | None = typer.Option(
        None,
        "--mqtt-port",
        help="MQTT broker port override (ec600/generic_mqtt)",
    ),
    control_port: int | None = typer.Option(None, "--control-port", help="Control API port override"),
    strict_startup: bool | None = typer.Option(
        None,
        "--strict-startup/--no-strict-startup",
        help="Fail fast on startup dependency degradation",
    ),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show runtime logs"),
):
    """Start device runtime, adapter ingress, and control endpoints."""
    from loguru import logger

    from opencane.agent.loop import AgentLoop
    from opencane.api.digital_task_service import DigitalTaskService
    from opencane.api.hardware_server import HardwareControlServer, create_adapter_from_config
    from opencane.api.lifelog_service import LifelogService
    from opencane.api.vision_server import VisionService
    from opencane.bus.queue import MessageBus
    from opencane.config.loader import load_config
    from opencane.control_plane import ControlPlaneClient
    from opencane.hardware.runtime import DeviceRuntimeCore
    from opencane.hardware.runtime.audio_pipeline import AudioPipeline
    from opencane.providers.transcription import (
        GroqTranscriptionProvider,
        OpenAITranscriptionProvider,
    )
    from opencane.providers.tts import OpenAITTSProvider, ToneTTSSynthesizer
    from opencane.safety.interaction_policy import InteractionPolicy
    from opencane.safety.policy import SafetyPolicy
    from opencane.session.manager import SessionManager
    from opencane.storage import SQLiteObservabilityStore

    config = load_config()
    if logs:
        logger.enable("opencane")
    else:
        logger.disable("opencane")

    if adapter:
        config.hardware.adapter = adapter
    if host:
        config.hardware.host = host
    if port:
        config.hardware.port = port
    if mqtt_host:
        config.hardware.mqtt.host = mqtt_host
    if mqtt_port:
        config.hardware.mqtt.port = mqtt_port
    if control_port:
        config.hardware.control_port = control_port

    strict_mode = (
        bool(config.hardware.strict_startup)
        if strict_startup is None
        else bool(strict_startup)
    )
    startup_errors: list[str] = []
    control_plane_client: ControlPlaneClient | None = None

    if not config.hardware.enabled:
        console.print("[yellow]hardware.enabled is false in config, starting anyway with current overrides.[/yellow]")
    if config.hardware.tts_mode not in {"device_text", "server_audio"}:
        msg = f"invalid hardware.tts_mode={config.hardware.tts_mode}"
        if strict_mode:
            startup_errors.append(msg)
        console.print(f"[yellow]{msg}, fallback to device_text[/yellow]")
        config.hardware.tts_mode = "device_text"
    config.hardware.apply_network_profile()
    safety_policy = SafetyPolicy.from_config(config)
    interaction_policy = InteractionPolicy.from_config(config)
    if config.hardware.control_plane.enabled and str(config.hardware.control_plane.base_url or "").strip():
        control_plane_client = ControlPlaneClient(
            enabled=True,
            base_url=config.hardware.control_plane.base_url,
            api_token=config.hardware.control_plane.api_token,
            runtime_config_path=config.hardware.control_plane.runtime_config_path,
            device_policy_path=config.hardware.control_plane.device_policy_path,
            timeout_seconds=config.hardware.control_plane.timeout_seconds,
            cache_ttl_seconds=config.hardware.control_plane.cache_ttl_seconds,
            fallback_runtime_config={
                "tts_mode": config.hardware.tts_mode,
                "no_heartbeat_timeout_s": max(20, config.hardware.heartbeat_seconds * 3),
            },
        )

    tts_synthesizer = None
    if config.hardware.tts_mode == "server_audio":
        openai_key = str(config.providers.openai.api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
        openai_base = config.providers.openai.api_base or os.environ.get("OPENAI_API_BASE")
        openai_headers = dict(config.providers.openai.extra_headers or {})
        custom_key = str(config.providers.custom.api_key or "").strip()
        custom_base = str(config.providers.custom.api_base or "").strip()
        custom_headers = dict(config.providers.custom.extra_headers or {})
        if openai_key or openai_headers:
            tts_synthesizer = OpenAITTSProvider(
                api_key=openai_key,
                api_base=openai_base,
                extra_headers=openai_headers,
            )
            console.print("[green]✓[/green] Server audio TTS provider: openai")
        elif (custom_key or custom_headers) and custom_base:
            tts_synthesizer = OpenAITTSProvider(
                api_key=custom_key,
                api_base=custom_base,
                extra_headers=custom_headers,
            )
            console.print("[green]✓[/green] Server audio TTS provider: custom-openai-compatible")
        else:
            tts_synthesizer = ToneTTSSynthesizer()
            msg = "server_audio is using built-in tone fallback (set OpenAI/custom key for natural speech)"
            if strict_mode:
                startup_errors.append(msg)
            console.print(f"[yellow]{msg}[/yellow]")

    provider = _make_provider(config)
    bus = MessageBus()
    session_manager = SessionManager(config.workspace_path)
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        safety_policy=safety_policy,
        max_subagents=config.agents.defaults.max_subagents,
    )

    vision_model = config.vision.model or config.agents.defaults.model
    vision_service = VisionService(
        provider,
        model=vision_model,
        max_image_bytes=config.vision.max_image_bytes,
        default_prompt=config.vision.default_prompt,
    ) if config.vision.enabled else None
    lifelog_service = None
    if config.lifelog.enabled:
        try:
            lifelog_service = LifelogService.from_config(config, analyzer=vision_service)
            console.print("[green]✓[/green] Lifelog service enabled")
        except Exception as e:
            msg = f"Lifelog service init failed, disabled: {e}"
            if strict_mode:
                startup_errors.append(msg)
            console.print(f"[yellow]{msg}[/yellow]")

    digital_task_service = None
    if config.digital_task.enabled:
        try:
            digital_task_service = DigitalTaskService.from_config(
                config,
                agent_loop=agent_loop,
            )
            console.print("[green]✓[/green] Digital task service enabled")
        except Exception as e:
            msg = f"Digital task service init failed, disabled: {e}"
            if strict_mode:
                startup_errors.append(msg)
            console.print(f"[yellow]{msg}[/yellow]")

    groq_stt_key = str(config.providers.groq.api_key or os.environ.get("GROQ_API_KEY") or "").strip()
    groq_stt_headers = dict(config.providers.groq.extra_headers or {})
    openai_stt_key = str(config.providers.openai.api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    openai_stt_base = config.providers.openai.api_base or os.environ.get("OPENAI_API_BASE")
    openai_stt_headers = dict(config.providers.openai.extra_headers or {})
    custom_stt_key = str(config.providers.custom.api_key or "").strip()
    custom_stt_base = str(config.providers.custom.api_base or "").strip()
    custom_stt_headers = dict(config.providers.custom.extra_headers or {})
    transcription_provider = None
    if groq_stt_key or groq_stt_headers:
        transcription_provider = GroqTranscriptionProvider(
            api_key=groq_stt_key,
            extra_headers=groq_stt_headers,
        )
        console.print("[green]✓[/green] STT provider: groq")
    elif openai_stt_key or openai_stt_headers:
        transcription_provider = OpenAITranscriptionProvider(
            api_key=openai_stt_key,
            api_base=openai_stt_base,
            extra_headers=openai_stt_headers,
        )
        console.print("[green]✓[/green] STT provider: openai")
    elif (custom_stt_key or custom_stt_headers) and custom_stt_base:
        transcription_provider = OpenAITranscriptionProvider(
            api_key=custom_stt_key,
            api_base=custom_stt_base,
            extra_headers=custom_stt_headers,
        )
        console.print("[green]✓[/green] STT provider: custom-openai-compatible")

    if not transcription_provider:
        msg = "No STT provider configured (Groq/OpenAI/custom); pure audio transcription is disabled."
        if strict_mode:
            startup_errors.append(msg)
        console.print(f"[yellow]{msg}[/yellow]")

    observability_store = None
    try:
        observability_store = SQLiteObservabilityStore(
            Path(config.hardware.observability_sqlite_path).expanduser(),
            max_rows=config.hardware.observability_max_samples,
        )
    except Exception as e:
        msg = f"Observability sqlite init failed: {e}"
        if strict_mode:
            startup_errors.append(msg)
        console.print(f"[yellow]{msg}[/yellow]")

    if strict_mode and startup_errors:
        console.print("[red]hardware strict startup failed[/red]")
        for item in startup_errors:
            console.print(f"  - {item}")
        if observability_store:
            observability_store.close()
        raise typer.Exit(1)

    async def _transcribe_audio(audio_bytes: bytes) -> str:
        if not transcription_provider:
            return ""
        filename = "audio.opus"
        content_type = "audio/ogg"
        if audio_bytes.startswith(b"RIFF"):
            filename = "audio.wav"
            content_type = "audio/wav"
        elif audio_bytes.startswith(b"OggS"):
            filename = "audio.ogg"
            content_type = "audio/ogg"
        return await transcription_provider.transcribe_bytes(
            audio_bytes,
            filename=filename,
            content_type=content_type,
        )

    audio_pipeline = AudioPipeline(
        transcribe_fn=_transcribe_audio if transcription_provider else None,
        enable_vad=config.hardware.audio.enable_vad,
        prebuffer_chunks=config.hardware.audio.prebuffer_chunks,
        jitter_window=config.hardware.audio.jitter_window,
        vad_silence_chunks=config.hardware.audio.vad_silence_chunks,
    )
    runtime = DeviceRuntimeCore(
        adapter=create_adapter_from_config(config.hardware),
        agent_loop=agent_loop,
        audio_pipeline=audio_pipeline,
        vision_service=vision_service,
        lifelog_service=lifelog_service,
        digital_task_service=digital_task_service,
        safety_policy=safety_policy,
        interaction_policy=interaction_policy,
        tts_mode=config.hardware.tts_mode,
        tts_synthesizer=tts_synthesizer,
        tts_audio_chunk_bytes=config.hardware.tts_audio_chunk_bytes,
        no_heartbeat_timeout_s=max(20, config.hardware.heartbeat_seconds * 3),
        device_auth_enabled=config.hardware.auth.device_auth_enabled,
        allow_unbound_devices=config.hardware.auth.allow_unbound_devices,
        require_activated_devices=config.hardware.auth.require_activated_devices,
        control_plane_client=control_plane_client,
        tool_result_enabled=config.hardware.tool_result.enabled,
        tool_result_mark_device_operation_enabled=config.hardware.tool_result.mark_device_operation_enabled,
        telemetry_normalize_enabled=config.hardware.telemetry.normalize_enabled,
        telemetry_persist_samples_enabled=config.hardware.telemetry.persist_samples_enabled,
    )
    if digital_task_service:
        async def _push_task_status(update: dict[str, object]) -> bool:
            device_id = str(update.get("device_id") or "").strip()
            if not device_id:
                return False
            session_id = str(update.get("session_id") or "").strip()
            task_data = update.get("task")
            task = task_data if isinstance(task_data, dict) else {}
            return await runtime.push_task_update(
                task_id=str(update.get("task_id") or ""),
                status=str(update.get("status") or ""),
                message=str(update.get("message") or ""),
                device_id=device_id,
                session_id=session_id,
                speak=bool(update.get("speak", True)),
                extra={
                    "event": str(update.get("event") or ""),
                    "error": str(task.get("error") or ""),
                    "result": task.get("result") if isinstance(task.get("result"), dict) else {},
                },
                trace_id=f"digital-task:{str(update.get('task_id') or '')}",
            )

        digital_task_service.set_status_callback(_push_task_status)

    console.print(f"{__logo__} hardware runtime")
    console.print(
        f"safety_policy={'on' if getattr(safety_policy, 'enabled', False) else 'off'} "
        f"threshold={getattr(safety_policy, 'low_confidence_threshold', 0.0)} "
        f"max_chars={getattr(safety_policy, 'max_output_chars', 0)}"
    )
    console.print(
        f"interaction_policy={'on' if getattr(interaction_policy, 'enabled', False) else 'off'} "
        f"emotion={'on' if getattr(interaction_policy, 'emotion_enabled', False) else 'off'} "
        f"proactive={'on' if getattr(interaction_policy, 'proactive_enabled', False) else 'off'} "
        f"silent={'on' if getattr(interaction_policy, 'silent_enabled', False) else 'off'}"
    )
    if config.hardware.adapter.lower() in {"ec600", "generic_mqtt"}:
        console.print(
            f"adapter={config.hardware.adapter} mqtt={config.hardware.mqtt.host}:{config.hardware.mqtt.port}"
        )
        console.print(
            f"topics up={config.hardware.mqtt.up_control_topic} audio={config.hardware.mqtt.up_audio_topic}"
        )
        if config.hardware.adapter.lower() == "generic_mqtt":
            console.print(f"device_profile={config.hardware.device_profile}")
        console.print(f"tts_mode={config.hardware.tts_mode}")
        console.print(
            f"profile={config.hardware.network_profile} "
            f"heartbeat={config.hardware.heartbeat_seconds}s "
            f"keepalive={config.hardware.mqtt.keepalive_seconds}s "
            f"reconnect={config.hardware.mqtt.reconnect_min_seconds}-{config.hardware.mqtt.reconnect_max_seconds}s"
        )
    else:
        console.print(
            f"adapter={config.hardware.adapter} ingress={config.hardware.host}:{config.hardware.port}"
        )
    console.print(
        f"control-api=http://{config.hardware.control_host}:{config.hardware.control_port}"
    )

    async def run() -> None:
        control: HardwareControlServer | None = None
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        refresher: _ControlPlaneRuntimeRefresher | None = None

        def _request_stop() -> None:
            stop_event.set()

        if os.name != "nt":
            signal.signal(signal.SIGINT, lambda *_: _request_stop())
            signal.signal(signal.SIGTERM, lambda *_: _request_stop())

        try:
            if control_plane_client is not None:
                refresher = _ControlPlaneRuntimeRefresher(
                    client=control_plane_client,
                    runtime=runtime,
                    safety_policy=safety_policy,
                    refresh_seconds=max(5.0, float(config.hardware.control_plane.cache_ttl_seconds)),
                )
                source, warning = await refresher.load_initial()
                console.print(
                    f"[green]✓[/green] control-plane source={source} "
                    f"tts_mode={runtime.tts_mode} timeout={runtime.no_heartbeat_timeout_s}s"
                )
                if warning:
                    console.print(f"[yellow]control-plane warning: {warning}[/yellow]")
            if digital_task_service:
                recovered = await digital_task_service.recover_unfinished_tasks(limit=200)
                if recovered > 0:
                    console.print(f"[yellow]Recovered {recovered} unfinished digital tasks[/yellow]")
            await runtime.start()
            control = HardwareControlServer(
                host=config.hardware.control_host,
                port=config.hardware.control_port,
                runtime=runtime,
                vision=vision_service,
                lifelog=lifelog_service,
                adapter=runtime.adapter,
                loop=loop,
                digital_task=digital_task_service,
                observability_store=observability_store,
                observability_max_samples=config.hardware.observability_max_samples,
                max_request_body_bytes=config.hardware.control_max_body_bytes,
                auth_enabled=config.hardware.auth.enabled,
                auth_token=config.hardware.auth.token,
                control_api_rate_limit_enabled=config.hardware.auth.control_api_rate_limit_enabled,
                control_api_rate_limit_rpm=config.hardware.auth.control_api_rate_limit_rpm,
                control_api_rate_limit_burst=config.hardware.auth.control_api_rate_limit_burst,
                control_api_replay_protection_enabled=config.hardware.auth.control_api_replay_protection_enabled,
                control_api_replay_window_seconds=config.hardware.auth.control_api_replay_window_seconds,
            )
            control.start()
            while not stop_event.is_set():
                if refresher is not None:
                    try:
                        refresh = await refresher.refresh_if_due()
                        if refresh and bool(refresh.get("source_changed")):
                            logger.info(
                                f"control-plane refresh source={refresh.get('source')} "
                                f"tts_mode={runtime.tts_mode} timeout={runtime.no_heartbeat_timeout_s}s"
                            )
                        if (
                            refresh
                            and bool(refresh.get("warning_changed"))
                            and str(refresh.get("warning") or "")
                        ):
                            logger.warning(f"control-plane refresh warning: {refresh.get('warning')}")
                    except Exception as e:
                        logger.warning(f"control-plane refresh failed: {e}")
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            if control:
                control.stop()
            await runtime.stop()
            if digital_task_service:
                await digital_task_service.shutdown()
            if lifelog_service:
                await lifelog_service.shutdown()
            if observability_store:
                observability_store.close()
            if control_plane_client:
                await control_plane_client.close()
            await agent_loop.close_mcp()

    asyncio.run(run())


if __name__ == "__main__":
    app()
