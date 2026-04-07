from flask import jsonify, request, current_app
from app.api import bp
from app.models.device import Device
from app.models.instagram_account import InstagramAccount
from app.utils.instagram_automation import InstagramAutomation
from app.utils.instagram_task_manager import InstagramTaskManager
from app.utils.device_manager import get_device_manager
from app.extensions import db
import logging
import requests
from datetime import datetime
from app.models.direct_message import DirectMessage
from app.models.post_comment import PostComment
from werkzeug.utils import secure_filename
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global task manager instance
_task_manager = None

def get_instagram_task_manager():
    """Get Instagram task manager instance using lazy initialization"""
    global _task_manager
    if _task_manager is None:
        device_manager = get_device_manager()
        if not device_manager:
            logger.error("DeviceManager not initialized")
            return None
        instagram_automation = InstagramAutomation(device_manager)
        _task_manager = InstagramTaskManager(instagram_automation, app=current_app._get_current_object())
    return _task_manager

def get_task_manager():
    """Helper function to get task manager instance"""
    global _task_manager
    if _task_manager is None:
        _task_manager = get_instagram_task_manager()
    return _task_manager

@bp.route('/instagram/login', methods=['POST'])
def instagram_login():
    """Login to Instagram account on specified device"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'password']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Check if account exists in database
        account = InstagramAccount.query.filter_by(username=data['username']).first()
        
        if not account:
            # Create new account if it doesn't exist
            account = InstagramAccount(
                username=data['username'],
                device_id=data['device_id'],
                login_status=False,
                email=data['email'],
                email_password=data['email_password'],
                password=data['password'],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(account)
        else:
            # Update existing account
            account.device_id = data['device_id']
            account.email = data.get('email')
            account.email_password = data.get('email_password')
            account.password = data.get('password')
            account.updated_at = datetime.utcnow()

        # Save changes before creating task
        db.session.commit()

        # Create login task
        task_id = task_manager.add_task('login', data)
        
        return jsonify({
            'success': True,
            'message': 'Login task created',
            'task_id': task_id,
            'account': {
                'id': account.id,
                'username': account.username,
                'device_id': account.device_id,
                'login_status': account.login_status,
                'last_login': account.last_login.isoformat() if account.last_login else None,
                'last_logout': account.last_logout.isoformat() if account.last_logout else None,
                'created_at': account.created_at.isoformat(),
                'updated_at': account.updated_at.isoformat()
            }
        })

    except Exception as e:
        logger.error(f"Error creating login task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/logout', methods=['POST'])
def instagram_logout():
    """Logout from Instagram account on specified device"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        if 'device_id' not in data:
            return jsonify({'error': 'device_id is required'}), 400

        # Create logout task
        task_id = task_manager.add_task('logout', data)
        
        return jsonify({
            'success': True,
            'message': 'Logout task created',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error creating logout task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Get status of a specific task"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        status = task_manager.get_task_status(task_id)
        if not status:
            return jsonify({'error': 'Task not found'}), 404

        return jsonify({
            'success': True,
            'task': status
        })

    except Exception as e:
        logger.error(f"Error getting task status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/tasks', methods=['GET'])
def get_all_tasks():
    """Get status of all tasks"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        tasks = task_manager.get_all_tasks()
        return jsonify({
            'success': True,
            'tasks': tasks
        })

    except Exception as e:
        logger.error(f"Error getting all tasks: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/like-post', methods=['POST'])
def like_post():
    """Like Instagram posts"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create like post task
        task_id = task_manager.add_task('like_post', data)
        
        return jsonify({
            'success': True,
            'message': 'Like post task created',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error creating like post task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/comment-story', methods=['POST'])
def comment_story():
    """Comment on Instagram stories"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create comment story task
        task_id = task_manager.add_task('comment_story', data)
        
        return jsonify({
            'success': True,
            'message': 'Comment story task created',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error creating comment story task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/follow', methods=['POST'])
def follow_user():
    """Follow an Instagram user"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create follow user task
        task_id = task_manager.add_task('follow_user', data)
        
        return jsonify({
            'success': True,
            'message': 'Follow user task created',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error creating follow user task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/view-story', methods=['POST'])
def view_story():
    """View Instagram stories"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create view story task
        task_id = task_manager.add_task('view_story', data)
        
        return jsonify({
            'success': True,
            'message': 'View story task created',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error creating view story task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/accounts/list', methods=['GET'])
def list_instagram_accounts():
    """Get all Instagram accounts with optional filtering"""
    try:
        # Get query parameters for filtering
        device_id = request.args.get('device_id')
        login_status = request.args.get('login_status')
        username = request.args.get('username')

        # Start with base query
        query = InstagramAccount.query

        # Apply filters if provided
        if device_id:
            query = query.filter_by(device_id=device_id)
        if login_status is not None:
            query = query.filter_by(login_status=login_status.lower() == 'true')
        if username:
            query = query.filter(InstagramAccount.username.ilike(f'%{username}%'))

        # Execute query and get accounts
        accounts = query.all()

        # Prepare response with detailed account information
        account_list = []
        for account in accounts:
            account_data = account.to_dict()
            
            # Add device information if available
            if account.device:
                account_data['device'] = {
                    'device_id': account.device.device_id,
                    'device_name': account.device.device_name,
                    'status': account.device.status,
                    'is_initialized': account.device.is_initialized
                }
            
            account_list.append(account_data)

        return jsonify({
            'success': True,
            'total_accounts': len(account_list),
            'accounts': account_list
        })
    except Exception as e:
        logger.error(f"Error in list_instagram_accounts: {str(e)}")
        return jsonify({'error': str(e)}), 500 

@bp.route('/instagram/actions/like-story', methods=['POST'])
def like_story():
    """API endpoint to like an Instagram story"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        required_fields = ['username', 'target_username', 'device_id']
        for field in required_fields:
            if field not in data:
                return jsonify({'status': 'error', 'message': f'Missing required field: {field}'}), 400

        task_manager = get_task_manager()
        if not task_manager:
            logger.error("Task manager service is not available")
            return jsonify({'status': 'error', 'message': 'Task manager service is not available'}), 503

        # Create a task to like the story
        task_id = task_manager.add_task('like_story', {
            'username': data['username'],
            'target_username': data['target_username'],
            'device_id': data['device_id']
        })

        return jsonify({
            'status': 'success',
            'message': 'Story like task created successfully',
            'task_id': task_id
        }), 200

    except Exception as e:
        logger.error(f"Error in like_story endpoint: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500 

@bp.route('/instagram/actions/unfollow', methods=['POST'])
def unfollow_user():
    """Unfollow an Instagram user"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create unfollow user task
        task_id = task_manager.add_task('unfollow_user', data)
        
        return jsonify({
            'success': True,
            'message': 'Unfollow user task created successfully',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error creating unfollow user task: {str(e)}")
        return jsonify({'error': str(e)}), 500 

@bp.route('/instagram/accounts/<username>', methods=['DELETE'])
def delete_instagram_account(username):
    """Delete an Instagram account"""
    try:
        device_manager = get_device_manager()
        if not device_manager:
            return jsonify({'error': 'Device manager not available'}), 500

        success, message = device_manager.delete_instagram_account(username)
        if success:
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400

    except Exception as e:
        logger.error(f"Error in delete_instagram_account: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/accounts/bulk-delete', methods=['POST'])
def bulk_delete_instagram_accounts():
    """Delete multiple Instagram accounts"""
    try:
        data = request.get_json()
        if not data or 'usernames' not in data:
            return jsonify({'error': 'usernames list is required'}), 400

        device_manager = get_device_manager()
        if not device_manager:
            return jsonify({'error': 'Device manager not available'}), 500

        success, result = device_manager.bulk_delete_instagram_accounts(data['usernames'])
        if success:
            return jsonify({
                'success': True,
                'message': result['message'],
                'results': result['results']
            })
        else:
            return jsonify({
                'success': False,
                'error': result
            }), 400

    except Exception as e:
        logger.error(f"Error in bulk_delete_instagram_accounts: {str(e)}")
        return jsonify({'error': str(e)}), 500 

@bp.route('/instagram/accounts', methods=['POST'])
def add_instagram_account():
    """Add a new Instagram account to the database.

    Required: username, password
    Optional: device_id, email, email_password, imap_server, imap_port
    """
    try:
        data = request.get_json() or {}
        if not data.get('username') or not data.get('password'):
            return jsonify({'error': 'username and password are required'}), 400

        if InstagramAccount.query.filter_by(username=data['username']).first():
            return jsonify({'error': 'Account already exists'}), 409

        device_id = data.get('device_id')
        if device_id:
            if not Device.query.filter_by(device_id=device_id).first():
                return jsonify({'error': f'Device {device_id} not found'}), 404

        new_account = InstagramAccount(
            username=data['username'],
            password=data['password'],
            device_id=device_id,
            email=data.get('email'),
            email_password=data.get('email_password'),
            imap_server=data.get('imap_server'),
            imap_port=int(data['imap_port']) if data.get('imap_port') else 993,
        )
        db.session.add(new_account)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Instagram account added successfully',
            'account': new_account.to_dict()
        }), 201

    except Exception as e:
        logger.error(f"Error adding Instagram account: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/accounts/bulk', methods=['POST'])
def add_instagram_accounts_bulk():
    """Add multiple Instagram accounts.

    Body: { "accounts": [ {username, password, device_id?, email?, email_password?,
                            imap_server?, imap_port?}, ... ] }
    """
    try:
        data = request.get_json() or {}
        accounts = data.get('accounts', [])
        if not isinstance(accounts, list) or not accounts:
            return jsonify({'error': 'accounts list is required'}), 400

        added, failed = [], []

        for acc in accounts:
            username = acc.get('username', '').strip()
            password = acc.get('password', '').strip()
            if not username or not password:
                failed.append({'account': username or '?', 'error': 'username and password required'})
                continue

            if InstagramAccount.query.filter_by(username=username).first():
                failed.append({'account': username, 'error': 'already exists'})
                continue

            device_id = acc.get('device_id')
            if device_id and not Device.query.filter_by(device_id=device_id).first():
                failed.append({'account': username, 'error': f'device {device_id} not found'})
                continue

            try:
                new_acc = InstagramAccount(
                    username=username,
                    password=password,
                    device_id=device_id,
                    email=acc.get('email'),
                    email_password=acc.get('email_password'),
                    imap_server=acc.get('imap_server'),
                    imap_port=int(acc['imap_port']) if acc.get('imap_port') else 993,
                )
                db.session.add(new_acc)
                added.append(username)
            except Exception as e:
                failed.append({'account': username, 'error': str(e)})

        db.session.commit()

        return jsonify({
            'success': True,
            'added': len(added),
            'failed': failed,
            'message': f'Added {len(added)}, failed {len(failed)}',
        })

    except Exception as e:
        logger.error(f"Error in bulk account addition: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/send-dm', methods=['POST'])
def send_direct_message():
    """Send direct message to an Instagram user"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username', 'message']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create DM record in database
        dm = DirectMessage(
            sender_username=data['username'],
            target_username=data['target_username'],
            message=data['message'],
            device_id=data['device_id']
        )
        db.session.add(dm)
        db.session.commit()

        # Create send DM task
        task_data = {
            'device_id': data['device_id'],
            'username': data['username'],
            'target_username': data['target_username'],
            'dm_message': data['message'],
            'dm_id': dm.id
        }
        task_id = task_manager.add_task('dm_to_user', task_data)
        
        return jsonify({
            'success': True,
            'message': 'Send DM task created',
            'task_id': task_id,
            'dm': dm.to_dict()
        })

    except Exception as e:
        logger.error(f"Error creating send DM task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/comment-post', methods=['POST'])
def comment_post():
    """Comment on an Instagram post"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'target_username', 'comment']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Create comment record in database
        comment = PostComment(
            sender_username=data['username'],
            target_username=data['target_username'],
            comment=data['comment'],
            device_id=data['device_id']
        )
        db.session.add(comment)
        db.session.commit()

        # Create comment task
        task_data = {
            'device_id': data['device_id'],
            'username': data['username'],
            'target_username': data['target_username'],
            'comment': data['comment'],
            'comment_id': comment.id
        }
        task_id = task_manager.add_task('comment_post', task_data)
        
        return jsonify({
            'success': True,
            'message': 'Comment post task created',
            'task_id': task_id,
            'comment': comment.to_dict()
        })

    except Exception as e:
        logger.error(f"Error creating comment post task: {str(e)}")
        return jsonify({'error': str(e)}), 500

def _is_valid_url(url):
    """Check if string is a valid URL"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def _validate_media_path(path, media_type='video'):
    """Validate if path is either a valid local file or URL
    Args:
        path (str): File path or URL
        media_type (str): Type of media ('video' or 'photo')
    Returns:
        tuple: (is_valid, error_message)
    """
    try:
        # Check if it's a URL
        if path.startswith(('http://', 'https://')):
            return True, None
        
        # If not URL, check local file
        if not os.path.exists(path):
            return False, f"{media_type.capitalize()} file not found at specified path"
        
        return True, None
    except Exception as e:
        return False, f"Error validating {media_type} path: {str(e)}"

@bp.route('/instagram/actions/post-reel', methods=['POST'])
def post_reel():
    """Post a reel on Instagram"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'video_path', 'caption']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Validate video path or URL
        is_valid, error_message = _validate_media_path(data['video_path'], 'video')
        if not is_valid:
            return jsonify({'error': error_message}), 400

        # Create post reel task
        task_data = {
            'device_id': data['device_id'],
            'username': data['username'],
            'video_path': data['video_path'],
            'caption': data['caption'],
            'music_query': data.get('music_query')  # Optional music
        }
        
        task_id = task_manager.add_task('post_reel', task_data)
        
        return jsonify({
            'success': True,
            'message': 'Post reel task created',
            'task_id': task_id,
            'task_details': {
                'username': data['username'],
                'device_id': data['device_id'],
                'video_path': data['video_path'],
                'caption': data['caption'],
                'music_query': data.get('music_query')
            }
        })

    except Exception as e:
        logger.error(f"Error creating post reel task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/post-photo', methods=['POST'])
def post_photo():
    """Post a photo on Instagram"""
    try:
        task_manager = get_task_manager()
        if not task_manager:
            return jsonify({'error': 'Instagram task manager not available'}), 500

        data = request.get_json()
        required_fields = ['device_id', 'username', 'photo_path', 'caption']
        if not all(field in data for field in required_fields):
            return jsonify({'error': f'Missing required fields: {", ".join(required_fields)}'}), 400

        # Validate photo path or URL
        is_valid, error_message = _validate_media_path(data['photo_path'], 'photo')
        if not is_valid:
            return jsonify({'error': error_message}), 400

        # Create post photo task
        task_data = {
            'device_id': data['device_id'],
            'username': data['username'],
            'photo_path': data['photo_path'],
            'caption': data['caption'],
            'music_query': data.get('music_query')  # Optional music
        }
        
        task_id = task_manager.add_task('post_photo', task_data)
        
        return jsonify({
            'success': True,
            'message': 'Post photo task created',
            'task_id': task_id,
            'task_details': {
                'username': data['username'],
                'device_id': data['device_id'],
                'photo_path': data['photo_path'],
                'caption': data['caption'],
                'music_query': data.get('music_query')
            }
        })

    except Exception as e:
        logger.error(f"Error creating post photo task: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/instagram/actions/upload-media', methods=['POST'])
def upload_media():
    """Upload media file for posting"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Get media type from request
        media_type = request.form.get('type', 'photo')  # 'photo' or 'reel'
        if media_type not in ['photo', 'reel']:
            return jsonify({'error': 'Invalid media type'}), 400

        # Create directory for uploads if it doesn't exist
        app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(app_root, 'uploads', media_type + 's')
        os.makedirs(upload_dir, exist_ok=True)

        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{secure_filename(file.filename)}"
        filepath = os.path.join(upload_dir, filename)

        # Save the file
        file.save(filepath)

        return jsonify({
            'success': True,
            'message': f'{media_type.capitalize()} uploaded successfully',
            'file_path': filepath
        })

    except Exception as e:
        logger.error(f"Error uploading media: {str(e)}")
        return jsonify({'error': str(e)}), 500