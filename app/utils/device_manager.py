import subprocess
import re
import os
from datetime import datetime
from app.extensions import db
from app.models.device import Device
from app.models.instagram_account import InstagramAccount
import logging
import platform

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DeviceManager:
    def __init__(self, assets_dir):
        """Initialize DeviceManager with assets directory path"""
        self.assets_dir = assets_dir
        self.device_connections = {}  # Store connection times for devices
        logger.info(f"Initializing DeviceManager with assets directory: {assets_dir}")
        
        # Set ADB paths based on OS
        if platform.system() == "Windows":
            self.adb_path = os.path.join(assets_dir, "adb.exe")
            self.required_dlls = [
                os.path.join(assets_dir, "AdbWinApi.dll"),
                os.path.join(assets_dir, "AdbWinUsbApi.dll")
            ]
            
            # Check for required DLLs
            for dll in self.required_dlls:
                if not os.path.exists(dll):
                    error_msg = f"Required DLL not found: {dll}"
                    logger.error(error_msg)
                    raise FileNotFoundError(error_msg)
        else:
            self.adb_path = os.path.join(assets_dir, "adb")
            self.required_dlls = []

        # Verify ADB executable exists
        if not os.path.exists(self.adb_path):
            error_msg = f"ADB executable not found at: {self.adb_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        # Test ADB functionality
        try:
            result = subprocess.run([self.adb_path, "version"], capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("ADB initialized successfully")
                logger.debug(f"ADB version info: {result.stdout}")
            else:
                error_msg = f"ADB test failed: {result.stderr}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = f"Error testing ADB: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    def get_connected_devices(self):
        """Get list of connected devices via ADB"""
        try:
            logger.info("Checking for connected devices via ADB...")
            
            # Get devices with timeout
            result = subprocess.run([self.adb_path, "devices"], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                logger.error(f"ADB command failed: {result.stderr}")
                return []
                
            logger.debug(f"Raw ADB devices output: {result.stdout}")
            devices = []
            
            # Parse device list
            for line in result.stdout.split('\n')[1:]:  # Skip first line (header)
                line = line.strip()
                if line and '\t' in line:
                    device_id, status = line.split('\t')
                    device_id = device_id.strip()
                    status = status.strip()
                    
                    if status == 'device':  # Only include fully connected devices
                        devices.append(device_id)
                        logger.debug(f"Found connected device: {device_id} with status: {status}")
                    else:
                        logger.debug(f"Device {device_id} found but status is: {status}")
            
            if not devices:
                logger.debug("No connected devices found")
            else:
                logger.debug(f"Found {len(devices)} connected device(s): {devices}")

            return devices
        except subprocess.TimeoutExpired:
            logger.error("ADB command timed out")
            return []
        except Exception as e:
            logger.error(f"Error getting connected devices: {str(e)}")
            return []

    def update_device_statuses(self):
        """Update status for all devices in database"""
        try:
            connected_devices = self.get_connected_devices()
            logger.info(f"Updating status for devices. Connected devices: {connected_devices}")
            
            devices = Device.query.all()
            current_time = datetime.utcnow()
            status_changes = []
            
            # Update connection times for newly connected devices
            for device_id in connected_devices:
                if device_id not in self.device_connections:
                    self.device_connections[device_id] = current_time
            
            # Remove disconnected devices from connection tracking
            disconnected_devices = set(self.device_connections.keys()) - set(connected_devices)
            for device_id in disconnected_devices:
                self.device_connections.pop(device_id, None)
            
            for device in devices:
                previous_status = device.status
                current_status = 'connected' if device.device_id in connected_devices else 'disconnected'
                
                if current_status != previous_status:
                    device.status = current_status
                    device.last_seen = current_time if current_status == 'connected' else device.last_seen
                    device.updated_at = current_time
                    status_changes.append(f"{device.device_name}({device.device_id}): {previous_status} -> {current_status}")
                elif current_status == 'connected':
                    # Update last_seen for connected devices
                    device.last_seen = current_time
                    device.updated_at = current_time
            
            if status_changes:
                logger.info(f"Status changes detected: {', '.join(status_changes)}")
            
            db.session.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating device statuses: {str(e)}")
            db.session.rollback()
            return False

    def assign_device(self, user_id, count=1):
        """Assign unassigned devices to a user"""
        try:
            available_devices = Device.query.filter_by(
                assigned_to=None, 
                status='connected'
            ).limit(count).all()

            if len(available_devices) < count:
                return False, f"Not enough available devices. Requested: {count}, Available: {len(available_devices)}"

            for device in available_devices:
                device.assigned_to = user_id
                device.last_seen = datetime.utcnow()

            db.session.commit()
            return True, [d.device_id for d in available_devices]
        except Exception as e:
            db.session.rollback()
            return False, str(e)

    def unassign_device(self, device_id):
        """Unassign a device and clear its data"""
        try:
            device = Device.query.filter_by(device_id=device_id).first()
            if not device:
                return False, "Device not found"

            device.assigned_to = None
            device.last_seen = datetime.utcnow()
            
            # Clear device data using ADB
            subprocess.run([self.adb_path, "-s", device_id, "shell", "pm", "clear", "com.instagram.android"])
            
            db.session.commit()
            return True, "Device unassigned successfully"
        except Exception as e:
            db.session.rollback()
            return False, str(e)

    def register_device(self, device_id):
        """Register a new device in the database"""
        try:
            if not device_id:
                return False, "Device ID is required"

            existing_device = Device.query.filter_by(device_id=device_id).first()
            if existing_device:
                return False, "Device already registered"

            # Create new device with auto-generated name
            new_device = Device(
                device_id=device_id,
                status='connected',
                last_seen=datetime.utcnow(),
                is_initialized=False,
                error_message=None
            )
            
            db.session.add(new_device)
            db.session.commit()
            logger.info(f"Successfully registered device {device_id} as {new_device.device_name}")
            return True, f"Device registered successfully as {new_device.device_name}"
        except Exception as e:
            logger.error(f"Error registering device {device_id}: {str(e)}")
            db.session.rollback()
            return False, str(e)

    def run_adb_command(self, device_id: str, command: str) -> bool:
        """Run an ADB command for a specific device"""
        try:
            if not device_id:
                raise ValueError("Device ID is required")

            # Construct the full command
            full_command = [self.adb_path]
            if device_id:
                full_command.extend(["-s", device_id])
            full_command.extend(command.split())

            # Run the command
            logger.debug(f"Running ADB command: {' '.join(full_command)}")
            result = subprocess.run(full_command, capture_output=True, text=True)
            
            # Check if command was successful
            if result.returncode != 0:
                logger.error(f"ADB command failed: {result.stderr}")
                return False
            
            logger.debug(f"ADB command successful: {result.stdout}")
            return True
        except Exception as e:
            logger.error(f"Error running ADB command: {str(e)}")
            return False 

    def delete_instagram_account(self, username):
        """Delete an Instagram account from the database"""
        try:
            if not username:
                return False, "Username is required"

            # Find the account
            account = InstagramAccount.query.filter_by(username=username).first()
            if not account:
                return False, "Account not found"

            # Get device ID before deletion for cleanup
            device_id = account.device_id

            # Delete the account
            db.session.delete(account)
            db.session.commit()

            # Clear Instagram data from device if it exists
            if device_id:
                try:
                    self.run_adb_command(device_id, "shell pm clear com.instagram.android")
                except Exception as e:
                    logger.warning(f"Failed to clear Instagram data from device {device_id}: {str(e)}")

            logger.info(f"Successfully deleted Instagram account {username}")
            return True, "Account deleted successfully"
        except Exception as e:
            logger.error(f"Error deleting Instagram account {username}: {str(e)}")
            db.session.rollback()
            return False, str(e)

    def bulk_delete_instagram_accounts(self, usernames):
        """Delete multiple Instagram accounts from the database"""
        try:
            if not usernames:
                return False, "No usernames provided"

            results = []
            for username in usernames:
                success, message = self.delete_instagram_account(username)
                results.append({
                    'username': username,
                    'success': success,
                    'message': message
                })

            # Count successes and failures
            successes = sum(1 for r in results if r['success'])
            failures = len(results) - successes

            logger.info(f"Bulk delete completed: {successes} successful, {failures} failed")
            return True, {
                'message': f"Bulk delete completed: {successes} successful, {failures} failed",
                'results': results
            }
        except Exception as e:
            logger.error(f"Error in bulk delete operation: {str(e)}")
            return False, str(e)

    def get_device_metrics(self, device_id):
        """Get comprehensive device metrics"""
        try:
            metrics = {
                'adb_uptime': self._get_adb_uptime(device_id),
                'phone_uptime': self._get_phone_uptime(device_id),
                'ram_usage': self._get_ram_usage(device_id),
                'disk_usage': self._get_disk_usage(device_id),
                'battery_level': self._get_battery_level(device_id),
                'instagram_version': self._get_instagram_version(device_id),
                'last_updated': datetime.utcnow().isoformat()
            }
            return metrics
        except Exception as e:
            logger.error(f"Error getting device metrics for {device_id}: {str(e)}")
            return None

    def _get_adb_uptime(self, device_id):
        """Get ADB connection uptime in seconds"""
        try:
            # Check if device is in our connections dict
            if device_id not in self.device_connections:
                # If not, add it with current time
                self.device_connections[device_id] = datetime.utcnow()
                return 0

            # Calculate uptime
            uptime = (datetime.utcnow() - self.device_connections[device_id]).total_seconds()
            return uptime
        except Exception as e:
            logger.error(f"Error getting ADB uptime: {str(e)}")
            return None

    def _get_phone_uptime(self, device_id):
        """Get phone uptime in seconds"""
        try:
            result = subprocess.run([self.adb_path, '-s', device_id, 'shell', 'cat', '/proc/uptime'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                # Get the first number from the output (system uptime in seconds)
                uptime = float(result.stdout.split()[0])
                return uptime
            return None
        except Exception as e:
            logger.error(f"Error getting phone uptime: {str(e)}")
            return None

    def _get_ram_usage(self, device_id):
        """Get RAM usage statistics using dumpsys meminfo"""
        try:
            # Get total RAM using dumpsys meminfo
            result = subprocess.run([self.adb_path, '-s', device_id, 'shell', 'dumpsys', 'meminfo'], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                output = result.stdout
                ram_info = {
                    'total': 0,
                    'used': 0,
                    'free': 0,
                    'usage_percent': 0
                }

                # Parse the output
                for line in output.splitlines():
                    if 'Total RAM:' in line:
                        # Handle different possible formats:
                        # Format 1: Total RAM: 5,955,276K (status normal)
                        # Format 2: Total RAM: 1937344 kB (status normal)
                        try:
                            # Remove 'Total RAM:', 'kB', 'K', '(status normal)' and any commas
                            total_str = line.split(':')[1].strip()
                            total_str = total_str.split('(')[0].strip()  # Remove (status normal)
                            total_str = total_str.replace(',', '').replace('kB', '').replace('K', '')
                            ram_info['total'] = int(total_str)
                        except Exception as e:
                            logger.error(f"Error parsing total RAM: {str(e)}")
                            continue

                    elif 'Free RAM:' in line:
                        # Handle different possible formats:
                        # Format 1: Free RAM: 2,002,580K
                        # Format 2: Free RAM: 843512 kB
                        try:
                            # Remove 'Free RAM:', 'kB', 'K' and any commas
                            free_str = line.split(':')[1].strip()
                            free_str = free_str.split('(')[0].strip()  # Remove any parenthetical
                            free_str = free_str.replace(',', '').replace('kB', '').replace('K', '')
                            ram_info['free'] = int(free_str)
                        except Exception as e:
                            logger.error(f"Error parsing free RAM: {str(e)}")
                            continue
                    
                if ram_info['total'] > 0:
                    ram_info['used'] = ram_info['total'] - ram_info['free']
                    ram_info['usage_percent'] = (ram_info['used'] / ram_info['total']) * 100
                    # Convert to MB for better readability
                    ram_info['total_mb'] = round(ram_info['total'] / 1024, 1)
                    ram_info['used_mb'] = round(ram_info['used'] / 1024, 1)
                    ram_info['free_mb'] = round(ram_info['free'] / 1024, 1)
                    return ram_info

                return None
            return None
        except Exception as e:
            logger.error(f"Error getting RAM usage: {str(e)}")
            return None

    def _get_disk_usage(self, device_id):
        """Get disk usage statistics"""
        try:
            result = subprocess.run([self.adb_path, '-s', device_id, 'shell', 'df'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception as e:
            logger.error(f"Error getting disk usage: {str(e)}")
            return None

    def _get_battery_level(self, device_id):
        """Get battery level and status"""
        try:
            result = subprocess.run([self.adb_path, '-s', device_id, 'shell', 'dumpsys', 'battery'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                battery_info = {}
                for line in result.stdout.splitlines():
                    if ':' in line:
                        key, value = line.split(':')
                        battery_info[key.strip()] = value.strip()
                return battery_info
            return None
        except Exception as e:
            logger.error(f"Error getting battery level: {str(e)}")
            return None

    def _get_instagram_version(self, device_id):
        """Get installed Instagram version"""
        try:
            result = subprocess.run([self.adb_path, '-s', device_id, 'shell', 'dumpsys', 'package', 'com.instagram.android', '|', 'grep', 'versionName'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                version_line = result.stdout.strip()
                if 'versionName=' in version_line:
                    return version_line.split('=')[1]
            return None
        except Exception as e:
            logger.error(f"Error getting Instagram version: {str(e)}")
            return None

# Create global device manager instance
device_manager = None

def init_device_manager(assets_dir):
    """Initialize the global device manager instance"""
    global device_manager
    try:
        device_manager = DeviceManager(assets_dir)
        logger.info("Global DeviceManager initialized successfully")
        return device_manager
    except Exception as e:
        logger.error(f"Failed to initialize global DeviceManager: {str(e)}")
        return None

def get_device_manager():
    """Get the global device manager instance"""
    return device_manager 