"""TikTok REST API endpoints.

All routes are prefixed with /api/tiktok/ via the blueprint registration
in app/api/__init__.py.
"""
import logging
from datetime import datetime

from flask import jsonify, request

from app.extensions import db
from app.models.device import Device
from app.models.tiktok_account import TikTokAccount
from app.utils.tiktok_task_manager import get_tiktok_task_manager

logger = logging.getLogger(__name__)


def _tm():
    tm = get_tiktok_task_manager()
    if tm is None:
        raise RuntimeError('TikTok task manager not available')
    return tm


def _require_fields(data: dict, fields: list) -> str | None:
    missing = [f for f in fields if f not in data]
    return f'Missing required fields: {", ".join(missing)}' if missing else None


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

def add_tiktok_account():
    """POST /tiktok/accounts

    Body: {username, password, device_id, email?, email_password?}
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['username', 'password', 'device_id'])
        if err:
            return jsonify({'error': err}), 400

        if TikTokAccount.query.filter_by(username=data['username']).first():
            return jsonify({'error': 'Account already exists'}), 409

        device = Device.query.filter_by(device_id=data['device_id']).first()
        if not device:
            return jsonify({'error': f'Device {data["device_id"]} not found'}), 404

        account = TikTokAccount(
            username=data['username'],
            password=data['password'],
            device_id=data['device_id'],
            email=data.get('email'),
            email_password=data.get('email_password'),
        )
        db.session.add(account)
        db.session.commit()
        return jsonify({'success': True, 'account': account.to_dict()}), 201

    except Exception as e:
        logger.error(f'add_tiktok_account: {e}')
        return jsonify({'error': str(e)}), 500


def update_tiktok_account(username: str):
    """PUT /tiktok/accounts/<username>

    Updates collection config fields. Accepts any subset of:
    targets, start_time, stop_time, daily_follow_limit, daily_like_limit,
    daily_comment_limit, daily_visit_limit, daily_story_like_limit,
    unfollow_limit, min_followers, max_followers, min_following, max_following,
    min_posts, max_posts, language_code, gender, comment_texts
    """
    try:
        account = TikTokAccount.query.filter_by(username=username).first()
        if not account:
            return jsonify({'error': 'Account not found'}), 404

        data = request.get_json() or {}
        config_fields = [
            'targets', 'start_time', 'stop_time',
            'daily_follow_limit', 'daily_like_limit', 'daily_comment_limit',
            'daily_visit_limit', 'daily_story_like_limit', 'unfollow_limit',
            'min_followers', 'max_followers', 'min_following', 'max_following',
            'min_posts', 'max_posts', 'language_code', 'gender', 'comment_texts',
        ]
        for field in config_fields:
            if field in data:
                setattr(account, field, data[field])

        account.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'account': account.to_dict()})

    except Exception as e:
        logger.error(f'update_tiktok_account: {e}')
        return jsonify({'error': str(e)}), 500


def list_tiktok_accounts():
    """GET /tiktok/accounts/list

    Query params: device_id, username, login_status
    """
    try:
        q = TikTokAccount.query
        if device_id := request.args.get('device_id'):
            q = q.filter_by(device_id=device_id)
        if username := request.args.get('username'):
            q = q.filter(TikTokAccount.username.ilike(f'%{username}%'))
        if login_status := request.args.get('login_status'):
            q = q.filter_by(login_status=login_status.lower() == 'true')

        accounts = q.all()
        return jsonify({'success': True, 'total': len(accounts),
                        'accounts': [a.to_dict() for a in accounts]})

    except Exception as e:
        logger.error(f'list_tiktok_accounts: {e}')
        return jsonify({'error': str(e)}), 500


def delete_tiktok_account(username: str):
    """DELETE /tiktok/accounts/<username>"""
    try:
        account = TikTokAccount.query.filter_by(username=username).first()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        db.session.delete(account)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Account {username} deleted'})

    except Exception as e:
        logger.error(f'delete_tiktok_account: {e}')
        return jsonify({'error': str(e)}), 500


def bulk_delete_tiktok_accounts():
    """POST /tiktok/accounts/bulk-delete  body: {"usernames": [...]}"""
    try:
        data = request.get_json() or {}
        usernames = data.get('usernames', [])
        if not usernames:
            return jsonify({'error': 'usernames list is required'}), 400

        deleted, not_found = [], []
        for uname in usernames:
            account = TikTokAccount.query.filter_by(username=uname).first()
            if account:
                db.session.delete(account)
                deleted.append(uname)
            else:
                not_found.append(uname)

        db.session.commit()
        return jsonify({'success': True, 'deleted': deleted, 'not_found': not_found})

    except Exception as e:
        logger.error(f'bulk_delete_tiktok_accounts: {e}')
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Task status
# ---------------------------------------------------------------------------

def get_tiktok_task_status(task_id: str):
    """GET /tiktok/tasks/<task_id>"""
    try:
        status = _tm().get_task_status(task_id)
        if not status:
            return jsonify({'error': 'Task not found'}), 404
        return jsonify({'success': True, 'task': status})
    except Exception as e:
        logger.error(f'get_tiktok_task_status: {e}')
        return jsonify({'error': str(e)}), 500


def get_all_tiktok_tasks():
    """GET /tiktok/tasks"""
    try:
        return jsonify({'success': True, 'tasks': _tm().get_all_tasks()})
    except Exception as e:
        logger.error(f'get_all_tiktok_tasks: {e}')
        return jsonify({'error': str(e)}), 500


def stop_tiktok_task(task_id: str):
    """POST /tiktok/tasks/<task_id>/stop — stops a running run_collection task."""
    try:
        stopped = _tm().stop_task(task_id)
        if not stopped:
            return jsonify({'error': 'Task not found or not running'}), 404
        return jsonify({'success': True, 'message': f'Stop signal sent to task {task_id}'})
    except Exception as e:
        logger.error(f'stop_tiktok_task: {e}')
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Single-target actions
# ---------------------------------------------------------------------------

def tiktok_follow():
    """POST /tiktok/actions/follow

    Body: {device_id, username, target_username}
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['device_id', 'username', 'target_username'])
        if err:
            return jsonify({'error': err}), 400

        task_id = _tm().add_task('follow', data)
        return jsonify({'success': True, 'message': 'Follow task created', 'task_id': task_id})

    except Exception as e:
        logger.error(f'tiktok_follow: {e}')
        return jsonify({'error': str(e)}), 500


