"""
tiktok_account_creator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Automates new TikTok account creation on a connected Android device using
uiautomator2 for UI automation and IMAP for email OTP verification.

Supports:
  - Email-based registration (recommended — fully automated via IMAP OTP)
  - Phone-based registration (OTP via SMS — requires real SIM)
  - Handles all pre-signup screens: Terms, Interests, Privacy update
  - Birthday picker, name, username, password, post-signup dismissal
  - Clears app data after every attempt (success or failure)

Selectors verified on:
  Device  : Huawei ATU-L21, Android 8.0.0
  TikTok  : 44.6.4  (com.zhiliaoapp.musically)
  uiautomator2: 3.2.0

NOTE: TikTok uses obfuscated resource-ids (e.g. "efn", "cl0") but exposes
      stable `text` attributes for all visible UI labels.  Selectors below
      prefer text= over resource-id=; content-desc is used only for the
      bottom-nav tabs which are confirmed description-based.
"""

import imaplib
import email as email_lib
import os
import platform
import re
import subprocess
import time
import uuid
import threading
import logging
from datetime import datetime
from typing import Optional, Tuple

import uiautomator2 as u2

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ADB_PATH = os.path.join(_PROJECT_ROOT, "assets", "adb.exe" if _IS_WINDOWS else "adb")

# ─────────────────────────────────────────────────────────────────────────────
# In-memory job registry
# ─────────────────────────────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()

PACKAGE = "com.zhiliaoapp.musically"


class CreationJob:
    def __init__(self, params: dict):
        self.task_id = str(uuid.uuid4())
        self.params = params
        self.status = "pending"
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
                if k not in ("email_password", "password")
            },
        }


def start_creation_job(params: dict) -> CreationJob:
    job = CreationJob(params)
    with _jobs_lock:
        _jobs[job.task_id] = job
    t = threading.Thread(
        target=_run_job, args=(job,), daemon=True,
        name=f"tt-create-{job.task_id[:8]}"
    )
    t.start()
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
    from app.models.tiktok_account import TikTokAccount

    flask_app = create_app()

    with flask_app.app_context():
        job.status = "running"
        p = job.params

        creator = TikTokAccountCreator()
        success, message, final_username = creator.create_account(
            device_id=p["device_id"],
            username=p.get("username", ""),
            password=p["password"],
            email=p.get("email"),
            email_password=p.get("email_password"),
            phone_number=p.get("phone_number"),
            full_name=p.get("full_name"),
            birthday=p.get("birthday", "1998-05-20"),
            imap_server=p.get("imap_server"),
            progress_cb=lambda step: setattr(job, "step", step),
        )

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
                existing = TikTokAccount.query.filter_by(username=final_username).first()
                if not existing:
                    account = TikTokAccount(
                        username=final_username,
                        password=p["password"],
                        device_id=p["device_id"],
                        email=p.get("email"),
                        email_password=p.get("email_password"),
                        login_status=True,
                    )
                    account.last_login = datetime.utcnow()
                    db.session.add(account)
                    db.session.commit()
                    logger.info("[TT-CREATOR] Saved to DB: %s", final_username)
            except Exception as db_err:
                logger.error("[TT-CREATOR] DB save failed: %s", db_err)
        else:
            job.status = "failed"
            job.error = message
            job.step = "Failed"


# ─────────────────────────────────────────────────────────────────────────────
# Main creator class
# ─────────────────────────────────────────────────────────────────────────────

