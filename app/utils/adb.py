import subprocess
import os
import time
from datetime import datetime
import uiautomator2 as u2

class ADBManager:
    def __init__(self):
        self.adb_path = "adb"
        self.assets_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets')

    def execute_operation(self, device_id, operation):
        """Execute ADB operation on device"""
        try:
            if operation == "clear_instagram":
                return self._clear_instagram(device_id)
            elif operation == "reboot":
                return self._reboot_device(device_id)
            elif operation == "clean_apps":
                return self._clean_apps(device_id)
            elif operation == "install_uiautomator":
                return self._install_uiautomator(device_id)
            elif operation == "clear_tiktok":
                return self._clear_tiktok(device_id)
            elif operation == "start_gnirehtet":
                return self._start_gnirehtet(device_id)
            elif operation == "stop_gnirehtet":
                return self._stop_gnirehtet(device_id)
            else:
                return False, f"Unknown operation: {operation}"
        except Exception as e:
            return False, str(e)

    def _clear_instagram(self, device_id):
        """Clear Instagram app data"""
        try:
            subprocess.run([
                self.adb_path, "-s", device_id, "shell", 
                "pm", "clear", "com.instagram.android"
            ], check=True)
            return True, "Instagram data cleared successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Failed to clear Instagram data: {str(e)}"

    def _clear_tiktok(self, device_id):
        """Clear TikTok app data"""
        try:
            subprocess.run([
                self.adb_path, "-s", device_id, "shell",
                "pm", "clear", "com.zhiliaoapp.musically"
            ], check=True)
            return True, "TikTok data cleared successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Failed to clear TikTok data: {str(e)}"

    def _reboot_device(self, device_id):
        """Reboot device"""
        try:
            subprocess.run([self.adb_path, "-s", device_id, "reboot"], check=True)
            return True, "Device reboot initiated"
        except subprocess.CalledProcessError as e:
            return False, f"Failed to reboot device: {str(e)}"

    def _clean_apps(self, device_id):
        """Clean unnecessary apps"""
        try:
            # List of common unnecessary apps
            apps_to_remove = [
                "com.google.android.youtube",
                "com.android.chrome",
                "com.google.android.gm",
                "com.google.android.apps.photos"
            ]
            
            for app in apps_to_remove:
                try:
                    subprocess.run([
                        self.adb_path, "-s", device_id, "shell", 
                        "pm", "disable-user", "--user", "0", app
                    ], check=True)
                except subprocess.CalledProcessError:
                    continue  # Skip if app doesn't exist or can't be disabled

            return True, "Unnecessary apps cleaned"
        except Exception as e:
            return False, f"Failed to clean apps: {str(e)}"

    def _install_uiautomator(self, device_id):
        """Install UIAutomator APKs"""
        try:
            apk_files = [
                "app-uiautomator.apk",
                "app-uiautomator-test.apk"
            ]
            
            for apk in apk_files:
                apk_path = os.path.join(self.assets_path, apk)
                if not os.path.exists(apk_path):
                    return False, f"UIAutomator APK not found: {apk}"
                
                subprocess.run([
                    self.adb_path, "-s", device_id, "install", "-r", apk_path
                ], check=True)

            return True, "UIAutomator installed successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Failed to install UIAutomator: {str(e)}"

    def _start_gnirehtet(self, device_id):
        """Start Gnirehtet for reverse tethering"""
        try:
            subprocess.run(["gnirehtet", "start", device_id], check=True)
            time.sleep(3)
            d = u2.connect(device_id)
            for attempt in range(10):  # Retry up to 10 seconds
                if d(text="Connection request").exists:
                    if d(text="OK").exists:
                        d(text="OK").click()
                        print(f"Clicked 'OK' button on 'Connection request' dialog for {device_id}.")
                        return True, "Gnirehtet started successfully"
                    else:
                       print(f"'Connection request' dialog found, but 'OK' button not visible. Retrying...")
            time.sleep(2)
            return True, "Gnirehtet started successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Failed to start Gnirehtet: {str(e)}"

    def _stop_gnirehtet(self, device_id):
        """Stop Gnirehtet"""
        try:
            subprocess.run(["gnirehtet", "stop", device_id], check=True)
            return True, "Gnirehtet stopped successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Failed to stop Gnirehtet: {str(e)}" 