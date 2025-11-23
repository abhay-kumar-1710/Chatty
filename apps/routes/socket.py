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
_app = None

def register_socket_handlers(app):
    """Registers the SocketIO handlers with the initialized app."""
    global _app
    _app = app
    
    @socketio.on('connect')
    def socket_connect(auth):
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
            print(f"Socket connected for user {user_id}")
            
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

            with _app.app_context():
                user = User.query.get(user_id)
                if user:
                    user.last_seen = datetime.now(IST).replace(tzinfo=None)
                    db.session.commit()
                    print(f"Updated last_seen for user {user_id}")

            socketio.emit("presence_update", {
                "user_id": user_id,
                "online": False,
                "last_seen": datetime.now(IST).isoformat(),
            }, broadcast=True)

        except Exception as e:
            print("Disconnect error:", e)

    @socketio.on('join_chat')
    def on_join_chat(data):
        token = data.get('token')
        other_id = data.get('other_id')
        
        if not (token and other_id):
            return

        try:
            my_id = int(decode_token(token)['sub'])
            other_id = int(other_id)
            
            if not UserChatList.query.filter_by(user_id=my_id, other_user_id=other_id).first():
                return

            a, b = sorted([my_id, other_id])
            room = f"chat_{a}_{b}"
            join_room(room)
            
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
            print("typing error:", e)
            return

        a, b = sorted([my_id, to_id])
        room = f"chat_{a}_{b}"
        socketio.emit("typing", {"from_id": my_id, "to_id": to_id, "is_typing": bool(is_typing)}, room=room)

    @socketio.on('send_message')
    def handle_send_message(data):
        token = data.get('token')
        to_id = data.get('to')
        content = data.get('content')
        media_url = data.get('media_url')
        media_type = data.get('media_type')
        
        if not (token and to_id and (content or media_url)):
            return

        try:
            my_id = int(decode_token(token)['sub'])
            to_id = int(to_id)
        except Exception as e:
            print(f"send_message auth error: {e}")
            return

        if not UserChatList.query.filter_by(user_id=my_id, other_user_id=to_id).first():
            return
        
        encrypted_content = encrypt_message(content) if content else ""

        with _app.app_context():
            try:
                new_message = Message(
                    sender_id=my_id,
                    receiver_id=to_id,
                    content=encrypted_content,
                    timestamp=datetime.utcnow(),
                    media_url=media_url,
                    media_type=media_type,
                )
                db.session.add(new_message)
                db.session.commit()
                
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
                socketio.emit("new_message", msg_payload, room=f"chat_{a}_{b}")
            except Exception as e:
                db.session.rollback()
                print(f"Message save error: {e}")

    @socketio.on('send_friend_request')
    def handle_send_friend_request(data):
        token = data.get('token')
        receiver_id = data.get('receiver_id')
        
        if not (token and receiver_id):
            return
        
        try:
            my_id = int(decode_token(token)['sub'])
            receiver_id = int(receiver_id)
        except Exception as e:
            print(f"friend_request auth error: {e}")
            return
            
        if my_id == receiver_id:
            return

        with _app.app_context():
            try:
                existing = FriendRequest.query.filter(
                    (FriendRequest.sender_id == my_id) & (FriendRequest.receiver_id == receiver_id) |
                    (FriendRequest.sender_id == receiver_id) & (FriendRequest.receiver_id == my_id)
                ).first()
                
                if existing or UserChatList.query.filter_by(user_id=my_id, other_user_id=receiver_id).first():
                    return

                sender_user = User.query.get(my_id)
                if not sender_user:
                    return

                new_req = FriendRequest(sender_id=my_id, receiver_id=receiver_id)
                db.session.add(new_req)
                db.session.commit()
                
                socketio.emit("notification", {
                    "id": new_req.id,
                    "sender_id": my_id,
                    "sender_name": sender_user.name,
                    "timestamp": new_req.timestamp.isoformat(),
                    "type": "friend_request"
                }, room=f"user_{receiver_id}")
            except Exception as e:
                db.session.rollback()
                print(f"Friend request error: {e}")

    @socketio.on('respond_friend_request')
    def handle_respond_friend_request(data):
        token = data.get('token')
        request_id = data.get('request_id')
        action = data.get('action')
        
        if not (token and request_id and action):
            return
        
        try:
            my_id = int(decode_token(token)['sub'])
            request_id = int(request_id)
        except Exception as e:
            print(f"respond error: {e}")
            return
            
        with _app.app_context():
            try:
                req_obj = FriendRequest.query.get(request_id)
                if not req_obj or req_obj.receiver_id != my_id:
                    return
                
                sender_id = req_obj.sender_id
                sender = User.query.get(sender_id)
                receiver = User.query.get(my_id)
                
                if action == 'accept':
                    for u1, u2 in [(my_id, sender_id), (sender_id, my_id)]:
                        if not UserChatList.query.filter_by(user_id=u1, other_user_id=u2).first():
                            db.session.add(UserChatList(user_id=u1, other_user_id=u2))
                
                sender_notif = Notification(
                    user_id=sender_id,
                    type="request_response",
                    content=f"{receiver.name} {action}ed your friend request.",
                    actor_id=my_id,
                    request_id=request_id
                )
                receiver_notif = Notification(
                    user_id=my_id,
                    type="request_resolved",
                    content=f"You {action}ed {sender.name}'s friend request.",
                    actor_id=sender_id,
                    request_id=request_id
                )
                db.session.add(sender_notif)
                db.session.add(receiver_notif)
                db.session.delete(req_obj)
                db.session.commit()
                
                if action == 'accept':
                    socketio.emit("chat_list_update", {
                        "user_id": my_id, "user_name": receiver.name,
                        "other_id": sender_id, "other_name": sender.name,
                        "type": "connection_success"
                    }, room=f"user_{sender_id}")
                    socketio.emit("chat_list_update", {
                        "user_id": my_id, "user_name": receiver.name,
                        "other_id": sender_id, "other_name": sender.name,
                        "type": "connection_success"
                    }, room=f"user_{my_id}")
                
                socketio.emit("notification", {
                    "id": sender_notif.id, "action": action,
                    "sender_id": my_id, "sender_name": receiver.name,
                    "type": "request_response",
                    "timestamp": datetime.now().isoformat()
                }, room=f"user_{sender_id}")
                
                socketio.emit("notification", {
                    "id": receiver_notif.id, "action": action,
                    "sender_id": sender_id, "sender_name": sender.name,
                    "type": "request_resolved",
                    "timestamp": datetime.now().isoformat()
                }, room=f"user_{my_id}")
                    
            except Exception as e:
                db.session.rollback()
                print(f"Respond error: {e}")

    @socketio.on('edit_message')
    def handle_edit_message(data):
        try:
            token = data.get('token')
            if not token:
                return
            auth_user_id = int(decode_token(token)['sub'])
            message_id = data.get('message_id')
            new_content = data.get('new_content')
            
            with _app.app_context():
                msg = Message.query.get(message_id)
                if not msg or msg.sender_id != auth_user_id:
                    return

                msg.content = encrypt_message(new_content)
                msg.is_edited = True
                db.session.commit()

                other_id = msg.receiver_id if msg.sender_id == auth_user_id else msg.sender_id
                socketio.emit('message_edited', {
                    'message_id': msg.id,
                    'new_content': new_content,
                    'is_edited': True,
                }, room=get_chat_room_name(auth_user_id, other_id))
        except Exception as e:
            print(f"Edit error: {e}")

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
                msg = Message.query.get(message_id)
                if not msg:
                    return

                if action == 'delete_for_everyone' and msg.sender_id == auth_user_id:
                    msg.is_deleted_for_everyone = True
                    db.session.commit()
                    
                    other_id = msg.receiver_id if msg.sender_id == auth_user_id else msg.sender_id
                    socketio.emit('message_deleted', {
                        'message_id': msg.id,
                        'action': 'delete_for_everyone',
                    }, room=get_chat_room_name(auth_user_id, other_id))
                        
                elif action == 'delete_for_me':
                    if msg.sender_id == auth_user_id:
                        msg.is_deleted_for_sender = True
                    elif msg.receiver_id == auth_user_id:
                        msg.is_deleted_for_recipient = True
                    db.session.commit()
                    
                    emit('message_deleted', {
                        'message_id': msg.id,
                        'action': 'delete_for_me',
                    }, room=f"user_{auth_user_id}")
        except Exception as e:
            print(f"Delete error: {e}")

    @socketio.on("pin_chat")
    def handle_pin_chat(data):
        token = data.get("token")
        other_user_id = int(data.get("other_user_id"))
        should_pin = bool(data.get("pin", True))

        try:
            my_id = int(decode_token(token)['sub'])
        except Exception as e:
            print(f"pin error: {e}")
            return

        with _app.app_context():
            try:
                entry = UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first()
                if not entry:
                    return

                pins = UserChatList.query.filter_by(user_id=my_id).filter(UserChatList.pin_priority > 0).order_by(UserChatList.pin_priority.asc()).all()

                if should_pin:
                    if (entry.pin_priority or 0) > 0:
                        old_pri = entry.pin_priority or 0
                        for p in pins:
                            if p.id != entry.id and (p.pin_priority or 0) < old_pri:
                                p.pin_priority = (p.pin_priority or 0) + 1
                        entry.pin_priority = 1
                    else:
                        for p in pins:
                            p.pin_priority = min((p.pin_priority or 0) + 1, 3)
                        entry.pin_priority = 1

                    overflow = UserChatList.query.filter_by(user_id=my_id).filter(UserChatList.pin_priority > 3).all()
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
                
                new_pins = UserChatList.query.filter_by(user_id=my_id).filter(UserChatList.pin_priority > 0).with_entities(UserChatList.other_user_id, UserChatList.pin_priority).order_by(UserChatList.pin_priority.asc()).all()
                socketio.emit("chat_pins_updated", {"pins": [{"other_user_id": uid, "pin_priority": pri} for (uid, pri) in new_pins]}, room=f"user_{my_id}")
            except Exception as e:
                db.session.rollback()
                print(f"Pin error: {e}")

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
            print(f"favorite error: {e}")
            return

        with _app.app_context():
            try:
                chat_entry = UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first()
                if chat_entry:
                    chat_entry.is_favorite = favorite
                    db.session.commit()
                    socketio.emit("favorites_updated", {"user_id": my_id, "favorites": [{"other_user_id": other_user_id, "is_favorite": favorite}]}, room=f"user_{my_id}")
            except Exception as e:
                db.session.rollback()
                print(f"Favorite error: {e}")

