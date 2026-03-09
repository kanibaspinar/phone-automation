from datetime import datetime
from app.extensions import db
from sqlalchemy import func, Text
from sqlalchemy.types import TypeDecorator
import json

class JSONType(TypeDecorator):
    """Enables JSON storage by encoding and decoding on the fly."""
    impl = Text

    def process_bind_param(self, value, dialect):
        if value is None:
            return '{}'
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return {}
        return json.loads(value)

class Device(db.Model):
    __tablename__ = 'devices'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), unique=True, nullable=False)
    device_name = db.Column(db.String(128))
    status = db.Column(db.String(32), default='disconnected')
    assigned_to = db.Column(db.String(128))
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_initialized = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text)
    metrics = db.Column(JSONType, default=lambda: {})

    def __init__(self, device_id, device_name=None, status='disconnected', assigned_to=None, 
                 last_seen=None, is_initialized=False, error_message=None):
        self.device_id = device_id
        self.device_name = device_name or self.generate_device_name()
        self.status = status
        self.assigned_to = assigned_to
        self.last_seen = last_seen or datetime.utcnow()
        self.is_initialized = is_initialized
        self.error_message = error_message
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.metrics = {}

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device_name,
            'status': self.status,
            'assigned_to': self.assigned_to,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'is_initialized': self.is_initialized,
            'error_message': self.error_message,
            'assigned_accounts': [account.username for account in self.assigned_accounts] if self.assigned_accounts else [],
            'metrics': self.metrics
        }

    def __repr__(self):
        return f'<Device {self.device_id}>'

    def generate_device_name(self):
        """Generate a sequential device name using simple numbers"""
        try:
            # Get the highest device name that's a number
            highest_device = db.session.query(Device).filter(
                Device.device_name.isnot(None)
            ).order_by(db.desc(Device.id)).first()

            if highest_device and highest_device.device_name and highest_device.device_name.isdigit():
                next_number = int(highest_device.device_name) + 1
            else:
                next_number = 1

            return str(next_number)
        except Exception as e:
            # Fallback to timestamp if there's an error
            return datetime.utcnow().strftime('%Y%m%d%H%M%S') 