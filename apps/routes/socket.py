import os
# os.environ["EVENTLET_NO_GREENDNS"] = "yes"
# os.environ["EVENTLET_HUB"] = "poll"
# os.environ["EVENTLET_NO_IPV6"] = "1"


from flask import request
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_jwt_extended import decode_token
from datetime import datetime, date
from apps.models import db, Message, UserChatList, FriendRequest, User, Notification # Import User and FriendRequest
from apps.utils import get_chat_room_name, decrypt_message, encrypt_message
from zoneinfo import ZoneInfo
# Initialize SocketIO without the app object yet
socketio = SocketIO()

IST = ZoneInfo("Asia/Kolkata")

online_users = set()


def register_socket_handlers(app):
    """Registers the SocketIO handlers with the initialized app."""
    # Since socketio is initialized globally, we only need to call it once
    
    
    @socketio.on('connect')
    def socket_connect(auth):
        """Authenticates the client connection using JWT."""
        # ... (keep existing logic for connect) ...
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
            
            # 1. Join a personal room for notifications
            join_room(f"user_{user_id}")
            online_users.add(user_id)

            # Broadcast presence (true)
            socketio.emit("presence_update", {
                "user_id": user_id,
                "online": True,
                "last_seen": None
            }) 
            print(f"Socket connected for user {user_id}. Joined room user_{user_id}")
            
        except Exception as e:
            print(f"Socket auth failed: {e}")
            return False
        
    
    # @socketio.on('disconnect')
    # def socket_disconnect():
    #     try:
    #         # You can read token from query string again if needed
    #         token = request.args.get('token')
    #         if not token: return
    #         user_id = int(decode_token(token)['sub'])
    #         if user_id in online_users:
    #             online_users.remove(user_id)

    #         # persist last_seen UTC
    #         with app.app_context():
    #             u = User.query.get(user_id)
    #             if u:
    #                 u.last_seen = datetime.now(IST).replace(tzinfo=None)
    #                 db.session.commit()

    #         socketio.emit("presence_update", {
    #             "user_id": user_id,
    #             "online": False,
    #             "last_seen": datetime.now(IST).isoformat()
    #         })
    #         print(f"user {user_id} disconnected. Last seen updated to IST.")
    #     except Exception as e:
    #         print("disconnect error:", e)
    
    
    
    @socketio.on("disconnect")
    def handle_disconnect():
        try:
            token = request.args.get("token")
            if not token:
                return

            data = decode_token(token)
            user_id = int(data["sub"])

            # ✅ Remove from in-memory set
            if user_id in online_users:
                online_users.remove(user_id)

            # ✅ Update last_seen in DB (with app context)
            # from apps.routes.__init__ import create_app
            # app = create_app()
            # with app.app_context():
            #     user = User.query.get(user_id)
                
            #     if user:
            #         user.last_seen = datetime.now(IST).replace(tzinfo=None)
            #         db.session.commit()
            #         print(f"✅ Updated last_seen for user {user_id}:", user.last_seen)

            # ✅ Broadcast presence update to all
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
        # ... (keep existing logic for join_chat) ...
        token = data.get('token')
        other_id = data.get('other_id')
        
        if not (token and other_id):
            return

        try:
            my_id = int(decode_token(token)['sub'])
            other_id = int(other_id)
            
            # Check if user has "added" the other person (optional security layer)
            if not UserChatList.query.filter_by(user_id=my_id, other_user_id=other_id).first():
                 print(f"user {my_id} tried to join chat with {other_id} but not in chat list.")
                 return # Fail silently or emit an error

            a, b = sorted([my_id, other_id])
            room = f"chat_{a}_{b}"
            join_room(room)
            print(f"user {my_id} joined chat room {room}")
            
        except Exception as e:
            print(f"join_chat failed: {e}")
            
            
    @socketio.on("typing")
    def handle_typing(data):
        # data: {token, to_id, is_typing: bool}
        token = data.get("token"); to_id = data.get("to_id"); is_typing = data.get("is_typing")
        if not (token and to_id is not None): return
        try:
            my_id = int(decode_token(token)["sub"])
            to_id = int(to_id)
        except Exception as e:
            print("typing auth error:", e); return

        # Only emit inside the A-B room so C never receives it
        a, b = sorted([my_id, to_id])
        room = f"chat_{a}_{b}"
        socketio.emit("typing", {
            "from_id": my_id,
            "to_id": to_id,
            "is_typing": bool(is_typing)
        }, room=room)

    # @socketio.on('send_message')
    # def handle_send_message(data):
    #     """Receives a message, saves it, and broadcasts it."""
    #     # ... (keep existing logic for send_message) ...
    #     token = data.get('token')
    #     to_id = data.get('to')
    #     content = data.get('content')
        
    #     media_url = data.get('media_url')
    #     media_type = data.get('media_type')
        
    #     if not (token and to_id and content):
    #         return

    #     try:
    #         my_id = int(decode_token(token)['sub'])
    #         to_id = int(to_id)
    #     except Exception as e:
    #         print(f"socket auth error on send_message: {e}")
    #         return

    #     # Check if user is allowed to chat (in their chat list)
    #     if not UserChatList.query.filter_by(user_id=my_id, other_user_id=to_id).first():
    #         print(f"User {my_id} not allowed to send message to {to_id}.")
    #         return

    #     # Save message
    #     with app.app_context():
    #         # 1. Save the message to DB
    #         new_message = Message(
    #             sender_id=my_id,
    #             receiver_id=to_id,
    #             content=content,
    #             timestamp=datetime.utcnow(),
    #             media_url=media_url,
    #             media_type=media_type,
    #         )
    #         db.session.add(new_message)
            
    #         # 2. COMMIT: Save the message instantly. 
    #         # A single commit here is usually best practice for individual messages.
    #         try:
    #             db.session.commit()
    #         except Exception as e:
    #             db.session.rollback()
    #             print(f"Failed to save message to DB: {e}")
    #             return
            
    #         # 3. Prepare the broadcast payload
    #         msg_payload = {
    #             'id': new_message.id,
    #             'sender_id': my_id,
    #             'receiver_id': to_id,
    #             'content': content,
    #             'timestamp': new_message.timestamp.isoformat()
    #         }
            
    #         # 4. Determine the room name (must be consistent with on_join_chat)
    #         a, b = sorted([my_id, to_id])
    #         room = f"chat_{a}_{b}"
            
    #         # 5. Broadcast the message to the room
    #         socketio.emit("new_message", msg_payload, room=room)
    #         print(f"Message sent to room {room} and saved to DB.")


    @socketio.on('send_message')
    def handle_send_message(data):
        """Receives a message, saves it, and broadcasts it."""
        token = data.get('token')
        to_id = data.get('to')
        content = data.get('content')
        
        media_url = data.get('media_url')
        media_type = data.get('media_type')
        
        # 🌟 FIX 1: The message is valid if it has (token, to_id) AND (content OR media_url)
        if not (token and to_id and (content or media_url)):
            print(f"socket error: Missing required fields for send_message. Content: {content}, Media: {media_url}")
            return

        try:
            my_id = int(decode_token(token)['sub'])
            to_id = int(to_id)
        except Exception as e:
            print(f"socket auth error on send_message: {e}")
            return

        # Check if user is allowed to chat (in their chat list)
        if not UserChatList.query.filter_by(user_id=my_id, other_user_id=to_id).first():
            print(f"User {my_id} not allowed to send message to {to_id}.")
            return
        
        
        encrypted_content = encrypt_message(content)

        # Save message
        with app.app_context():
            # 1. Save the message to DB
            new_message = Message(
                sender_id=my_id,
                receiver_id=to_id,
                content=encrypted_content,
                timestamp=datetime.utcnow(),
                media_url=media_url,
                media_type=media_type,
            )
            db.session.add(new_message)
            
            # 2. COMMIT: Save the message instantly. 
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Failed to save message to DB: {e}")
                return
            
            # 3. Prepare the broadcast payload
            msg_payload = {
                'id': new_message.id,
                'sender_id': my_id,
                'receiver_id': to_id,
                'content': content,
                'timestamp': new_message.timestamp.isoformat(),
                # 🌟 FIX 2: Include media data in the payload
                'media_url': media_url,
                'media_type': media_type
            }
            
            # 4. Determine the room name (must be consistent with on_join_chat)
            a, b = sorted([my_id, to_id])
            room = f"chat_{a}_{b}"
            
            # 5. Broadcast the message to the room
            socketio.emit("new_message", msg_payload, room=room)
            print(f"Message sent to room {room} and saved to DB.")

    @socketio.on('send_friend_request')
    def handle_send_friend_request(data):
        """Creates a friend request and notifies the receiver."""
        token = data.get('token')
        receiver_id = data.get('receiver_id')
        
        if not (token and receiver_id): return
        
        try:
            my_id = int(decode_token(token)['sub'])
            receiver_id = int(receiver_id)
        except Exception as e:
            print(f"socket auth error on send_friend_request: {e}")
            return
            
        if my_id == receiver_id: return # Cannot send request to self

        with app.app_context():
            # Check if request already exists (sender->receiver or receiver->sender)
            existing_req = FriendRequest.query.filter(
                (FriendRequest.sender_id == my_id) & (FriendRequest.receiver_id == receiver_id) |
                (FriendRequest.sender_id == receiver_id) & (FriendRequest.receiver_id == my_id)
            ).first()
            
            if existing_req:
                print("Request already exists or is pending.")
                return

            # Check if already in chat list
            if UserChatList.query.filter_by(user_id=my_id, other_user_id=receiver_id).first():
                print("Already added.")
                return

            sender_user = User.query.get(my_id)
            if not sender_user: return

            # Create request
            new_request = FriendRequest(sender_id=my_id, receiver_id=receiver_id)
            db.session.add(new_request)
            db.session.commit()
            
            # Prepare notification payload
            payload = {
                "id": new_request.id,
                "sender_id": my_id,
                "sender_name": sender_user.name,
                "timestamp": new_request.timestamp.isoformat(),
                "type": "friend_request" # Important for frontend
            }
            
            # Notify receiver
            socketio.emit("notification", payload, room=f"user_{receiver_id}")
            print(f"Sent friend request from {my_id} to {receiver_id}. Notified room user_{receiver_id}")
            
            # Also notify sender that the request was sent successfully
            # Optional: Add status to DB and fetch on connect to persist
            socketio.emit("request_sent", {"receiver_id": receiver_id}, room=f"user_{my_id}")

    # In apps/routes/socket.py

    @socketio.on('respond_friend_request')
    def handle_respond_friend_request(data):
        """Handles accepting or rejecting a friend request, and creates persistent notifications."""
        token = data.get('token')
        request_id = data.get('request_id')
        action = data.get('action') # 'accept' or 'reject'
        
        if not (token and request_id and action): return
        
        try:
            my_id = int(decode_token(token)['sub'])
            request_id = int(request_id)
        except Exception as e:
            print(f"socket auth error on respond_friend_request: {e}")
            return
            
        with app.app_context():
            request_obj = FriendRequest.query.get(request_id)
            
            if not request_obj or request_obj.receiver_id != my_id:
                print(f"Invalid request ID or user {my_id} is not the receiver.")
                return
            
            sender_id = request_obj.sender_id
            sender = User.query.get(sender_id)
            receiver = User.query.get(my_id)
            
            # All DB operations are added to the session first
            
            if action == 'accept':
                # Add to both users' chat lists (bidirectional)
                for user1_id, user2_id in [(my_id, sender_id), (sender_id, my_id)]:
                    # CRITICAL: Check for existing entry to prevent a unique constraint error
                    if not UserChatList.query.filter_by(user_id=user1_id, other_user_id=user2_id).first():
                        item = UserChatList(user_id=user1_id, other_user_id=user2_id)
                        db.session.add(item)
                
                # Payload is prepared, but emit is moved to after commit
                connection_payload = {
                    "user_id": my_id, 
                    "user_name": receiver.name,
                    "other_id": sender_id, 
                    "other_name": sender.name,
                    "type": "connection_success"
                }
                
            # --- START PERSISTENT NOTIFICATION LOGIC ---
            
            # 1. Notification for the original SENDER (The requester)
            sender_content = f"{receiver.name} {action}ed your friend request."
            sender_notification = Notification(
                user_id=sender_id,
                type="request_response", 
                content=sender_content,
                actor_id=my_id, # Responder is the actor
                request_id=request_id
            )
            db.session.add(sender_notification)
            
            # Prepare real-time payload for SENDER (ID will be set after commit)
            sender_response_payload = {
                "id": None, 
                "action": action,
                "sender_id": my_id, 
                "sender_name": receiver.name,
                "type": "request_response",
                "timestamp": datetime.now().isoformat()
            }
            
            # 2. Notification for the original RECEIVER (The responder)
            receiver_content = f"You {action}ed {sender.name}'s friend request."
            receiver_notification = Notification(
                user_id=my_id,
                type="request_resolved", 
                content=receiver_content,
                actor_id=sender_id, 
                request_id=request_id
            )
            db.session.add(receiver_notification)

            # Prepare real-time payload for RECEIVER (ID will be set after commit)
            receiver_response_payload = {
                "id": None, 
                "action": action,
                "sender_id": sender_id, 
                "sender_name": sender.name,
                "type": "request_resolved", 
                "timestamp": datetime.now().isoformat()
            }
            
            # Mark the request for deletion
            db.session.delete(request_obj)

            # Final DB Commit (CRITICAL: Safely commit all pending changes)
            try:
                db.session.commit()
                
                # Update payloads with the new, committed notification IDs
                sender_response_payload['id'] = sender_notification.id
                receiver_response_payload['id'] = receiver_notification.id
                
                # Emit socket events now that the database is safe
                if action == 'accept':
                    # Notify sender and receiver to update their chat list
                    socketio.emit("chat_list_update", connection_payload, room=f"user_{sender_id}")
                    socketio.emit("chat_list_update", connection_payload, room=f"user_{my_id}")
                    
                # Emit response to the requester (sender)
                socketio.emit("notification", sender_response_payload, room=f"user_{sender_id}")
                # Emit response to the responder (receiver)
                socketio.emit("notification", receiver_response_payload, room=f"user_{my_id}")


            except Exception as e:
                # If commit fails (e.g., unique constraint violation), ROLLBACK all pending changes
                db.session.rollback()
                print(f"!!! CRITICAL DB ERROR in handle_respond_friend_request: {e}")
                print("!!! Transaction rolled back. Chat list was NOT saved.")
                return
            
         
    # =========================================================
    # 💡 FIX 1: Message Editing (The change must be broadcast)
    # =========================================================
    @socketio.on('edit_message')
    def handle_edit_message(data):
        try:
            # 💡 FIX: Decode the token to get the actual user_id
            token = data.get('token')
            if not token: return
            auth_user_id = int(decode_token(token)['sub']) # Correctly get the user ID
            
            message_id = data.get('message_id')
            new_content = data.get('new_content')
            
            print("TOKEN WE GET IN EIDT MESSAGE--", token)
            print("auth_user_id WE GET IN EIDT MESSAGE--", auth_user_id)
            print("message_id WE GET IN EIDT MESSAGE--", message_id)
            print("new_content WE GET IN EIDT MESSAGE--", new_content)
            
            encrypted_content = encrypt_message(new_content)
            
            # --- Inside app context for DB operations ---
            with app.app_context(): 
                message = Message.query.get(message_id)
                # 💡 FIX: Compare sender_id with the *authenticated* user ID
                if not message or message.sender_id != auth_user_id:
                    print(f"Auth error: User {auth_user_id} tried to edit message {message_id} which they didn't send.")
                    return

                # 1. Update the database record
                message.content = encrypted_content
                message.is_edited = True
                db.session.commit()

                # 2. Identify the room and broadcast the change
                # Get the ID of the other user in the chat
                other_user_id = message.receiver_id if message.sender_id == auth_user_id else message.sender_id
                chat_room = get_chat_room_name(auth_user_id, other_user_id)
                
                payload = {
                    'message_id': message.id,
                    'new_content': new_content,
                    'is_edited': True,
                }
                # Emit to the chat room so both users get the updated message
                socketio.emit('message_edited', payload, room=chat_room) 
            
        except Exception as e:
            # The 'app.app_context()' is needed for 'db.session.rollback()', 
            # or the rollback should be handled after getting the context.
            # Assuming 'db' is available outside the context if the handler is registered correctly, 
            # but it's safer to wrap all DB calls in 'with app.app_context():'
            # db.session.rollback() # If within an app context
            print(f"ERROR during message edit: {e}")
            return

    # =========================================================
    # 💡 FIX 2: Message Deletion (The change must be broadcast)
    # =========================================================
    @socketio.on('delete_message')
    def handle_delete_message(data):
        try:
            # 💡 FIX: Decode the token to get the actual user_id
            token = data.get('token')
            if not token: return
            auth_user_id = int(decode_token(token)['sub']) # Correctly get the user ID
            
            message_id = data.get('message_id')
            action = data.get('action') # 'delete_for_me' or 'delete_for_everyone'

                
            print("TOKEN WE GET IN EIDT MESSAGE--", token)
            print("auth_user_id WE GET IN EIDT MESSAGE--", auth_user_id)
            print("message_id WE GET IN EIDT MESSAGE--", message_id)
            print("action WE GET IN EIDT MESSAGE--", action)
            
            # --- Inside app context for DB operations ---
            with app.app_context():
                message = Message.query.get(message_id)
                if not message:
                    return

                if action == 'delete_for_everyone':
                    # Check if the user is the sender (Allows "Delete for Everyone")
                    if message.sender_id == auth_user_id:
                        # 1. Update the database flag
                        message.is_deleted_for_everyone = True
                        db.session.commit()
                        
                        # 2. Identify the room and broadcast the change
                        other_user_id = message.receiver_id if message.sender_id == auth_user_id else message.sender_id
                        chat_room = get_chat_room_name(auth_user_id, other_user_id)
                        
                        payload = {
                            'message_id': message.id,
                            # 💡 FIX: Include the action in the payload for clients to handle
                            'action': 'delete_for_everyone', 
                        }
                        # Emit to the chat room so both users see the deletion
                        socketio.emit('message_deleted', payload, room=chat_room)
                    else:
                        print(f"Auth error: User {auth_user_id} tried to 'delete_for_everyone' message {message_id} which they didn't send.")
                        return # User must be the sender for 'delete_for_everyone'
                        
                elif action == 'delete_for_me':
                    
                    # 💡 CRITICAL FIX: Update the specific persistence flag in the database
                    if message.sender_id == auth_user_id:
                        # User is the sender, mark it as deleted for the sender
                        message.is_deleted_for_sender = True
                        print(f"DEBUG: Message {message_id} marked as DELETED FOR SENDER (User {auth_user_id})")
                    elif message.receiver_id == auth_user_id:
                        # User is the receiver, mark it as deleted for the recipient
                        message.is_deleted_for_recipient = True
                        print(f"DEBUG: Message {message_id} marked as DELETED FOR RECIPIENT (User {auth_user_id})")
                    db.session.commit() # Save the change to the database
                    
                    # Emit a confirmation to the user's private room as intended:
                    payload = {
                        'message_id': message.id,
                        'action': 'delete_for_me',
                    }
                    emit('message_deleted', payload, room=f"user_{auth_user_id}")
                
        except Exception as e:
            # db.session.rollback() # If within an app context
            print(f"ERROR during message delete: {e}")
            return
        
        
    #  PINNED CHATTES
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

        with app.app_context():
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
      
    # TOOGEELE FAVOURITES
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

        with app.app_context():
            chat_entry = UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first()
            if chat_entry:
                chat_entry.is_favorite = favorite
                db.session.commit()

                # Notify frontend to update
                socketio.emit(
                    "favorites_updated",
                    {"user_id": my_id, "favorites": [{"other_user_id": other_user_id, "is_favorite": favorite}]},
                    room=f"user_{my_id}"
                )

            
