"""
Authentication commands for the TU Wien Companion CLI.

This module provides commands for logging in and configuring
TUWEL authentication tokens.
"""

import time

import typer
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Prompt

from tiss_tuwel_cli.clients.tuwel import TuwelClient
from tiss_tuwel_cli.config import ConfigManager
from tiss_tuwel_cli.utils import parse_mobile_token

console = Console()
config = ConfigManager()


def login(
        manual: bool = typer.Option(False, "--manual", help="Start manual login by pasting a token URL instead of automating."),
        hybrid: bool = typer.Option(False, "--hybrid", help="Open browser for manual login, auto-capture token."),
        debug: bool = typer.Option(False, "--debug", help="Enable debug mode with non-headless browser and verbose logs.")
):
    """
    [Automated] Launches a browser to log in and captures the TUWEL token automatically.
    
    This command uses Playwright to automate the TUWEL login process.
    It can store your credentials in a local config.json file for fully automated
    future logins.
    """
    if manual:
        manual_login()
        return

    if hybrid:
        hybrid_login()
        return

    rprint("[yellow]Attempting automated TUWEL login...[/yellow]")

    user, passw = config.get_login_credentials()

    if not all([user, passw]):
        rprint("[cyan]No stored credentials found.[/cyan]")
        rprint("You can store your TUWEL credentials to enable fully automated logins.")

        save_creds = Prompt.ask("Store credentials for future logins?", choices=["y", "n"], default="y") == "y"

        if save_creds:
            rprint("[bold yellow]Warning:[/bold yellow] Credentials will be stored in plain text in your home directory.")
            rprint(f"Location: {config.config_file}")
            proceed = Prompt.ask("Continue?", choices=["y", "n"], default="y") == "y"
            if not proceed:
                rprint("[red]Aborted.[/red]")
                return

            user = Prompt.ask("Enter TUWEL Username")
            passw = Prompt.ask("Enter TUWEL Password", password=True)
            config.set_login_credentials(user, passw)
            rprint("[green]Credentials saved.[/green]")
        else:
            user = Prompt.ask("Enter TUWEL Username")
            passw = Prompt.ask("Enter TUWEL Password", password=True)

    with Progress() as progress:
        task = progress.add_task("[cyan]Logging in...", total=1)
        success = _run_playwright_login_internal(user, passw, debug)
        progress.update(task, advance=1)

    if success:
        rprint("[bold green]Token captured successfully![/bold green]")
        try:
            client = TuwelClient(config.get_tuwel_token())
            info = client.get_site_info()
            config.set_user_id(info.get('userid', 0))
            rprint(f"Authenticated as [cyan]{info.get('fullname')}[/cyan] (ID: {info.get('userid')}).")
        except Exception as e:
            rprint(f"[yellow]Warning: Token captured but validation failed: {e}[/yellow]")
    else:
        rprint("[bold red]Failed to capture token.[/bold red]")


LAUNCH_URL = "https://tuwel.tuwien.ac.at/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=student_api"


def _try_get_token(page, debug: bool) -> str:
    """
    Navigate to the mobile launch page and return the token URL if captured, else "".
    The server returns a 302 to moodlemobile://token=... which we capture from the
    Location header (reliable in headless mode; custom URI request events are not).
    """
    token_url = ""

    def on_response(response):
        nonlocal token_url
        if "launch.php" in response.url and response.status == 302:
            location = response.headers.get("location", "")
            if "moodlemobile://token=" in location:
                token_url = location
                if debug:
                    rprint(f"[bold green]>>> TOKEN URL CAPTURED from 302 redirect: {location}[/bold green]")

    page.on("response", on_response)

    try:
        page.goto(LAUNCH_URL)
    except PlaywrightTimeoutError:
        if debug:
            rprint("[magenta]Page.goto timed out (expected on moodlemobile:// redirect).[/magenta]")
    except Exception as e:
        if "net::ERR_ABORTED" not in str(e):
            raise
        if debug:
            rprint(f"[magenta]Ignoring expected ERR_ABORTED: {e}[/magenta]")

    # Poll briefly for the response handler to fire
    end_time = time.time() + (10 if not debug else 30)
    while time.time() < end_time:
        if token_url:
            break
        page.wait_for_timeout(100)

    page.remove_listener("response", on_response)
    return token_url


