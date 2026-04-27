"""
Authentication commands for the TU Wien Companion CLI.

This module provides commands for logging in and configuring
TUWEL authentication tokens.
"""

import os
import re
import time
from typing import Callable, Optional

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

OTP_INPUT_SELECTORS = (
    'input[aria-label*="Multi-Factor Authentication Code" i]',
    'input[autocomplete="one-time-code"]',
    'input[name="otp"]',
    'input[name*="otp" i]',
    'input[name="totp"]',
    'input[name*="code" i]',
    'input[name*="mfa" i]',
    'input[name="tokenCode"]',
    "input#otp",
    'input[id*="otp" i]',
    'input[id*="code" i]',
    'input[id*="mfa" i]',
    "input#totp",
    'input[inputmode="numeric"]',
    'input[type="tel"]',
)
OTP_SUBMIT_SELECTORS = (
    'button:has-text("Verify")',
    'button:has-text("Verify code")',
    'button:has-text("Continue")',
    'button:has-text("Weiter")',
    'button:has-text("Anmelden")',
    'button:has-text("Bestätigen")',
    'button:has-text("Log in")',
    'button:has-text("Sign in")',
    "button[type='submit']",
    "input[type='submit']",
)
USERNAME_INPUT_SELECTORS = (
    'input[name="username"]',
    "input#username",
    'input[autocomplete="username"]',
)
PASSWORD_INPUT_SELECTORS = (
    'input[name="password"]',
    "input#password",
    'input[type="password"]',
    'input[autocomplete="current-password"]',
)
LOGIN_SUBMIT_SELECTORS = (
    'button:has-text("Log in")',
    'button:has-text("Sign in")',
    'button:has-text("Anmelden")',
    'button:has-text("Weiter")',
    "button[type='submit']",
    "input[type='submit']",
)


def _normalize_otp_code(raw: Optional[str]) -> Optional[str]:
    """Normalize and validate a 6-digit OTP code."""
    if raw is None:
        return None
    code = str(raw).strip().replace(" ", "")
    if re.fullmatch(r"\d{6}", code):
        return code
    return None


def _get_env_otp_code() -> Optional[str]:
    """Read an OTP code from TUWEL_OTP_CODE if provided."""
    return _normalize_otp_code(os.getenv("TUWEL_OTP_CODE", ""))


def _find_first_visible(page, selectors):
    """Return the first visible element matching any selector."""
    for selector in selectors:
        try:
            element = page.query_selector(selector)
            if element and element.is_visible():
                return element
        except Exception:
            continue
    return None


def _find_first_visible_on_page_or_frames(page, selectors):
    """Search the current page and all frames for a visible matching element."""
    element = _find_first_visible(page, selectors)
    if element:
        return element

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        element = _find_first_visible(frame, selectors)
        if element:
            return element
    return None


def _is_on_authenticated_tuwel_page(page) -> bool:
    """Return True when browser is back on TUWEL and not on login/session-timeout screen."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False

    return "tuwel.tuwien.ac.at" in url and not _looks_like_tuwel_login_page(page)


def _fill_otp_input(otp_input, otp_code: str, debug: bool) -> bool:
    """Fill OTP in a robust way for number/text inputs and JS-heavy forms."""
    try:
        otp_input.click()
    except Exception:
        pass

    # Primary path
    try:
        otp_input.fill(otp_code)
    except Exception:
        # Fallback for stricter number fields
        try:
            otp_input.press("Control+a")
        except Exception:
            pass
        try:
            otp_input.type(otp_code, delay=20)
        except Exception:
            return False

    # Ensure frontend listeners see changes.
    try:
        otp_input.evaluate(
            """
            (el, code) => {
                el.value = String(code);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            otp_code,
        )
    except Exception:
        pass

    if debug:
        try:
            current_value = otp_input.input_value()
            rprint(f"[magenta]OTP field value length after fill: {len(current_value)}[/magenta]")
        except Exception:
            pass

    return True


def _submit_credentials_if_visible(
        page,
        user: str,
        passw: str,
        debug: bool,
        otp_code_provider: Optional[Callable[[], Optional[str]]] = None,
) -> bool:
    """Fill username/password (and OTP if present) and submit if login form is visible."""
    username_input = _find_first_visible_on_page_or_frames(page, USERNAME_INPUT_SELECTORS)
    password_input = _find_first_visible_on_page_or_frames(page, PASSWORD_INPUT_SELECTORS)

    if not username_input or not password_input:
        return False

    try:
        username_input.fill(user)
        password_input.fill(passw)
    except Exception:
        return False

    # Some IdP screens render all three fields in one form. Fill OTP before submit
    # when present so credentials + MFA are sent together.
    otp_input = _find_first_visible_on_page_or_frames(page, OTP_INPUT_SELECTORS)
    if otp_input:
        otp_code = _get_env_otp_code()
        if not otp_code and otp_code_provider:
            otp_code = _normalize_otp_code(otp_code_provider())

        if otp_code:
            if not _fill_otp_input(otp_input, otp_code, debug):
                return False
        else:
            if debug:
                rprint("[magenta]Combined login form has OTP field but no OTP code available yet.[/magenta]")
            return False

    submit = _find_first_visible_on_page_or_frames(page, LOGIN_SUBMIT_SELECTORS)
    if submit:
        submit.click()
    else:
        password_input.press("Enter")

    if debug:
        rprint("[magenta]Submitted credentials on TU Wien SSO page.[/magenta]")

    return True


