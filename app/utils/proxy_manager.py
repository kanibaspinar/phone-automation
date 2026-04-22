"""
proxy_manager.py
~~~~~~~~~~~~~~~~
Proxy lifecycle for account creation:

  1. Proxy6Client         — proxy6.net REST API (fetch / balance)
  2. LocalProxyForwarder  — unauthenticated local HTTP proxy that injects
                            Proxy-Authorization when tunnelling to proxy6.net
  3. Device helpers       — set / clear Android system proxy via ADB
  4. Gnirehtet helpers    — start / stop reverse tethering per device
  5. Pool helpers         — acquire / release proxies from the DB pool

Architecture
------------
Device ──Gnirehtet──▶ PC (local forwarder :PORT) ──▶ proxy6.net proxy ──▶ Internet

The device sets its Android global http_proxy to GNIREHTET_GATEWAY:PORT.
The local forwarder listens on 0.0.0.0:PORT and forwards all traffic to the
upstream proxy6.net proxy, injecting Proxy-Authorization automatically so the
Android device never needs to know the proxy credentials.
"""

import base64
import json
import logging
import os
import platform
import select
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ASSETS_DIR = os.path.join(_PROJECT_ROOT, "assets")
_IS_WINDOWS = platform.system() == "Windows"
ADB_PATH = os.path.join(_ASSETS_DIR, "adb.exe" if _IS_WINDOWS else "adb")
GNIREHTET_PATH = os.path.join(_ASSETS_DIR, "gnirehtet.exe" if _IS_WINDOWS else "gnirehtet")
SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "proxy_settings.json")

# IP of the host PC as seen by the Android device through the Gnirehtet VPN
GNIREHTET_GATEWAY = "10.0.2.2"

# ── Process / forwarder registries ───────────────────────────────────────────
_gnirehtet_procs: dict[str, subprocess.Popen] = {}
_gnirehtet_lock = threading.Lock()

_active_forwarders: dict[str, "LocalProxyForwarder"] = {}
_forwarders_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Settings (API key stored in proxy_settings.json)
# ─────────────────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f).get("proxy6_api_key", "")
        except Exception:
            pass
    return os.environ.get("PROXY6_API_KEY", "")


def set_api_key(key: str) -> None:
    data = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["proxy6_api_key"] = key
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# proxy6.net API client
# ─────────────────────────────────────────────────────────────────────────────

