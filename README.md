## Instagram Automation & Device Manager
Join to Discussion : [Phone Farmers](https://t.me/phonefarmers)


A Flask-based web application and API for managing Android devices and automating Instagram actions (login, follow/unfollow, likes, comments, stories, DMs, reels, photo posts, and more) using `uiautomator2`, ADB, and a task manager.
<img width="3290" height="1026" alt="image" src="https://github.com/user-attachments/assets/1b984807-7956-468b-9978-c8c387661099" />
<img width="3285" height="1605" alt="image" src="https://github.com/user-attachments/assets/a65013b4-8a9c-4dfe-94ed-7b54bf4108c6" />
<img width="3227" height="1560" alt="image" src="https://github.com/user-attachments/assets/90380f49-012d-4e71-bfbf-959b8aed29d4" />
<img width="3266" height="1564" alt="image" src="https://github.com/user-attachments/assets/29aa8a14-fd88-4871-8d83-6804019709be" />

### Features

- **Device management**
  - Discover, register, update, and delete Android devices.
  - Track device status (connected, disconnected, needs cleanup).
  - Bulk operations via ADB (clear Instagram, reboot, install helpers, start/stop `gnirehtet`, etc.).
- **Instagram account management**
  - Store accounts with device bindings and login state.
  - Bulk import and bulk delete accounts.
  - Track total and daily stats (likes, comments, follows, unfollows, story views, story likes, DMs).
- **Instagram automation**
  - Login and logout flows with email-based security code handling (TOTP/verification emails).
  - Actions: like posts, like stories, view stories, comment on posts, comment on stories, follow/unfollow users.
  - Content posting: upload media, post photos, post reels (from local files or URLs) with optional music and captions.
- **Task management**
  - Background task queue for Instagram actions.
  - Endpoints to create tasks and check their status.
- **Admin web UI**
  - Dashboard for devices, accounts, and stats.
  - Pages for device management, account management, and task monitoring.
  - Simple API docs page.
- **API layer**
  - RESTful JSON endpoints under `/api` for devices and Instagram actions.
  - CORS enabled so you can drive it from other services (e.g. FastAPI backend using `EXTERNAL_API_BASE_URL`).

---

## Architecture Overview

- **Backend framework**: Flask
- **Database**: SQLAlchemy + Flask-Migrate (default SQLite file `instagram_farm.db`)
- **Automation**: `uiautomator2`, ADB, Tesseract OCR (`pytesseract` + `Pillow`)
- **Background processing**: in-process task manager with worker threads
- **Admin UI**: Flask blueprints and Jinja templates under `/admin`
- **API**: Flask blueprints under `/api` for device and Instagram operations

Main entrypoints:

- **`run.py`** – starts the Flask app.
- **`app/__init__.py`** – application factory, database initialization, device manager, auto device manager, and background tasks.
- **`app/api/device_routes.py`** – JSON device APIs (assigning devices, listing free devices, bulk operations, etc.).
- **`app/api/instagram_routes.py`** – JSON Instagram action APIs and account management.
- **`app/admin/routes.py`** – admin dashboard and management pages.
- **`app/utils/instagram_automation.py`** – core Android + Instagram automation logic.

---

## Requirements

- **Python**: **3.8 – 3.13** (recommended: **3.11**)
- **OS**: Windows, macOS, or Linux
- **Android**:
  - Android device(s) with **USB debugging** enabled.
  - **Android platform tools** (ADB) installed and on your `PATH`.
- **Optional OCR**:
  - Tesseract installed (e.g. on Windows at `C:\Program Files\Tesseract-OCR\tesseract.exe`) for some flows that rely on OCR.

Python dependencies are managed via `requirements.txt` (Flask, SQLAlchemy, Flask-Migrate, Flask-Cors, Flask-Login, `uiautomator2`, `Pillow`, `pytesseract`, `emoji`, `requests`, etc.).

---

## Installing Python (Windows)

- **Download Python**:
  - Go to the official Python website: [python.org/downloads](https://www.python.org/downloads/).
  - Download a **Python 3.11.x** installer for Windows (recommended).
- **Install**:
  - Run the installer.
  - On the first screen, **check**: “Add Python 3.11 to PATH”.
  - Choose “Install Now” (or customize if you know what you’re doing).
- **Verify installation**:
  - Open PowerShell and run:

```bash
python --version
pip --version
```

Both commands should show Python 3.11 and a pip version.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/kanibaspinar/phone-automation.git
cd <phone-automation>
```

### 2. Create and activate a virtual environment

On **Windows (PowerShell)**:

```bash
python -m venv venv
venv\Scripts\activate
```

On **macOS / Linux**:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Configuration

Configuration is done via environment variables (loaded from `.env` using `python-dotenv`) and the `Config` class in `config.py`.

### 1. Create your `.env`

Use the provided `.env.example` as a template:

```bash
cp .env.example .env
```

Then edit `.env` and set:

```dotenv
SECRET_KEY=change-this-secret
DATABASE_URL=sqlite:///instagram_farm.db

# Required: external FastAPI base URL for device assignment responses, etc.
EXTERNAL_API_BASE_URL=https://your-fastapi.localto.net
```

> **Important:** `EXTERNAL_API_BASE_URL` is **required**. The app reads it as `Config.EXTERNAL_API_BASE_URL` and will fail to start if it’s missing.

### 2. Get your localtonet URL

This project expects `EXTERNAL_API_BASE_URL` to point to a public FastAPI (or other backend) URL. A common way to expose a local backend is **localtonet**.

- **Register & install localtonet**:
  - Go to [localtonet](https://localtonet.com/) and create an account.
  - Download and install the client for your OS (follow their documentation).
- **Run your FastAPI (or other) backend locally**.
- **Expose it via localtonet**:
  - Start a tunnel with localtonet pointing to your local backend port.
  - You will get a URL similar to `https://something.localto.net`.
- **Set it in `.env`**:

```dotenv
EXTERNAL_API_BASE_URL=https://your-subdomain.localto.net
```

Make sure this URL is reachable from the machines that call this Flask app.

### 3. Configure Tesseract (Windows)

Some flows (email verification, OCR-based clicks) use Tesseract:

- Install Tesseract from the official distribution.
- Make sure the binary is at:

```python
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

If your path is different, update it in `app/utils/instagram_automation.py`.

### 4. Configure ADB

- Install Android platform tools.
- Ensure `adb` is on your `PATH` (so `adb devices` works in a terminal).
- Connect your device(s) via USB (or network ADB).

---

## Running the Application

From the project root (with virtualenv activated and `.env` in place):

```bash
python run.py
```

By default, this will:

- Initialize the database (and recreate tables) using SQLAlchemy.
- Initialize the global device manager and background task runner.
- Start the Flask development server, usually at `http://127.0.0.1:5000`.

### Main URLs

- **Admin dashboard**: `http://127.0.0.1:5000/admin/`
- **Admin API docs page**: `http://127.0.0.1:5000/admin/api-docs`
- **REST API base**: `http://127.0.0.1:5000/api/`

---

## GNIrehtet (Reverse Tethering)

This app can use **GNIrehtet** to provide internet from your PC to all connected Android phones (reverse tethering), which is helpful for stable and controllable connections.

- **Install GNIrehtet**:
  - Download from the official GitHub repository (search for “GNIrehtet GitHub”).
  - Follow their installation instructions for your OS.
- **Enable reverse tethering**:
  - Connect your devices via USB and ensure `adb devices` lists them.
  - Use GNIrehtet commands (or the UI) to start reverse tethering for each device.
- **Integration with this app**:
  - Device bulk operations include actions like `start_gnirehtet` and `stop_gnirehtet`, which assume GNIrehtet is installed and available on the host machine.


📦 Installation of Web Mirror Screen for Android Phones

Install the required package:

pip install mysc[full]

This installs all required dependencies including the web interface and mirroring tools.

▶️ Running the Web Interface

After installation, start the mirror control panel via Command Line:

mysc-web

The system will automatically:

analyze connected devices

initialize the mirroring service

launch the web dashboard

By default the panel runs on:

http://localhost:51000
🌐 Remote Access (Optional)

If you want to access the panel remotely, you can create a web proxy using LocaltoNet.

Example

Create a tunnel for port:

51000

After connecting, LocaltoNet will generate a public URL such as:

https://yourdomain.localtonet.com

You can now access the mirror dashboard remotely from any browser.

## API Overview

All API endpoints return JSON.

- **Device endpoints** (in `app/api/device_routes.py`, under `/api`):
  - **List devices** – list all devices and their status.
  - **Create/update/delete devices** – manage device records.
  - **Assign devices** – assign one or more free devices to a user; response includes:
    - `server`: `EXTERNAL_API_BASE_URL`
    - `device_ids`: list of assigned device IDs
  - **List devices per user** – devices assigned to a specific `user_id`.
  - **List free devices** – unassigned devices with summary stats.
  - **Bulk operations** – run operations such as `clear_instagram`, `reboot`, `clean_apps`, `install_uiautomator`, `start_gnirehtet`, `stop_gnirehtet`.

- **Instagram endpoints** (in `app/api/instagram_routes.py`, under `/api`):
  - **Account lifecycle**:
    - Login / logout on a device.
    - Add single or multiple accounts.
    - List accounts with filters (device, login status, username search).
    - Delete single or multiple accounts.
  - **Engagement actions**:
    - Like posts, like stories, view stories.
    - Follow / unfollow users.
    - Comment on posts and stories.
    - Send direct messages.
  - **Content posting**:
    - Upload media files (photos/reels) to server-side storage.
    - Post reels and photos using local paths or remote URLs (downloaded and pushed via ADB).
  - **Task management**:
    - Create tasks for actions (login, like, comment, DM, post, etc.).
    - Query single task status or list all tasks.

For detailed parameters and response shapes, see `app/api/device_routes.py` and `app/api/instagram_routes.py`, or visit the admin API docs view at `/admin/api-docs`.

---

## Admin Dashboard

Under `/admin`, the web UI provides:

- **Dashboard** – overview of device counts, active accounts, total and daily stats.
- **Devices** – list and manage devices, trigger bulk ADB operations.
- **Instagram accounts** – list and manage accounts and their device bindings.
- **Tasks** – inspect queued and completed automation tasks.

These pages use the same underlying database models (`Device`, `InstagramAccount`, `DirectMessage`, `PostComment`) that back the API.

---

## Development Notes

- **Database migrations**:
  - The project includes Alembic migration scripts under `migrations/`.
  - You can use standard Flask-Migrate commands (`flask db migrate`, `flask db upgrade`) if you wire the CLI, but the app also calls `db.create_all()` on startup in `recreate_db`.
- **CORS**:
  - CORS is enabled globally so you can call the API from another frontend or backend (for example, from a FastAPI service at `EXTERNAL_API_BASE_URL`).
- **Logging**:
  - Logging is configured at `INFO` level in multiple modules to help debug device and automation issues.

---

## Security Considerations

- **Secrets in `.env`**:
  - Do **not** commit your real `.env` file to Git.
  - Only `.env.example` should be versioned.
- **Credentials storage**:
  - Instagram and email passwords are stored in the database in plain text in this version.
  - For production use, you should:
    - Encrypt sensitive fields at rest.
    - Restrict database and admin access.
    - Consider integrating a secrets manager.

---

## Integration & Premium Support

- **Integrations**:
  - The API can be integrated with other platforms such as **Nextpost** or your own panels/backends via `EXTERNAL_API_BASE_URL` and the `/api` endpoints.
- **Premium support / custom integrations**:
  - Telegram: [`@kanibaspinar`](https://t.me/kanibaspinar)
  - Email: `kanibaspinar07@gmail.com`
- Sharingtools Integration is free. 
## License

Add your license of choice here (for example, MIT, Apache 2.0, or proprietary), and include the corresponding `LICENSE` file in the repository.