def _run_playwright_login_internal(user: str, passw: str, debug: bool) -> bool:
    """
    Internal helper to run Playwright login. Returns True on success, False on failure.

    Fast path: if a saved browser session exists, go directly to launch.php — no SSO
    needed (~1-2s). Falls back to full SSO flow if the session has expired.
    """
    storage_state_path = config.config_dir / "browser_state.json"

    try:
        with sync_playwright() as p:
            if debug:
                rprint("[bold magenta]DEBUG MODE ENABLED[/bold magenta]")

            browser = p.chromium.launch(headless=not debug)

            if debug:
                def _log_request(req):
                    rprint(f"[magenta]>> Request: {req.method} {req.url}[/magenta]")
                def _log_response(res):
                    rprint(f"[magenta]<< Response: {res.status} {res.url}[/magenta]")

            # --- Fast path: reuse saved browser session ---
            token_url = ""
            if storage_state_path.exists():
                if debug:
                    rprint("[magenta]Trying fast path with saved browser session...[/magenta]")
                context = browser.new_context(storage_state=str(storage_state_path))
                page = context.new_page()
                if debug:
                    page.on("request", _log_request)
                    page.on("response", _log_response)
                token_url = _try_get_token(page, debug)
                if debug:
                    rprint(f"[magenta]Fast path result: {'success' if token_url else 'session expired, falling back'}[/magenta]")
                if not token_url:
                    context.close()

            # --- Slow path: full SSO login ---
            if not token_url:
                context = browser.new_context()
                page = context.new_page()
                if debug:
                    page.on("request", _log_request)
                    page.on("response", _log_response)

                # 1. Go to login page and authenticate via TU Wien SSO
                page.goto("https://tuwel.tuwien.ac.at/login/index.php")
                if debug:
                    rprint(f"[magenta]On page: {page.title()} ({page.url})[/magenta]")

                # 2. Click TU Wien Login button
                page.wait_for_selector('a:has-text("TU Wien Login")').click()
                if debug:
                    page.wait_for_selector('input[name="username"]')
                    rprint(f"[magenta]On page: {page.title()} ({page.url})[/magenta]")

                # 3. Fill and submit credentials
                page.fill('input[name="username"]', user)
                page.fill('input[name="password"]', passw)
                page.click('button:has-text("Log in")')
                page.wait_for_url('**/tuwel.tuwien.ac.at/**', timeout=30000)
                if debug:
                    rprint(f"[magenta]On page: {page.title()} ({page.url})[/magenta]")

                token_url = _try_get_token(page, debug)

            # Save session for fast path next time
            try:
                context.storage_state(path=str(storage_state_path))
                if debug:
                    rprint(f"[magenta]Browser session saved to {storage_state_path}[/magenta]")
            except Exception:
                pass

            if debug and not token_url:
                rprint("[bold red]DEBUG: Timed out waiting for token. Dumping page content:[/bold red]")
                try:
                    rprint(page.content())
                except Exception as e:
                    rprint(f"[bold red]Could not get page content: {e}[/bold red]")

            browser.close()

    except PlaywrightTimeoutError as e:
        rprint("[bold red]Login failed: Timed out waiting for a page element.[/bold red]")
        rprint("This could be due to a slow connection or a change in TUWEL's page structure.")
        if debug:
            rprint(f"[magenta]Playwright error: {e}[/magenta]")
        return False
    except Exception as e:
        rprint(f"[bold red]An unexpected error occurred:[/bold red] {e}")
        return False

    if not token_url:
        rprint("[bold red]Failed to capture the token URL.[/bold red]")
        rprint("It's possible the login failed or the page structure has changed.")
        return False

    found_token = parse_mobile_token(token_url)

    if found_token:
        config.set_tuwel_token(found_token)
        return True
    else:
        return False