def _complete_tuwien_idp_auth(
        page,
        user: str,
        passw: str,
        otp_code_provider: Optional[Callable[[], Optional[str]]],
        debug: bool,
) -> bool:
    """Drive TU Wien IdP auth state machine until TUWEL is reached."""
    deadline = time.time() + 120
    last_credentials_submit_at = 0.0

    while time.time() < deadline:
        if _is_on_authenticated_tuwel_page(page):
            return True

        # Try credentials first; on combined forms this fills username, password,
        # and OTP in one submission.
        now = time.time()
        if now - last_credentials_submit_at > 2.0:
            if _submit_credentials_if_visible(page, user, passw, debug, otp_code_provider):
                last_credentials_submit_at = now
                page.wait_for_timeout(500)
                continue

        # If page is already at standalone MFA step, handle OTP here.
        if _handle_optional_otp_challenge(page, otp_code_provider, debug, max_wait_seconds=1.5):
            page.wait_for_timeout(400)
            continue

        page.wait_for_timeout(250)

    return _is_on_authenticated_tuwel_page(page)


def _looks_like_tuwel_login_page(page) -> bool:
    """Detect if we landed on TUWEL login/session-timeout page instead of token redirect."""
    try:
        url = page.url or ""
        if "login/index.php" in url:
            return True

        login_btn = _find_first_visible_on_page_or_frames(
            page,
            ('a:has-text("TU Wien Login")', 'button:has-text("TU Wien Login")'),
        )
        if login_btn:
            return True

        body_text = (page.inner_text("body") or "").lower()
        return "session has timed out" in body_text and "tuwel" in body_text
    except Exception:
        return False


def _handle_optional_otp_challenge(
        page,
        otp_code_provider: Optional[Callable[[], Optional[str]]],
        debug: bool,
    max_wait_seconds: float = 45,
) -> bool:
    """Handle optional OTP/TOTP MFA challenge if the page asks for one."""
    otp_input = None
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline and not otp_input:
        otp_input = _find_first_visible_on_page_or_frames(page, OTP_INPUT_SELECTORS)
        if otp_input:
            if debug:
                rprint("[magenta]Detected OTP input field.[/magenta]")
            break
        page.wait_for_timeout(250)

    if not otp_input:
        return False

    otp_code = _get_env_otp_code()
    if not otp_code and otp_code_provider:
        otp_code = otp_code_provider()

    otp_code = _normalize_otp_code(otp_code)

    if not otp_code:
        rprint("[bold red]MFA challenge detected, but no valid 6-digit OTP code was provided.[/bold red]")
        rprint("Set [cyan]TUWEL_OTP_CODE[/cyan] or provide the code when prompted.")
        return False

    # Some SSO pages render the field first, then enable it a moment later.
    input_ready_deadline = time.time() + 10
    while time.time() < input_ready_deadline:
        try:
            if otp_input.is_enabled():
                break
        except Exception:
            pass
        page.wait_for_timeout(100)

    if not _fill_otp_input(otp_input, otp_code, debug):
        rprint("[bold red]Failed to fill OTP field.[/bold red]")
        return False

    submit = _find_first_visible(page, OTP_SUBMIT_SELECTORS)
    if submit:
        submit.click()
    else:
        otp_input.press("Enter")

    if debug:
        rprint("[magenta]Submitted OTP code for MFA challenge.[/magenta]")

    return True


