import os
from flask import jsonify, request, current_app
from app.api import bp
from app.models.device import Device
from app.models.instagram_account import InstagramAccount
from app.utils.device_manager import DeviceManager
from app.utils.adb import ADBManager
from app import db
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
    logger.info("DeviceManager initialized in device_routes")
except Exception as e:
    logger.error(f"Failed to initialize DeviceManager in device_routes: {str(e)}")
    device_manager = None

adb_manager = ADBManager()

def list_devices():
    """Get all devices.

    Optional query params:
      - platform: 'instagram' | 'tiktok' | 'both'
        When set, returns devices whose platform matches OR is 'both'.
    """
    try:
        from app.models.device import VALID_PLATFORMS
        q = Device.query
        platform = request.args.get('platform')
        if platform:
            if platform not in VALID_PLATFORMS:
                return jsonify({'error': f'Invalid platform. Must be one of: {", ".join(VALID_PLATFORMS)}'}), 400
            from app.models.device import PLATFORM_BOTH
            q = q.filter(
                db.or_(Device.platform == platform, Device.platform == PLATFORM_BOTH)
            )
        devices = q.all()
        return jsonify({
            'success': True,
            'devices': [device.to_dict() for device in devices]
        })
    except Exception as e:
        logger.error(f"Error getting devices: {str(e)}")
        return jsonify({'error': str(e)}), 500

def delete_device(device_id):
    """Delete a device and its associated Instagram accounts"""
    try:
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404

        # Find and delete associated Instagram accounts
        instagram_accounts = InstagramAccount.query.filter_by(device_id=device_id).all()
        for account in instagram_accounts:
            logger.info(f"Deleting Instagram account {account.username} associated with device {device_id}")
            db.session.delete(account)

        # Delete the device
        db.session.delete(device)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Device {device_id} and its {len(instagram_accounts)} associated Instagram accounts deleted successfully'
        })

    except Exception as e:
        logger.error(f"Error deleting device: {str(e)}")
        return jsonify({'error': str(e)}), 500

def unassign_device(device_id):
    """Unassign a device and remove its Instagram accounts"""
    try:
        device = Device.query.filter_by(device_id=device_id).first()
        if not device:
            return jsonify({'error': 'Device not found'}), 404

        # Find and delete associated Instagram accounts
        instagram_accounts = InstagramAccount.query.filter_by(device_id=device_id).all()
        for account in instagram_accounts:
            logger.info(f"Removing Instagram account {account.username} association with device {device_id}")
            account.device_id = None
            account.login_status = False
            account.last_logout = datetime.utcnow()

        # Update device status
        device.status = 'available'
        device.assigned_to = None
        device.last_seen = None
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Device {device_id} unassigned and {len(instagram_accounts)} Instagram accounts disassociated'
        })

    except Exception as e:
        logger.error(f"Error unassigning device: {str(e)}")
        return jsonify({'error': str(e)}), 500

def create_device():
    """Add a new device"""
    try:
        data = request.get_json()
        required_fields = ['device_id', 'name']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Check if device already exists
        existing_device = Device.query.get(data['device_id'])
        if existing_device:
            return jsonify({'error': 'Device already exists'}), 400

        # Create new device
        device = Device(
            device_id=data['device_id'],
            name=data['name'],
            status='available'
        )
        db.session.add(device)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Device added successfully',
            'device': device.to_dict()
        })

    except Exception as e:
        logger.error(f"Error adding device: {str(e)}")
        return jsonify({'error': str(e)}), 500

