import threading
import time
from app.models.device import Device
from app.extensions import db
from app.utils.device_manager import get_device_manager
import logging
from flask import current_app

logger = logging.getLogger(__name__)

class BackgroundTaskManager:
    def __init__(self, app=None):
        self.app = app
        self.threads = []
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.start_tasks()

    def update_device_metrics(self):
        """Background task to update device metrics"""
        while True:
            with self.app.app_context():
                try:
                    # Get device manager instance
                    device_manager = get_device_manager()
                    if not device_manager:
                        logger.error("Device manager not initialized, waiting 5 seconds before retry")
                        time.sleep(5)
                        continue

                    devices = Device.query.all()
                    for device in devices:
                        if device.status == 'connected':  # Only update metrics for connected devices
                            try:
                                metrics = device_manager.get_device_metrics(device.device_id)
                                if metrics:
                                    device.metrics = metrics
                                    db.session.commit()
                                    logger.info(f"Updated metrics for device {device.device_id}")
                            except Exception as e:
                                logger.error(f"Error updating metrics for device {device.device_id}: {str(e)}")
                                continue
                    time.sleep(60)  # Update every minute
                except Exception as e:
                    logger.error(f"Error updating device metrics: {str(e)}")
                    time.sleep(5)  # Wait before retrying

    def start_tasks(self):
        """Start all background tasks"""
        metrics_thread = threading.Thread(target=self.update_device_metrics, daemon=True)
        metrics_thread.start()
        self.threads.append(metrics_thread)
        logger.info("Started background tasks")

# Global instance
background_task_manager = None

def init_background_tasks(app):
    """Initialize the background task manager"""
    global background_task_manager
    if background_task_manager is None:
        background_task_manager = BackgroundTaskManager(app)
    return background_task_manager

def get_background_task_manager():
    """Get the background task manager instance"""
    return background_task_manager 