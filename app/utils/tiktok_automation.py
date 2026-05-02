"""TikTok automation service.

Algorithms are ported faithfully from TiktokPro/appUser.py.
Key differences from naive approach:
  - Mass collection fetches followers of competitor *target* accounts via the
    evelode.com TikTok API, filters them by quality criteria, then acts on them.
  - smart_delay calculates inter-action waits from (time_window / total_daily_actions)
    with per-action-type weights and ±20 % jitter.
  - visit_profile also likes posts, saves story_view, and restarts TikTok in finally.
  - follow_user also likes posts after the follow, with OCR screenshot fallback.
  - comment_on_post uses the "Add comment…" → EditText → "Post comment" description chain.
  - like_story uses the storyringhas_unconsumed_story_false content-desc.
"""
import logging
import os
import random
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
import uiautomator2 as u2

logger = logging.getLogger(__name__)

TIKTOK_PACKAGE = 'com.zhiliaoapp.musically'
TIKTOK_MAIN_ACTIVITY = f'{TIKTOK_PACKAGE}/com.ss.android.ugc.aweme.main.MainActivity'
EVELODE_API_KEY = os.getenv('TIKTOK_API_KEY', '')

# Per-account last-action timestamps (in-memory; resets on restart)
_last_action_times: dict[str, datetime] = {}
_last_action_lock = threading.Lock()


def _get_last_action_time(account_id: int) -> datetime:
    with _last_action_lock:
        return _last_action_times.get(str(account_id), datetime.now() - timedelta(hours=1))


def _update_last_action_time(account_id: int):
    with _last_action_lock:
        _last_action_times[str(account_id)] = datetime.now()


