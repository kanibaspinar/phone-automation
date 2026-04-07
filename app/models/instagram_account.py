from datetime import datetime
from app.extensions import db

class InstagramAccount(db.Model):
    __tablename__ = 'instagram_accounts'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120))
    device_id = db.Column(db.String(120), db.ForeignKey('devices.device_id', ondelete='SET NULL'), nullable=True)
    login_status = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(120))
    email_password = db.Column(db.String(120))
    imap_server = db.Column(db.String(120))
    imap_port = db.Column(db.Integer, default=993)
    last_login = db.Column(db.DateTime)
    last_logout = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Statistics
    total_likes = db.Column(db.Integer, default=0)
    total_comments = db.Column(db.Integer, default=0)
    total_follows = db.Column(db.Integer, default=0)
    total_unfollows = db.Column(db.Integer, default=0)
    total_stories_viewed = db.Column(db.Integer, default=0)
    total_story_likes = db.Column(db.Integer, default=0)
    total_dms = db.Column(db.Integer, default=0)
    
    # Daily statistics
    daily_likes = db.Column(db.Integer, default=0)
    daily_dms = db.Column(db.Integer, default=0)
    daily_comments = db.Column(db.Integer, default=0)
    daily_follows = db.Column(db.Integer, default=0)
    daily_unfollows = db.Column(db.Integer, default=0)
    daily_stories_viewed = db.Column(db.Integer, default=0)
    daily_story_likes = db.Column(db.Integer, default=0)
    last_daily_reset = db.Column(db.DateTime)

    # Relationship with Device model
    assigned_device = db.relationship('Device', backref=db.backref('assigned_accounts', lazy=True))

    def __init__(self, username, password, device_id=None, login_status=False,
                 email=None, email_password=None, imap_server=None, imap_port=993,
                 created_at=None, updated_at=None):
        self.username = username
        self.password = password
        self.device_id = device_id
        self.login_status = login_status
        self.email = email
        self.email_password = email_password
        self.imap_server = imap_server
        self.imap_port = imap_port
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = updated_at or datetime.utcnow()
        self.total_likes = 0
        self.total_comments = 0
        self.total_follows = 0
        self.total_unfollows = 0
        self.total_stories_viewed = 0
        self.total_story_likes = 0
        self.total_dms = 0
        self.daily_likes = 0
        self.daily_comments = 0
        self.daily_follows = 0
        self.daily_unfollows = 0
        self.daily_dms = 0
        self.daily_stories_viewed = 0
        self.daily_story_likes = 0
        self.last_daily_reset = datetime.utcnow()

    def update_stats(self, action_type):
        """Update account statistics based on action type"""
        now = datetime.utcnow()
        
        # Reset daily stats if last reset was not today
        if not self.last_daily_reset or self.last_daily_reset.date() < now.date():
            self.daily_likes = 0
            self.daily_comments = 0
            self.daily_follows = 0
            self.daily_unfollows = 0
            self.daily_dms = 0
            self.daily_stories_viewed = 0
            self.daily_story_likes = 0
            self.last_daily_reset = now

        # Update appropriate counter based on action type
        if action_type == 'like':
            self.total_likes += 1
            self.daily_likes += 1
        elif action_type == 'comment':
            self.total_comments += 1
            self.daily_comments += 1
        elif action_type == 'follow':
            self.total_follows += 1
            self.daily_follows += 1
        elif action_type == 'unfollow':
            self.total_unfollows += 1
            self.daily_unfollows += 1
        elif action_type == 'dm':
            self.total_dms += 1
            self.daily_dms += 1
        elif action_type == 'view_story':
            self.total_stories_viewed += 1
            self.daily_stories_viewed += 1
        elif action_type == 'like_story':
            self.total_story_likes += 1
            self.daily_story_likes += 1

        self.updated_at = now

    def get_daily_stats(self):
        """Get daily statistics"""
        return {
            'likes': self.daily_likes,
            'comments': self.daily_comments,
            'follows': self.daily_follows,
            'unfollows': self.daily_unfollows,
            'dms': self.daily_dms,
            'stories_viewed': self.daily_stories_viewed
        }

    def get_total_stats(self):
        """Get total statistics"""
        return {
            'likes': self.total_likes,
            'comments': self.total_comments,
            'follows': self.total_follows,
            'unfollows': self.total_unfollows,
            'dms': self.total_dms,
            'stories_viewed': self.total_stories_viewed
        }

    def format_last_login(self):
        """Format last login time for display"""
        if not self.last_login:
            return "Never"
        return self.last_login.strftime("%Y-%m-%d %H:%M:%S")

    def format_last_action(self):
        """Format last action time for display"""
        if not self.last_login:
            return "Never"
        return self.last_login.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'device_id': self.device_id,
            'login_status': self.login_status,
            'email': self.email,
            'imap_server': self.imap_server,
            'imap_port': self.imap_port,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'last_logout': self.last_logout.isoformat() if self.last_logout else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'stats': {
                'total': self.get_total_stats(),
                'daily': self.get_daily_stats(),
                'last_reset': self.last_daily_reset.isoformat() if self.last_daily_reset else None
            }
        }

    def __repr__(self):
        return f'<InstagramAccount {self.username}>' 