class Proxy6Client:
    _BASE = "https://proxy6.net/api/{key}/{method}"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, method: str, **params) -> dict:
        url = self._BASE.format(key=self.api_key, method=method)
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "yes":
            raise RuntimeError(f"proxy6.net API error: {data}")
        return data

    def get_proxies(self, state: str = "active") -> list[dict]:
        """Fetch all proxies. state: 'active' | 'expired' | 'all'"""
        data = self._get("getproxy", state=state)
        return list(data.get("list", {}).values())

    def get_balance(self) -> dict:
        data = self._get("getproxy", state="active")
        return {
            "balance": data.get("balance"),
            "currency": data.get("currency"),
            "count": data.get("list_count", 0),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Local proxy forwarder (HTTP + HTTPS CONNECT)
# ─────────────────────────────────────────────────────────────────────────────

class LocalProxyForwarder:
    """
    Minimal HTTP/HTTPS forwarding proxy.
    - Listens locally with no authentication required (device connects freely)
    - Injects Proxy-Authorization header for the upstream proxy6.net proxy
    - Handles both HTTP (plain forwarding) and HTTPS (CONNECT tunnelling)
    """

    def __init__(self, upstream_host: str, upstream_port: int,
                 upstream_user: str, upstream_pass: str):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self._auth_b64 = base64.b64encode(
            f"{upstream_user}:{upstream_pass}".encode()
        ).decode()
        self.local_port = self._free_port()
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def start(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", self.local_port))
        self._server.listen(20)
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True,
            name=f"proxy-fwd-{self.local_port}"
        )
        self._thread.start()
        logger.info("[PROXY_FWD] Port %d → %s:%d",
                    self.local_port, self.upstream_host, self.upstream_port)

    def stop(self) -> None:
        self._running = False
        try:
            if self._server:
                self._server.close()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while self._running:
            try:
                self._server.settimeout(1.0)
                client, _ = self._server.accept()
                threading.Thread(
                    target=self._handle_client, args=(client,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    logger.debug("[PROXY_FWD] Accept error")
                break

    def _handle_client(self, client: socket.socket) -> None:
        upstream: Optional[socket.socket] = None
        try:
            # Read request headers
            buf = b""
            client.settimeout(15)
            while b"\r\n\r\n" not in buf:
                chunk = client.recv(4096)
                if not chunk:
                    return
                buf += chunk

            header_block, _, body = buf.partition(b"\r\n\r\n")
            header_lines = header_block.split(b"\r\n")

            # Remove existing Proxy-Authorization and inject ours
            auth = f"Proxy-Authorization: Basic {self._auth_b64}".encode()
            filtered = [
                h for h in header_lines
                if not h.lower().startswith(b"proxy-authorization")
            ]
            filtered.append(auth)
            new_request = b"\r\n".join(filtered) + b"\r\n\r\n" + body

            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=10
            )
            upstream.sendall(new_request)
            self._relay(client, upstream)

        except Exception as exc:
            logger.debug("[PROXY_FWD] Client error: %s", exc)
        finally:
            for s in (client, upstream):
                if s:
                    try:
                        s.close()
                    except Exception:
                        pass

    @staticmethod
    def _relay(s1: socket.socket, s2: socket.socket) -> None:
        s1.settimeout(None)
        s2.settimeout(None)
        try:
            while True:
                r, _, _ = select.select([s1, s2], [], [], 60)
                if not r:
                    break
                for sock in r:
                    other = s2 if sock is s1 else s1
                    data = sock.recv(8192)
                    if not data:
                        return
                    other.sendall(data)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# DB pool helpers (require app context)
# ─────────────────────────────────────────────────────────────────────────────

def sync_proxies(api_key: str) -> tuple[int, int]:
    """
    Pull active proxies from proxy6.net and upsert them into the local DB.
    Returns (added, updated).
    """
    from app.extensions import db
    from app.models.proxy import Proxy

    client = Proxy6Client(api_key)
    remote = client.get_proxies(state="active")
    added = updated = 0

    for p in remote:
        proxy_id = str(p["id"])
        existing = Proxy.query.filter_by(proxy_id=proxy_id).first()
        expires = datetime.fromtimestamp(
            int(p["unixtime_end"]), tz=timezone.utc
        ).replace(tzinfo=None)

        if existing:
            existing.host = p["host"]
            existing.port = int(p["port"])
            existing.user = p["user"]
            existing.password = p["pass"]
            existing.proxy_type = p.get("type", "http")
            existing.country = p.get("country", "")
            existing.is_active = str(p.get("active", "1")) == "1"
            existing.expires_at = expires
            updated += 1
        else:
            db.session.add(Proxy(
                proxy_id=proxy_id,
                host=p["host"],
                port=int(p["port"]),
                user=p["user"],
                password=p["pass"],
                proxy_type=p.get("type", "http"),
                country=p.get("country", ""),
                is_active=str(p.get("active", "1")) == "1",
                status="available",
                expires_at=expires,
            ))
            added += 1

    db.session.commit()
    logger.info("[PROXY] Synced: +%d added, %d updated", added, updated)
    return added, updated


def acquire_proxy(task_id: str) -> Optional[object]:
    """
    Reserve an available proxy for a task.  Returns the Proxy ORM row or None.
    """
    from app.extensions import db
    from app.models.proxy import Proxy

    proxy = (
        Proxy.query
        .filter_by(status="available", is_active=True)
        .filter(Proxy.expires_at > datetime.utcnow())
        .first()
    )
    if proxy:
        proxy.status = "in_use"
        proxy.assigned_to = task_id
        proxy.last_used = datetime.utcnow()
        db.session.commit()
        logger.info("[PROXY] Acquired %s (%s:%d) for task %s",
                    proxy.proxy_id, proxy.host, proxy.port, task_id)
    else:
        logger.warning("[PROXY] No available proxy for task %s", task_id)
    return proxy


def release_proxy(task_id: str) -> None:
    """Return a proxy to the available pool."""
    from app.extensions import db
    from app.models.proxy import Proxy

    proxy = Proxy.query.filter_by(assigned_to=task_id, status="in_use").first()
    if proxy:
        proxy.status = "available"
        proxy.assigned_to = None
        db.session.commit()
        logger.info("[PROXY] Released proxy %s", proxy.proxy_id)


# ─────────────────────────────────────────────────────────────────────────────
# Forwarder lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def start_forwarder(task_id: str, host: str, port: int,
                    user: str, password: str) -> LocalProxyForwarder:
    fwd = LocalProxyForwarder(host, port, user, password)
    fwd.start()
    with _forwarders_lock:
        _active_forwarders[task_id] = fwd
    return fwd


def stop_forwarder(task_id: str) -> None:
    with _forwarders_lock:
        fwd = _active_forwarders.pop(task_id, None)
    if fwd:
        fwd.stop()
        logger.info("[PROXY] Forwarder stopped for task %s", task_id)


# ─────────────────────────────────────────────────────────────────────────────
# Gnirehtet helpers
# ─────────────────────────────────────────────────────────────────────────────

def start_gnirehtet(device_id: str) -> None:
    """Start Gnirehtet reverse tethering for a device (non-blocking background)."""
    with _gnirehtet_lock:
        if device_id in _gnirehtet_procs:
            if _gnirehtet_procs[device_id].poll() is None:
                logger.info("[PROXY] Gnirehtet already running for %s", device_id)
                return
        try:
            proc = subprocess.Popen(
                [GNIREHTET_PATH, "run", device_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _gnirehtet_procs[device_id] = proc
            logger.info("[PROXY] Gnirehtet started for %s (pid %d)", device_id, proc.pid)
        except Exception as exc:
            logger.error("[PROXY] Gnirehtet start failed for %s: %s", device_id, exc)


def stop_gnirehtet(device_id: str) -> None:
    with _gnirehtet_lock:
        proc = _gnirehtet_procs.pop(device_id, None)
    if proc and proc.poll() is None:
        proc.terminate()
        logger.info("[PROXY] Gnirehtet stopped for %s", device_id)


# ─────────────────────────────────────────────────────────────────────────────
# Android device proxy helpers (via ADB)
# ─────────────────────────────────────────────────────────────────────────────

def set_device_proxy(device_id: str, host: str, port: int) -> None:
    """Set Android global HTTP proxy via ADB settings."""
    try:
        subprocess.run(
            [ADB_PATH, "-s", device_id, "shell",
             "settings", "put", "global", "http_proxy", f"{host}:{port}"],
            capture_output=True, timeout=10
        )
        logger.info("[PROXY] Device %s → proxy %s:%d", device_id, host, port)
    except Exception as exc:
        logger.error("[PROXY] set_device_proxy failed: %s", exc)


def clear_device_proxy(device_id: str) -> None:
    """Clear Android global HTTP proxy via ADB settings."""
    try:
        subprocess.run(
            [ADB_PATH, "-s", device_id, "shell",
             "settings", "put", "global", "http_proxy", ":0"],
            capture_output=True, timeout=10
        )
        logger.info("[PROXY] Device %s proxy cleared", device_id)
    except Exception as exc:
        logger.error("[PROXY] clear_device_proxy failed: %s", exc)