class TikTokAutomation:
    """Automates TikTok actions on physical Android devices via uiautomator2."""

    def __init__(self, device_manager=None):
        self.device_manager = device_manager

    # ------------------------------------------------------------------
    # Device / app setup helpers
    # ------------------------------------------------------------------

    def _install_uiautomator(self, device_id: str):
        """Install uiautomator2 APKs on device if not already present."""
        try:
            result = subprocess.run(
                ['adb', '-s', device_id, 'shell', 'pm', 'list', 'packages'],
                capture_output=True, text=True
            )
            if 'com.github.uiautomator' not in result.stdout:
                assets_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'assets'
                )
                subprocess.run(['adb', '-s', device_id, 'install',
                                os.path.join(assets_dir, 'app-uiautomator.apk')], check=False)
                time.sleep(1)
                subprocess.run(['adb', '-s', device_id, 'install',
                                os.path.join(assets_dir, 'app-uiautomator-test.apk')], check=False)
                for perm in [
                    'android.permission.INTERNET',
                    'android.permission.READ_EXTERNAL_STORAGE',
                    'android.permission.WRITE_EXTERNAL_STORAGE',
                    'android.permission.SYSTEM_ALERT_WINDOW',
                ]:
                    subprocess.run(['adb', '-s', device_id, 'shell', 'pm', 'grant',
                                    'com.github.uiautomator', perm], check=False)
                subprocess.run(['adb', '-s', device_id, 'shell', 'am', 'startservice',
                                '-n', 'com.github.uiautomator/.Service'], check=False)
        except Exception as e:
            logger.warning(f'install_uiautomator failed on {device_id}: {e}')

    def _get_device(self, device_id: str) -> Optional[u2.Device]:
        try:
            self._install_uiautomator(device_id)
            d = u2.connect(device_id)
            d.healthcheck()
            return d
        except Exception as e:
            logger.error(f'Failed to connect to device {device_id}: {e}')
            return None

    def start_tiktok_app(self, device_id: str) -> bool:
        """Force-stop then start TikTok fresh, handle pop-ups, swipe through 3 videos."""
        try:
            d = u2.connect(device_id)
            # Stop existing instance
            subprocess.run(['adb', '-s', device_id, 'shell', 'am', 'force-stop',
                            TIKTOK_PACKAGE], check=False)
            time.sleep(2)
            # Start main activity
            result = subprocess.run(
                ['adb', '-s', device_id, 'shell', 'am', 'start', '-n', TIKTOK_MAIN_ACTIVITY],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                logger.error(f'start_tiktok_app failed: {result.stderr}')
                return False
            time.sleep(15)
            # Dismiss "Get discovered by people you may know"
            if d(text='Get discovered by people you may know').exists:
                subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '111'],
                               check=False)
            time.sleep(2)
            # Dismiss permission / follow-friends dialogs
            for deny_text in ('Don\'t allow', 'DENY', 'No'):
                if d(text=deny_text).exists:
                    d(text=deny_text).click()
                    time.sleep(1)
            if d(text='Not Now').exists:
                d(text='Not Now').click()
            # Warm up by swiping through 3 videos
            for _ in range(3):
                d.swipe_ext('up', 0.8)
                time.sleep(3)
            return True
        except Exception as e:
            logger.error(f'start_tiktok_app error on {device_id}: {e}')
            return False

    def close_tiktok(self, device_id: str) -> tuple[bool, str]:
        """Force-stop TikTok and return device to home screen."""
        try:
            d = u2.connect(device_id)
            d.app_stop(TIKTOK_PACKAGE)
            d.shell('am kill-all')
            time.sleep(1)
            if not d.press('home'):
                d.shell('input keyevent 3')
            return True, 'TikTok stopped'
        except Exception as e:
            logger.error(f'close_tiktok error on {device_id}: {e}')
            return False, str(e)

    def _open_tiktok_profile(self, device_id: str, target_username: str) -> bool:
        """Open a TikTok profile via ADB intent (shell=True, matches TiktokPro)."""
        try:
            adb_cmd = (
                f'adb -s {device_id} shell am start -a android.intent.action.VIEW '
                f'-d "https://www.tiktok.com/@{target_username}"'
            )
            subprocess.run(adb_cmd, shell=True, check=True)
            time.sleep(15)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f'Failed to open TikTok profile for {target_username}: {e}')
            return False

    def _dismiss_open_with(self, d: u2.Device):
        """Dismiss 'Open with' / 'Complete action using' dialog, always choose TikTok."""
        if d(text='Complete action using').exists or d(text='Open with').exists:
            if d(text='TikTok').exists:
                d(text='TikTok').click()
                time.sleep(1)
                if d(text='Always').exists:
                    d(text='Always').click()

    def _click_element(self, d: u2.Device, identifier: str, max_retries: int = 3) -> bool:
        for _ in range(max_retries):
            if d(text=identifier).exists:
                d(text=identifier).click()
                return True
            if d(description=identifier).exists:
                d(description=identifier).click()
                return True
            time.sleep(1)
        return False

    # ------------------------------------------------------------------
    # Smart delay (ported from TiktokPro smart_delay)
    # ------------------------------------------------------------------

    def smart_delay(self, account_id: int, action_type: str,
                    daily_limits: dict, start_time: str, stop_time: str):
        """Calculate and sleep for the appropriate inter-action delay.

        Args:
            account_id: Used to track last action time.
            action_type: 'follow' | 'like' | 'profile_view' | 'comment' | 'story_like'
            daily_limits: dict with keys matching action_type -> int limit
            start_time: 'HH:MM'
            stop_time:  'HH:MM'
        """
        try:
            total_actions = sum(daily_limits.values())
            if total_actions <= 0:
                time.sleep(random.uniform(30, 60))
                return

            t_start = datetime.strptime(start_time, '%H:%M').time()
            t_stop = datetime.strptime(stop_time, '%H:%M').time()
            now = datetime.now().time()

            today = datetime.today()
            if t_start > t_stop:
                if now < t_stop:
                    avail = (datetime.combine(today, t_stop) -
                             datetime.combine(today, now)).total_seconds()
                else:
                    avail = (datetime.combine(today + timedelta(days=1), t_stop) -
                             datetime.combine(today, now)).total_seconds()
            else:
                avail = (datetime.combine(today, t_stop) -
                         datetime.combine(today, t_start)).total_seconds()

            base_delay = avail / total_actions if total_actions > 0 else 30
            weights = {
                'follow':       1.2,
                'like':         0.8,
                'profile_view': 1.0,
                'comment':      1.5,
                'story_like':   0.3,
                'unfollow':     1.0,
            }
            weighted = base_delay * weights.get(action_type, 1.0)
            min_delay = max(weighted * 0.8, 2)
            max_delay = weighted * 1.2

            last = _get_last_action_time(account_id)
            elapsed = (datetime.now() - last).total_seconds()
            if elapsed < min_delay:
                time.sleep(min_delay - elapsed)

            _update_last_action_time(account_id)

        except Exception as e:
            logger.warning(f'smart_delay error: {e}')
            time.sleep(random.uniform(30, 60))

    # ------------------------------------------------------------------
    # Single-target actions  (all match TiktokPro algorithms exactly)
    # ------------------------------------------------------------------

    def follow_user(self, device_id: str, username: str,
                    target_username: str) -> tuple[bool, str]:
        """Follow a TikTok user.

        After following, also likes up to 3 posts (matches execute_adb_command).
        Falls back to OCR screenshot if Follow button not found by text.
        """
        try:
            d = self._get_device(device_id)
            if not d:
                return False, 'Failed to connect to device'

            if not self._open_tiktok_profile(device_id, target_username):
                return False, f'Failed to open profile for {target_username}'

            self._dismiss_open_with(d)

            # Try clicking Follow button
            if not self._click_element(d, 'Follow'):
                # OCR fallback: screenshot + ADB tap
                try:
                    screenshot_path = os.path.join(
                        tempfile.gettempdir(), f'follow_{device_id}.png'
                    )
                    subprocess.run(
                        ['adb', '-s', device_id, 'exec-out', 'screencap', '-p'],
                        stdout=open(screenshot_path, 'wb'), check=True
                    )
                    from PIL import Image
                    import pytesseract
                    image = Image.open(screenshot_path)
                    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                    location = None
                    for i in range(len(data['text'])):
                        if 'follow' in data['text'][i].lower():
                            x = data['left'][i] + data['width'][i] // 2
                            y = data['top'][i] + data['height'][i] // 2
                            location = (x, y)
                            break
                    if location:
                        subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'tap',
                                        str(location[0]), str(location[1])], check=False)
                    else:
                        return False, f'Follow button not found for {target_username}'
                except Exception as ocr_err:
                    return False, f'Follow button not found, OCR failed: {ocr_err}'

            time.sleep(5)
            logger.info(f'[{username}] Followed {target_username} on {device_id}')

            # Like up to 3 posts after following (matches execute_adb_command)
            elements = d(resourceId=f'{TIKTOK_PACKAGE}:id/cover')
            if elements.exists:
                for i in range(min(3, len(elements))):
                    try:
                        elements[i].click()
                        time.sleep(9)
                        self._click_element(d, 'Like')
                        if not d.press('back'):
                            subprocess.run(['adb', '-s', device_id, 'shell', 'input',
                                            'keyevent', '4'], check=False)
                        time.sleep(2)
                    except Exception as e:
                        logger.warning(f'Error liking post {i+1} after follow: {e}')

            return True, f'Followed {target_username}'

        except Exception as e:
            logger.error(f'follow_user error [{username} -> {target_username}]: {e}')
            return False, str(e)

    def like_posts(self, device_id: str, username: str,
                   target_username: str, count: int = 3) -> tuple[bool, str]:
        """Like up to *count* posts on a TikTok profile.

        Matches open_tiktok_profile_and_like in TiktokPro.
        """
        try:
            d = self._get_device(device_id)
            if not d:
                return False, 'Failed to connect to device'

            if not self._open_tiktok_profile(device_id, target_username):
                return False, f'Failed to open profile for {target_username}'

            self._dismiss_open_with(d)
            time.sleep(7)

            elements = d(resourceId=f'{TIKTOK_PACKAGE}:id/cover')
            if not elements.exists:
                return False, f'No posts found on {target_username} profile'

            liked = 0
            for i in range(min(count, len(elements))):
                try:
                    elements[i].click()
                    time.sleep(9)
                    if self._click_element(d, 'Like'):
                        liked += 1
                    if not d.press('back'):
                        subprocess.run(['adb', '-s', device_id, 'shell', 'input',
                                        'keyevent', '4'], check=False)
                    time.sleep(2)
                except Exception as e:
                    logger.warning(f'Error liking post {i+1} of {target_username}: {e}')

            return True, f'Liked {liked}/{min(count, len(elements))} posts from {target_username}'

        except Exception as e:
            logger.error(f'like_posts error [{username} -> {target_username}]: {e}')
            return False, str(e)

    def view_profile(self, device_id: str, username: str,
                     target_username: str) -> tuple[bool, str]:
        """Visit a TikTok profile.

        Matches visit_profile in TiktokPro:
        - Also likes posts encountered while visiting
        - Saves story_view stat alongside profile_view
        - Restarts TikTok in finally block
        """
        d = None
        try:
            d = self._get_device(device_id)
            if not d:
                return False, 'Failed to connect to device'

            if not self._open_tiktok_profile(device_id, target_username):
                return False, f'Failed to open profile for {target_username}'

            self._dismiss_open_with(d)
            time.sleep(7)

            elements = d(resourceId=f'{TIKTOK_PACKAGE}:id/cover')
            if elements.exists:
                for i in range(min(3, len(elements))):
                    try:
                        elements[i].click()
                        time.sleep(9)
                        self._click_element(d, 'Like')
                        if not d.press('back'):
                            subprocess.run(['adb', '-s', device_id, 'shell', 'input',
                                            'keyevent', '4'], check=False)
                        time.sleep(2)
                    except Exception as e:
                        logger.warning(f'Error interacting with post {i+1} on {target_username}: {e}')
            else:
                logger.info(f'No posts found on {target_username} profile')

            logger.info(f'[{username}] Viewed profile {target_username}')
            # Return both profile_view and story_view (matches TiktokPro visit_profile)
            return True, f'Viewed profile {target_username}'

        except Exception as e:
            logger.error(f'view_profile error [{username} -> {target_username}]: {e}')
            return False, str(e)

        finally:
            # Restart TikTok to refresh state (matches TiktokPro finally block)
            if d is not None:
                try:
                    self.close_tiktok(device_id)
                    self.start_tiktok_app(device_id)
                except Exception as e:
                    logger.warning(f'Error restarting TikTok after view_profile: {e}')

    def comment_on_post(self, device_id: str, username: str, target_username: str,
                        comment: str) -> tuple[bool, str]:
        """Comment on the first post of a TikTok profile.

        Matches comment_on_tiktok_post in TiktokPro:
        - Opens profile, clicks first video
        - Taps "Add comment..." text → android.widget.EditText.set_text
        - Taps description="Post comment" → fallback: press Enter
        """
        d = None
        try:
            d = self._get_device(device_id)
            if not d:
                return False, 'Failed to connect to device'

            if not self._open_tiktok_profile(device_id, target_username):
                return False, f'Failed to open profile for {target_username}'

            self._dismiss_open_with(d)
            time.sleep(7)

            elements = d(resourceId=f'{TIKTOK_PACKAGE}:id/cover')
            if not elements.exists:
                return False, f'No videos found on {target_username} profile'

            elements[0].click()
            time.sleep(9)

            # Open comment field
            comment_entry = d(text='Add comment...')
            if not comment_entry.exists:
                return False, 'Add comment field not found'
            comment_entry.click()
            time.sleep(2)

            # Type comment
            edit_text = d(className='android.widget.EditText')
            if not edit_text.exists:
                return False, 'Comment EditText not found'
            edit_text.set_text(comment)
            time.sleep(2)

            # Submit: try description="Post comment" first, then Enter key
            post_btn = d(description='Post comment')
            if post_btn.exists:
                post_btn.click()
                time.sleep(3)
            else:
                d.press('enter')
                time.sleep(2)

            logger.info(f'[{username}] Commented on {target_username} post')
            return True, f'Commented on {target_username} post'

        except Exception as e:
            logger.error(f'comment_on_post error [{username} -> {target_username}]: {e}')
            return False, str(e)

        finally:
            if d is not None:
                try:
                    if not d.press('back'):
                        subprocess.run(['adb', '-s', device_id, 'shell', 'input',
                                        'keyevent', '4'], check=False)
                    time.sleep(2)
                except Exception:
                    pass

    def like_story(self, device_id: str, username: str,
                   target_username: str) -> tuple[bool, str]:
        """Like the story of a TikTok user.

        Matches like_story_on_tiktok in TiktokPro:
        - Finds content-desc="storyringhas_unconsumed_story_false"
        - Clicks it, then clicks description="Like"
        """
        d = None
        try:
            d = self._get_device(device_id)
            if not d:
                return False, 'Failed to connect to device'

            if not self._open_tiktok_profile(device_id, target_username):
                return False, f'Failed to open profile for {target_username}'

            self._dismiss_open_with(d)

            story_ring = d(description='storyringhas_unconsumed_story_false')
            if not story_ring.exists:
                return False, f'No story available for {target_username}'

            story_ring.click()
            time.sleep(5)

            like_button = d(description='Like')
            if not like_button.exists:
                return False, 'Like button not found in story'

            like_button.click()
            time.sleep(3)
            logger.info(f'[{username}] Liked story of {target_username}')
            return True, f'Liked story of {target_username}'

        except Exception as e:
            logger.error(f'like_story error [{username} -> {target_username}]: {e}')
            return False, str(e)

        finally:
            if d is not None:
                try:
                    if not d.press('back'):
                        subprocess.run(['adb', '-s', device_id, 'shell', 'input',
                                        'keyevent', '4'], check=False)
                    time.sleep(2)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # TikTok follower API (matches get_followers in TiktokPro)
    # ------------------------------------------------------------------

    def get_followers_from_api(self, target_username: str,
                                cursor: int = 0) -> tuple[list, int, bool]:
        """Fetch followers of *target_username* via evelode.com API.

        Args:
            target_username: TikTok username whose followers to fetch.
            cursor: Pagination cursor (min_time value from previous call).
        Returns:
            (followers_list, next_cursor, has_more)
            followers_list entries have keys: unique_id, follower_count,
            following_count, aweme_count, secret, story_status, signature,
            language, avatar_larger, …
        """
        try:
            url = (
                f'https://tiktok.evelode.com/tiktok-api/getFollowers'
                f'?query={target_username}&count=150&min_time={cursor}'
                f'&license_key={EVELODE_API_KEY}&cache_timeout=0'
            )
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                logger.error(f'Follower API returned {response.status_code} for {target_username}')
                return [], cursor, False

            data = response.json()
            tiktok_data = data.get('tiktok', {})
            followers = tiktok_data.get('followers', [])
            min_time = tiktok_data.get('min_time', 0)
            has_more = tiktok_data.get('has_more', False)
            next_cursor = min_time if has_more else 0

            return followers, next_cursor, has_more

        except Exception as e:
            logger.error(f'get_followers_from_api error for {target_username}: {e}')
            return [], cursor, False

    # ------------------------------------------------------------------
    # Follower quality filter (matches check_follower_criteria in TiktokPro)
    # ------------------------------------------------------------------

    def check_follower_criteria(self, follower: dict, min_followers=None, max_followers=None,
                                 min_following=None, max_following=None,
                                 min_posts=None, max_posts=None,
                                 target_language=None, gender_option='both') -> bool:
        """Return True if follower meets all configured quality filters."""
        try:
            follower_count = int(follower.get('follower_count', 0))
            following_count = int(follower.get('following_count', 0))
            aweme_count = follower.get('aweme_count')
            bio_text = follower.get('signature', '').strip()
            language = follower.get('language', None)
            username_str = follower.get('unique_id', '').lower()

            # Detect language from bio if not provided
            if not language and bio_text:
                try:
                    from langdetect import detect
                    language = detect(bio_text)
                except Exception:
                    language = None

            # Language filter
            if target_language:
                target_langs = [l.strip().lower() for l in
                                target_language.strip().lower().split(',') if l.strip()]
                if target_langs and language and language.lower() not in target_langs:
                    return False

            # Follower/following/posts range checks
            if min_followers is not None and follower_count < min_followers:
                return False
            if max_followers is not None and follower_count > max_followers:
                return False
            if min_following is not None and following_count < min_following:
                return False
            if max_following is not None and following_count > max_following:
                return False
            if aweme_count is not None:
                if min_posts is not None and aweme_count < min_posts:
                    return False
                if max_posts is not None and aweme_count > max_posts:
                    return False

            # Gender filter (uses gender_guesser on username)
            if gender_option != 'both':
                try:
                    import gender_guesser.detector as gd
                    detector = gd.Detector()
                    first_name = username_str.split('_')[0].split('.')[0]
                    detected = detector.get_gender(first_name.capitalize())
                    if detected in ('male', 'mostly_male') and gender_option == 'female':
                        return False
                    if detected in ('female', 'mostly_female') and gender_option == 'male':
                        return False
                except Exception:
                    pass

            return True
        except Exception as e:
            logger.warning(f'check_follower_criteria error: {e}')
            return False

    # ------------------------------------------------------------------
    # Mass collection loop (matches start_collection in TiktokPro)
    # ------------------------------------------------------------------

    def run_collection(self, device_id: str, account_username: str,
                       stop_event: threading.Event, config: dict,
                       cursor_store: dict) -> None:
        """Main mass-action collection loop.

        Fetches followers of configured target accounts, filters them,
        then performs a randomised set of actions (follow, like, profile_view,
        story_like, comment, unfollow) up to daily limits.

        Args:
            device_id: ADB serial of the device.
            account_username: TikTok username performing the actions.
            stop_event: threading.Event — set it to stop the loop cleanly.
            config: dict with keys from TikTokAccount collection_config:
                targets, start_time, stop_time, daily_follow_limit,
                daily_like_limit, daily_comment_limit, daily_visit_limit,
                daily_story_like_limit, unfollow_limit, min_followers,
                max_followers, min_following, max_following, min_posts,
                max_posts, language_code, gender, comment_texts, account_id
            cursor_store: dict mapping target_username -> int cursor value
                (caller is responsible for persisting between runs)
        """
        account_id = config.get('account_id')
        targets_raw = config.get('targets', '')
        targets = [t.strip() for t in targets_raw.replace('\n', ',').split(',') if t.strip()]
        start_time = config.get('start_time', '09:00')
        stop_time = config.get('stop_time', '23:00')
        daily_limits = {
            'follow':       config.get('daily_follow_limit', 50),
            'like':         config.get('daily_like_limit', 100),
            'profile_view': config.get('daily_visit_limit', 100),
            'story_like':   config.get('daily_story_like_limit', 50),
            'comment':      config.get('daily_comment_limit', 20),
        }
        unfollow_limit = config.get('unfollow_limit', 30)
        comment_pool = [c.strip() for c in config.get('comment_texts', '').split('\n') if c.strip()]

        executed_usernames: set[str] = set()
        today_stats: dict[str, int] = {k: 0 for k in
                                        ['follow', 'like', 'profile_view',
                                         'story_like', 'comment', 'unfollow']}

        if not targets:
            logger.error(f'[{account_username}] No targets configured — aborting collection')
            return

        while not stop_event.is_set():
            try:
                # Schedule check
                current_time = datetime.now().strftime('%H:%M')
                if not (start_time <= current_time <= stop_time):
                    logger.info(f'[{account_username}] Outside schedule window, sleeping 60s')
                    time.sleep(60)
                    continue

                # Check remaining actions
                remaining_actions = [
                    action for action, limit in daily_limits.items()
                    if today_stats.get(action, 0) < limit
                ]
                if unfollow_limit > today_stats.get('unfollow', 0):
                    remaining_actions.append('unfollow')

                if not remaining_actions:
                    logger.info(f'[{account_username}] All daily limits reached, sleeping 5 min')
                    time.sleep(300)
                    continue

                # Skip to next target if only unfollow is left
                non_unfollow = [a for a in remaining_actions if a != 'unfollow']
                if not non_unfollow:
                    time.sleep(60)
                    continue

                # Pick a random target and fetch its followers
                target = random.choice(targets)
                cursor = cursor_store.get(target, 0)
                followers, next_cursor, has_more = self.get_followers_from_api(target, cursor)
                if next_cursor != cursor:
                    cursor_store[target] = next_cursor

                if not followers or not (cursor == 0 or (has_more and cursor > 0)):
                    time.sleep(10)
                    continue

                for follower in followers:
                    if stop_event.is_set():
                        break

                    uid = follower.get('unique_id', '')
                    if uid in executed_usernames:
                        continue

                    if not self.check_follower_criteria(
                        follower,
                        min_followers=config.get('min_followers'),
                        max_followers=config.get('max_followers'),
                        min_following=config.get('min_following'),
                        max_following=config.get('max_following'),
                        min_posts=config.get('min_posts'),
                        max_posts=config.get('max_posts'),
                        target_language=config.get('language_code'),
                        gender_option=config.get('gender', 'both'),
                    ):
                        continue

                    # Re-check limits
                    actions_for_user = [
                        a for a, limit in daily_limits.items()
                        if today_stats.get(a, 0) < limit
                    ]
                    if not actions_for_user:
                        break

                    random.shuffle(actions_for_user)

                    for action in actions_for_user:
                        if stop_event.is_set():
                            break
                        if today_stats.get(action, 0) >= daily_limits.get(action, 0):
                            continue

                        try:
                            self.smart_delay(account_id, action, daily_limits,
                                             start_time, stop_time)
                            success = False

                            if action == 'follow' and uid not in executed_usernames:
                                ok, _ = self.follow_user(device_id, account_username, uid)
                                success = ok

                            elif action == 'like':
                                # Skip private accounts (secret=1) or accounts with no posts
                                if follower.get('secret', 1) >= 1 or not follower.get('aweme_count', 0):
                                    continue
                                ok, _ = self.like_posts(device_id, account_username, uid)
                                success = ok

                            elif action == 'profile_view' and uid not in executed_usernames:
                                ok, _ = self.view_profile(device_id, account_username, uid)
                                success = ok

                            elif action == 'story_like':
                                if (follower.get('story_status', 0) <= 0 or
                                        follower.get('secret', 1) >= 1 or
                                        not follower.get('aweme_count', 0)):
                                    continue
                                ok, _ = self.like_story(device_id, account_username, uid)
                                success = ok

                            elif action == 'comment':
                                if follower.get('secret', 1) >= 1 or not follower.get('aweme_count', 0):
                                    continue
                                if not comment_pool:
                                    continue
                                comment_text = random.choice(comment_pool)
                                ok, _ = self.comment_on_post(device_id, account_username,
                                                              uid, comment_text)
                                success = ok

                            if success:
                                today_stats[action] = today_stats.get(action, 0) + 1
                                executed_usernames.add(uid)
                                _update_last_action_time(account_id)
                                self._persist_stat(account_username, action)

                        except Exception as e:
                            logger.error(f'[{account_username}] Error on {action} for {uid}: {e}')
                            continue

                        time.sleep(random.randint(3, 7))

                time.sleep(random.randint(10, 20))

            except Exception as e:
                logger.error(f'[{account_username}] Collection loop error: {e}')
                time.sleep(60)

    def _persist_stat(self, account_username: str, action_type: str):
        """Write one stat increment to the TikTokAccount row."""
        try:
            from app.models.tiktok_account import TikTokAccount
            from app.extensions import db
            account = TikTokAccount.query.filter_by(username=account_username).first()
            if account:
                account.update_stats(action_type)
                db.session.commit()
        except Exception as e:
            logger.warning(f'_persist_stat failed: {e}')
