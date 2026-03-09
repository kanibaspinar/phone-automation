from datetime import datetime
from flask import Blueprint

bp = Blueprint('filters', __name__)

@bp.app_template_filter('datetime')
def format_datetime(value):
    """Format a datetime string to a readable format"""
    if not value:
        return ''
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            dt = value
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return value 