def manual_login():
    """
    [Manual] Configure TUWEL token by pasting the redirect URL.
    
    This command guides you through manually obtaining and configuring
    your TUWEL authentication token. Use this if the automated login
    doesn't work.
    """
    console.print(Panel("[bold blue]TU Wien Companion Login[/bold blue]", expand=False))
    rprint("1. Go to: [link]https://tuwel.tuwien.ac.at/admin/tool/mobile/launch.php"
           "?service=moodle_mobile_app&passport=student_api[/link]")
    rprint("2. Login and wait for the 'Address not understood' or failed redirect page.")
    rprint("3. Copy the [bold]entire URL[/bold] from the address bar (starting with moodlemobile://) or find it in the developer console or in the network tab.")

    user_input = Prompt.ask("Paste URL or Token")

    token = parse_mobile_token(user_input)

    # If parsing failed, maybe they pasted the raw token directly?
    if not token and ":::" not in user_input and "token=" not in user_input:
        token = user_input

    if not token:
        rprint("[bold red]Invalid input format.[/bold red]")
        return

    try:
        client = TuwelClient(token)
        info = client.get_site_info()
        user_id = info.get('userid', 0)
        config.set_tuwel_token(token)
        config.set_user_id(user_id)
        rprint(f"[bold green]Success![/bold green] Authenticated as [cyan]{info.get('fullname')}[/cyan].")
    except Exception as e:
        rprint(f"[bold red]Authentication failed:[/bold red] {e}")


def hybrid_login():
    """
    [Hybrid] Opens a browser for manual login, captures the token automatically.
    
    This mode provides a middle ground between fully automated and manual login:
    - Browser opens visibly (non-headless)
    - User manually clicks through the login process
    - Token URL is captured automatically when login completes
    """
    console.print(Panel("[bold blue]Hybrid Login[/bold blue]", expand=False))
    rprint("[cyan]Opening browser for manual login...[/cyan]")
    rprint("[dim]Please log in manually. The token will be captured automatically.[/dim]")
    rprint()

    token_url = ""

    try:
        with sync_playwright() as p:
            storage_state_path = config.config_dir / "browser_state.json"

            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                storage_state=storage_state_path if storage_state_path.exists() else None
            )
            page = context.new_page()

            # Listener for the token URL
            def on_request(request):
                nonlocal token_url
                if "moodlemobile://token=" in request.url:
                    token_url = request.url
                    rprint(f"[bold green]✓ Token captured![/bold green]")

            page.on("request", on_request)

            # Navigate to the mobile token page which will trigger login
            page.goto("https://tuwel.tuwien.ac.at/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=student_api")

            rprint("[yellow]Waiting for you to complete login...[/yellow]")
            rprint("[dim]The browser will close automatically once the token is captured.[/dim]")

            # Poll for token capture - wait up to 5 minutes (manual login can take time)
            timeout_seconds = 300
            end_time = time.time() + timeout_seconds
            while time.time() < end_time:
                if token_url:
                    break
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    # Page may be closed or navigating
                    if token_url:
                        break
                    continue

            # Save session state
            try:
                context.storage_state(path=storage_state_path)
            except Exception:
                pass  # May fail if browser was closed

            browser.close()

    except PlaywrightTimeoutError:
        rprint("[bold red]Login timed out.[/bold red]")
        return
    except Exception as e:
        if not token_url:
            rprint(f"[bold red]Error during login:[/bold red] {e}")
            return

    if not token_url:
        rprint("[bold red]Failed to capture token. Please try again or use manual mode.[/bold red]")
        return

    found_token = parse_mobile_token(token_url)

    if found_token:
        config.set_tuwel_token(found_token)
        try:
            client = TuwelClient(found_token)
            info = client.get_site_info()
            config.set_user_id(info.get('userid', 0))
            rprint(f"[bold green]Success![/bold green] Authenticated as [cyan]{info.get('fullname')}[/cyan].")
        except Exception as e:
            rprint(f"[yellow]Token saved but validation failed: {e}[/yellow]")
    else:
        rprint("[bold red]Failed to parse token from URL.[/bold red]")
