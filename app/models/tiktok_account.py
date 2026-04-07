from datetime import datetime
from app.extensions import db


class TikTokAccount(db.Model):
    __tablename__ = 'tiktok_accounts'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120))
    device_id = db.Column(
        db.String(120),
        db.ForeignKey('devices.device_id', ondelete='SET NULL'),
        nullable=True,
    )
    login_status = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(120))
    email_password = db.Column(db.String(120))
    last_login = db.Column(db.DateTime)
    last_logout = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ---------------------------------------------------------------
    # Collection configuration (mirrors TiktokPro Account fields)
    # ---------------------------------------------------------------
    # Comma- or newline-separated competitor usernames to scrape followers from
    targets = db.Column(db.Text, default='')
    # Schedule window
    start_time = db.Column(db.String(5), default='09:00')   # HH:MM
    stop_time = db.Column(db.String(5), default='23:00')    # HH:MM
    # Daily action limits
    daily_follow_limit = db.Column(db.Integer, default=50)
    daily_like_limit = db.Column(db.Integer, default=100)
    daily_comment_limit = db.Column(db.Integer, default=20)
    daily_visit_limit = db.Column(db.Integer, default=100)
    daily_story_like_limit = db.Column(db.Integer, default=50)
    unfollow_limit = db.Column(db.Integer, default=30)
    # Follower quality filters
    min_followers = db.Column(db.Integer, nullable=True)
    max_followers = db.Column(db.Integer, nullable=True)
    min_following = db.Column(db.Integer, nullable=True)
    max_following = db.Column(db.Integer, nullable=True)
    min_posts = db.Column(db.Integer, nullable=True)
    max_posts = db.Column(db.Integer, nullable=True)
    language_code = db.Column(db.String(20), nullable=True)   # e.g. "en,tr"
    gender = db.Column(db.String(10), default='both')          # male/female/both
    # Newline-separated comment pool; random one is picked per comment action
    comment_texts = db.Column(db.Text, default='')

    # ---------------------------------------------------------------
    # Lifetime statistics
    # ---------------------------------------------------------------
    total_likes = db.Column(db.Integer, default=0)
    total_follows = db.Column(db.Integer, default=0)
    total_unfollows = db.Column(db.Integer, default=0)
    total_profile_views = db.Column(db.Integer, default=0)
    total_story_views = db.Column(db.Integer, default=0)
    total_story_likes = db.Column(db.Integer, default=0)
    total_comments = db.Column(db.Integer, default=0)

    # Daily statistics (reset each day)
    daily_likes = db.Column(db.Integer, default=0)
    daily_follows = db.Column(db.Integer, default=0)
    daily_unfollows = db.Column(db.Integer, default=0)
    daily_profile_views = db.Column(db.Integer, default=0)
    daily_story_views = db.Column(db.Integer, default=0)
    daily_story_likes = db.Column(db.Integer, default=0)
    daily_comments = db.Column(db.Integer, default=0)
    last_daily_reset = db.Column(db.DateTime)

    assigned_device = db.relationship(
        'Device',
        backref=db.backref('tiktok_accounts', lazy=True),
    )

    def __init__(self, username, password, device_id=None, login_status=False,
                 email=None, email_password=None, created_at=None, updated_at=None):
        self.username = username
        self.password = password
        self.device_id = device_id
        self.login_status = login_status
        self.email = email
        self.email_password = email_password
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = updated_at or datetime.utcnow()
        self.last_daily_reset = datetime.utcnow()

    def _maybe_reset_daily(self, now: datetime):
        if not self.last_daily_reset or self.last_daily_reset.date() < now.date():
            self.daily_likes = 0
            self.daily_follows = 0
            self.daily_unfollows = 0
            self.daily_profile_views = 0
            self.daily_story_views = 0
            self.daily_story_likes = 0
            self.daily_comments = 0
            self.last_daily_reset = now

    def update_stats(self, action_type: str):
        now = datetime.utcnow()
        self._maybe_reset_daily(now)

        mapping = {
            'like':         ('total_likes',         'daily_likes'),
            'follow':       ('total_follows',        'daily_follows'),
            'unfollow':     ('total_unfollows',      'daily_unfollows'),
            'profile_view': ('total_profile_views',  'daily_profile_views'),
            'story_view':   ('total_story_views',    'daily_story_views'),
            'story_like':   ('total_story_likes',    'daily_story_likes'),
            'comment':      ('total_comments',       'daily_comments'),
        }
        total_attr, daily_attr = mapping.get(action_type, (None, None))
        if total_attr:
            setattr(self, total_attr, getattr(self, total_attr) + 1)
            setattr(self, daily_attr, getattr(self, daily_attr) + 1)
        self.updated_at = now

    def get_daily_stats(self) -> dict:
        return {
            'follow':        self.daily_follows,
            'like':          self.daily_likes,
            'profile_view':  self.daily_profile_views,
            'story_view':    self.daily_story_views,
            'story_like':    self.daily_story_likes,
            'comment':       self.daily_comments,
            'unfollow':      self.daily_unfollows,
        }

    def get_total_stats(self) -> dict:
        return {
            'likes':          self.total_likes,
            'follows':        self.total_follows,
            'unfollows':      self.total_unfollows,
            'profile_views':  self.total_profile_views,
            'story_views':    self.total_story_views,
            'story_likes':    self.total_story_likes,
            'comments':       self.total_comments,
        }

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'username': self.username,
            'device_id': self.device_id,
            'login_status': self.login_status,
            'email': self.email,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'last_logout': self.last_logout.isoformat() if self.last_logout else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'collection_config': {
                'targets': self.targets,
                'start_time': self.start_time,
                'stop_time': self.stop_time,
                'daily_follow_limit': self.daily_follow_limit,
                'daily_like_limit': self.daily_like_limit,
                'daily_comment_limit': self.daily_comment_limit,
                'daily_visit_limit': self.daily_visit_limit,
                'daily_story_like_limit': self.daily_story_like_limit,
                'unfollow_limit': self.unfollow_limit,
                'min_followers': self.min_followers,
                'max_followers': self.max_followers,
                'min_following': self.min_following,
                'max_following': self.max_following,
                'min_posts': self.min_posts,
                'max_posts': self.max_posts,
                'language_code': self.language_code,
                'gender': self.gender,
            },
            'stats': {
                'total': self.get_total_stats(),
                'daily': self.get_daily_stats(),
                'last_reset': self.last_daily_reset.isoformat() if self.last_daily_reset else None,
            },
        }

    def __repr__(self):
        return f'<TikTokAccount {self.username}>'
