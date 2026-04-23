# Authentication Setup: TISS & TUWEL

## Overview

This project integrates with two TU Wien systems:

| System | Auth Required | Method |
|--------|--------------|--------|
| **TISS** | No | Public REST API, no token needed |
| **TUWEL** | Yes | Moodle mobile app token |

---

## TISS API

TISS (`https://tiss.tuwien.ac.at/api`) is a **public, unauthenticated API**. No setup is required — just instantiate the client and call it:

```python
from tiss_tuwel_cli.clients.tiss import TissClient

client = TissClient()
course = client.get_course_details("192.167", "2025W")
exams = client.get_exam_dates("192.167")
events = client.get_public_events()
```

---

## TUWEL API

TUWEL uses Moodle's web service API which requires a **mobile app token**.

### Token Format

The mobile login flow issues a `moodlemobile://` redirect URL. The token is base64-encoded in the URL:

```
moodlemobile://token=BASE64(PASSPORT:::TOKEN:::PRIVATE)
```

The actual API token is the **middle segment** (between the `:::` separators). See `utils.py:parse_mobile_token()`.

### Login Modes

Run `tiss-tuwel-cli login` to authenticate. Three modes are available:

#### 1. Automated (default)
```bash
tiss-tuwel-cli login
```
Uses Playwright (Chromium) to automate the full SSO login flow. Credentials can be stored in `~/.tu_companion/config.json` for future headless logins.

**Fast path**: If a browser session was previously saved to `~/.tu_companion/browser_state.json`, login skips SSO and goes straight to the token endpoint (~1–2s). Falls back to full SSO if the session has expired.

#### 2. Hybrid
```bash
tiss-tuwel-cli login --hybrid
```
Opens a visible browser window. You log in manually; the token is captured automatically. Useful if SSO has MFA or CAPTCHAs that block automation.

#### 3. Manual
```bash
tiss-tuwel-cli login --manual
```
No browser automation. Follow the printed instructions to open the launch URL in your own browser, then paste the resulting `moodlemobile://...` URL (or raw token) back into the terminal.

**Manual steps:**
1. Open: `https://tuwel.tuwien.ac.at/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=student_api`
2. Log in via TU Wien SSO
3. When the browser shows "Address not understood" (the `moodlemobile://` redirect), copy the full URL from the address bar or browser devtools Network tab
4. Paste it into the CLI prompt

### Debug Mode

Add `--debug` to any automated login to run Chromium in visible mode with full request/response logging:

```bash
tiss-tuwel-cli login --debug
```

### Configuration Storage

Config is stored at `~/.tu_companion/config.json`:

```json
{
    "tuwel_token": "<your_token>",
    "tuwel_userid": 12345,
    "tuwel_user": "e12345678",
    "tuwel_pass": "your_password"
}
```

> **Warning:** Credentials are stored in plain text. The CLI warns you before saving them.

Browser session state is cached at `~/.tu_companion/browser_state.json` (cookies + localStorage).

---

## Using the TUWEL Client

```python
from tiss_tuwel_cli.clients.tuwel import TuwelClient
from tiss_tuwel_cli.config import ConfigManager

config = ConfigManager()
client = TuwelClient(config.get_tuwel_token())

info = client.get_site_info()      # Validate token, get user info
courses = client.get_enrolled_courses()
calendar = client.get_upcoming_calendar()
```

### Token Refresh

Pass a `token_refresh_callback` to automatically re-authenticate when the token expires:

```python
def refresh():
    # Re-run login flow and return new token
    ...

client = TuwelClient(token, token_refresh_callback=refresh)
```

The client automatically retries on `invalidtoken`, `invalidsession`, and `accessexception` errors.

---

## Lessons Learned

### TISS API

**1. The API ignores `Accept: application/json` and often returns XML**

Setting `Accept: application/json` causes HTTP 500 errors on some TISS endpoints. Remove the header entirely and implement a JSON-then-XML fallback parser.

```python
# Don't do this — causes 500:
# requests.get(url, headers={"Accept": "application/json"})

# Do this instead — let TISS choose, then parse whatever comes back:
response = requests.get(url, timeout=self.timeout)
try:
    return response.json()
except ValueError:
    return parse_xml(response.content)  # fallback
```

**2. TISS returns HTTP 404 for "no results", not an empty list**

Endpoints like `/event` and `/examDates` return 404 when there are simply no records. Treat this as an empty list, not an error:

```python
if e.response.status_code == 404:
    if "/event" in endpoint or "/examDates" in endpoint:
        return []
```

**3. XML namespaces in TISS responses are non-trivial**

TISS XML uses two namespaces:
- `https://tiss.tuwien.ac.at/api/schemas/course/v10` (default)
- `https://tiss.tuwien.ac.at/api/schemas/i18n/v10` (ns2, for localized fields)

ElementTree requires explicit namespace URIs in `find()` calls. Build a fallback that tries both namespaced and non-namespaced tag lookups for robustness.

---

### TUWEL / Moodle API

**4. Moodle expects lists as indexed arrays, not bracket notation**

`key[]=value` is inconsistently handled across Moodle plugins. Use explicit indices:

```python
# Correct:
[("courseids[0]", 123), ("courseids[1]", 456)]

# Avoid:
[("courseids[]", 123), ("courseids[]", 456)]
```

**5. Capturing the token via HTTP 302 is reliable; request interception of `moodlemobile://` is not**

In headless Playwright, intercepting the custom URI scheme (`moodlemobile://`) via request events is unreliable. Instead, listen on `response` events for the `302` redirect from `launch.php` and read the `Location` header:

```python
def on_response(response):
    if "launch.php" in response.url and response.status == 302:
        location = response.headers.get("location", "")
        if "moodlemobile://token=" in location:
            token_url = location
```

**6. The `page.goto()` call raises on `moodlemobile://` redirect — this is expected**

When Chromium follows the `moodlemobile://` URI, `goto()` raises either `TimeoutError` or `net::ERR_ABORTED`. Both are harmless — the 302 response event fires before the exception. Catch and ignore both:

```python
try:
    page.goto(LAUNCH_URL)
except PlaywrightTimeoutError:
    pass  # Expected when moodlemobile:// redirect is followed
except Exception as e:
    if "net::ERR_ABORTED" not in str(e):
        raise
```

**7. Reusing saved browser sessions avoids full SSO on every login**

Save `context.storage_state()` after a successful login. On subsequent runs, load it with `browser.new_context(storage_state=path)` and go directly to `launch.php`. This skips the entire SSO flow when the session is still valid (~1–2s vs 5–15s for full login).

**8. The TUWEL token format requires base64 decoding and string splitting**

The token embedded in the `moodlemobile://` URL is:
```
base64( PASSPORT ::: ACTUAL_TOKEN ::: PRIVATE_KEY )
```
The passport is the value passed in the `?passport=` query param of the launch URL. The middle segment (`parts[1]`) is the Moodle web service token used for all API calls.
