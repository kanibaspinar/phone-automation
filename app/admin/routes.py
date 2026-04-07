from flask import render_template, jsonify, request, redirect, url_for, flash
from app.admin import bp
from app.models.device import Device
from app.models.instagram_account import InstagramAccount
from app.models.tiktok_account import TikTokAccount
from app.utils.device_manager import DeviceManager
from app.utils.adb import ADBManager
from app import db
import os
from datetime import datetime
import logging
from flask_login import login_required
import subprocess

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
    logger.info("DeviceManager initialized in admin routes")
except Exception as e:
    logger.error(f"Failed to initialize DeviceManager in admin routes: {str(e)}")
    device_manager = None

adb_manager = ADBManager()

@bp.route('/')
def dashboard():
    """Admin dashboard showing device overview"""
    try:
        # Force a device status update
        if device_manager:
            device_manager.update_device_statuses()
            logger.info("Device statuses updated for dashboard")
        
        devices = Device.query.all()
        instagram_accounts = InstagramAccount.query.all()
        
        # Debug logging
        logger.info(f"Retrieved {len(instagram_accounts)} Instagram accounts")
        for account in instagram_accounts:
            logger.info(f"Account: {account.username}, Status: {account.login_status}, Device: {account.device_id}")
        
        # Device statistics
        connected_count = sum(1 for d in devices if d.status == 'connected')
        assigned_count = sum(1 for d in devices if d.assigned_accounts)
        active_accounts = InstagramAccount.query.filter_by(login_status=True).count()
        ig_errors = InstagramAccount.query.filter(
            InstagramAccount.error_message.isnot(None),
            InstagramAccount.error_message != ''
        ).count()
        tiktok_accounts_all = TikTokAccount.query.all()
        tiktok_count = len(tiktok_accounts_all)
        
        # Calculate total and daily statistics across all accounts
        total_stats = {
            'likes': sum(acc.total_likes for acc in instagram_accounts),
            'comments': sum(acc.total_comments for acc in instagram_accounts),
            'follows': sum(acc.total_follows for acc in instagram_accounts),
            'unfollows': sum(acc.total_unfollows for acc in instagram_accounts),
            'stories_viewed': sum(acc.total_stories_viewed for acc in instagram_accounts),
            'story_likes': sum(acc.total_story_likes for acc in instagram_accounts)
        }
        
        daily_stats = {
            'likes': sum(acc.daily_likes for acc in instagram_accounts),
            'comments': sum(acc.daily_comments for acc in instagram_accounts),
            'follows': sum(acc.daily_follows for acc in instagram_accounts),
            'unfollows': sum(acc.daily_unfollows for acc in instagram_accounts),
            'stories_viewed': sum(acc.daily_stories_viewed for acc in instagram_accounts),
            'story_likes': sum(acc.daily_story_likes for acc in instagram_accounts)
        }
        
        logger.info(f"Dashboard stats - Total: {len(devices)}, Connected: {connected_count}, Assigned: {assigned_count}, Active Accounts: {active_accounts}")
        logger.info(f"Total stats - Likes: {total_stats['likes']}, Comments: {total_stats['comments']}, Follows: {total_stats['follows']}, Unfollows: {total_stats['unfollows']}")
        logger.info(f"Daily stats - Likes: {daily_stats['likes']}, Comments: {daily_stats['comments']}, Follows: {daily_stats['follows']}, Unfollows: {daily_stats['unfollows']}")
        
        tiktok_daily = {
            'follows':       sum(a.daily_follows       for a in tiktok_accounts_all),
            'likes':         sum(a.daily_likes         for a in tiktok_accounts_all),
            'comments':      sum(a.daily_comments      for a in tiktok_accounts_all),
            'story_likes':   sum(a.daily_story_likes   for a in tiktok_accounts_all),
            'profile_views': sum(a.daily_profile_views for a in tiktok_accounts_all),
        }
        tiktok_total = {
            'follows':       sum(a.total_follows       for a in tiktok_accounts_all),
            'likes':         sum(a.total_likes         for a in tiktok_accounts_all),
            'comments':      sum(a.total_comments      for a in tiktok_accounts_all),
        }

        stats = {
            'total_devices':    len(devices),
            'connected_devices': connected_count,
            'assigned_devices': assigned_count,
            'active_accounts':  active_accounts,
            'ig_total':         len(instagram_accounts),
            'ig_errors':        ig_errors,
            'tiktok_total':     tiktok_count,
            'total_stats':      total_stats,
            'daily_stats':      daily_stats,
            'tiktok_daily':     tiktok_daily,
            'tiktok_total_stats': tiktok_total,
        }

        return render_template('admin/dashboard.html',
                             stats=stats,
                             devices=devices)
    except Exception as e:
        logger.error(f"Error in dashboard route: {str(e)}")
        return render_template('admin/error.html', error=str(e))

@bp.route('/devices')
def devices():
    """Device management page"""
    devices = Device.query.all()
    return render_template('admin/devices.html', devices=devices)

@bp.route('/devices/bulk-operation', methods=['POST'])
def bulk_device_operation():
    """Handle bulk operations on devices"""
    data = request.get_json()
    device_ids = data.get('device_ids', [])
    operation = data.get('operation')
    
    results = []
    for device_id in device_ids:
        success, message = adb_manager.execute_operation(device_id, operation)
        results.append({'device_id': device_id, 'success': success, 'message': message})
    
    return jsonify({'success': True, 'results': results})

