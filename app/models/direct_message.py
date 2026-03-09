from datetime import datetime
from app.extensions import db

class DirectMessage(db.Model):
    __tablename__ = 'direct_messages'

    id = db.Column(db.Integer, primary_key=True)
    sender_username = db.Column(
        db.String(80), 
        db.ForeignKey('instagram_accounts.username', ondelete="CASCADE"), 
        nullable=False
    )
    target_username = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
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
    sender = db.relationship(
        'InstagramAccount', 
        backref=db.backref('sent_messages', lazy=True, cascade="all, delete-orphan")
    )
    device = db.relationship(
        'Device', 
        backref=db.backref('direct_messages', lazy=True, cascade="all, delete-orphan")
    )

    def __init__(self, sender_username, target_username, message, device_id, status='pending'):
        self.sender_username = sender_username
        self.target_username = target_username
        self.message = message
        self.device_id = device_id
        self.status = status

    def to_dict(self):
        return {
            'id': self.id,
            'sender_username': self.sender_username,
            'target_username': self.target_username,
            'message': self.message,
            'device_id': self.device_id,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

    def __repr__(self):
        return f'<DirectMessage {self.id}: {self.sender_username} -> {self.target_username}>' 