class TikTokAccountCreator:
    """
    Drives the TikTok signup flow on a connected Android device.

    Flow (email path):
      1. Accept Terms → Continue
      2. Interests screen → Skip
      3. Privacy update dialog → Got it
      4. Tap Profile tab (bottom nav)
      5. Guest profile → Sign up
      6. Signup method → Use phone or email → Email tab
      7. Enter email → Next
      8. Enter birthday (month/day/year picker) → Next
      9. Enter OTP (fetched via IMAP) → Next
     10. Enter name → Next
     11. Set/confirm username → Next
     12. Create password → Next
     13. Dismiss post-signup screens

    Flow (phone path):
      Same as above but step 6 stays on Phone tab; OTP comes via SMS.
    """

    T_SHORT  = 15
    T_MEDIUM = 30
    T_LONG   = 60

    def __init__(self):
        self._progress_cb = None
        self._device_id: Optional[str] = None

    # ── ADB helpers ───────────────────────────────────────────────────────────

    def _adb_run(self, *args) -> None:
        if not self._device_id:
            return
        try:
            subprocess.run(
                [_ADB_PATH, "-s", self._device_id] + list(args),
                timeout=10, check=False, capture_output=True,
            )
        except Exception as exc:
            logger.debug("[TT-CREATOR] ADB error: %s", exc)

    def _adb_type(self, text: str) -> None:
        """Type text via ADB input, handling special chars."""
        escaped = (
            text
            .replace("\\", "\\\\")
            .replace('"',  '\\"')
            .replace("'",  "\\'")
            .replace(" ",  "%s")
            .replace("(",  "\\(")
            .replace(")",  "\\)")
            .replace("&",  "\\&")
            .replace(";",  "\\;")
            .replace("<",  "\\<")
            .replace(">",  "\\>")
            .replace("|",  "\\|")
        )
        self._adb_run("shell", "input", "text", escaped)

    def _adb_clear_and_type(self, text: str) -> None:
        """Select-all, delete, then type via ADB."""
        self._adb_run("shell", "input", "keyevent", "KEYCODE_CTRL_A")
        time.sleep(0.1)
        self._adb_run("shell", "input", "keyevent", "KEYCODE_DEL")
        time.sleep(0.2)
        self._adb_type(text)

    # ── Public entry point ────────────────────────────────────────────────────

    def create_account(
        self,
        device_id: str,
        username: str = "",
        password: str = "",
        email: str = None,
        email_password: str = None,
        phone_number: str = None,
        full_name: str = None,
        birthday: str = "1998-05-20",
        imap_server: str = None,
        progress_cb=None,
    ) -> Tuple[bool, str, Optional[str]]:
        if not email and not phone_number:
            return False, "Either email or phone_number is required", None

        self._progress_cb = progress_cb or (lambda s: None)
        self._device_id = device_id
        full_name = full_name or (username.replace("_", " ").title() if username else "TikTok User")
        final_username = username

        self._progress("Connecting to device")
        try:
            d = u2.connect(device_id)
            d.implicitly_wait(2.0)
        except Exception as exc:
            return False, f"Cannot connect to device {device_id}: {exc}", None

        outcome: Tuple[bool, str, Optional[str]] = (False, "Unknown error", None)
        try:
            self._progress("Opening fresh TikTok")
            self._open_fresh(d)

            steps_done: set = set()
            deadline = time.time() + 600   # 10 min budget

            while time.time() < deadline:
                screen = self._detect_screen(d)
                logger.info("[TT-CREATOR] Screen: %s | Done: %s", screen, steps_done)

                # ── Success ───────────────────────────────────────────────
                if screen == "home":
                    self._progress("Reached home — done")
                    outcome = (True, f"Account {final_username} created", final_username)
                    return outcome

                if screen == "error":
                    outcome = (False, "TikTok showed a ban/violation screen", None)
                    return outcome

                if screen == "wrong_app":
                    self._progress("Returning to TikTok from wrong app")
                    self._adb_run("shell", "am", "start", "-n",
                                  f"{PACKAGE}/com.ss.android.ugc.aweme.main.MainActivity")
                    time.sleep(8)
                    continue

                if screen == "soft_error":
                    self._progress("Dismissing transient error")
                    for dismiss in ("Try again", "Retry", "OK", "Got it", "Refresh"):
                        if d(text=dismiss).exists:
                            d(text=dismiss).click()
                            time.sleep(3)
                            break
                    else:
                        # No dismiss button — restart TikTok
                        self._progress("Restarting TikTok after error")
                        d.app_stop(PACKAGE)
                        time.sleep(2)
                        d.app_start(PACKAGE)
                        time.sleep(8)
                    continue

                # ── Pre-signup one-time screens ───────────────────────────
                if screen == "terms" and "terms" not in steps_done:
                    self._progress("Accepting terms")
                    self._accept_terms(d)
                    steps_done.add("terms")
                    time.sleep(2)
                    continue

                if screen == "interests" and "interests" not in steps_done:
                    self._progress("Skipping interests")
                    self._skip_interests(d)
                    steps_done.add("interests")
                    time.sleep(3)
                    continue

                if screen == "privacy_update" and "privacy" not in steps_done:
                    self._progress("Dismissing privacy update")
                    d(text="Got it").click()
                    steps_done.add("privacy")
                    time.sleep(2)
                    continue

                if screen == "loading":
                    time.sleep(3)
                    continue

                # ── Navigate to signup ────────────────────────────────────
                if screen in ("feed_guest", "unknown") and "profile_tap" not in steps_done:
                    self._progress("Tapping Profile tab")
                    self._tap_profile_tab(d)
                    steps_done.add("profile_tap")
                    time.sleep(4)
                    continue

                if screen == "profile_guest" and "signup_tap" not in steps_done:
                    self._progress("Tapping Sign up")
                    self._tap_signup(d)
                    steps_done.add("signup_tap")
                    time.sleep(4)
                    continue

                # ── Signup method selector ────────────────────────────────
                # New TikTok: "Sign up for TikTok" modal → click "Continue with Email"
                # Old TikTok: "Use phone or email" → same result
                if screen == "signup_method" and "method" not in steps_done:
                    self._progress("Selecting email signup")
                    self._select_email_method(d)
                    steps_done.add("method")
                    time.sleep(3)
                    continue

                # ── Email entry (new TikTok) ──────────────────────────────
                if screen == "email_entry" and "contact" not in steps_done:
                    self._progress("Entering email")
                    ok = self._enter_email(d, email)
                    if not ok:
                        outcome = (False, "Failed to enter email address", None)
                        return outcome
                    steps_done.add("contact")
                    time.sleep(4)
                    continue

                # ── Email rejected by TikTok ──────────────────────────────
                if screen == "email_error":
                    outcome = (False, "TikTok rejected this email address — try a different email provider", None)
                    return outcome

                # ── Old-style contact form (phone/email tab picker) ───────
                if screen == "contact_form" and "contact_mode" not in steps_done:
                    self._progress(f"Selecting {'email' if email else 'phone'} tab")
                    self._select_contact_tab(d, use_email=bool(email))
                    steps_done.add("contact_mode")
                    time.sleep(2)
                    continue

                if screen in ("contact_form", "contact_entry") and "contact" not in steps_done:
                    self._progress(f"Entering {'email' if email else 'phone'}")
                    ok = self._enter_contact(d, email=email, phone=phone_number)
                    if not ok:
                        outcome = (False, "Failed to enter contact info", None)
                        return outcome
                    steps_done.add("contact")
                    time.sleep(3)
                    continue

                # ── Birthday ──────────────────────────────────────────────
                if screen == "birthday" and "birthday" not in steps_done:
                    self._progress("Entering birthday")
                    self._enter_birthday(d, birthday)
                    steps_done.add("birthday")
                    time.sleep(3)
                    continue

                # ── OTP ───────────────────────────────────────────────────
                if screen == "otp" and "otp" not in steps_done:
                    self._progress("Verifying OTP")
                    ok = self._verify_otp(d, email=email,
                                          email_password=email_password,
                                          imap_server=imap_server)
                    if not ok:
                        outcome = (False, "OTP verification failed", None)
                        return outcome
                    steps_done.add("otp")
                    time.sleep(3)
                    continue

                # ── Name ──────────────────────────────────────────────────
                if screen == "name" and "name" not in steps_done:
                    self._progress("Entering name")
                    self._enter_name(d, full_name)
                    steps_done.add("name")
                    time.sleep(3)
                    continue

                # ── Username ──────────────────────────────────────────────
                if screen == "username" and "username" not in steps_done:
                    self._progress("Setting username")
                    final_username = self._handle_username(d, username or final_username)
                    steps_done.add("username")
                    time.sleep(3)
                    continue

                # ── Password ──────────────────────────────────────────────
                if screen == "password" and "password" not in steps_done:
                    self._progress("Entering password")
                    self._enter_password(d, password)
                    steps_done.add("password")
                    time.sleep(3)
                    continue

                # ── Post-signup ───────────────────────────────────────────
                if screen in ("photo", "notifications", "contacts", "post_signup"):
                    self._progress("Dismissing post-signup screen")
                    self._dismiss_post_signup(d)
                    time.sleep(2)
                    continue

                # Recovery: step already done but screen didn't advance — tap Next
                recoverable = {
                    "birthday", "name", "username", "password", "contact",
                    "otp", "terms", "interests", "email_entry",
                }
                if screen in recoverable and screen in steps_done:
                    self._progress(f"Pushing past stuck {screen} screen")
                    self._tap_next(d)
                    time.sleep(2)
                    continue

                # Nothing matched — nudge and re-evaluate
                time.sleep(2)

            outcome = (False, "Timed out during account creation", None)
            return outcome

        except Exception as exc:
            logger.exception("[TT-CREATOR] Unexpected error")
            outcome = (False, f"Unexpected error: {exc}", None)
            return outcome

        finally:
            self._progress("Clearing app data")
            try:
                d.app_clear(PACKAGE)
                logger.info("[TT-CREATOR] App data cleared")
            except Exception as e:
                logger.warning("[TT-CREATOR] Could not clear app data: %s", e)

    # ── Screen detection ──────────────────────────────────────────────────────

    def _detect_screen(self, d) -> str:
        """
        Return a logical screen name based on current UI.
        Based on TikTok 2026 flow (traced live on device).

        Verified flow:
          launch → terms → interests → loading/feed_guest
          → (tap Profile) → profile_guest (login modal with Sign up link)
          → (tap Sign up) → signup_method ("Sign up for TikTok" with Continue with Email)
          → (tap Continue with Email) → email_entry ("Enter email address")
          → birthday → otp → name → username → password → post-signup → home
        """
        # ── Hard error / ban ─────────────────────────────────────────────
        if (d(textContains="banned").exists or
                d(textContains="violated").exists or
                d(textContains="Account suspended").exists):
            return "error"

        # ── Email rejected inline error ───────────────────────────────────
        # "Error" is an ImageView with content-desc="Error" (not text=)
        if ((d(description="Error").exists or d(textContains="Error").exists) and
                (d(textContains="Please try again").exists or
                 d(textContains="different method").exists or
                 d(textContains="already registered").exists)):
            return "email_error"

        # ── Terms / Welcome screen ────────────────────────────────────────
        # "Welcome to TikTok" or "Terms of Service" with a Continue button
        if (d(textContains="Terms and Policies").exists or
                d(textContains="Terms of Service").exists or
                d(textContains="Welcome to TikTok").exists):
            if d(text="Continue").exists:
                return "terms"

        # ── Interests picker ──────────────────────────────────────────────
        if (d(textContains="Choose your interests").exists or
                d(textContains="Choose what you like").exists):
            return "interests"

        # ── Privacy update dialog ─────────────────────────────────────────
        if d(textContains="Privacy Policy update").exists and d(text="Got it").exists:
            return "privacy_update"

        # ── Loading / splash ──────────────────────────────────────────────
        if (d(textContains="Finding content you like").exists or
                d(textContains="Swipe up to start").exists or
                d(textContains="Videos to make your day").exists):
            return "loading"

        # ── Play Store / wrong app ────────────────────────────────────────
        if d(textContains="Similar app available").exists:
            return "wrong_app"

        # ── OTP code entry ────────────────────────────────────────────────
        otp_hints = [
            "Enter the 4-digit code", "Enter the 6-digit code",
            "Enter code", "Verification code",
            "sent to your email", "sent to your phone",
            "Enter the code", "Didn't receive",
            "Resend code",
        ]
        if any(d(textContains=t).exists for t in otp_hints):
            return "otp"
        if d(text="Code").exists:
            return "otp"

        # ── Birthday ──────────────────────────────────────────────────────
        bday_hints = [
            "When's your birthday", "Enter your birthday",
            "Add birthday", "Date of birth", "Birthday",
            "You need to be at least",
        ]
        if any(d(textContains=t).exists for t in bday_hints):
            return "birthday"

        # ── Name screen ───────────────────────────────────────────────────
        if any(d(textContains=t).exists for t in
               ("What's your name", "Create your name", "Your name", "Enter your name")):
            return "name"

        # ── Username screen ───────────────────────────────────────────────
        if any(d(textContains=t).exists for t in
               ("Create username", "Choose a username", "Your username", "Username already")):
            return "username"

        # ── Password screen ───────────────────────────────────────────────
        if any(d(textContains=t).exists for t in
               ("Create password", "Set a password")):
            if d(className="android.widget.EditText").exists:
                return "password"

        # ── Post-signup screens ───────────────────────────────────────────
        if d(textContains="Turn on notifications").exists:
            return "notifications"
        if d(textContains="Add a photo").exists or d(textContains="profile photo").exists:
            return "photo"
        if d(textContains="Find friends").exists or d(textContains="Sync contacts").exists:
            return "contacts"

        # ── Email entry page ("Enter email address") ──────────────────────
        if (d(textContains="Enter email address").exists or
                d(description="Enter email address").exists):
            return "email_entry"

        # ── Signup method modal ("Sign up for TikTok") ────────────────────
        # New TikTok: phone default + "Continue with Email" + social logins
        if d(textContains="Sign up for TikTok").exists:
            return "signup_method"

        # ── Profile / login modal + guest profile ─────────────────────────
        # After tapping Profile: "Log in to TikTok" modal with
        #   "Don't have an account? Sign up" at the bottom
        if (d(textContains="Log in to TikTok").exists or
                d(textContains="Don't have an account").exists or
                d(text="Sign up").exists or d(text="Sign Up").exists or
                d(textContains="Sign up for TikTok").exists):
            return "profile_guest"

        # ── Old-style signup method (Use phone or email) ──────────────────
        if any(d(textContains=t).exists for t in
               ("Use phone or email", "Sign up with phone", "Sign up with email")):
            if not d(className="android.widget.EditText").exists:
                return "signup_method"

        # ── Old-style contact form ────────────────────────────────────────
        if d(className="android.widget.EditText").exists:
            if d(textContains="Phone").exists or d(textContains="Email").exists:
                return "contact_form"
            return "contact_entry"

        # ── Soft / transient error (only when nav bar is absent) ──────────
        if (d(textContains="Something went wrong").exists and
                not d(description="Profile").exists):
            return "soft_error"

        # ── Home (logged in) ──────────────────────────────────────────────
        # "For You" and "Following" appear on guest feed too — confirm logged in
        # by absence of login/signup modals and absence of feed-load errors
        if (d(description="For You").exists or d(description="Following").exists or
                d(resourceId="com.zhiliaoapp.musically:id/main_tab_bar").exists):
            if not (d(textContains="Log in to TikTok").exists or
                    d(textContains="Sign up for TikTok").exists or
                    d(textContains="Don't have an account").exists or
                    d(textContains="Something went wrong").exists or
                    d(textContains="Swipe up").exists):
                return "home"

        # ── Guest feed (no login, nav bar visible) ────────────────────────
        if d(description="Profile").exists:
            return "feed_guest"

        return "unknown"

    # ── Step implementations ──────────────────────────────────────────────────

    def _open_fresh(self, d) -> None:
        """Force-stop, clear all app data, then relaunch TikTok from scratch.
        Clearing before launch guarantees no leftover session is auto-resumed."""
        # Close any currently active app so TikTok opens in foreground
        d.press("home")
        time.sleep(1)
        d.app_stop(PACKAGE)
        time.sleep(1)
        d.app_clear(PACKAGE)   # wipe session/cache so signup screen always appears
        time.sleep(1)
        # Launch TikTok explicitly via am start to guarantee foreground
        self._adb_run("shell", "am", "start",
                      "-S",          # force restart if already running
                      "-n", f"{PACKAGE}/com.ss.android.ugc.aweme.main.MainActivity")
        time.sleep(10)  # TikTok loads slowly; needs network round-trip for splash
        # Fallback: if TikTok not in foreground, use uiautomator2 app_start
        try:
            current = d.app_current().get("package", "")
            if current != PACKAGE:
                d.app_start(PACKAGE)
                time.sleep(8)
        except Exception:
            pass

    def _accept_terms(self, d) -> None:
        """Tap 'Continue' on the Terms and Policies screen."""
        deadline = time.time() + self.T_MEDIUM
        while time.time() < deadline:
            if d(text="Continue").exists:
                d(text="Continue").click()
                return
            time.sleep(1.5)

    def _skip_interests(self, d) -> None:
        """Tap 'Skip' on the interests picker screen."""
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            if d(text="Skip").exists:
                d(text="Skip").click()
                return
            time.sleep(1.5)

    def _tap_profile_tab(self, d) -> None:
        """
        Tap the Profile tab in the bottom navigation.
        Confirmed: description='Profile' on the nav tab FrameLayout.
        Fallback: tap at known coordinates [576,1260][720,1358].
        """
        if d(description="Profile").exists:
            d(description="Profile").click()
            return
        # Coordinate fallback (bottom-right nav item, 720px wide display)
        d.click(648, 1309)

    def _tap_signup(self, d) -> None:
        """Tap 'Sign up' on the guest profile page or login modal."""
        deadline = time.time() + self.T_MEDIUM
        while time.time() < deadline:
            # Exact match first (faster)
            for label in ("Sign up", "Sign Up", "Create account", "Register"):
                if d(text=label).exists:
                    d(text=label).click()
                    return
            # textContains for "Don't have an account? Sign up" style links
            for phrase in ("Don't have an account", "Sign up", "Create account"):
                if d(textContains=phrase).exists:
                    d(textContains=phrase).click()
                    return
            time.sleep(1.5)

    def _select_email_method(self, d) -> None:
        """
        On the 'Sign up for TikTok' modal (new TikTok), click 'Continue with Email'.
        Falls back to old-style 'Use phone or email' selector for older versions.
        """
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            # New TikTok: "Continue with Email" button
            for label in ("Continue with Email", "Email"):
                if d(text=label).exists:
                    d(text=label).click()
                    return
                if d(textContains=label).exists:
                    d(textContains=label).click()
                    return
            # Old TikTok fallback: "Use phone or email"
            for label in ("Use phone or email", "Phone or email",
                           "Use phone / email"):
                if d(text=label).exists:
                    d(text=label).click()
                    return
                if d(textContains=label[:12]).exists:
                    d(textContains=label[:12]).click()
                    return
            time.sleep(1.5)

    def _enter_email(self, d, email: str) -> bool:
        """
        Enter email on the 'Enter email address' page (new TikTok signup flow).
        Splits on '@' and uses KEYCODE_AT so the @ character is reliable across ADB.
        """
        if not email:
            return False
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.5)
                self._adb_clear_and_type("")  # clear first
                time.sleep(0.2)
                local, _, domain = email.partition("@")
                self._adb_type(local)
                time.sleep(0.1)
                self._adb_run("shell", "input", "keyevent", "77")  # KEYCODE_AT
                time.sleep(0.1)
                self._adb_type(domain)
                time.sleep(1)
                self._tap_next(d)
                time.sleep(4)
                return True
            time.sleep(1.5)
        return False

    def _select_phone_email_method(self, d) -> None:
        """
        On the signup method screen, select 'Use phone or email'
        to get to the phone/email form (instead of social login).
        """
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            for label in ("Use phone or email", "Phone or email",
                           "Sign up with phone or email",
                           "Continue with phone or email",
                           "Use phone / email",
                           "Phone / email"):
                if d(text=label).exists:
                    d(text=label).click()
                    return
                if d(textContains=label[:12]).exists:
                    d(textContains=label[:12]).click()
                    return
            time.sleep(1.5)

    def _select_contact_tab(self, d, use_email: bool) -> None:
        """
        Switch to the Email or Phone tab on the contact form.
        TikTok shows "Phone" and "Email" tabs at the top of the form.
        """
        target = "Email" if use_email else "Phone"
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            if d(text=target).exists:
                d(text=target).click()
                time.sleep(1)
                return
            # Some versions use "Email address" or "Phone number"
            if use_email:
                for lbl in ("Email address", "Use email"):
                    if d(textContains=lbl).exists:
                        d(textContains=lbl).click()
                        time.sleep(1)
                        return
            else:
                for lbl in ("Phone number", "Use phone"):
                    if d(textContains=lbl).exists:
                        d(textContains=lbl).click()
                        time.sleep(1)
                        return
            time.sleep(1.5)

    def _enter_contact(self, d, email: str = None, phone: str = None) -> bool:
        """Type email or phone number into the input field and tap Next."""
        contact = email or phone
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.5)
                self._adb_clear_and_type(contact)
                time.sleep(1)
                self._tap_next(d)
                time.sleep(3)
                return True
            time.sleep(1.5)
        return False

    def _enter_birthday(self, d, birthday: str = "1998-05-20") -> None:
        """
        Fill in the birthday screen.
        TikTok uses a scroll-wheel (NumberPicker) for month/day/year on most
        versions. Some versions show three EditText fields.
        """
        try:
            year, month, day = [int(x) for x in birthday.split("-")]
        except (ValueError, AttributeError):
            year, month, day = 1998, 5, 20

        time.sleep(1.5)

        # Try EditText fields first
        fields = d(className="android.widget.EditText")
        try:
            n = len(fields)
        except Exception:
            n = 0

        if n >= 3:
            logger.info("[TT-CREATOR] Birthday: 3-field EditText mode")
            fields[0].clear_text(); fields[0].set_text(str(month)); time.sleep(0.3)
            fields[1].clear_text(); fields[1].set_text(str(day));   time.sleep(0.3)
            fields[2].clear_text(); fields[2].set_text(str(year));  time.sleep(0.3)
        elif n == 1:
            logger.info("[TT-CREATOR] Birthday: single-field mode")
            fields[0].clear_text()
            fields[0].set_text(f"{month:02d}/{day:02d}/{year}")
            time.sleep(0.3)
        else:
            # NumberPicker wheels — most common for TikTok
            logger.info("[TT-CREATOR] Birthday: wheel-picker mode")
            self._fill_date_wheels(d, year, month, day)

        self._tap_next(d)

    def _fill_date_wheels(self, d, year: int, month: int, day: int) -> None:
        """Scroll TikTok's birthday NumberPicker wheels."""
        MONTHS = ["January","February","March","April","May","June",
                  "July","August","September","October","November","December"]
        try:
            pickers = d(className="android.widget.NumberPicker")
            if len(pickers) < 3:
                logger.warning("[TT-CREATOR] Expected 3 pickers, got %d", len(pickers))
                # Try swiping in the birthday area instead
                self._swipe_to_date(d, year, month, day)
                return

            def _scroll_to(picker, target: str):
                for _ in range(30):
                    child = picker.child(className="android.widget.EditText")
                    if child.exists and child.get_text() == target:
                        return
                    picker.swipe("up", steps=3)
                    time.sleep(0.12)

            _scroll_to(pickers[0], MONTHS[month - 1])
            _scroll_to(pickers[1], str(day))
            _scroll_to(pickers[2], str(year))
        except Exception as exc:
            logger.error("[TT-CREATOR] Wheel picker failed: %s", exc)

    def _swipe_to_date(self, d, year: int, month: int, day: int) -> None:
        """
        Fallback for TikTok birthday pickers that use swipeable columns
        instead of standard NumberPicker widgets.
        Finds three scrollable columns by bounding box and swipes each one.
        """
        info = d.info
        screen_w = info.get('displayWidth', 720)
        screen_h = info.get('displayHeight', 1280)

        # TikTok birthday picker typically occupies middle 60% of screen height
        mid_y = int(screen_h * 0.55)

        # Column X positions: roughly 1/6, 3/6, 5/6 of screen width
        cols = [int(screen_w * 0.17), int(screen_w * 0.50), int(screen_w * 0.83)]
        targets = [str(month), str(day), str(year)]

        for cx, target in zip(cols, targets):
            # Swipe up to increment; max 40 swipes
            for _ in range(40):
                result = d(className="android.widget.EditText",
                           bounds=f"[{cx-60},{mid_y-30}][{cx+60},{mid_y+30}]")
                if result.exists and result.get_text() == target:
                    break
                d.swipe(cx, mid_y + 40, cx, mid_y - 40, steps=5)
                time.sleep(0.1)

    def _verify_otp(self, d, email: str = None, email_password: str = None,
                    imap_server: str = None) -> bool:
        """
        Fetch OTP from email via IMAP and enter it.
        TikTok sends a 4-digit or 6-digit code depending on version/region.
        """
        if not (email and email_password):
            logger.warning("[TT-CREATOR] No email credentials for OTP")
            return False

        self._progress("Waiting for OTP (up to 90 s)")
        code = self._fetch_otp_from_email(email, email_password, imap_server, max_wait=90)

        if not code:
            # Try resend
            for label in ("Resend code", "Resend", "Didn't receive a code",
                           "Send code again", "Didn\u2019t receive"):
                if d(textContains=label[:12]).exists:
                    d(textContains=label[:12]).click()
                    time.sleep(5)
                    break
            self._progress("Retrying OTP fetch")
            code = self._fetch_otp_from_email(email, email_password, imap_server, max_wait=60)

        if not code:
            return False

        logger.info("[TT-CREATOR] OTP: %s", code)
        self._progress(f"Entering OTP: {code}")

        fields = d(className="android.widget.EditText")
        try:
            box_count = len(fields) if fields.exists else 0
        except Exception:
            box_count = 0

        if box_count >= 4:
            # Individual digit boxes (TikTok uses 4 or 6 boxes on some versions)
            for i, digit in enumerate(code[:box_count]):
                try:
                    fields[i].click()
                    time.sleep(0.1)
                    self._adb_run("shell", "input", "text", digit)
                    time.sleep(0.15)
                except Exception:
                    pass
        else:
            # Single OTP input field
            if fields.exists:
                fields[0].click()
                time.sleep(0.3)
                self._adb_clear_and_type(code)
                time.sleep(1)

        self._tap_next(d)
        time.sleep(3)
        return True

    def _enter_name(self, d, full_name: str) -> None:
        """Enter full name on the name screen."""
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.5)
                self._adb_clear_and_type(full_name)
                time.sleep(0.8)
                self._tap_next(d)
                return
            time.sleep(1.5)

    def _handle_username(self, d, username: str) -> str:
        """
        Set desired username. TikTok auto-generates a suggestion — clear it
        and type the desired one. Append suffix if taken.
        """
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.4)
                self._adb_clear_and_type(username)
                time.sleep(3)   # wait for availability check

                if (d(textContains="already taken").exists or
                        d(textContains="not available").exists or
                        d(textContains="already been used").exists):
                    suffix = str(int(time.time()))[-4:]
                    alt = f"{username}_{suffix}"
                    logger.warning("[TT-CREATOR] %s taken, trying %s", username, alt)
                    fields[0].click()
                    time.sleep(0.3)
                    self._adb_clear_and_type(alt)
                    time.sleep(2)
                    username = alt

                self._tap_next(d)
                return username
            time.sleep(1.5)
        return username

    def _enter_password(self, d, password: str) -> None:
        """Enter password on the password creation screen."""
        deadline = time.time() + self.T_SHORT
        while time.time() < deadline:
            fields = d(className="android.widget.EditText")
            if fields.exists:
                fields[0].click()
                time.sleep(0.5)
                self._adb_clear_and_type(password)
                time.sleep(1)
                self._tap_next(d)
                return
            time.sleep(1.5)

    def _dismiss_post_signup(self, d) -> None:
        """Dismiss optional post-signup screens (notifications, contacts, etc.)."""
        for label in ("Skip", "Not Now", "Later", "Skip for now",
                       "Don't allow", "Decline", "Close", "Cancel", "No Thanks"):
            if d(text=label).exists:
                d(text=label).click()
                time.sleep(1.5)
                return
        if d(text="Allow").exists:
            d(text="Allow").click()
            time.sleep(1)
            return
        self._tap_next(d)

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _tap_next(self, d) -> bool:
        """Tap the primary forward action button."""
        labels = ("Next", "Continue", "Done", "Confirm", "Submit", "Verify", "OK", "Send")
        # text= (clickable first, then any)
        for label in labels:
            btn = d(text=label, clickable=True)
            if btn.exists:
                btn.click()
                time.sleep(1.5)
                return True
        for label in labels:
            if d(text=label).exists:
                d(text=label).click()
                time.sleep(1.5)
                return True
        # description= fallback (React Native / obfuscated buttons)
        for label in labels:
            btn = d(description=label, clickable=True)
            if btn.exists:
                btn.click()
                time.sleep(1.5)
                return True
        # ADB Enter as last resort
        self._adb_run("shell", "input", "keyevent", "KEYCODE_ENTER")
        time.sleep(1)
        return False

    def _progress(self, msg: str) -> None:
        logger.info("[TT-CREATOR] %s", msg)
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

    def _resolve_imap(self, email_address: str, override: str = None) -> tuple:
        """Returns (host, port) tuple. Supports 'host:port' in override."""
        if override:
            if ":" in override:
                host, _, port_str = override.rpartition(":")
                try:
                    return host.strip(), int(port_str.strip())
                except ValueError:
                    pass
            return override.strip(), 993
        domain = email_address.split("@")[-1].lower()
        host = self._IMAP_MAP.get(domain, "imap.firstmail.ltd")
        return host, 993

    def _fetch_otp_from_email(
        self,
        email_address: str,
        email_password: str,
        imap_server: str = None,
        max_wait: int = 90,
    ) -> Optional[str]:
        """Poll IMAP inbox for a TikTok OTP (4 or 6 digits)."""
        host, port = self._resolve_imap(email_address, imap_server)
        deadline = time.time() + max_wait

        while time.time() < deadline:
            try:
                mail = imaplib.IMAP4_SSL(host, port)
                mail.login(email_address, email_password)
                mail.select("inbox")

                # Try recent unseen first, then all recent TikTok mails
                for search_criteria in [
                    '(UNSEEN FROM "tiktok")',
                    '(FROM "tiktok")',
                    '(UNSEEN SUBJECT "TikTok")',
                    '(SUBJECT "TikTok")',
                    '(UNSEEN)',
                ]:
                    _, ids = mail.search(None, search_criteria)
                    id_list = ids[0].split() if ids and ids[0] else []
                    for eid in reversed(id_list[-10:]):
                        try:
                            _, data = mail.fetch(eid, "(RFC822)")
                            msg = email_lib.message_from_bytes(data[0][1])
                            body = self._extract_body(msg)
                            code = self._extract_otp_code(body)
                            if code:
                                mail.logout()
                                return code
                        except Exception:
                            continue
                    if id_list:
                        break  # found messages with this criteria, stop trying others

                mail.logout()
            except Exception as exc:
                logger.warning("[TT-CREATOR] IMAP error: %s", exc)

            time.sleep(8)

        return None

    @staticmethod
    def _extract_body(msg) -> str:
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
    def _extract_otp_code(text: str) -> Optional[str]:
        """
        Find 4-digit or 6-digit OTP in email body.
        TikTok sends codes like: 'Your verification code is 123456'
        or a large standalone 4/6-digit number.
        """
        # Prefer digit groups explicitly labelled as verification code
        for pattern in [
            r'verification code[^\d]*(\d{4,6})',
            r'code[^\d]*(\d{4,6})',
            r'\b(\d{6})\b',
            r'\b(\d{4})\b',
        ]:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                # Skip years
                if not re.match(r'^(19|20)\d{2}$', m):
                    return m
        return None
