"""
instagram_account_creator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Automates new Instagram account creation on a connected Android device
using uiautomator2 for UI automation and IMAP for email OTP verification.

Supports:
  - Email-based registration with automatic OTP retrieval via IMAP
  - Phone-based registration (OTP must be supplied externally if auto-SMS
    is not wired up)
  - Robust screen detection (handles Instagram UI version differences)
  - Birthday, name, password, username, and all post-signup screens

Selectors verified on:
  Device: Huawei ATU-L21, Android 8.0.0
  Instagram: 366.0.0.34.86
  uiautomator2: 3.2.0

NOTE: Instagram (React Native) uses content-desc (description) attributes
      for all interactive elements in the signup flow — NOT resource-id or text.
      Use d(description='...') for buttons/inputs; d(textContains='...') only
      for detecting screen titles which appear as plain View text nodes.
"""

import imaplib
import email as email_lib
import re
import time
import uuid
import threading
import logging
import random
import subprocess
from datetime import datetime
from typing import Optional, Tuple

import uiautomator2 as u2

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# In-memory job registry (task_id → CreationJob)
# ─────────────────────────────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()

PACKAGE = "com.instagram.android"


class CreationJob:
    """Tracks the lifecycle of one account-creation attempt."""

    def __init__(self, params: dict):
        self.task_id = str(uuid.uuid4())
        self.params = params
        self.status = "pending"           # pending | running | completed | failed
        self.step = "Queued"
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.created_at = datetime.utcnow()
        self.finished_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "step": self.step,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "params": {
                k: v for k, v in self.params.items()
                if k not in ("email_password", "password")   # don't leak creds
            },
        }


def start_creation_job(params: dict) -> CreationJob:
    """Submit a new creation job; returns the job immediately (runs in background)."""
    job = CreationJob(params)
    with _jobs_lock:
        _jobs[job.task_id] = job

    thread = threading.Thread(
        target=_run_job, args=(job,), daemon=True, name=f"ig-create-{job.task_id[:8]}"
    )
    thread.start()
    return job


def get_job(task_id: str) -> Optional[CreationJob]:
    with _jobs_lock:
        return _jobs.get(task_id)


def list_jobs() -> list:
    with _jobs_lock:
        return [j.to_dict() for j in _jobs.values()]


def _run_job(job: CreationJob) -> None:
    """Worker: runs inside a background thread. Pushes its own app context."""
    from app import create_app, db
    from app.models.instagram_account import InstagramAccount
    from app.utils import proxy_manager

    flask_app = create_app()

    with flask_app.app_context():
        job.status = "running"
        p = job.params

        # ── Proxy setup ───────────────────────────────────────────────────────
        proxy = proxy_manager.acquire_proxy(job.task_id)
        fwd = None
        if proxy:
            try:
                proxy_manager.start_gnirehtet(p["device_id"])
                time.sleep(6)   # wait for Gnirehtet VPN to establish on device
                fwd = proxy_manager.start_forwarder(
                    job.task_id, proxy.host, proxy.port,
                    proxy.user, proxy.password
                )
                time.sleep(1)
                logger.info("[CREATOR] Proxy ready: %s:%d via local port %d",
                            proxy.host, proxy.port, fwd.local_port)
            except Exception as proxy_err:
                logger.warning("[CREATOR] Proxy setup failed, continuing without: %s", proxy_err)
                fwd = None

        try:
            creator = InstagramAccountCreator()
            success, message, final_username = creator.create_account(
                device_id=p["device_id"],
                username=p["username"],
                password=p["password"],
                email=p.get("email"),
                email_password=p.get("email_password"),
                phone_number=p.get("phone_number"),
                full_name=p.get("full_name"),
                birthday=p.get("birthday", "1995-06-15"),
                imap_server=p.get("imap_server"),
                local_proxy_port=fwd.local_port if fwd else None,
                progress_cb=lambda step: setattr(job, "step", step),
            )
        finally:
            if fwd:
                proxy_manager.stop_forwarder(job.task_id)
            if proxy:
                proxy_manager.release_proxy(job.task_id)

        job.finished_at = datetime.utcnow()

        if success:
            job.status = "completed"
            job.step = "Done"
            job.result = {
                "username": final_username,
                "device_id": p["device_id"],
                "message": message,
            }
            try:
                existing = InstagramAccount.query.filter_by(username=final_username).first()
                if not existing:
                    account = InstagramAccount(
                        username=final_username,
                        password=p["password"],
                        device_id=p["device_id"],
                        email=p.get("email"),
                        email_password=p.get("email_password"),
                        imap_server=p.get("imap_server"),
                        imap_port=int(p.get("imap_port", 993)),
                        login_status=True,
                    )
                    account.last_login = datetime.utcnow()
                    db.session.add(account)
                    db.session.commit()
                    logger.info("[CREATOR] Account saved to DB: %s", final_username)
            except Exception as db_err:
                logger.error("[CREATOR] DB save failed: %s", db_err)
        else:
            job.status = "failed"
            job.error = message
            job.step = "Failed"


# ─────────────────────────────────────────────────────────────────────────────
# Main automation class
# ─────────────────────────────────────────────────────────────────────────────