def notify_new_user(user):
    """Broadcasts a notification about a new verified user."""
    if not _app:
        print("App context not available")
        return
        
    with _app.app_context():
        try:
            all_users = User.query.filter(User.id != user.id).all()
            for receiver in all_users:
                notif = Notification(
                    user_id=receiver.id,
                    type="new_user_verified",
                    content=f"{user.name} just joined the app!",
                    actor_id=user.id
                )
                db.session.add(notif)
            db.session.commit()

            payload = {"id": user.id, "name": user.name, "type": "new_user_verified", "timestamp": datetime.now().isoformat()}
            for receiver in all_users:
                socketio.emit("notification", payload, room=f"user_{receiver.id}")
        except Exception as e:
            db.session.rollback()
            print(f"notify_new_user error: {e}")
    
def check_and_send_birthday_notifications(app):
    """Checks for birthdays and sends notifications."""
    with app.app_context():
        try:
            today = date.today()
            birthday_users = User.query.filter(
                User.birthday != None,
                db.extract('month', User.birthday) == today.month,
                db.extract('day', User.birthday) == today.day
            ).all()
            
            if not birthday_users:
                print(f"No birthdays today")
                return

            for bday_user in birthday_users:
                friends = [item.user_id for item in UserChatList.query.filter_by(other_user_id=bday_user.id).all()]
                
                for friend_id in friends:
                    notif = Notification(
                        user_id=friend_id,
                        type="birthday_wish",
                        content=f"It's {bday_user.name}'s birthday today! 🎉",
                        actor_id=bday_user.id,
                        timestamp=datetime.utcnow()
                    )
                    db.session.add(notif)
                    db.session.flush()
                    
                    socketio.emit("notification", {
                        "id": notif.id,
                        "sender_id": bday_user.id,
                        "sender_name": bday_user.name,
                        "type": "birthday_wish",
                        "content": notif.content,
                        "timestamp": notif.timestamp.isoformat()
                    }, room=f"user_{friend_id}")
                
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Birthday notification error: {e}")