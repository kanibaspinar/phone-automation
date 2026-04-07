from flask import Blueprint

bp = Blueprint('api', __name__)

# Import routes after creating blueprint to avoid circular imports
from app.api import device_routes, instagram_routes, tiktok_routes

# ---------------------------------------------------------------------------
# Device routes
# ---------------------------------------------------------------------------
bp.add_url_rule('/devices/list', 'list_devices', device_routes.list_devices, methods=['GET'])
bp.add_url_rule('/devices/<device_id>', 'delete_device', device_routes.delete_device, methods=['DELETE'])
bp.add_url_rule('/devices/<device_id>/unassign', 'unassign_device', device_routes.unassign_device, methods=['POST'])
bp.add_url_rule('/devices/create', 'create_device', device_routes.create_device, methods=['POST'])
bp.add_url_rule('/devices/<device_id>', 'update_device', device_routes.update_device, methods=['PUT'])
bp.add_url_rule('/devices/assign', 'assign_device', device_routes.assign_device, methods=['POST'])
bp.add_url_rule('/devices/operations/bulk', 'bulk_operations', device_routes.bulk_operations, methods=['POST'])
bp.add_url_rule('/devices/user/<user_id>', 'get_user_devices', device_routes.get_user_devices, methods=['GET'])
bp.add_url_rule('/devices/free', 'get_free_devices', device_routes.get_free_devices, methods=['GET'])

# ---------------------------------------------------------------------------
# Instagram routes
# ---------------------------------------------------------------------------
bp.add_url_rule('/instagram/login', 'instagram_login', instagram_routes.instagram_login, methods=['POST'])
bp.add_url_rule('/instagram/logout', 'instagram_logout', instagram_routes.instagram_logout, methods=['POST'])
bp.add_url_rule('/instagram/tasks/<task_id>', 'get_task_status', instagram_routes.get_task_status, methods=['GET'])
bp.add_url_rule('/instagram/tasks', 'get_all_tasks', instagram_routes.get_all_tasks, methods=['GET'])
bp.add_url_rule('/instagram/accounts/list', 'list_instagram_accounts', instagram_routes.list_instagram_accounts, methods=['GET'])
bp.add_url_rule('/instagram/accounts/<username>', 'delete_instagram_account', instagram_routes.delete_instagram_account, methods=['DELETE'])
bp.add_url_rule('/instagram/accounts/bulk-delete', 'bulk_delete_instagram_accounts', instagram_routes.bulk_delete_instagram_accounts, methods=['POST'])
bp.add_url_rule('/instagram/accounts', 'add_instagram_account', instagram_routes.add_instagram_account, methods=['POST'])
bp.add_url_rule('/instagram/accounts/bulk', 'add_instagram_accounts_bulk', instagram_routes.add_instagram_accounts_bulk, methods=['POST'])
bp.add_url_rule('/instagram/actions/like-post', 'like_post', instagram_routes.like_post, methods=['POST'])
bp.add_url_rule('/instagram/actions/comment-story', 'comment_story', instagram_routes.comment_story, methods=['POST'])
bp.add_url_rule('/instagram/actions/follow', 'follow_user', instagram_routes.follow_user, methods=['POST'])
bp.add_url_rule('/instagram/actions/unfollow', 'unfollow_user', instagram_routes.unfollow_user, methods=['POST'])
bp.add_url_rule('/instagram/actions/view-story', 'view_story', instagram_routes.view_story, methods=['POST'])
bp.add_url_rule('/instagram/actions/like-story', 'like_story', instagram_routes.like_story, methods=['POST'])
bp.add_url_rule('/instagram/actions/send-dm', 'send_direct_message', instagram_routes.send_direct_message, methods=['POST'])
bp.add_url_rule('/instagram/actions/comment-post', 'comment_post', instagram_routes.comment_post, methods=['POST'])
bp.add_url_rule('/instagram/actions/post-reel', 'post_reel', instagram_routes.post_reel, methods=['POST'])
bp.add_url_rule('/instagram/actions/post-photo', 'post_photo', instagram_routes.post_photo, methods=['POST'])
bp.add_url_rule('/instagram/actions/upload-media', 'upload_media', instagram_routes.upload_media, methods=['POST'])

# ---------------------------------------------------------------------------
# TikTok routes
# ---------------------------------------------------------------------------
# Account management
bp.add_url_rule('/tiktok/accounts', 'add_tiktok_account', tiktok_routes.add_tiktok_account, methods=['POST'])
bp.add_url_rule('/tiktok/accounts/list', 'list_tiktok_accounts', tiktok_routes.list_tiktok_accounts, methods=['GET'])
bp.add_url_rule('/tiktok/accounts/<username>', 'update_tiktok_account', tiktok_routes.update_tiktok_account, methods=['PUT'])
bp.add_url_rule('/tiktok/accounts/<username>', 'delete_tiktok_account', tiktok_routes.delete_tiktok_account, methods=['DELETE'])
bp.add_url_rule('/tiktok/accounts/bulk-delete', 'bulk_delete_tiktok_accounts', tiktok_routes.bulk_delete_tiktok_accounts, methods=['POST'])
# Task status + stop
bp.add_url_rule('/tiktok/tasks', 'get_all_tiktok_tasks', tiktok_routes.get_all_tiktok_tasks, methods=['GET'])
bp.add_url_rule('/tiktok/tasks/<task_id>', 'get_tiktok_task_status', tiktok_routes.get_tiktok_task_status, methods=['GET'])
bp.add_url_rule('/tiktok/tasks/<task_id>/stop', 'stop_tiktok_task', tiktok_routes.stop_tiktok_task, methods=['POST'])
# Single-target actions
bp.add_url_rule('/tiktok/actions/follow', 'tiktok_follow', tiktok_routes.tiktok_follow, methods=['POST'])
bp.add_url_rule('/tiktok/actions/like-posts', 'tiktok_like_posts', tiktok_routes.tiktok_like_posts, methods=['POST'])
bp.add_url_rule('/tiktok/actions/view-profile', 'tiktok_view_profile', tiktok_routes.tiktok_view_profile, methods=['POST'])
bp.add_url_rule('/tiktok/actions/comment', 'tiktok_comment', tiktok_routes.tiktok_comment, methods=['POST'])
bp.add_url_rule('/tiktok/actions/like-story', 'tiktok_like_story', tiktok_routes.tiktok_like_story, methods=['POST'])
# Mass collection (fetches followers of targets via API, filters, acts)
bp.add_url_rule('/tiktok/actions/run-collection', 'tiktok_run_collection', tiktok_routes.tiktok_run_collection, methods=['POST'])