class InstagramAccountCreator:
    """
    Drives the Instagram signup flow on a connected Android device.
    Uses a screen-detection state machine so it can handle different
    Instagram versions where the step order may vary.

    All interactive element selectors use content-desc (description)
    as verified from live device debugging on Instagram 366.x.
    """

    T_SHORT = 8
    T_MEDIUM = 25
    T_LONG = 60

    def __init__(self):
        self._progress_cb = None

    # ── Public entry point ────────────────────────────────────────────────────

    def create_account(
        self,
        device_id: str,
        username: str,
        password: str,
        email: str = None,
        email_password: str = None,
        phone_number: str = None,
        full_name: str = None,
        birthday: str = "1995-06-15",
        imap_server: str = None,
        local_proxy_port: int = None,
        progress_cb=None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Create a new Instagram account.
        local_proxy_port: if set, routes device traffic through this local
                          forwarder port via Gnirehtet (proxy6.net integration).
        Returns: (success, message, final_username)
        """
        if not email and not phone_number:
            return False, "Either email or phone_number is required", None

        self._progress_cb = progress_cb or (lambda s: None)
        full_name = full_name or username.replace("_", " ").title()
        final_username = username

        self._progress("Connecting to device")
        try:
            d = u2.connect(device_id)
            d.implicitly_wait(2.0)
        except Exception as exc:
            return False, f"Cannot connect to device {device_id}: {exc}", None

        # ── Set device proxy if a local forwarder is available ────────────────
        if local_proxy_port:
            from app.utils import proxy_manager
            self._progress(f"Setting device proxy → port {local_proxy_port}")
            proxy_manager.set_device_proxy(
                device_id,
                proxy_manager.GNIREHTET_GATEWAY,
                local_proxy_port,
            )
            time.sleep(2)

        outcome: Tuple[bool, str, Optional[str]] = (False, "Unknown error", None)
        try:
            self._progress("Opening fresh Instagram")
            self._open_fresh(d)

            steps_done: set = set()
            deadline = time.time() + 480   # 8 min total

            while time.time() < deadline:
                screen = self._detect_screen(d)
                logger.info("[CREATOR] Screen: %s | Done: %s", screen, steps_done)

                if screen == "home":
                    self._progress("Reached home — done")
                    outcome = (True, f"Account {final_username} created", final_username)
                    return outcome

                if screen == "error_try_again":
                    outcome = (False, "Instagram showed an error screen", None)
                    return outcome

                if screen in ("welcome", "unknown") and "signup_nav" not in steps_done:
                    self._progress("Navigating to signup")
                    if not self._navigate_to_signup(d):
                        return False, "Could not find signup button", None
                    steps_done.add("signup_nav")
                    time.sleep(3)
                    continue

                if screen == "contact_mode" and "mode_select" not in steps_done:
                    self._progress("Selecting contact method")
                    self._select_contact_mode(d, use_email=bool(email))
                    steps_done.add("mode_select")
                    time.sleep(2)
                    continue

                # Fallback: if mode_select is done but screen still reads contact_mode,
                # treat it as contact_entry and try to enter the contact value directly.
                if screen == "contact_mode" and "mode_select" in steps_done and "contact" not in steps_done:
                    self._progress(f"Entering {'email' if email else 'phone'} (fallback from contact_mode)")
                    ok = self._enter_contact(d, email=email, phone=phone_number)
                    if not ok:
                        return False, "Failed to enter contact", None
                    steps_done.add("contact")
                    time.sleep(3)
                    continue

                if screen in ("contact_entry", "input_generic") and "contact" not in steps_done:
                    self._progress(f"Entering {'email' if email else 'phone'}")
                    ok = self._enter_contact(d, email=email, phone=phone_number)
                    if not ok:
                        return False, "Failed to enter contact", None
                    steps_done.add("contact")
                    time.sleep(3)
                    continue

                if screen == "birthday" and "birthday" not in steps_done:
                    self._progress("Entering birthday")
                    self._enter_birthday(d, birthday)
                    steps_done.add("birthday")
                    time.sleep(3)
                    continue

                # Recovery: birthday was handled but screen didn't advance
                if screen == "birthday" and "birthday" in steps_done:
                    self._progress("Pushing past birthday screen")
                    # Tap Set first (closes picker/bottom-sheet if still open),
                    # then always tap Next to advance the main birthday screen.
                    # NEVER press back here — that reverses progress.
                    self._tap_birthday_set_button(d)
                    time.sleep(1)
                    self._tap_next(d)
                    time.sleep(1)
                    continue

                if screen == "otp" and "otp" not in steps_done:
                    self._progress("Verifying OTP")
                    ok = self._verify_otp(
                        d, email=email, email_password=email_password,
                        imap_server=imap_server
                    )
                    if not ok:
                        return False, "OTP verification failed (code not received)", None
                    steps_done.add("otp")
                    time.sleep(3)
                    continue

                if screen == "name" and "name" not in steps_done:
                    self._progress("Entering name")
                    self._enter_name(d, full_name)
                    steps_done.add("name")
                    time.sleep(3)
                    continue

                # Recovery: name was entered but screen didn't advance
                if screen == "name" and "name" in steps_done:
                    self._progress("Pushing past name screen")
                    self._tap_next(d)
                    time.sleep(2)
                    continue

                if screen == "password" and "password" not in steps_done:
                    self._progress("Entering password")
                    self._enter_password(d, password)
                    steps_done.add("password")
                    time.sleep(3)
                    continue

                if screen == "username" and "username" not in steps_done:
                    self._progress("Setting username")
                    final_username = self._handle_username(d, username)
                    steps_done.add("username")
                    time.sleep(3)
                    continue

                if screen == "agree_terms" and "agree_terms" not in steps_done:
                    self._progress("Agreeing to Instagram terms")
                    self._tap_agree(d)
                    steps_done.add("agree_terms")
                    time.sleep(3)
                    continue

                # Recovery: agree was tapped but screen didn't advance
                if screen == "agree_terms" and "agree_terms" in steps_done:
                    self._progress("Retrying agree tap")
                    self._tap_agree(d)
                    time.sleep(3)
                    continue

                if screen in ("photo", "notifications", "contacts",
                               "save_login", "suggestions", "post_signup"):
                    self._progress("Dismissing post-signup screen")
                    self._dismiss_post_signup(d)
                    time.sleep(2)
                    continue

                # ── Context-aware fallback for unrecognised input screens ────
                # When title-text detection fails, infer the screen from the
                # sequence of completed steps and handle accordingly.
                if screen == "input_generic" and "contact" in steps_done:
                    if "name" not in steps_done:
                        self._progress("Entering name (inferred)")
                        self._enter_name(d, full_name)
                        steps_done.add("name")
                        time.sleep(3)
                    elif "password" not in steps_done:
                        self._progress("Entering password (inferred)")
                        self._enter_password(d, password)
                        steps_done.add("password")
                        time.sleep(3)
                    elif "birthday" not in steps_done:
                        self._progress("Entering birthday (inferred)")
                        self._enter_birthday(d, birthday)
                        steps_done.add("birthday")
                        time.sleep(3)
                    elif "username" not in steps_done:
                        self._progress("Setting username (inferred)")
                        final_username = self._handle_username(d, username)
                        steps_done.add("username")
                        time.sleep(3)
                    else:
                        self._tap_next(d)
                        time.sleep(2)
                    continue

                time.sleep(2)

            outcome = (False, "Timed out during account creation", None)
            return outcome

        except Exception as exc:
            logger.exception("[CREATOR] Unexpected error")
            outcome = (False, f"Unexpected error: {exc}", None)
            return outcome

        finally:
            self._progress("Clearing app data")
            try:
                d.app_clear(PACKAGE)
                logger.info("[CREATOR] App data cleared for %s", PACKAGE)
            except Exception as clear_err:
                logger.warning("[CREATOR] Could not clear app data: %s", clear_err)
            if local_proxy_port:
                try:
                    from app.utils import proxy_manager
                    proxy_manager.clear_device_proxy(device_id)
                except Exception as px_err:
                    logger.warning("[CREATOR] Could not clear device proxy: %s", px_err)

    # ── Screen detection ──────────────────────────────────────────────────────

    def _detect_screen(self, d) -> str:
        """
        Inspect current UI and return a logical screen name.
        Detection uses text nodes (View labels); actions use content-desc.
        Order: most-specific checks first.
        """
        # ── Home (logged in) ──────────────────────────────────────────────────
        # IMPORTANT: do NOT check description="Home" alone — it matches the
        # Android system nav-bar home button on every screen.
        # Only return "home" when the Instagram tab bar is confirmed present.
        if d(resourceId="com.instagram.android:id/tab_bar").exists:
            return "home"
        # Fallback: both "Home" AND "Reels" must exist together — Reels is
        # Instagram-specific so it won't appear on the login/signup screen.
        if d(description="Reels").exists and d(description="Home").exists:
            return "home"

        # ── Error screen ──────────────────────────────────────────────────────
        if d(textContains="Something went wrong").exists and d(textContains="Try Again").exists:
            return "error_try_again"
        # Rate-limit / ban error ("Try Again Later")
        if d(textContains="Try Again Later").exists or d(textContains="We limit how often").exists:
            return "error_try_again"

        # ── OTP code entry ────────────────────────────────────────────────────
        # Detected via title text node ("Enter the confirmation code") which IS
        # a plain View text attribute — textContains is correct here.
        otp_titles = [
            "Enter the confirmation code",
            "Enter confirmation code",
            "We sent you a code",
            "Enter the 6-digit code",
            "Check your email",
            "We sent a code",
            "confirm your account",
        ]
        if any(d(textContains=t).exists for t in otp_titles):
            return "otp"
        # Also detect via the confirmed OTP input field description
        if d(description="Code input entry field").exists:
            return "otp"

        # ── Birthday ──────────────────────────────────────────────────────────
        bday_titles = [
            "Add your birthday", "Enter your birthday",
            "What's your birthday", "What\u2019s your birthday",
            "birthday", "Birthday",
            # Age-entry variant ("How old are you?")
            "How old are you", "old are you",
            "Use date of birth",
        ]
        if any(d(textContains=t).exists for t in bday_titles):
            return "birthday"
        # Also detect via content-desc (React Native may render title as desc)
        if any(d(description=t).exists for t in bday_titles):
            return "birthday"
        # Date-picker wheel: if month names are visible on screen it's birthday
        # Instagram uses abbreviated month names in the NumberPicker ("Jan", "Feb", ...)
        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December",
                       "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                       "Set date"]
        if any(d(textContains=m).exists for m in month_names):
            return "birthday"
        # NumberPicker widgets = date scroll wheel = birthday screen
        if d(className="android.widget.NumberPicker").exists:
            return "birthday"

        # ── Name ─────────────────────────────────────────────────────────────
        name_titles = [
            "What's your name", "What\u2019s your name",
            "Enter your name", "Add your name",
        ]
        if any(d(textContains=t).exists for t in name_titles):
            return "name"

        # ── Password ─────────────────────────────────────────────────────────
        pw_titles = [
            "Create a password", "Choose a password",
            "Enter a password",
        ]
        if any(d(textContains=t).exists for t in pw_titles):
            return "password"

        # ── Username ─────────────────────────────────────────────────────────
        un_titles = [
            "Choose a username", "What will you be called",
            "Create a username",
        ]
        if any(d(textContains=t).exists for t in un_titles):
            return "username"

        # ── Agree to terms ───────────────────────────────────────────────────
        if (d(textContains="Agree to Instagram").exists
                or d(textContains="terms and policies").exists
                or d(text="I agree").exists
                or d(description="I agree").exists):
            return "agree_terms"

        # ── Post-signup: profile photo ────────────────────────────────────────
        if d(textContains="profile photo").exists or d(textContains="Add photo").exists:
            return "photo"

        # ── Post-signup: notifications ────────────────────────────────────────
        if d(textContains="Turn on notifications").exists:
            return "notifications"

        # ── Post-signup: contacts ─────────────────────────────────────────────
        if d(textContains="Connect Contacts").exists or d(textContains="Find contacts").exists:
            return "contacts"

        # ── Post-signup: save login ───────────────────────────────────────────
        if d(textContains="Save your login").exists or d(textContains="Save Login").exists:
            return "save_login"

        # ── Post-signup: suggestions ──────────────────────────────────────────
        if d(textContains="Follow people").exists or d(textContains="suggested for you").exists:
            return "suggestions"

        # ── Welcome / entry screen ────────────────────────────────────────────
        # New Instagram shows "Get started" instead of "Create new account"
        if d(description="Get started").exists:
            return "welcome"
        # Older/alternate versions
        if d(description="Create new account").exists:
            return "welcome"
        if d(text="Create new account").exists or d(text="Sign up").exists:
            return "welcome"
        if d(textContains="Join Instagram").exists:
            return "welcome"

        # ── Contact entry fields ──────────────────────────────────────────────
        # MUST be checked BEFORE contact_mode: the email entry screen has BOTH
        # the actual input field (desc='Email,' / 'Mobile number,') AND the
        # tab-switcher button (desc='Sign up with mobile number'). Input field
        # presence means we're already on the entry screen, not mode-select.
        if d(description="Email,").exists or d(description="Mobile number,").exists:
            return "contact_entry"

        # ── Contact mode tab selector ─────────────────────────────────────────
        # Only reached when no input field is present — still on tab-selector.
        # Confirmed: these are content-desc values on the tab buttons.
        if (d(description="Sign up with email").exists or
                d(description="Sign up with mobile number").exists):
            return "contact_mode"

        # ── Generic input screen ──────────────────────────────────────────────
        if d(className="android.widget.EditText").exists:
            return "input_generic"

        return "unknown"

    # ── Step implementations ──────────────────────────────────────────────────

    def _open_fresh(self, d) -> None:
        """Force-stop, clear all app data, then relaunch Instagram from scratch.
        Clearing before launch guarantees no leftover session is auto-resumed."""
        d.app_stop(PACKAGE)
        time.sleep(1)
        d.app_clear(PACKAGE)   # wipe session/cache so signup screen always appears
        time.sleep(1)
        d.app_start(PACKAGE)
        time.sleep(5)
        # Dismiss any first-run permission dialogs
        for _ in range(3):
            for label in ("Allow", "OK", "Accept"):
                if d(text=label).exists:
                    d(text=label).click()
                    time.sleep(1)

    def _navigate_to_signup(self, d) -> bool:
        """
        Tap 'Create new account' on the welcome screen.
        Verified: button uses content-desc='Create new account' on Instagram 366.x
        """
        deadline = time.time() + self.T_MEDIUM
        while time.time() < deadline:
            # New Instagram 2024+ shows "Get started" instead of "Create new account"
            if d(description="Get started").exists:
                d(description="Get started").click()
                time.sleep(3)
                return True
            # Primary — confirmed content-desc
            if d(description="Create new account").exists:
                d(description="Create new account").click()
                time.sleep(3)
                return True
            # Fallback for older versions that use text
            for label in ("Get started", "Create new account", "Sign up", "Sign Up", "Create Account"):
                if d(text=label).exists:
                    d(text=label).click()
                    time.sleep(3)
                    return True
            time.sleep(2)
        return False

    def _select_contact_mode(self, d, use_email: bool) -> None:
        """
        Switch to email or phone tab on the contact entry screen.
        Verified: tab buttons use content-desc on Instagram 366.x
          Email path:  description='Sign up with email'
          Phone path:  description='Sign up with mobile number'
        """
        if use_email:
            # The default screen shows "What's your mobile number?" — tap email tab
            for desc in ("Sign up with email", "Use email address", "Email address"):
                if d(description=desc).exists:
                    d(description=desc).click()
                    time.sleep(2)
                    return
            # Text fallback
            for label in ("Sign up with email address", "Use email address"):
                if d(text=label).exists:
                    d(text=label).click()
                    time.sleep(2)
                    return
        else:
            # The default is already phone — only tap if on email screen
            for desc in ("Sign up with mobile number", "Use mobile number"):
                if d(description=desc).exists:
                    d(description=desc).click()
                    time.sleep(2)
                    return

    def _enter_contact(self, d, email: str = None, phone: str = None) -> bool:
        """
        Type email or phone into the contact input field and tap Next.
        Verified field content-desc values on Instagram 366.x:
          Email field:  description='Email,'        (note trailing comma)
          Phone field:  description='Mobile number,' (note trailing comma)

        Instagram defaults to the phone tab. If email is requested but the
        phone tab is showing, switch to the email tab first (and vice-versa).
        """
        contact = email or phone
        use_email = bool(email)

        # Switch to the correct tab if needed before entering the value
        if use_email and not d(description="Email,").exists:
            for desc in ("Sign up with email", "Use email address", "Email address"):
                if d(description=desc).exists:
                    d(description=desc).click()
                    time.sleep(2)
                    break
            else:
                for label in ("Sign up with email address", "Use email address"):
                    if d(text=label).exists:
                        d(text=label).click()
                        time.sleep(2)
                        break
        elif not use_email and not d(description="Mobile number,").exists:
            for desc in ("Sign up with mobile number", "Use mobile number"):
                if d(description=desc).exists:
                    d(description=desc).click()
                    time.sleep(2)
                    break

        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            if use_email and d(description="Email,").exists:
                field = d(description="Email,")
                field.click()
                time.sleep(0.4)
                field.clear_text()
                time.sleep(0.3)
                field.set_text(contact)
                time.sleep(1)
                self._tap_next(d)
                time.sleep(3)
                return True

            if not use_email and d(description="Mobile number,").exists:
                field = d(description="Mobile number,")
                field.click()
                time.sleep(0.4)
                field.clear_text()
                time.sleep(0.3)
                field.set_text(contact)
                time.sleep(1)
                self._tap_next(d)
                time.sleep(3)
                return True

            # Fallback: any EditText on screen
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.4)
                fields[0].clear_text()
                time.sleep(0.3)
                fields[0].set_text(contact)
                time.sleep(1)
                self._tap_next(d)
                time.sleep(3)
                return True

            time.sleep(1.5)
        return False

    def _enter_birthday(self, d, birthday: str = "1995-06-15") -> None:
        """
        Fill in the birthday screen.

        Instagram's birthday flow has two variants:
          A. Picker is already visible on screen (inline wheels or DatePicker dialog)
          B. Picker is in a bottom sheet — must tap the date field first to open it,
             then tap Set to confirm, then tap Next on the main screen.

        For EditText variants (some older device/app combos):
          - 3 separate EditText fields (month/day/year)
          - 1 combined field (MM/DD/YYYY)
        """
        try:
            year, month, day = [int(x) for x in birthday.split("-")]
        except (ValueError, AttributeError):
            year, month, day = 1995, 6, 15

        # Always use a random date in 1990-2000 for wheel pickers
        rand_year  = random.randint(1990, 2000)
        rand_month = random.randint(1, 12)
        rand_day   = random.randint(1, 28)

        time.sleep(2)

        # ── Log screen hierarchy for diagnostics ──────────────────────────────
        try:
            xml_snippet = d.dump_hierarchy()[:3000]
            logger.info("[CREATOR] Birthday screen XML: %s", xml_snippet)
        except Exception:
            pass

        # ── Variant 0: "How old are you?" — tap "Use date of birth" to switch ───
        # Instagram shows an age-entry screen; we tap "Use date of birth" to get
        # the actual date picker instead of trying to type an age number.
        if (d(textContains="How old are you").exists
                or d(textContains="old are you").exists):
            logger.info("[CREATOR] Birthday: 'How old are you?' screen detected")
            # Try to switch to the date-of-birth picker
            switched = False
            for label in ("Use date of birth", "date of birth", "Date of birth",
                          "Use Date of Birth", "DATE OF BIRTH"):
                if d(textContains=label).exists:
                    logger.info("[CREATOR] Tapping '%s' to open date picker", label)
                    d(textContains=label).click()
                    time.sleep(2)
                    switched = True
                    break
                if d(description=label).exists:
                    logger.info("[CREATOR] Tapping desc='%s' to open date picker", label)
                    d(description=label).click()
                    time.sleep(2)
                    switched = True
                    break
            if not switched:
                # "Use date of birth" not found — fall back to typing the age
                logger.info("[CREATOR] 'Use date of birth' not found, typing age")
                rand_age = datetime.now().year - rand_year
                device_id = getattr(d, 'serial', None)
                try:
                    field = d(className="android.widget.EditText")
                    if field.exists:
                        field.click()
                        time.sleep(0.5)
                    if device_id:
                        subprocess.run(
                            ['adb', '-s', device_id, 'shell', 'input', 'text', str(rand_age)],
                            timeout=5, check=False
                        )
                    else:
                        if field.exists:
                            field.clear_text()
                            field.set_text(str(rand_age))
                except Exception as exc:
                    logger.warning("[CREATOR] Age entry error: %s", exc)
                time.sleep(0.5)
                self._tap_next(d)
                return
            # Switched to date picker — fall through to NumberPicker handling below

        # ── Variant 1: native DatePicker dialog ───────────────────────────────
        if d(className="android.widget.DatePicker").exists:
            logger.info("[CREATOR] Birthday: DatePicker dialog")
            try:
                if d(className="android.widget.NumberPicker").exists:
                    self._fill_date_wheels(d, rand_year, rand_month, rand_day)
                    time.sleep(1)
            except Exception as exc:
                logger.warning("[CREATOR] DatePicker wheel error: %s", exc)
            self._tap_birthday_set_button(d)  # OK / Set / Done on the dialog
            time.sleep(1)
            self._tap_next(d)                 # Next on the main screen
            return

        # ── Variant 2: 3-field EditText ───────────────────────────────────────
        fields = d(className="android.widget.EditText")
        try:
            n = len(fields)
        except Exception:
            n = 0

        if n >= 3:
            logger.info("[CREATOR] Birthday: 3-field EditText mode")
            try:
                fields[0].click(); time.sleep(0.3)
                fields[0].clear_text(); fields[0].set_text(str(rand_month)); time.sleep(0.3)
                fields[1].click(); time.sleep(0.3)
                fields[1].clear_text(); fields[1].set_text(str(rand_day)); time.sleep(0.3)
                fields[2].click(); time.sleep(0.3)
                fields[2].clear_text(); fields[2].set_text(str(rand_year)); time.sleep(0.5)
            except Exception as exc:
                logger.warning("[CREATOR] Birthday 3-field error: %s", exc)
            time.sleep(1)
            self._tap_next(d)
            return

        # ── Variant 3: single EditText ────────────────────────────────────────
        if n == 1:
            logger.info("[CREATOR] Birthday: single-field EditText mode")
            try:
                fields[0].click(); time.sleep(0.3)
                fields[0].clear_text()
                fields[0].set_text(f"{rand_month:02d}/{rand_day:02d}/{rand_year}")
                time.sleep(0.5)
            except Exception as exc:
                logger.warning("[CREATOR] Birthday single-field error: %s", exc)
            time.sleep(1)
            self._tap_next(d)
            return

        # ── Variants 4 / 5: NumberPicker wheels (possibly in bottom sheet) ────
        if not d(className="android.widget.NumberPicker").exists:
            # Picker not yet visible — tap the date field to open the bottom sheet
            logger.info("[CREATOR] Birthday: pickers not visible, tapping date field")
            self._open_birthday_picker(d)
            time.sleep(2)

        pickers = d(className="android.widget.NumberPicker")
        try:
            picker_count = len(pickers)
        except Exception:
            picker_count = 0

        if picker_count >= 1:
            logger.info("[CREATOR] Birthday: %d NumberPicker(s), target=%d-%02d-%02d",
                        picker_count, rand_year, rand_month, rand_day)
            self._fill_date_wheels(d, rand_year, rand_month, rand_day)
            time.sleep(1.5)
            # Tap Set — confirms the date and closes the picker / bottom sheet
            self._tap_birthday_set_button(d)
            time.sleep(1.5)
        else:
            logger.info("[CREATOR] Birthday: no picker fields found, using default date")

        # Always tap Next to advance the main birthday screen
        self._tap_next(d)

    def _open_birthday_picker(self, d) -> None:
        """Tap the date display on the main birthday screen to open the picker."""
        # Try description-based taps (React Native)
        for desc in ("Set date", "Choose date", "Birthday", "birthday",
                     "Date of birth", "Edit", "Tap to change"):
            if d(description=desc).exists:
                logger.info("[CREATOR] Opening picker via desc=%r", desc)
                d(description=desc).click()
                return
        # Tap any visible month-name text (the date display)
        for m in ("January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"):
            el = d(textContains=m)
            if el.exists:
                logger.info("[CREATOR] Opening picker via month text %r", m)
                el.click()
                return
        # Tap any 4-digit year text
        import re as _re
        try:
            xml = d.dump_hierarchy()
            for year_str in _re.findall(r'\b(19\d{2}|20\d{2})\b', xml):
                el = d(text=year_str)
                if el.exists:
                    logger.info("[CREATOR] Opening picker via year text %r", year_str)
                    el.click()
                    return
        except Exception:
            pass
        # Coordinate fallback: center of screen (date field is usually there)
        w, h = d.window_size()
        logger.info("[CREATOR] Opening picker via center coordinate")
        d.click(w // 2, int(h * 0.5))

    def _tap_birthday_set_button(self, d) -> bool:
        """
        Tap the Set / OK / Done button that confirms the selected date.
        Used to close a picker dialog or bottom sheet.
        Does NOT tap Next — that is handled separately by _tap_next.
        """
        for label in ("Set", "SET", "OK", "Ok", "Done", "DONE"):
            if d(description=label).exists:
                logger.info("[CREATOR] Tapping Set button via desc=%r", label)
                d(description=label).click()
                time.sleep(1)
                return True
            if d(text=label).exists:
                logger.info("[CREATOR] Tapping Set button via text=%r", label)
                d(text=label).click()
                time.sleep(1)
                return True
        logger.debug("[CREATOR] Set button not found (may not be needed)")
        return False

    def _tap_birthday_advance(self, d) -> None:
        """
        Tap the button that confirms / advances past the birthday date picker.
        Primary action is always "Set" (confirms the date selection).
        Falls back to other labels for phones where the button text differs.
        """
        # "Set" is always first — it is the primary confirm button on the date picker.
        # Other labels are fallbacks for phones that show a different button.
        LABELS = ("Set", "SET", "Next", "NEXT", "Done", "DONE", "OK", "Continue", "Confirm")

        # 1. content-desc (React Native / Instagram style)
        for label in LABELS:
            if d(description=label).exists:
                logger.info("[CREATOR] Birthday advance via desc=%r", label)
                d(description=label).click()
                time.sleep(1.5)
                return

        # 2. text attribute (native Android buttons)
        for label in LABELS:
            if d(text=label).exists:
                logger.info("[CREATOR] Birthday advance via text=%r", label)
                d(text=label).click()
                time.sleep(1.5)
                return

        # 3. any Button widget — tap the last one (positive/forward action)
        try:
            buttons = d(className="android.widget.Button")
            count = len(buttons)
            if count > 0:
                info = buttons[count - 1].info
                logger.info("[CREATOR] Birthday advance via Button[%d] desc=%r text=%r",
                            count - 1,
                            info.get("contentDescription"),
                            info.get("text"))
                buttons[count - 1].click()
                time.sleep(1.5)
                return
        except Exception as exc:
            logger.debug("[CREATOR] Button fallback error: %s", exc)

        # 4. coordinate fallback — top-right (Instagram signup Next position)
        try:
            w, h = d.window_size()
            x, y = int(w * 0.88), int(h * 0.06)
            logger.info("[CREATOR] Birthday advance via coordinate (%d, %d)", x, y)
            d.click(x, y)
            time.sleep(1.5)
        except Exception as exc:
            logger.warning("[CREATOR] Coordinate tap failed: %s", exc)

    def _fill_date_wheels(self, d, year: int, month: int, day: int) -> None:
        """
        Set NumberPicker wheels to the exact target date.

        Instagram's DatePicker dialog uses:
          - Abbreviated month names: "Jan", "Feb", ..., "Dec"
          - Zero-padded day values:  "01", "02", ..., "31"
          - Full 4-digit year values: "1924", ..., "2010"
          - Picker order left-to-right: Month, Day, Year
        """
        # Abbreviated month names as shown in Instagram's NumberPicker
        SHORT_MONTH = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        # Zero-padded day values as shown in the picker
        DAY_VALUES = [f"{i:02d}" for i in range(1, 32)]
        # Year range shown in Instagram's birthday picker
        YEAR_VALUES = [str(y) for y in range(1924, 2011)]

        try:
            pickers = d(className="android.widget.NumberPicker")
            n = len(pickers)
            if n == 0:
                logger.warning("[CREATOR] No NumberPickers found")
                return

            # ── Role detection: read current value of each picker ─────────────
            current_vals: list[str] = []
            for i in range(min(n, 3)):
                val = self._read_picker(pickers[i])
                current_vals.append(val)
                logger.info("[CREATOR] Picker[%d] current=%r", i, val)

            month_idx = day_idx = year_idx = None
            for i, val in enumerate(current_vals):
                if val in SHORT_MONTH:
                    month_idx = i
                elif len(val) == 4 and val.isdigit() and 1900 <= int(val) <= 2100:
                    year_idx = i
                elif val.isdigit() and 1 <= int(val) <= 31:
                    day_idx = i
                elif len(val) == 2 and val.isdigit() and 1 <= int(val) <= 31:
                    # zero-padded day like "05"
                    day_idx = i

            # Positional fallback: Month / Day / Year left-to-right
            detected = {i for i in (month_idx, day_idx, year_idx) if i is not None}
            remaining = [i for i in range(min(n, 3)) if i not in detected]
            needed = [r for r in ("month", "day", "year")
                      if {"month": month_idx, "day": day_idx, "year": year_idx}[r] is None]
            for role, idx in zip(needed, remaining):
                if role == "month":   month_idx = idx
                elif role == "day":   day_idx   = idx
                elif role == "year":  year_idx  = idx

            logger.info("[CREATOR] Picker roles: month=%s day=%s year=%s",
                        month_idx, day_idx, year_idx)

            # ── Set each picker via swipe ─────────────────────────────────────
            if month_idx is not None:
                self._set_picker(d, pickers[month_idx],
                                 SHORT_MONTH[month - 1], SHORT_MONTH)

            if day_idx is not None:
                self._set_picker(d, pickers[day_idx],
                                 f"{day:02d}", DAY_VALUES)

            if year_idx is not None:
                self._set_picker(d, pickers[year_idx],
                                 str(year), YEAR_VALUES)

        except Exception as exc:
            logger.error("[CREATOR] _fill_date_wheels failed: %s", exc)

    def _type_into_picker(self, d, picker, value: str) -> bool:
        """
        Tap the center of a NumberPicker to activate its EditText, then type the value.

        Strategy order:
          1. ADB shell input text  — bypasses UIAutomator injection, most reliable
          2. picker EditText child set_text
          3. focused EditText set_text

        Returns True if typing was attempted (success cannot be verified without
        reading the picker, which is unreliable on most devices).
        """
        try:
            b = picker.info.get("bounds", {})
            cx = (b.get("left", 0) + b.get("right", 540)) // 2
            cy = (b.get("top",  0) + b.get("bottom", 300)) // 2
        except Exception:
            cx, cy = 270, 500

        logger.info("[CREATOR] Type into picker: value=%r  cx=%d cy=%d", value, cx, cy)

        # Tap center to activate the picker and open its inline EditText
        d.click(cx, cy)
        time.sleep(0.6)

        # ── Strategy 1: ADB shell input (bypasses UIAutomator injection) ─────
        try:
            device_id = d.serial
            # Select-all + delete any existing text first
            subprocess.run(
                ['adb', '-s', device_id, 'shell', 'input', 'keyevent', 'KEYCODE_CTRL_A'],
                timeout=5, check=False
            )
            time.sleep(0.1)
            subprocess.run(
                ['adb', '-s', device_id, 'shell', 'input', 'text', str(value)],
                timeout=5, check=False
            )
            time.sleep(0.3)
            subprocess.run(
                ['adb', '-s', device_id, 'shell', 'input', 'keyevent', 'KEYCODE_ENTER'],
                timeout=5, check=False
            )
            time.sleep(0.5)
            logger.info("[CREATOR] Typed %r via adb input text", value)
            return True
        except Exception as exc:
            logger.debug("[CREATOR] adb input failed: %s", exc)

        # ── Strategy 2: picker EditText child ────────────────────────────────
        try:
            edit = picker.child(className="android.widget.EditText")
            if edit.exists:
                edit.clear_text()
                time.sleep(0.15)
                edit.set_text(str(value))
                time.sleep(0.2)
                d.press("enter")
                time.sleep(0.5)
                logger.info("[CREATOR] Typed %r via EditText child", value)
                return True
        except Exception as exc:
            logger.debug("[CREATOR] EditText child failed: %s", exc)

        # ── Strategy 3: any focused EditText ─────────────────────────────────
        try:
            edit = d(className="android.widget.EditText", focused=True)
            if edit.exists:
                edit.clear_text()
                time.sleep(0.15)
                edit.set_text(str(value))
                time.sleep(0.2)
                d.press("enter")
                time.sleep(0.5)
                logger.info("[CREATOR] Typed %r via focused EditText", value)
                return True
        except Exception as exc:
            logger.debug("[CREATOR] Focused EditText failed: %s", exc)

        logger.warning("[CREATOR] All typing strategies failed for value=%r", value)
        return False

    # ── NumberPicker helpers ──────────────────────────────────────────────────

    def _read_picker(self, picker) -> str:
        """Read the currently displayed value of a NumberPicker via multiple strategies."""
        # Strategy 1: EditText child text
        try:
            child = picker.child(className="android.widget.EditText")
            if child.exists:
                val = (child.get_text() or "").strip()
                if val:
                    return val
        except Exception:
            pass
        # Strategy 2: picker's own info attributes
        try:
            info = picker.info
            for key in ("text", "contentDescription"):
                val = (info.get(key) or "").strip()
                if val:
                    return val
        except Exception:
            pass
        return ""

    def _set_picker(self, d, picker, target: str, values: list) -> None:
        """
        Scroll a NumberPicker wheel to the target value by tapping the adjacent
        items (prev/next buttons) one at a time.

        Tap-based approach is reliable because:
          - One tap = exactly one step (no momentum/inertia issues)
          - Works on dialog pickers, bottom sheets, and inline pickers
          - No coordinate calibration needed beyond item height

        Layout (standard Android NumberPicker, 3 visible items):
          ┌──────────────┐ ← top   (prev item — tapping decreases value)
          │  prev item   │
          ├──────────────┤
          │ CURRENT/EDIT │ ← middle (selected value)
          ├──────────────┤
          │  next item   │ ← bottom (next item — tapping increases value)
          └──────────────┘ ← bottom

        Strategy:
          1. Read current selected value from the EditText child.
          2. Compute delta (target_idx - current_idx).
          3. Tap the appropriate adjacent button delta times.
          4. If current value is unknown (not in values list), scan by tapping.
        """
        if target not in values:
            logger.warning("[CREATOR] Target %r not in picker values list", target)
            return

        target_idx = values.index(target)

        # ── Picker geometry ───────────────────────────────────────────────────
        try:
            b = picker.info.get("bounds", {})
            cx      = (b.get("left", 0) + b.get("right", 540)) // 2
            cy      = (b.get("top", 0) + b.get("bottom", 300)) // 2
            item_h  = max(40, (b.get("bottom", 300) - b.get("top", 0)) // 3)
        except Exception:
            cx, cy, item_h = 270, 568, 120

        device_id = getattr(d, 'serial', None)
        logger.info("[CREATOR] Picker→%r idx=%d  cx=%d cy=%d item_h=%d  device=%s",
                    target, target_idx, cx, cy, item_h, device_id)

        def _tap(y: int) -> None:
            if device_id:
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'input', 'tap', str(cx), str(y)],
                    timeout=5, check=False
                )
            else:
                d.click(cx, y)
            time.sleep(0.35)

        def tap_next() -> None:
            """Tap the bottom adjacent item → value INCREASES."""
            _tap(cy + item_h)

        def tap_prev() -> None:
            """Tap the top adjacent item → value DECREASES."""
            _tap(cy - item_h)

        def get_selected() -> str:
            """Read the currently displayed value from the NumberPicker's EditText."""
            try:
                child = picker.child(resourceId="android:id/numberpicker_input")
                if child.exists:
                    return (child.get_text() or "").strip()
            except Exception:
                pass
            return ""

        # ── Already on target? ────────────────────────────────────────────────
        current = get_selected()
        if current == target:
            logger.info("[CREATOR] Picker: %r already selected", target)
            return

        logger.info("[CREATOR] Picker: current=%r → target=%r", current, target)

        # ── Compute delta from known current position ─────────────────────────
        curr_idx = values.index(current) if current in values else None

        if curr_idx is not None:
            delta = target_idx - curr_idx
            logger.info("[CREATOR] Picker: delta=%d steps", delta)
            if delta > 0:
                for i in range(delta):
                    tap_next()
                    if get_selected() == target:
                        logger.info("[CREATOR] Reached %r after %d taps", target, i + 1)
                        return
            elif delta < 0:
                for i in range(-delta):
                    tap_prev()
                    if get_selected() == target:
                        logger.info("[CREATOR] Reached %r after %d taps", target, i + 1)
                        return
        else:
            # Current value not in the known values list (e.g. year=2026 not in 1924-2010).
            # Try to read current as integer and compute numeric delta directly.
            try:
                curr_int = int(current)
                tgt_int  = int(target)
                delta = tgt_int - curr_int
                logger.info("[CREATOR] Picker: numeric delta=%d (curr=%s→tgt=%s)",
                            delta, current, target)
                if delta > 0:
                    for i in range(delta):
                        tap_next()
                        if get_selected() == target:
                            return
                elif delta < 0:
                    for i in range(-delta):
                        tap_prev()
                        if get_selected() == target:
                            return
            except (ValueError, TypeError):
                pass

            # Last resort: blind scan through all values
            n = len(values)
            logger.info("[CREATOR] Picker: scanning up to %d steps for %r", n * 2, target)
            for i in range(n + 5):
                tap_next()
                if get_selected() == target:
                    logger.info("[CREATOR] Found %r after %d fwd taps", target, i + 1)
                    return
            for i in range(n + 5):
                tap_prev()
                if get_selected() == target:
                    logger.info("[CREATOR] Found %r after %d rev taps", target, i + 1)
                    return

        sel = get_selected()
        if sel == target:
            logger.info("[CREATOR] Picker confirmed: %r", target)
        else:
            logger.warning("[CREATOR] Picker: wanted %r, got %r", target, sel)

    def _verify_otp(self, d, email: str = None, email_password: str = None,
                    imap_server: str = None) -> bool:
        """
        Fetch OTP from email via IMAP and submit it.
        Verified field/button content-desc on Instagram 366.x:
          Input:  description='Code input entry field'
          Next:   description='Next'
          Resend: description='I didn\u2019t get the code'  (unicode right-single-quote!)
          Dialog: description='Resend confirmation code'
                  description='Close'
        """
        if not (email and email_password):
            logger.warning("[CREATOR] No email credentials — cannot auto-verify OTP")
            return False

        self._progress("Waiting for OTP email (up to 90 s)")
        code = self._fetch_otp_from_email(email, email_password, imap_server, max_wait=90)

        if not code:
            # Tap "I didn't get the code" to trigger resend dialog
            # NOTE: Instagram uses unicode right-single-quote \u2019 in "didn't"
            resend_btn = (
                d(description="I didn\u2019t get the code") or
                d(description="I didn't get the code")
            )
            resend_desc = "I didn\u2019t get the code"
            if d(description=resend_desc).exists:
                d(description=resend_desc).click()
                time.sleep(2)
                # In the resend dialog, tap "Resend confirmation code"
                if d(description="Resend confirmation code").exists:
                    d(description="Resend confirmation code").click()
                    time.sleep(2)
                elif d(description="Close").exists:
                    d(description="Close").click()
                    time.sleep(1)
            # Also try plain text fallback for older versions
            elif d(textContains="didn").exists and d(textContains="get the code").exists:
                for el in d(className="android.widget.TextView"):
                    try:
                        if "didn" in el.get_text():
                            el.click()
                            time.sleep(2)
                            break
                    except Exception:
                        pass

            self._progress("Retrying OTP fetch")
            code = self._fetch_otp_from_email(email, email_password, imap_server, max_wait=60)

        if not code:
            logger.error("[CREATOR] OTP not received")
            return False

        logger.info("[CREATOR] OTP retrieved: %s", code)
        self._progress(f"Entering OTP: {code}")

        # Verified: OTP input uses content-desc='Code input entry field'
        if d(description="Code input entry field").exists:
            field = d(description="Code input entry field")
            field.click()
            time.sleep(0.4)
            field.clear_text()
            field.set_text(code)
            time.sleep(1)
        else:
            # Fallback: any EditText on screen
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].clear_text()
                fields[0].set_text(code)
                time.sleep(1)

        self._tap_next(d)
        time.sleep(3)
        return True

    def _enter_name(self, d, full_name: str) -> None:
        """
        Enter full name and tap Next.
        Verified on Instagram: Next button is cls=Button, clickable=True,
        desc='Next', bounds=[48,624][1032,756] (center 540,690 with keyboard open).
        """
        device_id = getattr(d, 'serial', None)
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            # Locate the name input field
            field = None
            for desc in ("Full name,", "Full name", "Name,", "Name",
                         "Your name", "Enter your name"):
                if d(description=desc).exists:
                    field = d(description=desc)
                    break
            if field is None:
                fields = d(className="android.widget.EditText")
                if fields.exists:
                    field = fields[0]

            if field is None:
                time.sleep(1.5)
                continue

            # Focus field and type via ADB (keyboard-safe, no IME pop-up issues)
            field.click()
            time.sleep(0.6)
            if device_id:
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'input', 'keyevent', 'KEYCODE_CTRL_A'],
                    timeout=5, check=False
                )
                time.sleep(0.1)
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'input', 'keyevent', 'KEYCODE_DEL'],
                    timeout=5, check=False
                )
                time.sleep(0.2)
                name_adb = full_name.replace(' ', '%s')
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'input', 'text', name_adb],
                    timeout=5, check=False
                )
            else:
                field.clear_text()
                field.set_text(full_name)
            time.sleep(0.8)

            # ── Tap Next (confirmed: clickable=True Button with desc='Next') ──
            # Attempt 1: direct uiautomator2 click on the clickable Button
            for desc in ("Next", "Continue", "Done"):
                btn = d(description=desc, clickable=True)
                if btn.exists:
                    btn.click()
                    time.sleep(2)
                    return

            # Attempt 2: ADB tap using real element bounds (works even if focus
            # is still in text field causing click interception)
            for desc in ("Next", "Continue", "Done"):
                btn = d(description=desc)
                if btn.exists:
                    b = btn.info.get("bounds", {})
                    if b:
                        bx = (b["left"] + b["right"]) // 2
                        by = (b["top"] + b["bottom"]) // 2
                        if device_id:
                            subprocess.run(
                                ['adb', '-s', device_id, 'shell', 'input', 'tap',
                                 str(bx), str(by)],
                                timeout=5, check=False
                            )
                            time.sleep(2)
                            return

            # Attempt 3: IME Done/Next action key
            if device_id:
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'input', 'keyevent', 'KEYCODE_ENTER'],
                    timeout=5, check=False
                )
                time.sleep(2)

            # Attempt 4: known coordinate fallback — Next button center on
            # this device is at ~(w/2, h*0.32) when keyboard is open
            w, h = d.window_size()
            if device_id:
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'input', 'tap',
                     str(w // 2), str(int(h * 0.32))],
                    timeout=5, check=False
                )
            time.sleep(2)
            return

    def _enter_password(self, d, password: str) -> None:
        """Enter password. Tries description-based field first, then any EditText."""
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            # Instagram 366.x uses trailing comma in content-desc for input fields
            for desc in ("Password,", "Password", "Create password",
                         "Enter password", "Choose a password"):
                if d(description=desc).exists:
                    field = d(description=desc)
                    field.click()
                    time.sleep(0.4)
                    field.clear_text()
                    field.set_text(password)
                    time.sleep(1.5)   # wait for password-strength validation
                    self._tap_next(d)
                    return

            # Fallback: any EditText — must click before set_text
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.4)
                fields[0].clear_text()
                fields[0].set_text(password)
                time.sleep(1.5)
                self._tap_next(d)
                return

            time.sleep(1.5)

    def _handle_username(self, d, username: str) -> str:
        """
        Clear auto-suggested username, enter desired one, handle conflicts.
        Returns the final username that was accepted.
        """
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            # Try description-based field first
            for desc in ("Username,", "Username", "Choose a username,",
                         "Choose a username", "Enter username"):
                if d(description=desc).exists:
                    field = d(description=desc)
                    field.click()
                    time.sleep(0.4)
                    field.clear_text()
                    time.sleep(0.3)
                    field.set_text(username)
                    time.sleep(3)
                    username = self._resolve_username_conflict(d, field, username)
                    self._tap_next(d)
                    return username

            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].clear_text()
                time.sleep(0.4)
                fields[0].set_text(username)
                time.sleep(3)
                username = self._resolve_username_conflict(d, fields[0], username)
                self._tap_next(d)
                return username

            time.sleep(1.5)
        return username

    def _resolve_username_conflict(self, d, field, username: str) -> str:
        """If username is taken, append a suffix and retry."""
        unavailable = (
            d(textContains="not available").exists or
            d(textContains="isn't available").exists or
            d(textContains="isn\u2019t available").exists or
            d(textContains="already taken").exists or
            d(textContains="Username not available").exists
        )
        if unavailable:
            suffix = str(int(time.time()))[-4:]
            alt = f"{username}_{suffix}"
            logger.warning("[CREATOR] Username %s taken, trying %s", username, alt)
            field.clear_text()
            field.set_text(alt)
            time.sleep(2)
            return alt
        return username

    def _dismiss_post_signup(self, d) -> None:
        """
        Dismiss any optional post-signup screen.
        Tries content-desc first, then text, then presses Back.
        """
        # content-desc buttons (Instagram 366.x style)
        for desc in ("Skip", "Not Now", "Not now", "Later", "Skip for now",
                      "Skip this step", "Decline", "Close", "Cancel"):
            if d(description=desc).exists:
                d(description=desc).click()
                time.sleep(1.5)
                return

        # text-based buttons (older Instagram versions / system dialogs)
        for label in ("Skip", "Not Now", "Not now", "No Thanks", "Later",
                       "Skip for now", "Don't Allow", "Decline",
                       "Skip this step", "Turn Off", "Add Later",
                       "Continue without", "Close", "Cancel"):
            if d(text=label).exists:
                d(text=label).click()
                time.sleep(1.5)
                return

        # System permission: "Don't allow" / "Allow"
        if d(text="Don't allow").exists:
            d(text="Don't allow").click()
            time.sleep(1)
            return
        if d(text="Allow").exists:
            d(text="Allow").click()
            time.sleep(1)
            return

        # Last resort: try Next/Continue
        self._tap_next(d)

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _tap_agree(self, d) -> None:
        """
        Tap 'I agree' on the terms screen.
        Confirmed: outer element is cls=Button, clickable=True, desc='I agree',
        bounds=[48,1299][1032,1431] → center (540, 1365).
        """
        device_id = getattr(d, 'serial', None)

        # Attempt 1: direct click on the clickable Button
        btn = d(description="I agree", clickable=True)
        if btn.exists:
            btn.click()
            time.sleep(2)
            return

        # Attempt 2: ADB tap using real element bounds
        for selector in (dict(description="I agree"), dict(text="I agree")):
            el = d(**selector)
            if el.exists:
                b = el.info.get("bounds", {})
                bx = (b.get("left", 48) + b.get("right", 1032)) // 2
                by = (b.get("top", 1299) + b.get("bottom", 1431)) // 2
                if device_id:
                    subprocess.run(
                        ['adb', '-s', device_id, 'shell', 'input', 'tap', str(bx), str(by)],
                        timeout=5, check=False
                    )
                time.sleep(2)
                return

        # Last-resort: tap hardcoded center from live inspection
        if device_id:
            subprocess.run(
                ['adb', '-s', device_id, 'shell', 'input', 'tap', '540', '1365'],
                timeout=5, check=False
            )
            time.sleep(2)

    def _tap_next(self, d) -> bool:
        """
        Tap the primary forward button.
        Verified: Instagram 366.x uses content-desc='Next' on the Next button.

        Instagram's React Native UI often renders a button as:
          - android.widget.Button  (clickable=True)  ← the actual button
          - android.view.View      (clickable=False)  ← label inside the button
        Both share the same content-desc ('Next'). We must click the Button,
        not the View. Always filter with clickable=True.
        """
        labels = ("Next", "Continue", "Done", "Confirm", "Submit", "OK", "Create", "Verify")
        # Primary: content-desc on a clickable element
        for desc in labels:
            btn = d(description=desc, clickable=True)
            if btn.exists:
                btn.click()
                time.sleep(1.5)
                return True
        # Fallback: text on a clickable element
        for label in labels:
            btn = d(text=label, clickable=True)
            if btn.exists:
                btn.click()
                time.sleep(1.5)
                return True
        return False

    def _progress(self, msg: str) -> None:
        logger.info("[CREATOR] %s", msg)
        if self._progress_cb:
            self._progress_cb(msg)

    # ── Email / IMAP helpers ──────────────────────────────────────────────────

    _IMAP_MAP = {
        "gmail.com":       "imap.gmail.com",
        "googlemail.com":  "imap.gmail.com",
        "outlook.com":     "imap-mail.outlook.com",
        "hotmail.com":     "imap-mail.outlook.com",
        "live.com":        "imap-mail.outlook.com",
        "yahoo.com":       "imap.mail.yahoo.com",
        "gmx.net":         "imap.gmx.net",
        "gmx.com":         "imap.gmx.net",
        "gmx.de":          "imap.gmx.net",
        "rambler.ru":      "imap.rambler.ru",
        "mail.ru":         "imap.mail.ru",
        "yandex.com":      "imap.yandex.com",
        "yandex.ru":       "imap.yandex.ru",
        "icloud.com":      "imap.mail.me.com",
        "me.com":          "imap.mail.me.com",
    }

    def _resolve_imap(self, email_address: str, override: str = None) -> str:
        if override:
            return override
        domain = email_address.split("@")[-1].lower()
        return self._IMAP_MAP.get(domain, "imap.firstmail.ltd")

    def _fetch_otp_from_email(
        self,
        email_address: str,
        email_password: str,
        imap_server: str = None,
        max_wait: int = 90,
    ) -> Optional[str]:
        """
        Poll email inbox via IMAP until a 6-digit Instagram OTP is found.
        Checks the 5 most recent Instagram emails.
        """
        server = self._resolve_imap(email_address, imap_server)
        deadline = time.time() + max_wait

        while time.time() < deadline:
            try:
                mail = imaplib.IMAP4_SSL(server)
                mail.login(email_address, email_password)
                mail.select("inbox")

                _, ids = mail.search(None, '(FROM "instagram")')
                id_list = ids[0].split() if ids[0] else []

                for eid in reversed(id_list[-5:]):
                    try:
                        _, data = mail.fetch(eid, "(RFC822)")
                        msg = email_lib.message_from_bytes(data[0][1])
                        body = self._extract_body(msg)
                        code = self._extract_6digit_code(body)
                        if code:
                            mail.logout()
                            return code
                    except Exception:
                        continue

                mail.logout()
            except Exception as exc:
                logger.warning("[CREATOR] IMAP error: %s", exc)

            time.sleep(8)

        return None

    @staticmethod
    def _extract_body(msg) -> str:
        """Pull all text content from an email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    try:
                        body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                pass
        return body

    @staticmethod
    def _extract_6digit_code(text: str) -> Optional[str]:
        """Find a 6-digit OTP code in text (skipping years like 1990, 2024)."""
        matches = re.findall(r'\b(\d{6})\b', text)
        for m in matches:
            if not re.match(r'^(19|20)\d{2}$', m):   # skip 4-digit years misread as 6
                return m
        return None