def login(
        manual: bool = typer.Option(False, "--manual", help="Start manual login by pasting a token URL instead of automating."),
        hybrid: bool = typer.Option(False, "--hybrid", help="Open browser for manual login, auto-capture token."),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode with non-headless browser and verbose logs."),
    otp_code: Optional[str] = typer.Option(None, "--otp-code", help="Optional 6-digit TUWEL Authenticator code for MFA."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Only try the browser session fast-path; exit immediately (code 1) if the session is expired instead of prompting for credentials or OTP."),
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

    provided_otp_code = _normalize_otp_code(otp_code)
    if otp_code and not provided_otp_code:
        rprint("[bold red]Invalid --otp-code value.[/bold red] Expected a 6-digit code.")
        return

    cached_prompt_otp: Optional[str] = None

    def _prompt_for_otp_code() -> Optional[str]:
        nonlocal cached_prompt_otp
        if provided_otp_code:
            return provided_otp_code
        env_code = _get_env_otp_code()
        if env_code:
            return env_code
        if cached_prompt_otp:
            return cached_prompt_otp
        code = Prompt.ask("Enter 6-digit TUWEL Authenticator code", password=True)
        cached_prompt_otp = _normalize_otp_code(code)
        return cached_prompt_otp

    if non_interactive:
        success = _run_playwright_login_internal(
            user or "", passw or "", debug, fast_path_only=True
        )
        if success:
            rprint("[bold green]Session reused successfully.[/bold green]")
        else:
            rprint("[bold red]No active session.[/bold red]")
            raise typer.Exit(code=1)
        return

    if not all([user, passw]):
        rprint("[cyan]No stored credentials found.[/cyan]")
        rprint("Tip: you can also set TUWEL_USERNAME and TUWEL_PASSWORD in a .env file.")
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
        success = _run_playwright_login_internal(
            user,
            passw,
            debug,
            otp_code_provider=_prompt_for_otp_code,
            force_full_sso=bool(provided_otp_code),
        )
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

    # Poll briefly for the response handler to fire. If we clearly landed on
    # the TUWEL login/session-timeout page, bail out early and trigger fallback.
    end_time = time.time() + (10 if not debug else 30)
    login_page_seen_at = None
    while time.time() < end_time:
        if token_url:
            break
        if _looks_like_tuwel_login_page(page):
            if login_page_seen_at is None:
                login_page_seen_at = time.time()
            elif time.time() - login_page_seen_at > 1.2:
                if debug:
                    rprint("[magenta]Detected TUWEL login/session-timeout page; fast-path token capture unavailable.[/magenta]")
                break
        else:
            login_page_seen_at = None
        page.wait_for_timeout(100)

    page.remove_listener("response", on_response)
    return token_url


def _run_playwright_login_internal(
    user: str,
    passw: str,
    debug: bool,
    otp_code_provider: Optional[Callable[[], Optional[str]]] = None,
    force_full_sso: bool = False,
    fast_path_only: bool = False,
) -> bool:
    """
    Internal helper to run Playwright login. Returns True on success, False on failure.

    Uses a saved browser session fast-path when available to avoid unnecessary
    MFA prompts. If force_full_sso=True, skips session reuse and runs full SSO
    immediately. If fast_path_only=True, returns False immediately when the
    session is expired instead of attempting interactive SSO.
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

            token_url = ""

            # --- Fast path: reuse saved browser session ---
            if not force_full_sso and storage_state_path.exists():
                if debug:
                    rprint("[magenta]Trying fast path with saved browser session...[/magenta]")
                fast_context = browser.new_context(storage_state=str(storage_state_path))
                fast_page = fast_context.new_page()
                if debug:
                    fast_page.on("request", _log_request)
                    fast_page.on("response", _log_response)
                token_url = _try_get_token(fast_page, debug)
                if debug:
                    rprint(f"[magenta]Fast path result: {'success' if token_url else 'no valid session, falling back'}[/magenta]")
                fast_context.close()
            elif force_full_sso and debug:
                rprint("[magenta]OTP code provided: skipping session fast path and running full SSO.[/magenta]")

            # --- Fast-path-only mode: don't attempt interactive SSO ---
            if not token_url and fast_path_only:
                browser.close()
                return False

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
                    rprint(f"[magenta]On page: {page.title()} ({page.url})[/magenta]")

                # 3. Complete SSO flow (credentials + optional MFA) in one state-driven loop.
                if not _complete_tuwien_idp_auth(page, user, passw, otp_code_provider, debug):
                    raise PlaywrightTimeoutError("TU Wien SSO flow did not complete in time.")

                if debug:
                    rprint(f"[magenta]On page: {page.title()} ({page.url})[/magenta]")

                token_url = _try_get_token(page, debug)

                # Save session for future fast-path logins.
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


def logout():
    """Clear the saved TUWEL token and browser session, forcing a fresh login next time."""
    cleared = []

    config.clear_token()
    cleared.append("token")

    browser_state = config.config_dir / "browser_state.json"
    if browser_state.exists():
        browser_state.unlink()
        cleared.append("browser session")

    rprint(f"[green]Logged out.[/green] Cleared: {', '.join(cleared)}.")
    rprint("Run [cyan]tiss-tuwel-cli login[/cyan] to authenticate again.")


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

    # Common mistake: users paste an intermediate TU Wien SSO URL instead of the
    # final moodlemobile://token=... redirect URL.
    if "idp.zid.tuwien.ac.at" in user_input or "AuthState=" in user_input:
        rprint("[bold red]That is an intermediate SSO URL, not the TUWEL token redirect.[/bold red]")
        rprint("[yellow]After login, copy the URL that starts with[/yellow] [cyan]moodlemobile://token=[/cyan]")
        rprint("[yellow]or copy only the token value itself.[/yellow]")
        return

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
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            # Listener for 302 redirect Location header (most reliable in browser automation)
            def on_response(response):
                nonlocal token_url
                if "launch.php" in response.url and response.status == 302:
                    location = response.headers.get("location", "")
                    if "moodlemobile://token=" in location:
                        token_url = location
                        rprint("[bold green]✓ Token captured![/bold green]")

            # Fallback listener for engines that emit the custom URI as a request URL
            def on_request(request):
                nonlocal token_url
                if "moodlemobile://token=" in request.url:
                    token_url = request.url
                    rprint("[bold green]✓ Token captured![/bold green]")

            page.on("response", on_response)
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