# Register new user notification (moved from users.py/verify_otp)
def notify_new_user(user):
    """Broadcasts a notification about a new verified user AND saves it."""
    payload = {
        "id": user.id, 
        "name": user.name, 
        "type": "new_user_verified",
        "timestamp": datetime.now().isoformat()
    }

    # 1. Save the notification for ALL users (can be optimized to only connected/active users)
    all_users = User.query.filter(User.id != user.id).all() # Notify everyone except the new user
    for receiver in all_users:
        new_notification = Notification(
            user_id=receiver.id,
            type="new_user_verified",
            content=f"{user.name} just joined the app!",
            actor_id=user.id # The new user is the actor
        )
        db.session.add(new_notification)
    db.session.commit()

    # 2. Emit the real-time notification
    # socketio.emit("notification", payload)
    
    for receiver in all_users:
        socketio.emit(
            "notification",
            payload,
            room=f"user_{receiver.id}"
        )
    
    
def check_and_send_birthday_notifications(app):
    """
    Checks for birthdays today and sends notifications to friends of the birthday user.
    This must be run by a scheduler inside the app context.
    """
    with app.app_context():
        # IMPORTANT: Run this inside app_context to access DB and Models
        
        today = date.today()
        
        # 1. Find users whose birthday is today (ignoring the year)
        # Note: This is an efficient way to query month and day parts in SQLite/Postgres/MySQL
        birthday_users = User.query.filter(
            User.birthday != None, # Exclude users who haven't set a birthday
            db.extract('month', User.birthday) == today.month,
            db.extract('day', User.birthday) == today.day
        ).all()
        
        if not birthday_users:
            print(f"[{datetime.now()}] No birthdays today.")
            return

        for bday_user in birthday_users:
            print(f"[{datetime.now()}] Happy Birthday to {bday_user.name} (ID: {bday_user.id})!")
            
            # 2. Find all users who have the birthday user in their chat list (i.e., their friends)
            # Find all UserChatList entries where the *other_user_id* is the birthday user
            friends_to_notify_ids = [
                item.user_id 
                for item in UserChatList.query.filter_by(other_user_id=bday_user.id).all()
                # Exclude blocked relationships if necessary (check UserChatList.is_blocked if you implemented it)
            ]
            
            # 3. Create a persistent Notification for each friend and send a live notification
            for friend_id in friends_to_notify_ids:
                
                # a. Create persistent notification in DB
                content = f"It's {bday_user.name}'s birthday today! Send a warm message 🎉"
                new_notification = Notification(
                    user_id=friend_id,
                    type="birthday_wish", # New type for the frontend to recognize
                    content=content,
                    actor_id=bday_user.id, # Birthday user is the actor
                    timestamp=datetime.utcnow() 
                )
                db.session.add(new_notification)
                db.session.flush() # Flushes the session to assign an ID
                
                # b. Prepare real-time payload for Sidebar.jsx
                payload = {
                    "id": new_notification.id,
                    "sender_id": bday_user.id, 
                    "sender_name": bday_user.name,
                    "type": "birthday_wish", 
                    "content": content,
                    "timestamp": new_notification.timestamp.isoformat()
                }
                
                # c. Send live notification to the friend's personal room
                socketio.emit("notification", payload, room=f"user_{friend_id}")
                print(f"Sent birthday notification to user_{friend_id}")
        
        # 4. Commit all new notifications to the database
        try:
            db.session.commit()
            print(f"[{datetime.now()}] Successfully saved and sent birthday notifications.")
        except Exception as e:
            db.session.rollback()
            print(f"[{datetime.now()}] ERROR saving birthday notifications: {e}")