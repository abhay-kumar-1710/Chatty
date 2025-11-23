import os
os.environ["EVENTLET_NO_GREENDNS"] = "yes"
os.environ["EVENTLET_HUB"] = "poll"
os.environ["EVENTLET_NO_IPV6"] = "1"

from flask import request
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_jwt_extended import decode_token
from datetime import datetime, date
from apps.models import db, Message, UserChatList, FriendRequest, User, Notification
from apps.utils import get_chat_room_name, decrypt_message, encrypt_message
from zoneinfo import ZoneInfo

socketio = SocketIO()
IST = ZoneInfo("Asia/Kolkata")
online_users = set()

# Global reference to the app (will be set during register_socket_handlers)
_app = None

def register_socket_handlers(app):
    """Registers the SocketIO handlers with the initialized app."""
    global _app
    _app = app  # ✅ Store the app reference globally
    
    @socketio.on('connect')
    def socket_connect(auth):
        """Authenticates the client connection using JWT."""
        token = None
        if auth and 'token' in auth:
            token = auth['token']
        else:
            token = request.args.get('token')

        if not token:
            print("Socket auth failed: No token provided")
            return False

        try:
            decoded = decode_token(token)
            user_id = int(decoded['sub'])
            
            join_room(f"user_{user_id}")
            online_users.add(user_id)

            socketio.emit("presence_update", {
                "user_id": user_id,
                "online": True,
                "last_seen": None
            }) 
            print(f"Socket connected for user {user_id}. Joined room user_{user_id}")
            
        except Exception as e:
            print(f"Socket auth failed: {e}")
            return False
    
    @socketio.on("disconnect")
    def handle_disconnect():
        try:
            token = request.args.get("token")
            if not token:
                return

            data = decode_token(token)
            user_id = int(data["sub"])

            if user_id in online_users:
                online_users.remove(user_id)

            # ✅ Use the stored _app reference instead of creating a new one
            with _app.app_context():
                user = User.query.get(user_id)
                
                if user:
                    user.last_seen = datetime.now(IST).replace(tzinfo=None)
                    db.session.commit()
                    print(f"✅ Updated last_seen for user {user_id}:", user.last_seen)

            socketio.emit(
                "presence_update",
                {
                    "user_id": user_id,
                    "online": False,
                    "last_seen": datetime.now(IST).isoformat(),
                },
                broadcast=True,
            )

        except Exception as e:
            print("❌ Disconnect error:", e)

    @socketio.on('join_chat')
    def on_join_chat(data):
        """Joins the specific chat room for two users."""
        token = data.get('token')
        other_id = data.get('other_id')
        
        if not (token and other_id):
            return

        try:
            my_id = int(decode_token(token)['sub'])
            other_id = int(other_id)
            
            if not UserChatList.query.filter_by(user_id=my_id, other_user_id=other_id).first():
                 print(f"user {my_id} tried to join chat with {other_id} but not in chat list.")
                 return

            a, b = sorted([my_id, other_id])
            room = f"chat_{a}_{b}"
            join_room(room)
            print(f"user {my_id} joined chat room {room}")
            
        except Exception as e:
            print(f"join_chat failed: {e}")
            
    @socketio.on("typing")
    def handle_typing(data):
        token = data.get("token")
        to_id = data.get("to_id")
        is_typing = data.get("is_typing")
        
        if not (token and to_id is not None):
            return
        
        try:
            my_id = int(decode_token(token)["sub"])
            to_id = int(to_id)
        except Exception as e:
            print("typing auth error:", e)
            return

        a, b = sorted([my_id, to_id])
        room = f"chat_{a}_{b}"
        socketio.emit("typing", {
            "from_id": my_id,
            "to_id": to_id,
            "is_typing": bool(is_typing)
        }, room=room)

    @socketio.on('send_message')
    def handle_send_message(data):
        """Receives a message, saves it, and broadcasts it."""
        token = data.get('token')
        to_id = data.get('to')
        content = data.get('content')
        media_url = data.get('media_url')
        media_type = data.get('media_type')
        
        if not (token and to_id and (content or media_url)):
            print(f"socket error: Missing required fields for send_message.")
            return

        try:
            my_id = int(decode_token(token)['sub'])
            to_id = int(to_id)
        except Exception as e:
            print(f"socket auth error on send_message: {e}")
            return

        if not UserChatList.query.filter_by(user_id=my_id, other_user_id=to_id).first():
            print(f"User {my_id} not allowed to send message to {to_id}.")
            return
        
        encrypted_content = encrypt_message(content) if content else ""

        with _app.app_context():
            new_message = Message(
                sender_id=my_id,
                receiver_id=to_id,
                content=encrypted_content,
                timestamp=datetime.utcnow(),
                media_url=media_url,
                media_type=media_type,
            )
            db.session.add(new_message)
            
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Failed to save message to DB: {e}")
                return
            
            msg_payload = {
                'id': new_message.id,
                'sender_id': my_id,
                'receiver_id': to_id,
                'content': content,
                'timestamp': new_message.timestamp.isoformat(),
                'media_url': media_url,
                'media_type': media_type
            }
            
            a, b = sorted([my_id, to_id])
            room = f"chat_{a}_{b}"
            
            socketio.emit("new_message", msg_payload, room=room)
            print(f"Message sent to room {room} and saved to DB.")

    @socketio.on('send_friend_request')
    def handle_send_friend_request(data):
        """Creates a friend request and notifies the receiver."""
        token = data.get('token')
        receiver_id = data.get('receiver_id')
        
        if not (token and receiver_id):
            return
        
        try:
            my_id = int(decode_token(token)['sub'])
            receiver_id = int(receiver_id)
        except Exception as e:
            print(f"socket auth error on send_friend_request: {e}")
            return
            
        if my_id == receiver_id:
            return

        with _app.app_context():
            existing_req = FriendRequest.query.filter(
                (FriendRequest.sender_id == my_id) & (FriendRequest.receiver_id == receiver_id) |
                (FriendRequest.sender_id == receiver_id) & (FriendRequest.receiver_id == my_id)
            ).first()
            
            if existing_req:
                print("Request already exists or is pending.")
                return

            if UserChatList.query.filter_by(user_id=my_id, other_user_id=receiver_id).first():
                print("Already added.")
                return

            sender_user = User.query.get(my_id)
            if not sender_user:
                return

            new_request = FriendRequest(sender_id=my_id, receiver_id=receiver_id)
            db.session.add(new_request)
            db.session.commit()
            
            payload = {
                "id": new_request.id,
                "sender_id": my_id,
                "sender_name": sender_user.name,
                "timestamp": new_request.timestamp.isoformat(),
                "type": "friend_request"
            }
            
            socketio.emit("notification", payload, room=f"user_{receiver_id}")
            socketio.emit("request_sent", {"receiver_id": receiver_id}, room=f"user_{my_id}")

    @socketio.on('respond_friend_request')
    def handle_respond_friend_request(data):
        """Handles accepting or rejecting a friend request."""
        token = data.get('token')
        request_id = data.get('request_id')
        action = data.get('action')
        
        if not (token and request_id and action):
            return
        
        try:
            my_id = int(decode_token(token)['sub'])
            request_id = int(request_id)
        except Exception as e:
            print(f"socket auth error on respond_friend_request: {e}")
            return
            
        with _app.app_context():
            request_obj = FriendRequest.query.get(request_id)
            
            if not request_obj or request_obj.receiver_id != my_id:
                print(f"Invalid request ID or user {my_id} is not the receiver.")
                return
            
            sender_id = request_obj.sender_id
            sender = User.query.get(sender_id)
            receiver = User.query.get(my_id)
            
            if action == 'accept':
                for user1_id, user2_id in [(my_id, sender_id), (sender_id, my_id)]:
                    if not UserChatList.query.filter_by(user_id=user1_id, other_user_id=user2_id).first():
                        item = UserChatList(user_id=user1_id, other_user_id=user2_id)
                        db.session.add(item)
                
                connection_payload = {
                    "user_id": my_id, 
                    "user_name": receiver.name,
                    "other_id": sender_id, 
                    "other_name": sender.name,
                    "type": "connection_success"
                }
            
            sender_content = f"{receiver.name} {action}ed your friend request."
            sender_notification = Notification(
                user_id=sender_id,
                type="request_response", 
                content=sender_content,
                actor_id=my_id,
                request_id=request_id
            )
            db.session.add(sender_notification)
            
            sender_response_payload = {
                "id": None, 
                "action": action,
                "sender_id": my_id, 
                "sender_name": receiver.name,
                "type": "request_response",
                "timestamp": datetime.now().isoformat()
            }
            
            receiver_content = f"You {action}ed {sender.name}'s friend request."
            receiver_notification = Notification(
                user_id=my_id,
                type="request_resolved", 
                content=receiver_content,
                actor_id=sender_id, 
                request_id=request_id
            )
            db.session.add(receiver_notification)

            receiver_response_payload = {
                "id": None, 
                "action": action,
                "sender_id": sender_id, 
                "sender_name": sender.name,
                "type": "request_resolved", 
                "timestamp": datetime.now().isoformat()
            }
            
            db.session.delete(request_obj)

            try:
                db.session.commit()
                
                sender_response_payload['id'] = sender_notification.id
                receiver_response_payload['id'] = receiver_notification.id
                
                if action == 'accept':
                    socketio.emit("chat_list_update", connection_payload, room=f"user_{sender_id}")
                    socketio.emit("chat_list_update", connection_payload, room=f"user_{my_id}")
                    
                socketio.emit("notification", sender_response_payload, room=f"user_{sender_id}")
                socketio.emit("notification", receiver_response_payload, room=f"user_{my_id}")

            except Exception as e:
                db.session.rollback()
                print(f"!!! CRITICAL DB ERROR in handle_respond_friend_request: {e}")
                return

    @socketio.on('edit_message')
    def handle_edit_message(data):
        try:
            token = data.get('token')
            if not token:
                return
            auth_user_id = int(decode_token(token)['sub'])
            
            message_id = data.get('message_id')
            new_content = data.get('new_content')
            
            encrypted_content = encrypt_message(new_content)
            
            with _app.app_context(): 
                message = Message.query.get(message_id)
                if not message or message.sender_id != auth_user_id:
                    print(f"Auth error: User {auth_user_id} tried to edit message {message_id}")
                    return

                message.content = encrypted_content
                message.is_edited = True
                db.session.commit()

                other_user_id = message.receiver_id if message.sender_id == auth_user_id else message.sender_id
                chat_room = get_chat_room_name(auth_user_id, other_user_id)
                
                payload = {
                    'message_id': message.id,
                    'new_content': new_content,
                    'is_edited': True,
                }
                socketio.emit('message_edited', payload, room=chat_room) 
            
        except Exception as e:
            print(f"ERROR during message edit: {e}")
            return

    @socketio.on('delete_message')
    def handle_delete_message(data):
        try:
            token = data.get('token')
            if not token:
                return
            auth_user_id = int(decode_token(token)['sub'])
            
            message_id = data.get('message_id')
            action = data.get('action')
                
            with _app.app_context():
                message = Message.query.get(message_id)
                if not message:
                    return

                if action == 'delete_for_everyone':
                    if message.sender_id == auth_user_id:
                        message.is_deleted_for_everyone = True
                        db.session.commit()
                        
                        other_user_id = message.receiver_id if message.sender_id == auth_user_id else message.sender_id
                        chat_room = get_chat_room_name(auth_user_id, other_user_id)
                        
                        payload = {
                            'message_id': message.id,
                            'action': 'delete_for_everyone', 
                        }
                        socketio.emit('message_deleted', payload, room=chat_room)
                    else:
                        print(f"Auth error: User {auth_user_id} tried to 'delete_for_everyone' message {message_id}")
                        return
                        
                elif action == 'delete_for_me':
                    if message.sender_id == auth_user_id:
                        message.is_deleted_for_sender = True
                    elif message.receiver_id == auth_user_id:
                        message.is_deleted_for_recipient = True
                    db.session.commit()
                    
                    payload = {
                        'message_id': message.id,
                        'action': 'delete_for_me',
                    }
                    emit('message_deleted', payload, room=f"user_{auth_user_id}")
                
        except Exception as e:
            print(f"ERROR during message delete: {e}")
            return

    @socketio.on("pin_chat")
    def handle_pin_chat(data):
        token = data.get("token")
        other_user_id = int(data.get("other_user_id"))
        should_pin = bool(data.get("pin", True))

        try:
            my_id = int(decode_token(token)['sub'])
        except Exception as e:
            print(f"pin_chat auth error: {e}")
            return

        with _app.app_context():
            entry = UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first()
            if not entry:
                return

            pins = (UserChatList.query
                    .filter_by(user_id=my_id)
                    .filter(UserChatList.pin_priority > 0)
                    .order_by(UserChatList.pin_priority.asc())
                    .all())

            if should_pin:
                if (entry.pin_priority or 0) > 0:
                    old_pri = entry.pin_priority or 0
                    for p in pins:
                        if p.id == entry.id:
                            continue
                        if (p.pin_priority or 0) < old_pri:
                            p.pin_priority = (p.pin_priority or 0) + 1
                    entry.pin_priority = 1
                else:
                    for p in pins:
                        p.pin_priority = min((p.pin_priority or 0) + 1, 3)
                    entry.pin_priority = 1

                overflow = (UserChatList.query
                            .filter_by(user_id=my_id)
                            .filter(UserChatList.pin_priority > 3)
                            .all())
                for p in overflow:
                    p.pin_priority = 0
            else:
                removed_pri = entry.pin_priority
                entry.pin_priority = 0
                if removed_pri > 0:
                    for p in pins:
                        if p.id != entry.id and p.pin_priority > removed_pri:
                            p.pin_priority -= 1

            db.session.commit()

            new_pins = (UserChatList.query
                .filter_by(user_id=my_id)
                .filter(UserChatList.pin_priority > 0)
                .with_entities(UserChatList.other_user_id, UserChatList.pin_priority)
                .order_by(UserChatList.pin_priority.asc())
                .all())

            payload = {
                "pins": [{"other_user_id": uid, "pin_priority": pri} for (uid, pri) in new_pins]
            }
            socketio.emit("chat_pins_updated", payload, room=f"user_{my_id}")

    @socketio.on("toggle_favorite")
    def handle_toggle_favorite(data):
        token = data.get("token")
        other_user_id = data.get("other_user_id")
        favorite = data.get("favorite")

        if not token or other_user_id is None:
            return

        try:
            my_id = int(decode_token(token)["sub"])
            other_user_id = int(other_user_id)
        except Exception as e:
            print(f"Socket auth error in toggle_favorite: {e}")
            return

        with _app.app_context():
            chat_entry = UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first()
            if chat_entry:
                chat_entry.is_favorite = favorite
                db.session.commit()

                socketio.emit(
                    "favorites_updated",
                    {"user_id": my_id, "favorites": [{"other_user_id": other_user_id, "is_favorite": favorite}]},
                    room=f"user_{my_id}"
                )

