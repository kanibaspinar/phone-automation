import uiautomator2 as u2
import time
import os
import subprocess
import re
import imaplib
import email
from datetime import datetime
from PIL import Image
import pytesseract
from app import db
from app.models.instagram_account import InstagramAccount

class InstagramAutomation:
    def __init__(self):
        self.package_name = "com.instagram.android"
        self.activity = "com.instagram.mainactivity.MainActivity"
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

    def _get_device(self, device_id):
        """Connect to device using uiautomator2"""
        try:
            return u2.connect(device_id)
        except Exception as e:
            print(f"Error connecting to device {device_id}: {str(e)}")
            return None

    def _take_screenshot(self, device_id, output_path='screenshot.png'):
        """Take a screenshot of the device"""
        subprocess.run(['adb', '-s', device_id, 'exec-out', 'screencap', '-p'], stdout=open(output_path, 'wb'))
        return output_path

    def _detect_text_with_tesseract(self, d, target_text):
        """Detect text on screen using Tesseract OCR"""
        screenshot_path = self._take_screenshot(d.serial)
        try:
            img = Image.open(screenshot_path)
            text = pytesseract.image_to_string(img)
            return target_text in text
        except Exception as e:
            print(f"Error in detect_text_with_tesseract: {e}")
            return False

    def _click_text_with_tesseract(self, d, target_text):
        """Click text on screen using Tesseract OCR"""
        screenshot_path = self._take_screenshot(d.serial)
        try:
            img = Image.open(screenshot_path)
            boxes = pytesseract.image_to_boxes(img)
            width, height = img.size
            for b in boxes.splitlines():
                b = b.split(' ')
                text, x1, y1, x2, y2 = b[0], int(b[1]), int(b[2]), int(b[3]), int(b[4])
                if text in target_text:
                    x_center = (x1 + x2) // 2
                    y_center = height - (y1 + y2) // 2
                    d.click(x_center, y_center)
                    return True
            return False
        except Exception as e:
            print(f"Error in click_text_with_tesseract: {e}")
            return False

    def _extract_verification_code(self, text):
        """Extract 6-digit verification code from text"""
        code_pattern = r"\b\d{6}\b"
        match = re.search(code_pattern, text)
        return match.group() if match else None

    def _check_email_for_code(self, email_address, email_password, imap_server=None):
        """Check email for verification code"""
        try:
            if not imap_server:
                if "gmx.net" in email_address or "gmx.com" in email_address:
                    imap_server = "imap.gmx.net"
                elif "hotmail.com" in email_address or "outlook.com" in email_address:
                    imap_server = "imap-mail.outlook.com"
                elif "gmail.com" in email_address:
                    imap_server = "imap.gmail.com"
                else:
                    imap_server = "imap.firstmail.ltd"

            mail = imaplib.IMAP4_SSL(imap_server)
            mail.login(email_address, email_password)
            mail.select("inbox")

            _, email_ids = mail.search(None, "ALL")
            latest_email_id = email_ids[0].split()[-1]
            _, email_data = mail.fetch(latest_email_id, "(RFC822)")
            email_body = email.message_from_bytes(email_data[0][1])
            
            code = self._extract_verification_code(email_body.get_payload(decode=True).decode("utf-8"))
            mail.logout()
            return code
        except Exception as e:
            print(f"Error checking email: {e}")
            return None

    def _handle_security_verification(self, d, device_id, email_address, email_password):
        """Handle Instagram security verification"""
        try:
            if d(text="Enter Confirmation Code").exists or self._detect_text_with_tesseract(d, "Enter Confirmation Code"):
                code = self._check_email_for_code(email_address, email_password)
                if code:
                    d(className="android.widget.EditText").set_text(code)
                    d(text="Next").click()
                    time.sleep(5)
                    return True
            return False
        except Exception as e:
            print(f"Error handling security verification: {e}")
            return False

    def _clear_instagram_data(self, device_id):
        """Clear Instagram app data"""
        try:
            subprocess.run(['adb', '-s', device_id, 'shell', 'pm', 'clear', self.package_name], check=True)
            time.sleep(2)
            return True
        except Exception as e:
            print(f"Error clearing Instagram data: {e}")
            return False

    def login(self, device_id, username, password, email=None, email_password=None):
        """Login to Instagram account on device"""
        d = u2.connect(device_id)
        d.set_fastinput_ime(True)
        try:
            # Close and clear Instagram data
            d.app_start(self.package_name)
            time.sleep(9)  # Give some time for the app to open completely

            # Check if "Username, email or mobile number" is present
            username_field_xpath = '//*[@text="Username, email or mobile number"]'
            check_email_xpath = '//*[@text="Check your email"]'
            enter_code_xpath = '//*[@text="Enter code"]'
            if d.xpath(username_field_xpath).exists:
                d.xpath(username_field_xpath).click()
                d.xpath(username_field_xpath).set_text(username)
                time.sleep(1)

                # Enter password
                password_field_xpath = '//*[@text="Password"]'
                if d.xpath(password_field_xpath).exists:
                    d.xpath(password_field_xpath).click()
                    d.xpath(password_field_xpath).set_text(password)
                else:
                    self._click_text_with_tesseract(d, "Password")
                    d.set_text(password)

                # Click "Log in" button
                login_button_xpath = '//*[@text="Log in"]'
                if d.xpath(login_button_xpath).exists:
                    d.xpath(login_button_xpath).click()
                else:
                    self._click_text_with_tesseract(d, "Log in")
                time.sleep(20)

                # Handle security verification
                if email and email_password:
                    self._handle_security_verification(d, device_id, email, email_password)
                time.sleep(7)

                # Handle "Check your notifications on another device"
                check_notifications_xpath = '//*[@text="Check your notifications on another device"]'
                if d.xpath(check_notifications_xpath).exists:
                    print("Waiting for 60 seconds for notifications check...")
                    time.sleep(60)

                    if d.xpath(check_notifications_xpath).exists:
                        print("Still detecting the notification check message after 60 seconds")
                        account = InstagramAccount.query.filter_by(username=username).first()
                        if account:
                            account.login_status = False
                            db.session.commit()
                        return False, "Login failed - Notification check timeout"

                # Handle email verification
                if d.xpath(check_email_xpath).exists:
                    print("Instagram asks to check email for a verification code.")
                    time.sleep(20)
                    verification_code = self._check_email_for_code(email, email_password)
                    if verification_code:
                        print(f"Verification code received: {verification_code}")
                        if d.xpath(enter_code_xpath).exists:
                            d.xpath(enter_code_xpath).click()
                            d.xpath(enter_code_xpath).set_text(verification_code)
                            time.sleep(3)

                            # Submit the code
                            continue_button_xpath = '//*[@text="Continue"]'
                            if d.xpath(continue_button_xpath).exists:
                                d.xpath(continue_button_xpath).click()
                                print("Clicked the 'Continue' button.")
                                time.sleep(15)
                            else:
                                print("Failed to find the 'Continue' button.")
                                return False, "Login failed - Continue button not found"

                # Check for login rejection
                rejected_xpath = '//*[@text="rejected"]'
                if d.xpath(rejected_xpath).exists:
                    print("Login rejected")
                    account = InstagramAccount.query.filter_by(username=username).first()
                    if account:
                        account.login_status = False
                        db.session.commit()
                    return False, "Login rejected"

                # Handle "This was me" message
                if d(text="This was me").exists:
                    d(text="This was me").click()
                    time.sleep(2)

                # Handle post-login prompts
                if d(text="Save Your Login Info?").exists:
                    d(text="Not Now").click()
                if d(text="Turn on Notifications").exists:
                    d(text="Not Now").click()

                # Handle additional prompts
                allow_access_xpath = '//*[@text="Allow access to contacts to find people to follow"]'
                if d.xpath(allow_access_xpath).exists:
                    if d.xpath('//*[@text="Skip"]').exists:
                        d.xpath('//*[@text="Skip"]').click()
                    else:
                        self._click_text_with_tesseract(d, "Skip")
                time.sleep(2)

                location_services_xpath = '//*[@text="To use Location Services, allow Instagram to access your location"]'
                if d.xpath(location_services_xpath).exists:
                    if d.xpath('//*[@text="Continue"]').exists:
                        d.xpath('//*[@text="Continue"]').click()
                    else:
                        self._click_text_with_tesseract(d, "Continue")

                # Update account status
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.login_status = True
                    account.last_login = datetime.utcnow()
                    db.session.commit()
                return True, "Login successful"

            else:
                # Handle existing account switching
                chevron_selector = 'com.instagram.android:id/action_bar_title_chevron'
                if d(resourceId=chevron_selector).exists:
                    d(resourceId=chevron_selector).click()
                    time.sleep(2)
                else:
                    if d(description="Profile").exists:
                        d(description="Profile").click()
                        time.sleep(2)
                        if d(resourceId=chevron_selector).exists:
                            d(resourceId=chevron_selector).click()
                            time.sleep(2)
                        else:
                            print("Cannot find account switcher chevron")
                            return False, "Cannot find account switcher"

                # Handle "Add Instagram account"
                if d.xpath('//*[@text="Add Instagram account"]').exists:
                    d.xpath('//*[@text="Add Instagram account"]').click()
                    time.sleep(2)
                else:
                    d.swipe_ext("up", 0.8)
                    time.sleep(2)
                    d.swipe_ext("up", 0.8)
                    time.sleep(2)
                    if d.xpath('//*[@text="Add Instagram account"]').exists:
                        d.xpath('//*[@text="Add Instagram account"]').click()
                    else:
                        self._click_text_with_tesseract(d, "Add Instagram account")
                    time.sleep(2)

                # Handle "Log into existing account"
                if d.xpath('//*[@text="Log in to existing account"]').exists:
                    d.xpath('//*[@text="Log in to existing account"]').click()
                else:
                    self._click_text_with_tesseract(d, "Log in to existing account")
                time.sleep(2)

                # Recursively call login for the new account flow
                return self.login(device_id, username, password, email, email_password)

        except Exception as e:
            print(f"Error in login function: {e}")
            account = InstagramAccount.query.filter_by(username=username).first()
            if account:
                account.login_status = False
                db.session.commit()
            return False, str(e)

    def like_post_task(self, username, target_username, device_id):
        """Like a post from target user"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to profile
            self.click_explore(d)
            time.sleep(2)
            search_box = d(text="Search")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            edit_text.set_text(target_username)
            time.sleep(3)
            profile = d(text=target_username, resourceIdMatches="com.instagram.android:id/row_search_user_username")
            if not profile.exists:
                profile = d(resourceIdMatches="com.instagram.android:id/row_search_user_username")
                if profile.exists:
                    profile.click()
                    time.sleep(5)
                else:
                    print(f"Profile {target_username} not found in search results")
                    return False, "Profile not found"
            else:
                profile.click()
                time.sleep(5)

            # Click first post
            if self.click_post_button(d):
                time.sleep(3)
                if self.like_posts_and_reels(d, device_id):
                    return True, "Post liked successfully"
                else:
                    return False, "Failed to like post"
            else:
                return False, "Failed to click post"

        except Exception as e:
            return False, str(e)

    def comment_story_task(self, username, target_username, device_id, comment="👏"):
        """Comment on user's story"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            self.click_explore(d)
            time.sleep(2)
            search_box = d(text="Search")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            edit_text.set_text(target_username)
            time.sleep(3)

            # Look for story ring
            story_ring = d(resourceId="com.instagram.android:id/row_search_avatar_in_ring")
            if not story_ring.exists:
                print("No active story found")
                return False, "No active story found"

            story_ring.click()
            story_ringtwo = d(resourceId="com.instagram.android:id/reel_ring")
            if story_ringtwo.exists:
                story_ringtwo.click()
            time.sleep(5)

            # Add comment
            comment_button = d.xpath('//*[@resource-id="com.instagram.android:id/viewer_reel_item_toolbar_container"]/android.widget.ImageView[1]')
            if comment_button.exists:
                comment_button.click()
                time.sleep(1)
                comment_text_input = d(resourceId="com.instagram.android:id/story_comment_text")
                if comment_text_input.exists:
                    comment_text_input.click()
                    time.sleep(2)
                    comment_text_input.set_text(comment)
                    time.sleep(2)
                    d(text="Send").click()
                    time.sleep(3)
                    like_button = d(resourceId="com.instagram.android:id/toolbar_like_button")
                    if like_button.exists:
                        like_button.click()
                        time.sleep(2)
                    return True, "Comment posted successfully"
                else:
                    print("Comment text input not found")
                    return False, "Comment text input not found"
            else:
                print("Comment button not found")
                return False, "Comment button not found"

        except Exception as e:
            return False, str(e)

    def follow_user_task(self, username, target_username, device_id):
        """Follow a user"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to profile
            self.click_explore(d)
            time.sleep(2)
            search_box = d(text="Search")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            edit_text.set_text(target_username)
            time.sleep(3)
            profile = d(text=target_username, resourceIdMatches="com.instagram.android:id/row_search_user_username")
            if not profile.exists:
                print(f"Profile {target_username} not found in search results")
                return False, "Profile not found"
            profile.click()
            time.sleep(5)

            # Click follow button if available
            follow_button = d.xpath('//*[@text="Follow" or @text="Follow Back"]')
            if follow_button.exists:
                follow_button.click()
                time.sleep(2)
                
                # Handle "Follow Anyway" prompt if it appears
                follow_anyway = d.xpath('//*[@text="Follow Anyway"]')
                if follow_anyway.exists:
                    follow_anyway.click()
                    time.sleep(1)

                return True, "User followed successfully"

            return False, "Already following or user not found"

        except Exception as e:
            return False, str(e)

    def view_story_task(self, username, target_username, device_id):
        """View user's story"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            self.click_explore(d)
            time.sleep(2)
            search_box = d(text="Search")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            edit_text.set_text(target_username)
            time.sleep(3)

            # Look for story ring
            story_ring = d(resourceId="com.instagram.android:id/row_search_avatar_in_ring")
            if not story_ring.exists:
                print("No active story found")
                return False, "No active story found"

            story_ring.click()
            time.sleep(5)  # Wait longer to ensure story is viewed

            # Like the story if possible
            like_button = d(resourceId="com.instagram.android:id/toolbar_like_button")
            if like_button.exists:
                like_button.click()
                time.sleep(1)

            return True, "Story viewed successfully"

        except Exception as e:
            return False, str(e)

    def click_explore(self, d):
        """Click explore tab"""
        d(description="Search and explore").click()

    def click_post_button(self, d):
        """Click post button on profile"""
        post_button = d(text="Posts") if d(text="Posts").exists else d(text="posts")
        if post_button.exists:
            post_button.click()
            time.sleep(3)
            return True
        else:
            print("Post button not found")
            return False

    def like_posts_and_reels(self, d, device_id):
        """Like posts and reels"""
        retries = 0
        max_retries = 1
        max_likes = 1
        liked_count = 0

        while retries < max_retries and liked_count < max_likes:
            items = d.xpath('//*[contains(@content-desc, "Photo by") or contains(@content-desc, "Reel by")]').all()
            if not items:
                d.swipe_ext("up", 0.8)
                time.sleep(2)
                retries += 1
            else:
                for item in items:
                    if liked_count >= max_likes:
                        break
                    try:
                        item.click()
                        time.sleep(3)
                        width, height = d.window_size()
                        d.double_click(width/2, height/2)
                        time.sleep(1)
                        liked_count += 1
                        self.back_with_failover(d, device_id)
                        time.sleep(3)
                    except Exception as e:
                        print(f"Failed to click item: {e}")

                if liked_count >= max_likes:
                    break

                retries = 0

        return liked_count >= max_likes

    def back_with_failover(self, d, device_id):
        """Press back button with failover to ADB"""
        try:
            d.press("back")
            time.sleep(1)
        except Exception as e:
            os.system(f"adb -s {device_id} shell input keyevent 4")
            time.sleep(1)

    def switch_account(self, device_id, target_username):
        """Switch to another Instagram account"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Click profile tab
            d(description="Profile").click()
            time.sleep(2)

            # Click account switcher
            chevron = d(resourceId="com.instagram.android:id/action_bar_title_chevron")
            if chevron.exists:
                chevron.click()
                time.sleep(2)

                # Look for target account
                account = d(text=target_username)
                if account.exists:
                    account.click()
                    time.sleep(5)
                    return True, "Account switched successfully"
                
                # Try scrolling if account not found
                d.swipe_ext("up", 0.8)
                time.sleep(1)
                
                account = d(text=target_username)
                if account.exists:
                    account.click()
                    time.sleep(5)
                    return True, "Account switched successfully"

            return False, "Could not switch account"

        except Exception as e:
            return False, str(e) 