import os
import time
import json
import logging
from app.extensions import db
from app.models.instagram_account import InstagramAccount
from datetime import datetime
import uiautomator2 as u2
import subprocess
import re
import imaplib
import email
from PIL import Image
import pytesseract
import emoji  # Add this import at the top

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class InstagramAutomation:
    def __init__(self, device_manager):
        """Initialize Instagram automation with device manager"""
        if not device_manager:
            raise ValueError("DeviceManager is required for Instagram automation")
        self.device_manager = device_manager
        self.package_name = "com.instagram.android"
        self.activity = "com.instagram.mainactivity.MainActivity"
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
        logger.info("Instagram automation initialized with device manager")

    def _get_device(self, device_id):
        """Connect to device using uiautomator2"""
        try:
            return u2.connect(device_id)
        except Exception as e:
            print(f"Error connecting to device {device_id}: {str(e)}")
            return None

    def _take_screenshot(self, device_id, output_path='screenshot.png'):
        """Take a screenshot of the device"""
        subprocess.run([self.device_manager.adb_path, '-s', device_id, 'exec-out', 'screencap', '-p'], stdout=open(output_path, 'wb'))
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
                elif "rambler.ru" in email_address:
                    imap_server = "imap.rambler.ru"
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

    def _handle_security_verification(self, d, device_id, email_address, email_password, max_retries=3):
        """Handle Instagram security verification with retry and code resend"""
        try:
            for attempt in range(max_retries):
                if (
                    d(text="Enter confirmation code").exists or
                    self._detect_text_with_tesseract(d, "Enter Confirmation Code") or
                    d(text="Enter Confirmation Code").exists or
                    d(text="Enter the 6-digit code we sent to").exists or
                    d(text="Check your email").exists
                ):
                    code = self._check_email_for_code(email_address, email_password)
                    time.sleep(7)
    
                    if code:
                        if d(className="android.widget.EditText").exists:
                            d(className="android.widget.EditText").set_text(code)
    
                        for btn_text in ["Confirm", "Continue", "Next"]:
                            if d(text=btn_text).exists:
                                d(text=btn_text).click()
                                return True
                        time.sleep(15)
                    else:
                        print(f"[Attempt {attempt+1}] Code not received. Trying to request new code.")
                        if d(text="Get a new code").exists:
                            d(text="Get a new code").click()
                            time.sleep(5)  # Allow UI to process and code to resend
    
            print("Failed to verify after retrying.")
            return False
        except Exception as e:
            print(f"Error handling security verification: {e}")
            return False



    def _clear_instagram_data(self, device_id):
        """Clear Instagram app data"""
        try:
            subprocess.run([self.device_manager.adb_path, '-s', device_id, 'shell', 'pm', 'clear', self.package_name], check=True)
            time.sleep(2)
            return True
        except Exception as e:
            print(f"Error clearing Instagram data: {e}")
            return False

    def check_current_account(self, username, d, device_id):
        """Check if the current account is the target account"""
        try:
            profile_button = d(description="Profile")
            if profile_button.exists:
                profile_button.click()
                time.sleep(9)
            else:
                logger.info("Profile button not found")
                return False
            
            username_on_profile = d(text=username)
            return username_on_profile.exists
        except Exception as e:
            logger.error(f"Error checking current account: {str(e)}")
            return False

    def switch_account_if_needed(self, username, device_id, d):
        """Switch to target account if not currently active"""
        try:
            if not self.check_current_account(username, d, device_id):
                self.ensure_instagram_open(device_id)
                success, message = self.switch_account(device_id, username)
                if success:
                    account = InstagramAccount.query.filter_by(username=username, device_id=device_id).first()
                    if account:
                        account.login_status = True
                        account.last_login = datetime.utcnow()
                        db.session.commit()
                return success, message
            else:
                account = InstagramAccount.query.filter_by(username=username, device_id=device_id).first()
                if account:
                    account.login_status = True
                    account.last_login = datetime.utcnow()
                    db.session.commit()
                return True, "Already logged in to correct account"
        except Exception as e:
            logger.error(f"Error in switch_account_if_needed: {str(e)}")
            return False, str(e)

    def ensure_instagram_open(self, device_id):
        """Ensure Instagram is open"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Check if Instagram is open
            if d.app_current() != self.package_name:
                d.app_start(self.package_name)
                time.sleep(9)
            d.swipe_ext("up", 0.8)
            time.sleep(2)
            d.swipe_ext("up", 0.8)
            time.sleep(2)

            return True, "Instagram is open"
        except Exception as e:
            logger.error(f"Error ensuring Instagram is open: {str(e)}")
            return False, str(e)

    def ensure_correct_account(self, username, d, device_id):
        """Check if account exists and is logged in on device"""
        try:
            # Click profile tab
            profile_button = d(description="Profile")
            if profile_button.exists:
                profile_button.click()
                time.sleep(9)
            else:
                return False

            # Check if we're on the correct account
            username_on_profile = d(text=username)
            account = InstagramAccount.query.filter_by(username=username).first()
            if username_on_profile.exists:
                # Update database status
                
                if not account:
                    # Create new account entry if it doesn't exist
                    account = InstagramAccount(
                        username=username,
                        device_id=device_id,
                        login_status=True,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    db.session.add(account)
                else:
                    # Update existing account
                    account.device_id = device_id
                    account.login_status = True
                    account.last_login = datetime.utcnow()
                    account.updated_at = datetime.utcnow()
                
                db.session.commit()
                return True

            return False
        except Exception as e:
            logger.error(f"Error in ensure_correct_account: {str(e)}")
            return False

    def _add_or_update_account(self, username, password, device_id, login_status, email=None, email_password=None):
        """Add or update account in database with login status"""
        try:
            account = InstagramAccount.query.filter_by(username=username).first()
            if not account:
                # Create new account
                account = InstagramAccount(
                    username=username,
                    password=password,
                    device_id=device_id,
                    login_status=login_status,
                    email=email,
                    email_password=email_password,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                if login_status:
                    account.last_login = datetime.utcnow()
                db.session.add(account)
            else:
                # Update existing account
                account.device_id = device_id
                account.login_status = login_status
                account.email = email
                account.password = password
                account.email_password = email_password
                account.updated_at = datetime.utcnow()
                if login_status:
                    account.last_login = datetime.utcnow()
                else:
                    account.last_logout = datetime.utcnow()

            db.session.commit()
            return account
        except Exception as e:
            logger.error(f"Error adding/updating account in database: {str(e)}")
            return None

    def set_text_with_failover(self, d, xpath, text, device_id):
        """Set text with multiple fallback methods
        Args:
            d: Device object
            xpath: XPath selector
            text: Text to input
            device_id: Device ID for ADB fallback
        """
        try:
            # Method 1: Try direct setText
            element = d.xpath(xpath)
            if element.exists:
                try:
                    element.set_text(text)
                    time.sleep(1)
                    return True
                except Exception as e:
                    logger.info(f"Direct setText failed: {str(e)}, trying alternative methods")

            # Method 2: Try click + ADB input
            try:
                element.click()
                time.sleep(1)
                self.adb_input_text(device_id, text)
                time.sleep(1)
                return True
            except Exception as e:
                logger.info(f"Click + ADB input failed: {str(e)}, trying next method")

            # Method 3: Try send_keys
            try:
                d.send_keys(text)
                time.sleep(1)
                return True
            except Exception as e:
                logger.info(f"send_keys failed: {str(e)}")
            return True

        except Exception as e:
            logger.error(f"All text input methods failed: {str(e)}")
            return False

    def adb_input_text(self, device_id, text):
        """Input text using ADB
        Args:
            device_id: Device ID
            text: Text to input
        """
        try:
            # Clear existing text first
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '123'], check=True)  # Move to end
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '29,29,29,29,29'], check=True)  # Select all
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '67'], check=True)  # Backspace

            # Input new text
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'text', text.replace(' ', '%s')], check=True)
            time.sleep(1)
            return True
        except Exception as e:
            logger.error(f"ADB text input failed: {str(e)}")
            return False

    def login(self, device_id, username, password, email=None, email_password=None, recurs=False):
        """Login to Instagram account"""
        try:
            d = self._get_device(device_id)
            if not d:
                self._add_or_update_account(username, password, device_id, False, email, email_password)
                return False, "Failed to connect to device"

            d.set_fastinput_ime(True)
            
            # Ensure Instagram is open
            self.ensure_instagram_open(device_id)
            time.sleep(12)

            # Check if account already exists and is logged in
            account_exists = self.ensure_correct_account(username, d, device_id)
            if account_exists:
                self._add_or_update_account(username, password, device_id, True, email, email_password)
                return True, "Already logged in to correct account"
                
            add_another = '//*[@text="Use another profile"]'
            if d.xpath(add_another).exists:
                d.xpath(add_another).click()
                time.sleep(2)
            if recurs:
                d.swipe_ext("up", 0.8)
                time.sleep(2)
                d.swipe_ext("up", 0.8)
                time.sleep(2)
                if d.xpath(add_another).exists:
                    d.xpath(add_another).click()
                    time.sleep(2)

            # Check if login fields exist
            username_field_xpath = '//*[@text="Username, email or mobile number"]'
            username_field_xpath2 = '//*[@text="Phone number, email or username"]'
            if d.xpath(username_field_xpath).exists:   
               d.xpath(username_field_xpath).click()
               time.sleep(3)
               if d(description="Clear Username, email or mobile number text").exists:
                    d(description="Clear Username, email or mobile number text").click()
                    time.sleep(2)
            if d.xpath(username_field_xpath2).exists:   
               username_field_xpath = username_field_xpath2
               d.xpath(username_field_xpath).click()
               time.sleep(3)
               if d(description="Clear Username, email or mobile number text").exists:
                    d(description="Clear Username, email or mobile number text").click()
                    time.sleep(2)            
            
            if d.xpath(username_field_xpath).exists:
                # Clear Instagram data and proceed with fresh login
                
                # Fill login form
                self.set_text_with_failover(d, username_field_xpath, username, device_id)
                
                password_field = d.xpath('//*[@text="Password"]')
                password_fields2 = 'com.instagram.android:id/password'
                password_field2 = d(resourceId=password_fields2)
                if password_field2.exists:
                    password_field2.set_text(password)
                if password_field.exists:
                   self.set_text_with_failover(d, password_field, password, device_id)

                # Click login button
                login_button = d.xpath('//*[@text="Log in"]')
                if not login_button.exists:
                    self._add_or_update_account(username, password, device_id, False, email, email_password)
                    return False, "Login button not found"
                login_button.click()
                time.sleep(15)

                # Handle security verification if needed
                if email and email_password or d(text="Confirm it's you").exists or d(text="Check your email").exists or d(text="Enter confirmation code").exists:
                    if d(text="Email").exists:
                       d(text="Email").click()
                    time.sleep(3)
                    if d(text="Continue").exists:
                        d(text="Continue").click()
                    time.sleep(7)
                    if not self._handle_security_verification(d, device_id, email, email_password):
                        self._add_or_update_account(username, password, device_id, False, email, email_password)
                        return False, "Security verification failed"

                # Check for login rejection
                rejected_xpath = '//*[@text="rejected"]'
                if d.xpath(rejected_xpath).exists:
                    logger.info("Login rejected")
                    self._add_or_update_account(username, password, device_id, False, email, email_password)
                    return False, "Login rejected"

                # Handle post-login prompts
                time.sleep(7)
                self._handle_post_login_prompts(d)

                # Update account status as logged in
                self._add_or_update_account(username, password, device_id, True, email, email_password)
                return True, "Login successful"
            else:
                # First try switching account if already logged in
                if d(description="Profile").exists:
                        d(description="Profile").click()
                        time.sleep(7)
                else:
                    logger.info("Cannot find account profile button")
                    self._add_or_update_account(username, password, device_id, False, email, email_password)
                    return False, "Cannot find account profile button 2"
                # If switching failed, try adding a new account
                # Handle existing account switching
                chevron_selector = 'com.instagram.android:id/action_bar_title_chevron'
                if d(resourceId=chevron_selector).exists:
                    d(resourceId=chevron_selector).click()
                    time.sleep(2)
                else:
                    if d(description="Profile").exists:
                        d(description="Profile").click()
                        time.sleep(7)
                        if d(resourceId=chevron_selector).exists:
                            d(resourceId=chevron_selector).click()
                            time.sleep(5)
                        else:
                            logger.info("Cannot find account switcher chevron")
                            self._add_or_update_account(username, password, device_id, False, email, email_password)
                            return False, "Cannot find account switcher"     
                time.sleep(2)
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
                if d.xpath('//*[@text="Add account"]').exists:
                    d.xpath('//*[@text="Add account"]').click()
                    time.sleep(2)
                # Handle "Log into existing account"
                login_elem = d.xpath('//*[@text="Log into existing account" or @content-desc="Log into existing account"]')
                if login_elem.exists:
                    login_elem.click()
                else:
                    self._click_text_with_tesseract(d, "Log into existing account")
                time.sleep(7)
                if d.xpath('//*[@text="Switch Accounts"]').exists:
                    d.xpath('//*[@text="Switch Accounts"]').click()
                time.sleep(7)
                # Recursively call login for the new account flow
                return self.login(device_id, username, password, email, email_password, True)

        except Exception as e:
            logger.error(f"Error in login: {str(e)}")
            self._add_or_update_account(username, password, device_id, False, email, email_password)
            return False, str(e)

    def _handle_post_login_prompts(self, d):
        """Handle various prompts that appear after login"""
        try:
            # Handle "This was me" message
            if d(text="This was me").exists:
                d(text="This was me").click()
                time.sleep(9)

            # Handle post-login prompts
            if d(text="Save your login info?").exists:
                d(text="Not now").click()
                time.sleep(9)
            if d(text="Turn on notifications").exists:
                d(text="Not now").click()
                time.sleep(9)

            # Handle additional prompts
            allow_access_xpath = '//*[@text="Allow access to contacts to find people to follow"]'
            if d.xpath(allow_access_xpath).exists:
                if d.xpath('//*[@text="Skip"]').exists:
                    d.xpath('//*[@text="Skip"]').click()
                    time.sleep(9)
                else:
                    self._click_text_with_tesseract(d, "Skip")
                    time.sleep(7)
            time.sleep(7)

            location_services_xpath = '//*[@text="To use Location Services, allow Instagram to access your location"]'
            if d.xpath(location_services_xpath).exists:
                if d.xpath('//*[@text="Continue"]').exists:
                    d.xpath('//*[@text="Continue"]').click()
                    time.sleep(2)
                    if d.xpath('//*[@text="ALLOW"]').exists:
                       d.xpath('//*[@text="ALLOW"]').click()
                       time.sleep(7)
                else:
                    self._click_text_with_tesseract(d, "Continue")
                    time.sleep(7)
        except Exception as e:
            logger.error(f"Error handling post-login prompts: {str(e)}")

    def logout(self, device_id):
        """Logout from Instagram account"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Clear app data to force logout
            if self._clear_instagram_data(device_id):
                return True, "Logged out successfully"
            return False, "Failed to clear app data"

        except Exception as e:
            return False, str(e)

    def like_post(self, device_id, username, target_username):
        """Like a post from target user"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to profile
            self.click_explore(d)
            time.sleep(7)
            if d(text="Search").exists:
               search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
               search_box = d(text="Ask Meta AI or Search")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            self.set_text_with_failover(d, "com.instagram.android:id/action_bar_search_edit_text", target_username, device_id)
            time.sleep(5)
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
            time.sleep(5)
            if self.click_post_button(d):
                time.sleep(3)
                if self.like_posts_and_reels(d, device_id):
                    # Update account statistics
                    account = InstagramAccount.query.filter_by(username=username).first()
                    if account:
                        account.update_stats('like')
                        db.session.commit()
                    return True, "Post liked successfully"
                else:
                    return False, "Failed to like post"
            else:
                return False, "Failed to click post"

        except Exception as e:
            return False, str(e)
    def comment_story(self, device_id, username, target_username, comment="👏"):
        """Comment on user's story"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            self.click_explore(d)
            time.sleep(2)
            if d(text="Search").exists:
               search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
               search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            self.set_text_with_failover(d, "com.instagram.android:id/action_bar_search_edit_text", target_username, device_id)
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
                
                # Decode Unicode text if needed
                decoded_comment = self._decode_unicode_text(comment)
                
                # Try to set text with failover mechanism
                comment_input_xpath = '//*[@resource-id="com.instagram.android:id/layout_comment_thread_edittext"]'
                if not self.set_text_with_failover(d, comment_input_xpath, decoded_comment, device_id):
                    return False, "Failed to input comment text"
                
                time.sleep(2)
                
                # Try to click post button
                try:
                    if d(resourceId="com.instagram.android:id/layout_comment_thread_post_button_icon").exists:
                        d(resourceId="com.instagram.android:id/layout_comment_thread_post_button_icon").click()
                    elif d(description="Post").exists:
                        d(description="Post").click()
                    else:
                        logger.error("Post button not found")
                        return False, "Post button not found"
                except Exception as e:
                    logger.error(f"Error clicking post button: {e}")
                    return False, f"Error clicking post button: {e}"

                time.sleep(3)
                
                # Update account statistics (counts as view_story)
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('view_story')
                    db.session.commit()
                return True, "Comment posted successfully"
            else:
                print("Comment button not found")
                return False, "Comment button not found"

        except Exception as e:
            return False, str(e)
    
    def follow_user(self, device_id, username, target_username):
        """Follow a user"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to profile
            self.click_explore(d)
            time.sleep(2)
            if d(text="Search").exists:
               search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
               search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            self.set_text_with_failover(d, "com.instagram.android:id/action_bar_search_edit_text", target_username, device_id)
            time.sleep(3)
            profile = d(text=target_username, resourceIdMatches="com.instagram.android:id/row_search_user_username")
            if not profile.exists:
                print(f"Profile {target_username} not found in search results")
                return False, "Profile not found"
            profile.click()
            time.sleep(5)

            # Click follow button if available
            follow_button = d.xpath('//*[@text="Follow" or @text="Follow back" or @text="Follow Back"]')
            if follow_button.exists:
                follow_button.click()
                time.sleep(2)
                
                # Handle "Follow Anyway" prompt if it appears
                follow_anyway = d.xpath('//*[@text="Follow Anyway"]')
                if follow_anyway.exists:
                    follow_anyway.click()
                    time.sleep(1)

                # Update account statistics
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('follow')
                    db.session.commit()
                return True, "User followed successfully"

            return False, "Already following or user not found"

        except Exception as e:
            return False, str(e)

    def view_story(self, device_id, username, target_username):
        """View user's story"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            self.click_explore(d)
            time.sleep(3)
            if d(text="Search").exists:
               search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
               search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                print("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(5)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                print("Search input not found")
                return False, "Search input not found"
            self.set_text_with_failover(d, "com.instagram.android:id/action_bar_search_edit_text", target_username, device_id)
            time.sleep(5)

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
                time.sleep(3)

            # Update account statistics
            account = InstagramAccount.query.filter_by(username=username).first()
            if account:
                account.update_stats('view_story')
                db.session.commit()

            return True, "Story viewed successfully"

        except Exception as e:
            return False, str(e)

    def click_explore(self, d):
        """Click explore tab"""
        time.sleep(3)
        d(description="Search and explore").click()

    def click_post_button(self, d):
        """Click post button on profile"""
        post_button = d.xpath('//*[@resource-id="com.instagram.android:id/row_profile_header_textview_post_count"]')
        if post_button.exists:
            post_button.click()
            time.sleep(5)
            return True
        else:
            # Scroll down to find post carousel
            d.swipe_ext("up", 0.8)
            time.sleep(3)
            return True

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
                time.sleep(5)
                retries += 1
            else:
                for item in items:
                    if liked_count >= max_likes:
                        break
                    try:
                        item.click()
                        time.sleep(5)
                        width, height = d.window_size()
                        d.double_click(width/2, height/2)
                        d.double_click(width/2, height/2)
                        time.sleep(1)
                        liked_count += 1
                        self.back_with_failover(d, device_id)
                        time.sleep(5)
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
            time.sleep(2)
        except Exception as e:
            subprocess.run([self.device_manager.adb_path, '-s', device_id, 'shell', 'input', 'keyevent', '4'], 
                         check=True, 
                         stdout=subprocess.PIPE, 
                         stderr=subprocess.PIPE)
            time.sleep(3)

    def switch_account(self, device_id, target_username):
        """Switch to another Instagram account"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Click profile tab
            time.sleep(3)
            d(description="Profile").click()
            time.sleep(7)

            # Click account switcher
            chevron = d(resourceId="com.instagram.android:id/action_bar_title_chevron")
            if chevron.exists:
                chevron.click()
                time.sleep(3)

                # Look for target account
                account = d(text=target_username)
                if account.exists:
                    account.click()
                    time.sleep(5)
                    return True, "Account switched successfully"
                
                # Try scrolling if account not found
                d.swipe_ext("up", 0.8)
                time.sleep(2)
                
                account = d(text=target_username)
                if account.exists:
                    account.click()
                    time.sleep(5)
                    return True, "Account switched successfully"

            return False, "Could not switch account"

        except Exception as e:
            return False, str(e)

    def close_instagram(self, device_id):
        """Close Instagram app properly"""
        try:
            logger.info(f"Closing Instagram app on device {device_id}")
            subprocess.run([self.device_manager.adb_path, '-s', device_id, 'shell', 'am', 'force-stop', 'com.instagram.android'], 
                         check=True, 
                         stdout=subprocess.PIPE, 
                         stderr=subprocess.PIPE)
            time.sleep(2)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error closing Instagram app: {e.stderr.decode('utf-8')}")
            return False
        except Exception as e:
            logger.error(f"Error closing Instagram app: {str(e)}")
            return False

    def like_story(self, device_id, username, target_username):
        """Like a user's story"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            time.sleep(7)
            self.click_explore(d)
            time.sleep(7)
            if d(text="Search").exists:
               search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
               search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                logger.error("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(1)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                logger.error("Search input not found")
                return False, "Search input not found"
            self.set_text_with_failover(d, "com.instagram.android:id/action_bar_search_edit_text", target_username, device_id)
            time.sleep(6)

            # Look for story ring and click it
            story_ring = d(resourceId="com.instagram.android:id/row_search_avatar_in_ring")
            if story_ring.exists:
                story_ring.click()
                story_ringtwo = d(resourceId="com.instagram.android:id/reel_ring")
                if story_ringtwo.exists:
                    story_ringtwo.click()
                time.sleep(7)
                
                # Like the story
                like_button = d(resourceId="com.instagram.android:id/toolbar_like_button")
                if like_button.exists:
                    like_button.click()
                    time.sleep(1)
                
                # Go back
                self.back_with_failover(d, device_id)
                
                # Update account statistics
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('like_story')
                    db.session.commit()
                
                return True, "Story liked successfully"
            else:
                logger.info(f"No active story found for user {target_username}")
                return False, "No active story found"

        except Exception as e:
            logger.error(f"Error in like_story: {str(e)}")
            return False, str(e)

    def unfollow_user(self, device_id, username, target_username):
        """Unfollow a user"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to profile
            time.sleep(7)
            self.click_explore(d)
            time.sleep(7)
            if d(text="Search").exists:
               search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
               search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                logger.error("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(5)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                logger.error("Search input not found")
                return False, "Search input not found"
            self.set_text_with_failover(d, "com.instagram.android:id/action_bar_search_edit_text", target_username, device_id)
            time.sleep(3)
            profile = d(text=target_username, resourceIdMatches="com.instagram.android:id/row_search_user_username")
            if not profile.exists:
                logger.error(f"Profile {target_username} not found in search results")
                return False, "Profile not found"
            profile.click()
            time.sleep(7)

            # Click unfollow button if available
            unfollow_button = d.xpath('//*[@resource-id="com.instagram.android:id/profile_header_follow_button"]')
            if unfollow_button.exists:
                unfollow_button.click()
                time.sleep(3)
                
                # Handle "Unfollow" confirmation dialog
                unfollow_confirm = d.xpath('//*[@text="Unfollow"]')
                if unfollow_confirm.exists:
                    unfollow_confirm.click()
                    time.sleep(3)

                # Update account statistics
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('unfollow')
                    db.session.commit()
                return True, "User unfollowed successfully"

            return False, "Not following or user not found"

        except Exception as e:
            logger.error(f"Error in unfollow_user: {str(e)}")
            return False, str(e)
    def contains_unicode_escapes(self, text):
    # Regular expression to detect Unicode escape sequences (\u followed by 4 hex digits)
       return bool(re.search(r'\\u[0-9a-fA-F]{4}', text))
    def _decode_unicode_text(self, text):
        """Decode Unicode escape sequences and handle emojis only if needed"""
        if self.contains_unicode_escapes(text):
            try:
                decoded_text = json.loads(f'"{text}"')
                return decoded_text
            except json.JSONDecodeError as e:
                print(f"Error decoding Unicode text: {e}")
        return text
    def clean_invalid_json_chars(self, text):
    # Remove unescaped control characters
        return re.sub(r'[\x00-\x1F\x7F]', '', text)
    def _send_text_via_adb(self, device_id, text):
        escaped_text = text.replace(' ', '%s').replace('&', '\\&').replace('(', '\\(').replace(')', '\\)')
        command = f'adb -s {device_id} shell input text "{escaped_text}"'
        subprocess.run(command, shell=True)
    

    def dm_to_user(self, device_id, username, target_username, dm_message):
        """Send a DM to a user"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            time.sleep(7)
            self.click_explore(d)
            time.sleep(7)
            if d(text="Search").exists:
                search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
                search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                logger.error("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(3)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                logger.error("Search input not found")
                return False, "Search input not found"
            edittextid="com.instagram.android:id/action_bar_search_edit_text"
            self.set_text_with_failover(d, edittextid, target_username, device_id)
            time.sleep(3)
            profile = d(text=target_username, resourceIdMatches="com.instagram.android:id/row_search_user_username")
            if not profile.exists:
                logger.error(f"Profile {target_username} not found in search results")
                return False, "Profile not found"
            profile.click()
            time.sleep(9)

            # Click unfollow button if available
            dm_button = d(text="Message")
            if dm_button.exists:
                dm_button.click()
                time.sleep(7)
                
                dm_text_input = d.xpath('//*[@resource-id="com.instagram.android:id/row_thread_composer_edittext"]')
                if dm_text_input.exists:
                    dm_text_input.click()
                    time.sleep(3)
                    
                    # Decode Unicode text before sending
                    edit_text2 = d(className="android.widget.EditText")
                    
                    # Use the safe text setting method
                    edittextid="com.instagram.android:id/row_thread_composer_edittext"
                    self.set_text_with_failover(d, edittextid, dm_message, device_id)
                    
                    time.sleep(2)
                    
                    send_button = d(text="Send") or d(resourceId="com.instagram.android:id/row_thread_composer_send_button_icon")
                    if send_button.exists:
                        send_button.click()
                        time.sleep(3)
                    else:
                        return False, "Send button not found"

                # Update account statistics
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('dm')
                    db.session.commit()
                return True, "DM Sent to user successfully"
            else:
                d(description="Options").click()
                time.sleep(2)
                dm_button = d(text="Send message")
                if dm_button.exists:
                    dm_button.click()
                    time.sleep(7)
                
                    dm_text_input = d.xpath('//*[@resource-id="com.instagram.android:id/row_thread_composer_edittext"]')
                    if dm_text_input.exists:
                        dm_text_input.click()
                        time.sleep(3)
                    
                    # Decode Unicode text before sending
                        edit_text2 = d(className="android.widget.EditText")
                    
                    # Use the safe text setting method
                        edittextid="com.instagram.android:id/row_thread_composer_edittext"
                        self.set_text_with_failover(d, edittextid, dm_message, device_id)
                    
                        time.sleep(2)
                    
                        send_button = d(text="Send") or d(resourceId="com.instagram.android:id/row_thread_composer_send_button_icon")
                        if send_button.exists:
                           send_button.click()
                           time.sleep(3)
                    else:
                        return False, "Send button not found"

                # Update account statistics
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('dm')
                    db.session.commit()
                return True, "DM Sent to user successfully"


            return False, "Something went wrong"

        except Exception as e:
            logger.error(f"Error in dm_to_user: {str(e)}")
            return False, str(e)

    def comment_post(self, device_id, username, target_username, comment):
        """Comment on a post"""
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Navigate to search
            time.sleep(7)
            self.click_explore(d)
            time.sleep(7)
            if d(text="Search").exists:
                search_box = d(text="Search")
            elif d(text="Ask Meta AI or Search").exists:
                search_box = d(text="Ask Meta AI or Search")
            elif d(text="Search with Meta AI").exists:
               search_box = d(text="Search with Meta AI")
            if not search_box.exists:
                logger.error("Search box not found")
                return False, "Search box not found"
            search_box.click()
            time.sleep(3)
            edit_text = d(className="android.widget.EditText")
            if not edit_text.exists:
                logger.error("Search input not found")
                return False, "Search input not found"
            edittextid="com.instagram.android:id/action_bar_search_edit_text"
            self.set_text_with_failover(d, edittextid, target_username, device_id)
            time.sleep(3)
            profile = d(text=target_username, resourceIdMatches="com.instagram.android:id/row_search_user_username")
            if not profile.exists:
                logger.error(f"Profile {target_username} not found in search results")
                return False, "Profile not found"
            profile.click()
            time.sleep(9)
            if self.click_post_button(d):
                time.sleep(3)
            else:
                return False, "Posts not found"

            items = d.xpath('//*[contains(@content-desc, "Photo by") or contains(@content-desc, "Reel by")]').all()
            if not items:
                d.swipe_ext("up", 0.8)
                time.sleep(5)
            if items and len(items) > 0:
                items[0].click()
                time.sleep(3)
                comment_button = d.xpath('//*[@resource-id="com.instagram.android:id/row_feed_button_comment"]')
                if comment_button.exists:
                    comment_button.click()
                    time.sleep(7)
                    
                    comment_text_input = d.xpath('//*[@resource-id="com.instagram.android:id/layout_comment_thread_edittext"]')
                    if comment_text_input.exists:
                        comment_text_input.click()
                        time.sleep(3)
                        
                        edit_text = d(className="android.widget.EditText")
                        edittextid="com.instagram.android:id/layout_comment_thread_edittext"
                        self.set_text_with_failover(d, edittextid, comment, device_id)
                        
                        time.sleep(2)
                        
                        send_button = d.xpath('//*[@resource-id="com.instagram.android:id/layout_comment_thread_post_button" or @resource-id="com.instagram.android:id/layout_comment_thread_post_button_icon"]')
                        if send_button.exists:
                            send_button.click()
                            time.sleep(3)
                        else:
                            return False, "Send button not found"

                # Update account statistics
                account = InstagramAccount.query.filter_by(username=username).first()
                if account:
                    account.update_stats('comment')
                    db.session.commit()
                return True, "Comment posted successfully"

            return False, "Something went wrong"

        except Exception as e:
            logger.error(f"Error in comment_post: {str(e)}")
            return False, str(e)

    def post_reel(self, device_id, video_path, caption, music_query=None):
        """Post a reel on Instagram
        Args:
            device_id (str): Device ID
            video_path (str): Path to video file
            caption (str): Caption text
            music_query (str, optional): Music to search for. Defaults to None.
        """
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"

            # Push video file to device's DCIM/Camera directory
            try:
                if video_path.startswith(('http://', 'https://')):
                    try:
                       video_path = self._download_media_from_url(video_path, 'reel')
                    except Exception as e:
                       return False, f"Failed to download video from URL: {str(e)}"
                # Create DCIM/Camera directory if it doesn't exist
                subprocess.run([
                    'adb', '-s', device_id, 'shell',
                    'mkdir', '-p', '/storage/emulated/0/DCIM/Camera'
                ], check=True)

                # Get filename and device path
                filename = os.path.basename(video_path)
                device_path = f'/storage/emulated/0/DCIM/Camera/{filename}'

                # Push the file
                subprocess.run([
                    'adb', '-s', device_id, 'push',
                    video_path, device_path
                ], check=True)

                # Set proper file permissions
                subprocess.run([
                    'adb', '-s', device_id, 'shell',
                    'chmod', '644', device_path
                ], check=True)

                # Trigger media scanner
                subprocess.run([
                    'adb', '-s', device_id, 'shell',
                    'am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
                    '-d', f'file://{device_path}'
                ], check=True)

                # Wait for media scanner
                time.sleep(5)

            except Exception as e:
                return False, f"Failed to push video: {str(e)}"

            # Click create button
            create_button = d(resourceId="com.instagram.android:id/creation_tab")
            if not create_button.exists:
                return False, "Create button not found"
            create_button.click()
            time.sleep(3)

            # Handle permissions
            self._handle_permissions(d)

            # Select REEL
            reel_button = d(resourceId="com.instagram.android:id/cam_dest_clips")
            
            if not reel_button.exists:
                return False, "Reel button not found"
            reel_button.click()
            time.sleep(2)
            
            if d(text="Start new video").exists:
               d(text="Start new video").click()
               time.sleep(1)

            # Handle "Create longer reels" prompt
            if d(text="OK").exists:
                d(text="OK").click()
                time.sleep(1)


            # Get all gallery items and find the most recent one by checking descriptions
            gallery_items = d(resourceId="com.instagram.android:id/gallery_grid_item_thumbnail")
            if gallery_items.exists:
                # Click the first gallery item since we can't get all items
                gallery_items.click()
                time.sleep(3)
            else:
                return False, "No gallery items found"
            time.sleep(3)
            # Add music if specified
            if music_query:
                success = self._add_music_to_post(d, music_query)
                if not success:
                    return False, "Failed to add music"

            # Click Next
            time.sleep(7)
            if d(text="Next").exists:
                d(text="Next").click()
                time.sleep(9)
            else:
                return False, "Next button not found"
            time.sleep(6)
            # Handle "New ways to reuse" prompt
            if d(text="OK").exists:
                d(text="OK").click()
                time.sleep(1)

            # Add caption
            time.sleep(6)
            caption_input = d(resourceId="com.instagram.android:id/caption_input_text_view")
            if caption_input.exists:
                caption_input.click()
                time.sleep(2)
                captionid="com.instagram.android:id/caption_input_text_view"
                self.set_text_with_failover(d, captionid, caption, device_id)
                
                time.sleep(3)
            else:
                return False, "Caption input not found"
            if d(text="OK").exists:
               d(text="OK").click()
               time.sleep(1)
            # Share
            time.sleep(7)
            share_button = d(text="Share")
            if share_button.exists:
                share_button.click()
                time.sleep(7) 
                if d(description="Back to Home").exists:
                   d(description="Back to Home").click()
                   time.sleep(7)
                time.sleep(90)  # Wait for upload to complete
                return True, "Reel posted successfully"
            else:
                return False, "Share button not found"

        except Exception as e:
            return False, f"Error posting reel: {str(e)}"

    def post_photo(self, device_id, photo_path, caption, music_query=None):
        """Post a photo on Instagram
        Args:
            device_id (str): Device ID
            photo_path (str): Path to photo file
            caption (str): Caption text
            music_query (str, optional): Music to search for. Defaults to None.
        """
        try:
            d = self._get_device(device_id)
            if not d:
                return False, "Failed to connect to device"
           
                if photo_path.startswith(('http://', 'https://')):
                    try:
                       photo_path = self._download_media_from_url(photo_path, 'photo')
                    except Exception as e:
                       return False, f"Failed to download photo from URL: {str(e)}"

            # Push photo file to device's DCIM/Camera directory
            time.sleep(3)
            try:
                # Create DCIM/Camera directory if it doesn't exist
                subprocess.run([
                    'adb', '-s', device_id, 'shell',
                    'mkdir', '-p', '/storage/emulated/0/DCIM/Camera'
                ], check=True)

                # Get filename and device path
                filename = os.path.basename(photo_path)
                device_path = f'/storage/emulated/0/DCIM/Camera/{filename}'

                # Push the file
                subprocess.run([
                    'adb', '-s', device_id, 'push',
                    photo_path, device_path
                ], check=True)

                # Set proper file permissions
                subprocess.run([
                    'adb', '-s', device_id, 'shell',
                    'chmod', '644', device_path
                ], check=True)

                # Trigger media scanner
                subprocess.run([
                    'adb', '-s', device_id, 'shell',
                    'am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
                    '-d', f'file://{device_path}'
                ], check=True)

                # Wait for media scanner
                time.sleep(7)

            except Exception as e:
                return False, f"Failed to push photo: {str(e)}"

            # Click create button
            create_button = d(resourceId="com.instagram.android:id/creation_tab")
            if not create_button.exists:
                return False, "Create button not found"
            create_button.click()
            time.sleep(7)

            # Handle permissions
            self._handle_permissions(d)

            # Select POST
            time.sleep(7)
            post_button = d(resourceId="com.instagram.android:id/cam_dest_feed")
            if not post_button.exists:
                return False, "Post button not found"
            post_button.click()
            time.sleep(6)
            if d(text="Create a sticker").exists:
                d(text="Not now").click()
                time.sleep(2)

            gallery_items = d(resourceId="com.instagram.android:id/gallery_grid_item_thumbnail")
            if gallery_items.exists:
                # Click the first gallery item since we can't get all items
                gallery_items.click()
                time.sleep(6)
            else:
                return False, "No gallery items found"

            # Handle "Find your vibe" prompt
            if d(text="OK").exists:
                d(text="OK").click()
                time.sleep(2)
            time.sleep(9)
            if d(text="Next").exists:
                d(text="Next").click()
                time.sleep(3)

            # Add music if specified
            time.sleep(9)
            if music_query:
                success = self._add_music_to_post2(d, music_query)
                if not success:
                    return False, "Failed to add music"

            # Click Next
            time.sleep(9)
            if d(text="Next").exists:
                d(text="Next").click()
                time.sleep(3)
            else:
                return False, "Next button not found"

            # Add caption
            if d(text="OK").exists:
                d(text="OK").click()
                time.sleep(3)
            time.sleep(9)
            caption_input = d(resourceId="com.instagram.android:id/caption_input_text_view")
            if caption_input.exists:
                caption_input.click()
                time.sleep(1)
                captionid="com.instagram.android:id/caption_input_text_view"
                self.set_text_with_failover(d, captionid, caption, device_id)
                time.sleep(2)
            else:
                return False, "Caption input not found"

            # Share
            time.sleep(5)
            if d(text="OK").exists:
                d(text="OK").click()
                time.sleep(3)
            time.sleep(4)
            share_button = d(text="Share")
            if share_button.exists:
                share_button.click()
                time.sleep(7) 
                if d(text="Back to Home").exists:
                   d(text="Back to Home").click()
                   time.sleep(7)                
                time.sleep(90)  # Wait for upload to complete
                return True, "Photo posted successfully"
            else:
                return False, "Share button not found"

        except Exception as e:
            return False, f"Error posting photo: {str(e)}"

    def _handle_permissions(self, d):
        """Handle permission prompts
        Args:
            d: Device object
        """
        # Handle media access permission
        if d(text="Allow Instagram to access photos, media and files on your device?").exists:
            if d(text="ALLOW").exists:
               d(text="ALLOW").click()
            if d(text="Allow").exists:
               d(text="Allow").click()
               time.sleep(1)
        time.sleep(3)
        if d(text="ALLOW").exists:
           d(text="ALLOW").click()
           time.sleep(3)
        if d(text="Allow").exists:
           d(text="Allow").click()
           time.sleep(3)
        time.sleep(3)
        if d(text="Start new video").exists:
            d(text="Start new video").click()
            time.sleep(1)
        if d(text="Create a sticker").exists:
            d(text="Not now").click()
            time.sleep(2)
        time.sleep(3)
        if d(text="Not now").exists:
            d(text="Not now").click()
            time.sleep(2)
        # Handle camera permission
        if d(text="Allow Instagram to take pictures and record video?").exists:
            if d(text="ALLOW").exists:
               d(text="ALLOW").click()
            if d(text="Allow").exists:
               d(text="Allow").click()
            time.sleep(1)
        time.sleep(3)
        # Handle audio permission
        if d(text="Allow Instagram to record audio?").exists:
            if d(text="ALLOW").exists:
               d(text="ALLOW").click()
            if d(text="Allow").exists:
               d(text="Allow").click()
            time.sleep(3)

    def _add_music_to_post(self, d, music_query):
        """Add music to post
        Args:
            d: Device object
            music_query (str): Music to search for
        """
        try:
            # Click add music button
            music_button = d(resourceId="com.instagram.android:id/clips_action_bar_volume_controls_button") or \
                          d(resourceId="com.instagram.android:id/music_row_title")
            if not music_button.exists:
                return False
            music_button.click()
            time.sleep(2)

            # Search for music
            search_input = d(resourceId="com.instagram.android:id/row_search_edit_text")
            if search_input.exists:
                search_input.click()
                time.sleep(1)
                search_input.set_text(music_query)
                time.sleep(3)
            else:
                return False

            # Select first track
            track = d(resourceId="com.instagram.android:id/track_container")
            if track.exists:
                track.click()
                time.sleep(2)
            else:
                return False

            # Click done/action button
            done_button = d(resourceId="com.instagram.android:id/music_editor_done_button") or \
                         d(resourceId="com.instagram.android:id/select_button") 
            if done_button.exists:
                done_button.click()
                time.sleep(2)
            else:
                return False
            time.sleep(2)
            
            done_button2 = d(resourceId="com.instagram.android:id/music_editor_done_button") or \
                         d(text="Done") 
            if done_button2.exists:
                done_button2.click()
                time.sleep(2)
                return True
            else:
                return False

        except Exception as e:
            print(f"Error adding music: {str(e)}")
            return False
            
            
    def _add_music_to_post2(self, d, music_query):
        """Add music to post
        Args:
            d: Device object
            music_query (str): Music to search for
        """
        try:
            # Click add music button
            music_button = d(resourceId="com.instagram.android:id/clips_action_bar_volume_controls_button") or \
                          d(description="Audio")
            if not music_button.exists:
                return False
            music_button.click()
            time.sleep(2)

            # Search for music
            search_input = d(resourceId="com.instagram.android:id/row_search_edit_text")
            if search_input.exists:
                search_input.click()
                time.sleep(1)
                search_input.set_text(music_query)
                time.sleep(3)
            else:
                return False

            # Select first track
            track = d(resourceId="com.instagram.android:id/track_container")
            if track.exists:
                track.click()
                time.sleep(2)
            else:
                return False

            # Click done/action button
            done_button = d(resourceId="com.instagram.android:id/bottom_sheet_done_button") or \
                         d(resourceId="com.instagram.android:id/select_button") 
            if done_button.exists:
                done_button.click()
                time.sleep(2)
                return True
            else:
                return False
            time.sleep(2)

        except Exception as e:
            print(f"Error adding music: {str(e)}")
            return False
        
    def _download_media_from_url(self, url, media_type='photo'):
        """Download media from URL to local uploads folder
        Args:
            url (str): URL of the media file
            media_type (str): Type of media ('photo' or 'reel')
        Returns:
            str: Path to downloaded file in local uploads directory
        """
        try:
            import requests
            import mimetypes
            from urllib.parse import urlparse
            from datetime import datetime

            # Get the application root directory
            app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            
            # Create uploads directory structure
            uploads_dir = os.path.join(app_root, 'uploads')
            media_dir = os.path.join(uploads_dir, f"{media_type}s")  # 'photos' or 'reels'
            
            # Create directories if they don't exist
            os.makedirs(uploads_dir, exist_ok=True)
            os.makedirs(media_dir, exist_ok=True)
            
            # Generate unique filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Get file extension from URL or mimetype
            parsed_url = urlparse(url)
            path = parsed_url.path
            original_filename = os.path.basename(path)
            
            # If no extension in filename, try to get it from content-type
            if '.' not in original_filename:
                response = requests.head(url)
                content_type = response.headers.get('content-type')
                extension = mimetypes.guess_extension(content_type)
                if extension:
                    file_name = f"{timestamp}_{media_type}{extension}"
                else:
                    file_name = f"{timestamp}_{media_type}.{'mp4' if media_type == 'reel' else 'jpg'}"
            else:
                # Keep original extension but add timestamp
                file_extension = os.path.splitext(original_filename)[1]
                file_name = f"{timestamp}_{media_type}{file_extension}"

            # Create full file path
            local_path = os.path.join(media_dir, file_name)
            
            # Download the file with progress logging
            response = requests.get(url, stream=True)
            response.raise_for_status()  # Raise exception for bad status codes
            
            file_size = int(response.headers.get('content-length', 0))
            logger.info(f"Downloading {media_type} from URL: {url}")
            logger.info(f"File size: {file_size/1024/1024:.2f} MB")
            
            downloaded_size = 0
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        # Log progress every 10%
                        if file_size > 0:
                            progress = (downloaded_size / file_size) * 100
                            if progress % 10 < 1:  # Log at approximately every 10%
                                logger.info(f"Download progress: {progress:.1f}%")

            logger.info(f"Download completed: {local_path}")
            
            # Verify file was downloaded successfully
            if not os.path.exists(local_path):
                raise Exception("File download failed - file not found")
            
            if os.path.getsize(local_path) == 0:
                os.remove(local_path)
                raise Exception("File download failed - empty file")

            return local_path

        except Exception as e:
            logger.error(f"Error downloading media from URL: {str(e)}")
            # Clean up partial download if it exists
            if 'local_path' in locals() and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except:
                    pass
            raise