def tiktok_like_posts():
    """POST /tiktok/actions/like-posts

    Body: {device_id, username, target_username, count?=3}
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['device_id', 'username', 'target_username'])
        if err:
            return jsonify({'error': err}), 400

        data.setdefault('count', 3)
        task_id = _tm().add_task('like_posts', data)
        return jsonify({'success': True, 'message': 'Like posts task created', 'task_id': task_id})

    except Exception as e:
        logger.error(f'tiktok_like_posts: {e}')
        return jsonify({'error': str(e)}), 500


def tiktok_view_profile():
    """POST /tiktok/actions/view-profile

    Body: {device_id, username, target_username}
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['device_id', 'username', 'target_username'])
        if err:
            return jsonify({'error': err}), 400

        task_id = _tm().add_task('view_profile', data)
        return jsonify({'success': True, 'message': 'View profile task created', 'task_id': task_id})

    except Exception as e:
        logger.error(f'tiktok_view_profile: {e}')
        return jsonify({'error': str(e)}), 500


def tiktok_comment():
    """POST /tiktok/actions/comment

    Body: {device_id, username, target_username, comment}
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['device_id', 'username', 'target_username', 'comment'])
        if err:
            return jsonify({'error': err}), 400

        task_id = _tm().add_task('comment', data)
        return jsonify({'success': True, 'message': 'Comment task created', 'task_id': task_id})

    except Exception as e:
        logger.error(f'tiktok_comment: {e}')
        return jsonify({'error': str(e)}), 500


def tiktok_like_story():
    """POST /tiktok/actions/like-story

    Body: {device_id, username, target_username}
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['device_id', 'username', 'target_username'])
        if err:
            return jsonify({'error': err}), 400

        task_id = _tm().add_task('like_story', data)
        return jsonify({'success': True, 'message': 'Like story task created', 'task_id': task_id})

    except Exception as e:
        logger.error(f'tiktok_like_story: {e}')
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Mass collection  (fetches followers of target accounts via API, filters, acts)
# ---------------------------------------------------------------------------

def tiktok_run_collection():
    """POST /tiktok/actions/run-collection

    Starts the full mass-action collection loop for an account.
    Fetches followers of configured target accounts, filters by quality
    criteria, then performs a randomised mix of actions up to daily limits.

    Body (required):
      device_id, username

    Body (optional — override account's saved config):
      targets          comma/newline-separated competitor usernames to scrape
      start_time       "HH:MM"
      stop_time        "HH:MM"
      daily_follow_limit
      daily_like_limit
      daily_comment_limit
      daily_visit_limit
      daily_story_like_limit
      unfollow_limit
      min_followers / max_followers
      min_following / max_following
      min_posts / max_posts
      language_code    e.g. "en,tr"
      gender           male | female | both
      comment_texts    newline-separated comment pool
    """
    try:
        data = request.get_json() or {}
        err = _require_fields(data, ['device_id', 'username'])
        if err:
            return jsonify({'error': err}), 400

        username = data['username']

        # Load saved config from DB, then let request body override
        account = TikTokAccount.query.filter_by(username=username).first()
        if not account:
            return jsonify({'error': f'TikTok account {username} not found'}), 404

        # Build config: start with account's saved values, override with request fields
        config = account.to_dict()['collection_config'].copy()
        config['account_id'] = account.id
        overridable = [
            'targets', 'start_time', 'stop_time',
            'daily_follow_limit', 'daily_like_limit', 'daily_comment_limit',
            'daily_visit_limit', 'daily_story_like_limit', 'unfollow_limit',
            'min_followers', 'max_followers', 'min_following', 'max_following',
            'min_posts', 'max_posts', 'language_code', 'gender', 'comment_texts',
        ]
        for field in overridable:
            if field in data:
                config[field] = data[field]

        if not config.get('targets', '').strip():
            return jsonify({'error': 'No targets configured. Set targets on the account first.'}), 400

        params = {'device_id': data['device_id'], 'username': username, **config}
        task_id = _tm().add_task('run_collection', params)

        return jsonify({
            'success': True,
            'message': f'Collection started for {username}',
            'task_id': task_id,
            'config': config,
        })

    except Exception as e:
        logger.error(f'tiktok_run_collection: {e}')
        return jsonify({'error': str(e)}), 500
