from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash # Keep imports clean
from apps.utils import decrypt_message
from zoneinfo import ZoneInfo
from sqlalchemy.orm import relationship

db = SQLAlchemy()

ist_now = lambda: datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)

# Models
# ==============================================================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(120), unique=True, nullable=False)   # add unique=True
    password_hash = db.Column(db.String(200), nullable=False)
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # === NEW COLUMNS FOR PROFILE ===
    image_url = db.Column(db.String(512), nullable=True) # Public URL to the profile image
    description = db.Column(db.String(500), nullable=True) # User's brief description/bio
    # ===============================
    birthday = db.Column(db.Date, nullable=True)
    
    last_seen = db.Column(db.DateTime, nullable=True)


        
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'verified': self.verified,
            'image_url': self.image_url,
            'description': self.description,
            'birthday': self.birthday.isoformat() if self.birthday else None,
            'created_at': self.created_at.isoformat()
        }
    
class OTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    code = db.Column(db.String(6), nullable=False)  # store plain for simplicity
    # expires_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, default=ist_now)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    content = db.Column(db.String(512), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # === NEW COLUMNS FOR EDIT/DELETE ===
    is_edited = db.Column(db.Boolean, default=False) # Tracks if the message was edited
    is_deleted_for_everyone = db.Column(db.Boolean, default=False) # Tracks Delete For Everyone
    
    # Tracks Delete For Me: If true, the message should be filtered for that specific user's role
    is_deleted_for_sender = db.Column(db.Boolean, default=False)
    is_deleted_for_recipient = db.Column(db.Boolean, default=False)
    # ===================================
    
    media_url = db.Column(db.String(512), nullable=True) # URL from Cloudinary
    media_type = db.Column(db.String(50), nullable=True) # e.g., 'image', 'video', 'pdf', 'raw'
    
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='received_messages')
    
    def to_dict(self):
        decrypted_content = decrypt_message(self.content)
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'receiver_id': self.receiver_id,
            # 'content': self.content,
            'content': decrypted_content,
            'timestamp': self.timestamp.isoformat(),
            'is_edited': self.is_edited,
            'is_deleted_for_everyone': self.is_deleted_for_everyone,
            'media_url': self.media_url,
            'media_type': self.media_type,
            # Note: The 'is_deleted_for_sender/recipient' flags are not sent directly, 
            # as the server's message fetching logic must use them to filter messages.
        }

    # New requirement: Model for storing which users a user has 'added' to their chat list
class UserChatList(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=False)
    other_user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=False)
    # True if 'user_id' has blocked 'other_user_id'
    is_blocked = db.Column(db.Boolean, default=False)
    pin_priority = db.Column(db.Integer, default=0)
    is_favorite = db.Column(db.Boolean, default=False)

    # Ensures a user cannot add the same 'other_user' more than once
    __table_args__ = (db.UniqueConstraint('user_id', 'other_user_id', name='_user_other_uc'),)
    
    
class FriendRequest(db.Model):
    """Represents a pending chat request between two users."""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    # ADD THESE RELATIONSHIPS
    sender = db.relationship('User', foreign_keys=[sender_id], backref=db.backref('sent_requests', passive_deletes=True))
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref=db.backref('received_requests', passive_deletes=True))

    __table_args__ = (
        db.UniqueConstraint('sender_id', 'receiver_id', name='_unique_friend_request'),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            # "sender_name": self.sender.name, # Assuming a 'sender' relationship exists
            "timestamp": self.timestamp.isoformat(),
        }
        
        
class Notification(db.Model):
    __tablename__ = 'notification'

    id = db.Column(db.Integer, primary_key=True)
    
    # The user who is RECEIVING this notification
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=False)
    
    # Type of notification (e.g., 'new_user_verified', 'request_response_accept', 'request_response_reject')
    # Use a descriptive type so the frontend knows how to render it
    type = db.Column(db.String(50), nullable=False)
    
    # Message content (e.g., "John just joined!")
    content = db.Column(db.String(255), nullable=False)
    
    # Optional: ID of the user who initiated the action (e.g., the user who sent the request or joined)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="SET NULL"), nullable=True) 
    
    # Optional: ID of the friend request this notification is related to (for responses)
    # This helps group a response notification with the original request, though we'll mainly use 'content' for simplicity.
    request_id = db.Column(db.Integer, db.ForeignKey('friend_request.id', ondelete="SET NULL"), nullable=True) 
    
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship to the receiving user
    receiver = db.relationship('User', foreign_keys=[user_id], backref='notifications')
    # Relationship to the actor user
    actor = db.relationship('User', foreign_keys=[actor_id], backref='actor_notifications')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'type': self.type,
            'content': self.content,
            'actor_id': self.actor_id,
            'actor_name': self.actor.name if self.actor else None,
            'request_id': self.request_id,
            'timestamp': self.timestamp.isoformat()
        }