@bp.route('/devices/delete/<device_id>', methods=['DELETE'])
def delete_device(device_id):
    """Delete a device from the database"""
    try:
        device = Device.query.filter_by(device_id=device_id).first()
        if not device:
            return jsonify({'success': False, 'message': 'Device not found'})
        
        # Delete the device
        db.session.delete(device)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Device deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@bp.route('/devices/manage', methods=['GET'])
def manage_devices():
    """Get all devices for management"""
    try:
        if not device_manager:
            return jsonify({"error": "Device manager not initialized"}), 500

        devices = Device.query.all()
        return jsonify({
            'success': True,
            'devices': [{
                'id': device.id,
                'device_id': device.device_id,
                'device_name': device.device_name,
                'status': device.status,
                'assigned_to': device.assigned_to,
                'last_seen': device.last_seen.isoformat() if device.last_seen else None,
                'created_at': device.created_at.isoformat() if device.created_at else None,
                'updated_at': device.updated_at.isoformat() if device.updated_at else None,
                'is_initialized': device.is_initialized,
                'error_message': device.error_message
            } for device in devices]
        })
    except Exception as e:
        logger.error(f"Error in manage_devices: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/debug/accounts')
def debug_accounts():
    """Debug route to check Instagram accounts"""
    try:
        accounts = InstagramAccount.query.all()
        return jsonify({
            'total_accounts': len(accounts),
            'accounts': [{
                'id': account.id,
                'username': account.username,
                'device_id': account.device_id,
                'login_status': account.login_status,
                'email': account.email,
                'last_login': account.last_login.isoformat() if account.last_login else None,
                'last_logout': account.last_logout.isoformat() if account.last_logout else None,
                'created_at': account.created_at.isoformat() if account.created_at else None,
                'updated_at': account.updated_at.isoformat() if account.updated_at else None,
                'total_likes': account.total_likes,
                'total_comments': account.total_comments,
                'total_follows': account.total_follows,
                'total_stories_viewed': account.total_stories_viewed
            } for account in accounts]
        })
    except Exception as e:
        logger.error(f"Error in debug_accounts: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/accounts')
def instagram_accounts():
    """Instagram accounts management page"""
    try:
        devices = Device.query.all()
        instagram_accounts = InstagramAccount.query.all()
        
        # Debug logging
        logger.info(f"Retrieved {len(instagram_accounts)} Instagram accounts for management page")
        
        return render_template('admin/instagram_accounts.html',
                             devices=devices,
                             instagram_accounts=instagram_accounts)
    except Exception as e:
        logger.error(f"Error in instagram_accounts route: {str(e)}")
        return render_template('admin/error.html', error=str(e))

@bp.route('/tiktok/accounts')
def tiktok_accounts():
    """TikTok accounts management page"""
    try:
        devices = Device.query.all()
        accounts = TikTokAccount.query.all()
        return render_template('admin/tiktok_accounts.html',
                               devices=devices,
                               tiktok_accounts=accounts)
    except Exception as e:
        logger.error(f"Error in tiktok_accounts route: {str(e)}")
        return render_template('admin/error.html', error=str(e))


@bp.route('/tasks')
def tasks():
    """Task manager page"""
    try:
        from app.utils.instagram_task_manager import get_task_manager
        from flask import current_app
        
        logger.info("Initializing task manager in tasks route")
        
        # Get task manager instance
        task_manager = get_task_manager()
        if not task_manager:
            error_msg = "Task manager service is not available. Please check the logs for details."
            logger.error(error_msg)
            return render_template('admin/error.html', error=error_msg)

        # Get all tasks
        try:
            tasks = task_manager.get_all_tasks()
            logger.info(f"Retrieved {len(tasks) if tasks else 0} tasks")
            if tasks:
                for task in tasks:
                    logger.info(f"Task: ID={task.get('task_id')}, Type={task.get('task_type')}, Status={task.get('status')}")
            
            if tasks is None:
                logger.warning("Tasks is None, setting to empty list")
                tasks = []
                
        except Exception as e:
            logger.error(f"Error getting tasks: {str(e)}")
            tasks = []
            
        # Sort tasks by created_at in descending order (newest first)
        try:
            tasks.sort(key=lambda x: x.get('created_at', datetime.min) if isinstance(x, dict) 
                      else (x.created_at if hasattr(x, 'created_at') else datetime.min), 
                      reverse=True)
        except Exception as e:
            logger.error(f"Error sorting tasks: {str(e)}")
            
        # Create a sample task for testing if no tasks exist
        if not tasks and current_app.debug:
            logger.info("Creating sample task for testing")
            sample_task = {
                'task_id': 'sample-123',
                'task_type': 'like_post',
                'status': 'completed',
                'params': {
                    'username': 'test_user',
                    'target_username': 'target_user'
                },
                'created_at': datetime.now().isoformat(),
                'completed_at': datetime.now().isoformat(),
                'result': 'Sample task result',
                'error': None
            }
            tasks = [sample_task]
            
        return render_template('admin/tasks.html', tasks=tasks)
    except Exception as e:
        error_msg = f"Error loading tasks: {str(e)}"
        logger.error(error_msg)
        return render_template('admin/error.html', error=error_msg)

@bp.route('/api-docs')
def api_docs():
    """API Documentation page"""
    return render_template('admin/api_docs.html')

@bp.route('/devices/show-screen/<device_id>', methods=['POST'])
def show_device_screen(device_id):
    """Show device screen using scrcpy"""
    try:
        subprocess.Popen(['scrcpy', '-s', device_id])
        return jsonify({
            'success': True,
            'message': f'Showing screen of device {device_id}'
        })
    except Exception as e:
        logger.error(f"Failed to show screen of device {device_id}: {e}")
        return jsonify({
            'success': False,
            'message': f'Failed to show screen: {str(e)}'
        }), 500

