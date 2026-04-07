from datetime import datetime
from app.extensions import db

class PostComment(db.Model):
    __tablename__ = 'post_comments'

    id = db.Column(db.Integer, primary_key=True)
    sender_username = db.Column(
        db.String(80), 
        db.ForeignKey('instagram_accounts.username', ondelete="CASCADE"), 
        nullable=False
    )
    target_username = db.Column(db.String(80), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    device_id = db.Column(
        db.String(120), 
        db.ForeignKey('devices.device_id', ondelete="CASCADE"), 
        nullable=False
    )
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sender = db.relationship('InstagramAccount', backref=db.backref('post_comments', cascade="all, delete-orphan"))
    device = db.relationship('Device', backref=db.backref('post_comments', cascade="all, delete-orphan"))

    def __init__(self, sender_username, target_username, comment, device_id, status='pending'):
        self.sender_username = sender_username
        self.target_username = target_username
        self.comment = comment
        self.device_id = device_id
        self.status = status

    def to_dict(self):
        return {
            'id': self.id,
            'sender_username': self.sender_username,
            'target_username': self.target_username,
            'comment': self.comment,
            'device_id': self.device_id,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

    def __repr__(self):
        return f'<PostComment {self.id}: {self.sender_username} -> {self.target_username}>' 