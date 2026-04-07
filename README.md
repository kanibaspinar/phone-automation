# Instagram & TikTok Phone Farm Automation

A Flask-based web application and REST API for managing Android devices and automating both **Instagram** and **TikTok** actions (login, follow/unfollow, likes, comments, stories, DMs, reels, photo posts, and more) using `uiautomator2`, ADB, and a task manager.

---
<img width="3827" height="1455" alt="image" src="https://github.com/user-attachments/assets/3c36675a-1b34-488b-bec5-232198ffe3a3" />

<img width="3829" height="1070" alt="image" src="https://github.com/user-attachments/assets/8ec787d6-66ec-4f59-8527-4ada26da6b11" />

<img width="3834" height="1158" alt="image" src="https://github.com/user-attachments/assets/47316868-b457-4f1c-b88b-51f61305943d" />
<img width="3828" height="1154" alt="image" src="https://github.com/user-attachments/assets/b53bde07-36bd-4d66-bdbb-769e2897c8d5" />
<img width="3820" height="1137" alt="image" src="https://github.com/user-attachments/assets/8a2103e6-d4dc-4e80-97e7-d148f37ff458" />
<img width="3832" height="1515" alt="image" src="https://github.com/user-attachments/assets/f5fa4e7e-c256-4994-a95c-98bd3db16026" />

## Features

- **Device management**
  - Discover, register, update, and delete Android devices.
  - Track device status (connected, disconnected, needs cleanup).
  - Bulk ADB operations: clear Instagram data, clear TikTok data, reboot, install helpers, start/stop `gnirehtet`.
- **Instagram account management**
  - Store accounts with device bindings, IMAP server/port, and login state.
  - Bulk import (`user:pass:email:email_password:imap_server:imap_port`) and bulk delete.
  - Track total and daily stats (likes, comments, follows, unfollows, story views, story likes, DMs).
- **TikTok account management**
  - Store accounts with target usernames, follow/like limits.
  - Track total and daily stats (follows, likes, comments, story likes, profile views).
  - Mass collection: fetch followers of target accounts, filter, and act.
- **Instagram automation**
  - Login and logout flows with email-based security code handling (IMAP/verification emails).
  - Actions: like posts, like stories, view stories, comment on posts/stories, follow/unfollow, send DMs.
  - Content posting: upload media, post photos, post reels with optional music and captions.
- **TikTok automation**
  - Follow, like posts, view profiles, comment, like stories.
  - `run-collection`: mass action across followers of target accounts with smart delays.
- **Task management**
  - Background task queues for Instagram and TikTok actions.
  - Live AJAX task monitor with auto-refresh (10s) and platform filtering.
  - Stop running TikTok collection tasks via UI or API.
- **Admin web UI**
  - Dashboard: device stats, IG logged-in/error counts, TikTok account count, daily action breakdowns.
  - Pages for device management, Instagram accounts, TikTok accounts, task monitoring, API docs.
  - Dark / light mode toggle (persisted in `localStorage`).
  - Add account modals with single and bulk import tabs.
- **API layer**
  - RESTful JSON endpoints under `/api` for devices, Instagram, and TikTok.
  - CORS enabled so you can drive it from other services.
  - Configurable `SERVER_URL` via `.env` (replaces hardcoded tunnel URL).

---

## Architecture Overview

| Layer | Technology |
|-------|-----------|
| Backend framework | Flask 3.0.3 |
| Database | SQLAlchemy 2.0 + Flask-Migrate / Alembic (SQLite by default) |
| Automation | `uiautomator2`, `adbutils`, ADB |
| OCR | `pytesseract` + `Pillow` + OpenCV |
| Background tasks | APScheduler + in-process worker threads |
| Admin UI | Flask blueprints + Jinja2 + Tailwind CSS v3 (CDN) + Font Awesome |
| API | Flask blueprints under `/api` |

Main entry points:

- **`run.py`** — starts the Flask app (`debug=True`)
- **`app/__init__.py`** — application factory, DB init, blueprint registration, background tasks
- **`app/api/device_routes.py`** — device JSON APIs
- **`app/api/instagram_routes.py`** — Instagram action APIs and account management
- **`app/api/tiktok_routes.py`** — TikTok action APIs and account management
- **`app/admin/routes.py`** — admin dashboard and UI pages
- **`app/utils/instagram_automation.py`** — core Android + Instagram UI automation
- **`app/utils/tiktok_automation.py`** — core Android + TikTok UI automation

---

## Requirements

- **Python**: 3.8 – 3.13 (recommended: **3.11**)
- **OS**: Windows, macOS, or Linux
- **Android**: devices with USB debugging enabled + ADB on your `PATH`
- **Optional OCR**: Tesseract installed (Windows default: `C:\Program Files\Tesseract-OCR\tesseract.exe`)

---

## Installing Python (Windows)

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download **Python 3.11.x**.
2. Run the installer — **check "Add Python 3.11 to PATH"**.
3. Verify:

```bash
python --version
pip --version
```

---

## Quick Setup (Windows)

### Option A — Automated (recommended)

Run **`install.bat`** — it handles everything automatically:

1. Checks Python is installed
2. Creates the `venv` virtual environment
3. Installs all dependencies from `requirements.txt`
4. Creates a `.env` file with default values (if one doesn't exist)
5. Creates the `uploads/` folder
6. Runs database migrations (`flask db upgrade`)

```
Double-click install.bat
```

Then start the server:

```
Double-click start.bat
```

### Option B — Manual

**1. Clone the repository**

```bash
git clone https://github.com/kanibaspinar/phone-automation.git
cd phone-automation
```

**2. Create and activate a virtual environment**

Windows:
```bash
python -m venv venv
venv\Scripts\activate
```

macOS / Linux:
```bash
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies**

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

**4. Run database migrations**

```bash
flask db upgrade
```

**5. Start the server**

```bash
python run.py
```

---

## Configuration

Create a `.env` file in the project root (or let `install.bat` generate one for you):

```dotenv
SECRET_KEY=change-this-to-a-random-secret
DATABASE_URL=sqlite:///instagram_farm.db

# Your public tunnel URL (localtonet, ngrok, etc.)
SERVER_URL=https://your-subdomain.localto.net
```

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `your-secret-key-here` | Flask session secret — change in production |
| `DATABASE_URL` | `sqlite:///instagram_farm.db` | SQLAlchemy DB URI |
| `SERVER_URL` | `http://localhost:5000` | Public URL returned in device assignment API responses |

### Getting a localtonet URL

1. Register at [localtonet.com](https://localtonet.com/) and install the client.
2. Run your backend locally.
3. Expose it: the client gives you a URL like `https://something.localto.net`.
4. Set `SERVER_URL=https://something.localto.net` in `.env`.

### Tesseract OCR (Windows)

Install from the official distribution and ensure the binary is at:

```
C:\Program Files\Tesseract-OCR\tesseract.exe
```

If your path differs, update it in `app/utils/instagram_automation.py`.

### ADB

Install Android platform tools and ensure `adb` is on your `PATH` (verify with `adb devices`).

---

## Running the Application

```bash
# With venv active:
python run.py

# Or on Windows:
start.bat
```

App starts at `http://127.0.0.1:5000`.

| URL | Description |
|-----|-------------|
| `http://127.0.0.1:5000/admin/` | Admin dashboard |
| `http://127.0.0.1:5000/admin/api-docs` | API documentation |
| `http://127.0.0.1:5000/api/` | REST API base |

---

## API Overview

All endpoints return JSON. Full docs at `/admin/api-docs`.

### Devices (`/api/devices`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/devices/list` | List all devices |
| POST | `/devices/create` | Create device |
| PUT | `/devices/{device_id}` | Update device |
| DELETE | `/devices/{device_id}` | Delete device |
| POST | `/devices/assign` | Assign device to user |
| POST | `/devices/{device_id}/unassign` | Unassign device |
| POST | `/devices/operations/bulk` | Bulk ADB ops (reboot, clear_instagram, clear_tiktok, etc.) |
| GET | `/devices/user/{user_id}` | Devices for user |
| GET | `/devices/free` | Unassigned devices |

### Instagram (`/api/instagram`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/instagram/accounts/list` | List accounts |
| POST | `/instagram/accounts` | Add account (supports `imap_server`, `imap_port`) |
| POST | `/instagram/accounts/bulk` | Bulk import |
| DELETE | `/instagram/accounts/{username}` | Delete account |
| POST | `/instagram/accounts/bulk-delete` | Delete multiple |
| POST | `/instagram/login` | Login on device |
| POST | `/instagram/logout` | Logout |
| GET | `/instagram/tasks` | List all tasks |
| GET | `/instagram/tasks/{task_id}` | Task status |
| POST | `/instagram/actions/like-post` | Like a post |
| POST | `/instagram/actions/follow` | Follow user |
| POST | `/instagram/actions/unfollow` | Unfollow user |
| POST | `/instagram/actions/view-story` | View story |
| POST | `/instagram/actions/like-story` | Like story |
| POST | `/instagram/actions/comment-post` | Comment on post |
| POST | `/instagram/actions/comment-story` | Comment on story |
| POST | `/instagram/actions/send-dm` | Send DM |
| POST | `/instagram/actions/post-reel` | Post reel |
| POST | `/instagram/actions/post-photo` | Post photo |
| POST | `/instagram/actions/upload-media` | Upload media |

### TikTok (`/api/tiktok`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tiktok/accounts/list` | List accounts |
| POST | `/tiktok/accounts` | Add account |
| PUT | `/tiktok/accounts/{username}` | Update account |
| DELETE | `/tiktok/accounts/{username}` | Delete account |
| POST | `/tiktok/accounts/bulk-delete` | Delete multiple |
| GET | `/tiktok/tasks` | List all tasks |
| GET | `/tiktok/tasks/{task_id}` | Task status |
| POST | `/tiktok/tasks/{task_id}/stop` | Stop task |
| POST | `/tiktok/actions/follow` | Follow user |
| POST | `/tiktok/actions/like-posts` | Like posts |
| POST | `/tiktok/actions/view-profile` | View profile |
| POST | `/tiktok/actions/comment` | Comment |
| POST | `/tiktok/actions/like-story` | Like story |
| POST | `/tiktok/actions/run-collection` | Mass collection |

---

## Admin Dashboard

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/admin/` | Device stats, IG active/errors, TT count, daily breakdowns |
| Devices | `/admin/devices` | Device table, bulk ADB actions |
| Instagram Accounts | `/admin/instagram/accounts` | Account table, single/bulk add modal |
| TikTok Accounts | `/admin/tiktok/accounts` | Account table, single/bulk add modal |
| Tasks | `/admin/tasks` | Live task monitor (IG + TikTok), auto-refresh |
| API Docs | `/admin/api-docs` | Full REST API documentation with search |

---

## GNIrehtet (Reverse Tethering)

Provides internet from your PC to all connected Android phones over USB — useful for stable connections.

1. Download from the official GNIrehtet GitHub repository.
2. Connect devices via USB (`adb devices` should list them).
3. Start reverse tethering via GNIrehtet.
4. The app's bulk operations include `start_gnirehtet` and `stop_gnirehtet` actions.

---

## Database Migrations

Migration files live in `migrations/versions/`. To apply:

```bash
flask db upgrade
```

To create a new migration after model changes:

```bash
flask db migrate -m "description"
flask db upgrade
```

---

## Security Considerations

- **Do not commit `.env`** — add it to `.gitignore` (already done).
- Instagram and email passwords are stored in plain text in this version. For production, encrypt sensitive fields and restrict DB access.
- Change `SECRET_KEY` to a long random string before deploying.

---

## Integration & Premium Support

- The API integrates with platforms like **Nextpost** or custom panels via `SERVER_URL` and the `/api` endpoints.
- **Telegram**: [@kanibaspinar](https://t.me/kanibaspinar)
- **Email**: kanibaspinar07@gmail.com

---