def notify_new_user(user):
    """Broadcasts a notification about a new verified user AND saves it."""
    payload = {
        "id": user.id, 
        "name": user.name, 
        "type": "new_user_verified",
        "timestamp": datetime.now().isoformat()
    }

    all_users = User.query.filter(User.id != user.id).all()
    for receiver in all_users:
        new_notification = Notification(
            user_id=receiver.id,
            type="new_user_verified",
            content=f"{user.name} just joined the app!",
            actor_id=user.id
        )
        db.session.add(new_notification)
    db.session.commit()

    for receiver in all_users:
        socketio.emit(
            "notification",
            payload,
            room=f"user_{receiver.id}"
        )
    
def check_and_send_birthday_notifications(app):
    """Checks for birthdays today and sends notifications to friends."""
    with app.app_context():
        today = date.today()
        
        birthday_users = User.query.filter(
            User.birthday != None,
            db.extract('month', User.birthday) == today.month,
            db.extract('day', User.birthday) == today.day
        ).all()
        
        if not birthday_users:
            print(f"[{datetime.now()}] No birthdays today.")
            return

        for bday_user in birthday_users:
            print(f"[{datetime.now()}] Happy Birthday to {bday_user.name} (ID: {bday_user.id})!")
            
            friends_to_notify_ids = [
                item.user_id 
                for item in UserChatList.query.filter_by(other_user_id=bday_user.id).all()
            ]
            
            for friend_id in friends_to_notify_ids:
                content = f"It's {bday_user.name}'s birthday today! Send a warm message 🎉"
                new_notification = Notification(
                    user_id=friend_id,
                    type="birthday_wish",
                    content=content,
                    actor_id=bday_user.id,
                    timestamp=datetime.utcnow() 
                )
                db.session.add(new_notification)
                db.session.flush()
                
                payload = {
                    "id": new_notification.id,
                    "sender_id": bday_user.id, 
                    "sender_name": bday_user.name,
                    "type": "birthday_wish", 
                    "content": content,
                    "timestamp": new_notification.timestamp.isoformat()
                }
                
                socketio.emit("notification", payload, room=f"user_{friend_id}")
                print(f"Sent birthday notification to user_{friend_id}")
        
        try:
            db.session.commit()
            print(f"[{datetime.now()}] Successfully saved and sent birthday notifications.")
        except Exception as e:
            db.session.rollback()
            print(f"[{datetime.now()}] ERROR saving birthday notifications: {e}")