def update_device(device_id):
    """Update device information"""
    try:
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404

        data = request.get_json()
        if 'name' in data:
            device.name = data['name']
        if 'status' in data:
            device.status = data['status']
        if 'assigned_to' in data:
            old_assigned_to = device.assigned_to
            device.assigned_to = data['assigned_to']
            
            # If device is being unassigned, handle Instagram accounts
            if old_assigned_to and not data['assigned_to']:
                instagram_accounts = InstagramAccount.query.filter_by(device_id=device_id).all()
                for account in instagram_accounts:
                    logger.info(f"Removing Instagram account {account.username} association with device {device_id}")
                    account.device_id = None
                    account.login_status = False
                    account.last_logout = datetime.utcnow()

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Device updated successfully',
            'device': device.to_dict()
        })

    except Exception as e:
        logger.error(f"Error updating device: {str(e)}")
        return jsonify({'error': str(e)}), 500

def assign_device():
    """Assign a device to a user"""
    try:
        if not device_manager:
            return jsonify({"error": "Device manager not initialized"}), 500

        data = request.get_json()
        user_id = data.get('user_id')
        count = data.get('count', 1)

        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        success, result = device_manager.assign_device(user_id, count)
        if success:
            return jsonify({'success': True, 'server': current_app.config['SERVER_URL'], 'device_ids': result})
        else:
            return jsonify({'success': False, 'error': result}), 400

    except Exception as e:
        logger.error(f"Error in assign_device: {str(e)}")
        return jsonify({'error': str(e)}), 500

def bulk_operations():
    """Perform bulk operations on devices"""
    data = request.get_json()
    device_ids = data.get('device_ids', [])
    operation = data.get('operation')
    
    valid_operations = ['clear_instagram', 'reboot', 'clean_apps', 'install_uiautomator', 
                       'start_gnirehtet', 'stop_gnirehtet']

    if not device_ids or operation not in valid_operations:
        return jsonify({
            'success': False, 
            'error': f'Invalid operation. Valid operations are: {", ".join(valid_operations)}'
        }), 400

    results = []
    for device_id in device_ids:
        success, message = adb_manager.execute_operation(device_id, operation)
        results.append({'device_id': device_id, 'success': success, 'message': message})

    return jsonify({'success': True, 'results': results}) 

@bp.route('/devices/user/<user_id>', methods=['GET'])
def get_user_devices(user_id):
    """Get all devices assigned to a specific user with their Instagram account counts"""
    try:
        # Get all devices assigned to the user
        devices = Device.query.filter_by(assigned_to=user_id).all()
        
        # Create response with device info and account counts
        device_list = []
        for device in devices:
            device_info = device.to_dict()
            # Count Instagram accounts associated with this device
            account_count = InstagramAccount.query.filter_by(device_id=device.device_id).count()
            device_info['account_count'] = account_count
            device_list.append(device_info)
        
        return jsonify({
            'success': True,
            'server': current_app.config['SERVER_URL'],
            'user_id': user_id,
            'total_devices': len(devices),
            'devices': device_list
        })
    except Exception as e:
        logger.error(f"Error getting devices for user {user_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/devices/free', methods=['GET'])
def get_free_devices():
    """Get all unassigned devices (not assigned to any user)"""
    try:
        # Get all devices that are not assigned to any user
        devices = Device.query.filter_by(assigned_to=None).all()
        
        # Create response with device info and account counts
        device_list = []
        for device in devices:
            device_info = device.to_dict()
            # Check if device has any leftover accounts that need cleanup
            account_count = InstagramAccount.query.filter_by(device_id=device.device_id).count()
            device_info['account_count'] = account_count
            device_info['status'] = 'needs_cleanup' if account_count > 0 else device.status
            device_list.append(device_info)
        
        return jsonify({
            'success': True,
            'server': current_app.config['SERVER_URL'],
            'total_free_devices': len(devices),
            'devices': device_list,
            'summary': {
                'total': len(devices),
                'connected': sum(1 for d in devices if d.status == 'connected'),
                'disconnected': sum(1 for d in devices if d.status == 'disconnected'),
                'needs_cleanup': sum(1 for d in device_list if d['status'] == 'needs_cleanup')
            }
        })
    except Exception as e:
        logger.error(f"Error getting free devices: {str(e)}")
        return jsonify({'error': str(e)}), 500