"""
CLI commands for the TU Wien Companion.

This package contains the Typer-based CLI application and its commands
for interacting with TISS and TUWEL services.
"""

import typer
from rich import print as rprint
from rich.console import Console

from tiss_tuwel_cli.clients.tiss import TissClient
from tiss_tuwel_cli.clients.tuwel import TuwelClient
from tiss_tuwel_cli.config import ConfigManager

# Initialize the CLI application
app = typer.Typer(
    help="TU Wien Companion - TISS & TUWEL CLI",
    add_completion=False,
    invoke_without_command=True,
)

# Shared console and configuration instances
console = Console()
config = ConfigManager()
tiss = TissClient()


@app.callback()
def main(
        ctx: typer.Context,
        interactive: bool = typer.Option(
            False,
            "--interactive",
            "-i",
            help="Start in interactive menu mode",
        ),
):
    """
    TU Wien Companion - TISS & TUWEL CLI.

    Run without arguments to start an interactive shell.
    Use -i or --interactive to start in menu mode.
    """
    if interactive:
        from tiss_tuwel_cli.cli.interactive import interactive as run_interactive
        run_interactive()
        raise typer.Exit()
    elif ctx.invoked_subcommand is None:
        # No command and no interactive flag - start shell mode
        from tiss_tuwel_cli.cli.shell import start_shell
        start_shell()
        raise typer.Exit()


def get_tuwel_client(force_new_token: bool = False, silent: bool = False) -> TuwelClient:
    """
    Get an authenticated TUWEL client, automatically handling token validation and refresh.

    Args:
        force_new_token: If True, will force a new token to be fetched even if a valid one exists.
        silent: If True, suppress user-facing status/error messages.

    Returns:
        An authenticated TuwelClient instance.

    Raises:
        typer.Exit: If no token can be obtained.
    """
    def _auto_login(*, silent: bool = True) -> bool:
        """Try to refresh login in the background using saved credentials."""
        if not config.get_setting("auto_login", True):
            return False

        user, passw = config.get_login_credentials()
        if not user or not passw:
            return False

        if not silent:
            rprint("[yellow]No valid token found. Attempting automatic re-login...[/yellow]")

        from tiss_tuwel_cli.cli.auth import _run_playwright_login_internal
        return _run_playwright_login_internal(user, passw, False, fast_path_only=True)

    token = config.get_tuwel_token()

    # 1. If no token (or forced), try background login first.
    if not token or force_new_token:
        success = _auto_login(silent=silent)
        if success:
            token = config.get_tuwel_token()

        if not token:
            if config.get_setting("auto_login", True) and not config.has_credentials():
                if not silent:
                    rprint("[bold red]Error:[/bold red] TUWEL credentials are not saved, so auto-login cannot run.")
                    rprint("Run [green]tiss-tuwel-cli login[/green] once to store credentials, then future logins happen automatically.")
            elif not config.get_setting("auto_login", True):
                if not silent:
                    rprint("[bold red]Error:[/bold red] Auto-login is disabled and no valid token is available.")
                    rprint("Enable auto-login in settings or run [green]tiss-tuwel-cli login[/green].")
            else:
                if not silent:
                    rprint("[bold red]Error:[/bold red] Automatic login failed.")
            raise typer.Exit(code=1)

    def refresh_callback() -> str:
        """Refresh the token when the API reports an auth/session error."""
        if not _auto_login(silent=True):
            raise Exception("Auto-login failed.")
        new_token = config.get_tuwel_token()
        if not new_token:
            raise Exception("Auto-login did not produce a token.")
        return new_token

    # Return the client directly — the token_refresh_callback handles invalid tokens
    # on-demand when an actual API call fails. Eagerly validating here with get_site_info()
    # causes unnecessary token churn: every transient error triggers a new login, and
    # each new Moodle mobile token invalidates the previous one.
    return TuwelClient(token, token_refresh_callback=refresh_callback)


# Import and register command modules
from tiss_tuwel_cli.cli import auth, courses, dashboard, features, timeline, todo, settings, rc

# Register commands
app.command()(auth.login)
app.command()(auth.logout)
app.command()(dashboard.dashboard)
app.command()(courses.courses)
app.command()(courses.assignments)
app.command()(courses.grades)
app.command()(courses.checkmarks)
app.command()(courses.download)
app.command()(courses.tiss_course)
app.command(name="track-participation")(courses.track_participation)
app.command(name="participation-stats")(courses.participation_stats)
app.command(name="open-vowi")(courses.open_vowi)

# Register new feature commands
app.command(name="export-calendar")(features.export_calendar)
app.command(name="course-stats")(features.course_statistics)
app.command(name="unified-view")(features.unified_course_view)

# Other commands
app.command()(timeline.timeline)
app.command()(todo.todo)
app.command()(settings.settings)
app.command()(rc.rc)

__all__ = ["app", "console", "config", "tiss", "get_tuwel_client"]
