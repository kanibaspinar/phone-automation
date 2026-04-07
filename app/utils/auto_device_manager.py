import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional
from flask import current_app
from app.extensions import db
from app.models.device import Device
from app.utils.device_manager import DeviceManager
from app.utils.task_manager import TaskManager
import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AutoDeviceManager:
    def __init__(self):
        """Initialize AutoDeviceManager"""
        self.app = None
        self.device_manager = None
        self.monitor_thread = None
        self.should_run = False
        self._lock = threading.Lock()
        self._connected_devices: Dict[str, datetime] = {}
        self.task_manager = TaskManager()
        logger.info("AutoDeviceManager initialized")

    def init_app(self, app):
        """Initialize with Flask app context"""
        self.app = app
        
        # Get the assets directory path
        app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        assets_dir = os.path.join(app_root, 'assets')
        
        # Create assets directory if it doesn't exist
        if not os.path.exists(assets_dir):
            os.makedirs(assets_dir)
            logger.info(f"Created assets directory at: {assets_dir}")

        # Initialize DeviceManager with assets directory
        try:
            self.device_manager = DeviceManager(assets_dir)
            logger.info("DeviceManager initialized in AutoDeviceManager")
        except Exception as e:
            logger.error(f"Failed to initialize DeviceManager in AutoDeviceManager: {str(e)}")
            return

        # Start monitoring thread
        self.should_run = True
        self.monitor_thread = threading.Thread(target=self._monitor_devices)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        logger.info("Device monitoring thread started")

    def stop(self):
        """Stop the device monitoring thread"""
        self.should_run = False
        if self.monitor_thread:
            self.monitor_thread.join()
            logger.info("Device monitoring thread stopped")

    def _monitor_devices(self):
        """Monitor devices and update their status"""
        consecutive_errors = 0
        last_adb_restart = None
        MIN_RESTART_INTERVAL = 1200  # Minimum 5 minutes between ADB restarts
        
        while self.should_run:
            with self.app.app_context():
                try:
                    if self.device_manager:
                        # Get currently connected devices from ADB
                        connected_devices = set(self.device_manager.get_connected_devices())
                        logger.debug(f"Currently connected devices: {connected_devices}")
                        
                        # Get all devices from database
                        devices = Device.query.all()
                        current_time = datetime.utcnow()
                        status_changes = []
                        
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
                        
                        # Register any new devices that are connected but not in database
                        for device_id in connected_devices:
                            device = Device.query.filter_by(device_id=device_id).first()
                            if not device:
                                success, message = self.device_manager.register_device(device_id)
                                if success:
                                    logger.info(f"New device registered: {device_id} - {message}")
                                else:
                                    logger.error(f"Failed to register device {device_id}: {message}")
                        
                        db.session.commit()
                        consecutive_errors = 0  # Reset error counter on success
                except Exception as e:
                    logger.error(f"Error in device monitoring: {str(e)}")
                    db.session.rollback()
                    consecutive_errors += 1
                    
                    # Only restart ADB if we have persistent errors and enough time has passed since last restart
                    current_time = datetime.utcnow()
                    if consecutive_errors >= 5:  # Increased threshold
                        if (not last_adb_restart or 
                            (current_time - last_adb_restart).total_seconds() > MIN_RESTART_INTERVAL):
                            logger.warning("Multiple consecutive errors in device monitoring, attempting ADB server restart")
                            try:
                                # Try to restart ADB server
                                subprocess.run([self.device_manager.adb_path, "kill-server"], capture_output=True, text=True, timeout=5)
                                time.sleep(2)  # Give it time to shut down
                                subprocess.run([self.device_manager.adb_path, "start-server"], capture_output=True, text=True, timeout=5)
                                logger.info("ADB server restarted after persistent errors")
                                last_adb_restart = current_time
                                consecutive_errors = 0
                            except Exception as restart_error:
                                logger.error(f"Error restarting ADB server: {str(restart_error)}")
                        else:
                            logger.debug("Skipping ADB restart due to minimum interval not met")
            
            # Sleep for 3 seconds before next check
            time.sleep(3)

    def _discover_devices(self):
        """Actively discover and add new devices"""
        try:
            with self.app.app_context():
                # Get all connected devices from ADB
                connected_devices = set(self.device_manager.get_connected_devices())
                
                # Get all devices from database
                db_devices = set(d.device_id for d in Device.query.all())
                
                # Find new devices that aren't in the database
                new_devices = connected_devices - db_devices
                
                # Add each new device
                for device_id in new_devices:
                    try:
                        self._handle_new_device(device_id)
                        logger.info(f"Discovered and added new device: {device_id}")
                    except Exception as e:
                        logger.error(f"Failed to add discovered device {device_id}: {str(e)}")
                
                if new_devices:
                    logger.info(f"Device discovery found {len(new_devices)} new device(s)")
                
        except Exception as e:
            logger.error(f"Error in device discovery: {str(e)}")

    def _initialize_pending_devices(self):
        """Initialize devices that are connected but not initialized"""
        try:
            with self.app.app_context():
                devices = Device.query.filter_by(
                    status='connected',
                    is_initialized=False
                ).all()
                
                for device in devices:
                    try:
                        self._initialize_device(device.device_id)
                        device.is_initialized = True
                        db.session.commit()
                    except Exception as e:
                        logger.error(f"Error initializing device {device.device_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Error in device initialization task: {str(e)}")

    def _update_device_statuses(self):
        """Update status of all devices"""
        try:
            with self.app.app_context():
                self.device_manager.update_device_statuses()
        except Exception as e:
            logger.error(f"Error updating device statuses: {str(e)}")

    def _handle_new_device(self, device_id: str):
        """Handle newly connected device"""
        try:
            with self._lock:
                self._connected_devices[device_id] = datetime.utcnow()

            with self.app.app_context():
                device = Device.query.filter_by(device_id=device_id).first()
                if not device:
                    # Use device manager to register the device with P1, P2, P3 naming
                    success, message = self.device_manager.register_device(device_id)
                    if success:
                        logger.info(f"New device registered: {device_id} - {message}")
                    else:
                        logger.error(f"Failed to register device {device_id}: {message}")
                else:
                    device.status = 'connected'
                    device.last_seen = datetime.utcnow()
                    device.updated_at = datetime.utcnow()
                    db.session.commit()
                    logger.info(f"Updated existing device: {device_id} as {device.device_name}")

        except Exception as e:
            logger.error(f"Error handling new device {device_id}: {str(e)}")
            db.session.rollback()

    def _handle_disconnected_device(self, device_id: str):
        """Handle disconnected device"""
        try:
            with self._lock:
                if device_id in self._connected_devices:
                    del self._connected_devices[device_id]

            with self.app.app_context():
                device = Device.query.filter_by(device_id=device_id).first()
                if device:
                    device.status = 'disconnected'
                    device.last_seen = datetime.utcnow()
                    db.session.commit()
                    logger.info(f"Device disconnected: {device_id}")

        except Exception as e:
            logger.error(f"Error handling disconnected device {device_id}: {str(e)}")

    def _initialize_device(self, device_id: str):
        """Initialize a newly connected device"""
        try:
            # Grant necessary permissions
            self._grant_permissions(device_id)
            
            # Prepare device for automation
            self._prepare_device(device_id)
            
            logger.info(f"Device initialized: {device_id}")
            
        except Exception as e:
            logger.error(f"Error initializing device {device_id}: {str(e)}")
            raise

    def _grant_permissions(self, device_id: str):
        """Grant necessary permissions to the device"""
        try:
            permissions = [
                "android.permission.WRITE_EXTERNAL_STORAGE",
                "android.permission.READ_EXTERNAL_STORAGE",
                "android.permission.INTERNET"
            ]
            
            for permission in permissions:
                if not self.device_manager.run_adb_command(
                    device_id,
                    f"shell pm grant com.instagram.android {permission}"
                ):
                    raise Exception(f"Failed to grant permission: {permission}")
                
        except Exception as e:
            logger.error(f"Error granting permissions to device {device_id}: {str(e)}")
            raise

    def _prepare_device(self, device_id: str):
        """Prepare device for automation"""
        try:
            # Set stay awake
            if not self.device_manager.run_adb_command(
                device_id,
                "shell settings put global stay_on_while_plugged_in 3"
            ):
                raise Exception("Failed to set stay awake")
            
            # Disable animations
            animation_settings = [
                "window_animation_scale 0",
                "transition_animation_scale 0",
                "animator_duration_scale 0"
            ]
            
            for setting in animation_settings:
                if not self.device_manager.run_adb_command(
                    device_id,
                    f"shell settings put global {setting}"
                ):
                    raise Exception(f"Failed to set animation setting: {setting}")
                
        except Exception as e:
            logger.error(f"Error preparing device {device_id}: {str(e)}")
            raise

    def get_connected_devices(self) -> Dict[str, datetime]:
        """Get list of currently connected devices with their last seen times"""
        with self._lock:
            return self._connected_devices.copy()

    def get_device_status(self, device_id: str) -> str:
        """Get current status of a device"""
        with self.app.app_context():
            device = Device.query.filter_by(device_id=device_id).first()
            return device.status if device else 'unknown'

    def get_task_status(self) -> List[Dict]:
        """Get status of all background tasks"""
        return self.task_manager.get_task_status() 