# 🦉 TU TISS TUWEL CLI / TU Companion CLI

A Python CLI tool that merges TISS and TUWEL into a single terminal interface.

## Features

- **Unified Timeline** - TUWEL deadlines and TISS exams in one chronological list, exportable as `.ics`
- **Urgent Todo Alerts** - Notifications for upcoming Kreuzerlübungen with nothing ticked
- **Participation Tracking** - Track exercise frequency and estimate call probability
- **Bulk Downloads** - Download all course files recursively
- **Shell Integration** - `rc` command for quick status in your shell startup
- **Configurable Settings** - Widget selection, auto-login toggle, setup wizard

## Installation

Requires Python 3.9+.

```bash
git clone https://github.com/spheppner/tiss-tuwel-cli.git
cd tiss-tuwel-cli
uv sync
uv run playwright install
```

Run commands with `uv run`:

```bash
uv run tiss-tuwel-cli --help
```

## Authentication

Three login modes available:

### Fully Automated 🤖
Headless browser automation. Credentials can be saved for future logins.

```bash
uv run tiss-tuwel-cli login
```

### Hybrid 🌐
Opens visible browser for manual login. Token captured automatically.

```bash
uv run tiss-tuwel-cli login --hybrid
```

### Manual 🕵️
Generate the `moodlemobile://` token URL yourself and paste it.

```bash
uv run tiss-tuwel-cli login --manual
```

## Usage

### Core Commands

| Command                           | Description                                         |
|-----------------------------------|-----------------------------------------------------|
| `dashboard`                       | Upcoming deadlines, calendar events, tips           |
| `timeline`                        | Merged TUWEL/TISS timeline. Use `--export` for .ics |
| `todo`                            | Kreuzerlübung alerts (< 24h deadline, 0 ticked)     |
| `courses`                         | List enrolled courses                               |
| `assignments`                     | Active assignments with deadline highlighting       |
| `checkmarks`                      | Ticked vs total examples per course                 |
| `grades [course_id]`              | Grade table for a course                            |
| `download [course_id]`            | Download all course files                           |
| `tiss-course [number] [semester]` | Query TISS for course info                          |
| `settings`                        | Configure preferences and widgets                   |
| `rc`                              | One-line summary for shell startup                  |

### Shell Integration

Add to your `.bashrc` or `.zshrc`:

```bash
uv run tiss-tuwel-cli rc
```

Output example: `📅 2 deadlines | ⚠️ 1 urgent | 🎓 1 exam reg`

Configure displayed widgets via `tiss-tuwel-cli settings`.

### Interactive Mode

```bash
uv run tiss-tuwel-cli -i
```

Menu-driven interface organized into:
- **📚 Study** - Courses, Assignments, Checkmarks, Grades, Participation
- **📅 Planning & Deadlines** - Dashboard, Weekly, Timeline, Urgent Tasks
- **🛠️ Tools & Utilities** - Unified View, Exam Registration, Export Calendar, TISS Search
- **⚙️ Settings** - Configure widgets, auto-login, credentials

## Settings

Access via `uv run tiss-tuwel-cli settings` or interactive menu:

- **Auto-login** - Silent re-authentication when token expires
- **RC Widgets** - Choose what appears in the `rc` command output
- **Setup Wizard** - Guided initial configuration
- **Credential Management** - Save or delete stored credentials

## Known Issues

- **"Access Control Exception"**: Moodle quirk. Usually still works if token is saved.
- **TISS Limitations**: Read-only API. Cannot register for exams.
- **Windows**: Use Windows Terminal if display issues occur.

## Contributing

Contributions welcome. Submit a Pull Request.

### Credits
- TUWEL API: [student-api-documentation](https://github.com/tuwel-api/student-api-documentation)
- TISS API: [tiss public-api](https://tiss.tuwien.ac.at/api/dokumentation)

## Technical & Privacy Disclosure

### ⚠ Disclaimer
This tool uses unofficial methods. **Use at your own risk.**

The developers of this tool are unable to verify the complete security of the methods used, as they involve simulating user interactions and utilizing undocumented APIs intended for the official Moodle mobile app.

### 🔑 Token Mechanism
The TUWEL authentication flow in this tool simulates the login process used by the official Moodle Mobile App. 
- **Method:** It launches a browser (automated or manual), navigates to the official specific login page for the mobile app, and captures the `moodlemobile://token=` URL redirect.
- **Source:** [student-api-documentation](https://github.com/tuwel-api/student-api-documentation)

### ⏱ Token Expiration & Auto-Login
The access tokens generated via this method have a **very short lifetime (approx. 2-5 minutes)**. 

**Why use Auto-Login?**
Because the token expires so quickly, using the CLI without `auto_login` enabled would require you to manually log in almost every time you run a command or even in the middle of a session.

The **Auto-Login** feature securely (locally) stores your username and password to perform a silent, background authentication whenever your token expires. This ensures a seamless experience without constant interruptions. 

*Credentials are stored locally in `~/.tu_companion/config.json`. They are never sent anywhere except directly to the TUWEL login page.*