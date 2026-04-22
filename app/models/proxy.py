from app.extensions import db
from datetime import datetime


class Proxy(db.Model):
    __tablename__ = "proxies"

    id = db.Column(db.Integer, primary_key=True)
    proxy_id = db.Column(db.String(50), unique=True, nullable=False)   # proxy6.net ID
    host = db.Column(db.String(100), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    user = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(100), nullable=False)
    proxy_type = db.Column(db.String(10), default="http")              # http | socks5
    country = db.Column(db.String(10), default="")
    is_active = db.Column(db.Boolean, default=True)                    # proxy6 "active" flag
    status = db.Column(db.String(20), default="available")             # available | in_use
    assigned_to = db.Column(db.String(100))                            # task_id when in_use
    expires_at = db.Column(db.DateTime)
    last_used = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "proxy_id": self.proxy_id,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "proxy_type": self.proxy_type,
            "country": self.country,
            "is_active": self.is_active,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
