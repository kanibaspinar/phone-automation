import os
from flask import jsonify, request, current_app
from app.api import bp
from app.models.device import Device
from app.models.instagram_account import InstagramAccount
from app.utils.device_manager import DeviceManager
from app.extensions import db
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the assets directory path
app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
assets_dir = os.path.join(app_root, 'assets')

# Create assets directory if it doesn't exist
if not os.path.exists(assets_dir):
    os.makedirs(assets_dir)
    logger.info(f"Created assets directory at: {assets_dir}")

# Initialize DeviceManager with assets directory
try:
    device_manager = DeviceManager(assets_dir)
    logger.info("DeviceManager initialized in routes")
except Exception as e:
    logger.error(f"Failed to initialize DeviceManager in routes: {str(e)}")
    device_manager = None

@bp.route('/devices', methods=['GET'])
def get_devices():
    """Get all available (unassigned) devices and their status"""
    try:
        if not device_manager:
            return jsonify({"error": "Device manager not initialized"}), 500

        devices = Device.query.filter(
            Device.assigned_to.is_(None),
            Device.status == 'connected'
        ).all()
        
        return jsonify({
            'success': True,
            'devices': [{
                'id': device.id,
                'device_id': device.device_id,
                'device_name': device.device_name,
                'status': device.status,
                'last_seen': device.last_seen.isoformat() if device.last_seen else None,
                'created_at': device.created_at.isoformat() if device.created_at else None,
                'updated_at': device.updated_at.isoformat() if device.updated_at else None,
                'is_initialized': device.is_initialized,
                'error_message': device.error_message,
                'instagram_accounts': [{
                    'username': acc.username,
                    'login_status': acc.login_status,
                    'last_login': acc.last_login.isoformat() if acc.last_login else None
                } for acc in device.instagram_accounts]
            } for device in devices]
        })
    except Exception as e:
        logger.error(f"Error in get_devices: {str(e)}")
        return jsonify({"error": str(e)}), 500

@bp.route('/devices/status', methods=['GET'])
def get_devices_status():
    """Get real-time status of all devices"""
    try:
        device_manager.update_device_statuses()
        devices = Device.query.all()
        return jsonify({
            'success': True,
            'devices': [{
                'device_id': device.device_id,
                'status': device.status,
                'last_seen': device.last_seen.isoformat() if device.last_seen else None,
                'is_initialized': device.is_initialized,
                'error_message': device.error_message
            } for device in devices]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500 