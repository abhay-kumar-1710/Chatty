import os
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, render_template
from flask_jwt_extended import (
    create_access_token, jwt_required, get_jwt_identity, decode_token
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_, and_
from apps.models import db, User, OTP, Message, UserChatList, FriendRequest, Notification
from apps.utils import send_email, gen_otp, decrypt_message
from apps.routes.socket import socketio, notify_new_user


import cloudinary
import cloudinary.uploader
import os

from zoneinfo import ZoneInfo
# import datetime

IST = ZoneInfo("Asia/Kolkata")

# Configure Cloudinary
cloudinary.config(
  cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'),
  api_key = os.getenv('CLOUDINARY_API_KEY'),
  api_secret = os.getenv('CLOUDINARY_API_SECRET'),
  secure = True
)

user_bp = Blueprint('user', __name__)

# ===== Web Route =====
@user_bp.route('/test')
def index():
    # Renders the index.html from the project root's 'templates' folder
    return "Server running"


# @user_bp.route('/presence', methods=['GET'])
# @jwt_required()
# def presence():
#     ids_param = request.args.get('ids', '')
#     try:
#         id_list = [int(x) for x in ids_param.split(',') if x.strip().isdigit()]
#     except:
#         id_list = []
#     if not id_list:
#         return jsonify({"users": []})

#     users = User.query.filter(User.id.in_(id_list)).all()
#     # online is best-effort from socket.py in-memory set
#     from apps.routes.socket import online_users
#     res = []
#     for u in users:
#         res.append({
#             "user_id": u.id,
#             "online": (u.id in online_users),
#             "last_seen": u.last_seen.replace(tzinfo=ZoneInfo("Asia/Kolkata")).isoformat() if u.last_seen else None        })
#     return jsonify({"users": res})



@user_bp.route("/presence", methods=["GET"])
@jwt_required()
def presence():
    ids_param = request.args.get("ids", "")
    try:
        id_list = [int(x) for x in ids_param.split(",") if x.strip().isdigit()]
    except:
        id_list = []

    if not id_list:
        return jsonify({"users": []})

    users = User.query.filter(User.id.in_(id_list)).all()
    from apps.routes.socket import online_users
    res = []
    for u in users:
        res.append({
            "user_id": u.id,
            "online": (u.id in online_users),
            "last_seen": u.last_seen.replace(tzinfo=IST).isoformat() if u.last_seen else None
        })
    return jsonify({"users": res})


# ===== Auth Routes =====
@user_bp.route('/register', methods=['POST'])
def register():
    data = request.json
    name = data.get('name')
    email = data.get('email')
    password = data.get('password') # Frontend should handle confirm_password check
    
    print("DATA FOR REGISTER", data)
    
    if not (name and email and password):
        return jsonify({"msg": "Missing required fields"}), 400

    if User.query.filter_by(name=name).first():
        return jsonify({"msg": "Username already exist!"}), 400

    
    if User.query.filter_by(email=email).first():
        return jsonify({"msg": "Email already registered"}), 400


    u = User(name=name, email=email)
    u.set_password(password) # Use model method to set hashed password
    db.session.add(u)
    db.session.commit()

    code = gen_otp()
    otp = OTP(user_id=u.id, code=code, expires_at=(datetime.now(IST) + timedelta(minutes=10)).replace(tzinfo=None))
    db.session.add(otp)
    db.session.commit()

    # Send email
    subject = "Your Chat App OTP"
    body = f"Hi {name},\n\nYour verification code is: {code}\nIt expires in 10 minutes."
    send_email(email, subject, body)

    return jsonify({"msg": "Registration successful, OTP sent to email"}), 201

@user_bp.route('/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json
    email = data.get('email')
    code = data.get('pin')

    print("DATA FOR OTP ", data)
    u = User.query.filter_by(email=email).first()
    print("VERIFY OTP USER ", u)
    if not u:
        return jsonify({"msg": "User not found"}), 404

    otp = OTP.query.filter_by(user_id=u.id, code=code).order_by(OTP.expires_at.desc()).first()
    
    if not otp or otp.expires_at < datetime.now():
        return jsonify({"msg": "Invalid or expired OTP"}), 400
    
    u.verified = True
    db.session.delete(otp)
    db.session.commit()

    # Notify all connected clients about the new verified user via function
    notify_new_user(u)

    return jsonify({"msg": "Email verified successfully"}), 200

@user_bp.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    user = User.query.filter_by(email=email).first()
    
    if not user or not user.check_password(password): # Use model method for password check
        return jsonify({"msg": "Invalid email or password"}), 401
    
    if not user.verified:
        return jsonify({"msg": "Email not verified"}), 403
    
    # JWT identity is the user's ID (must be string)
    access_token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": access_token, "user": user.to_dict()})


# ==============================================================================
# ===== Forgot Password Routes (NEW) =====
# ==============================================================================

@user_bp.route('/forgot-password/send-otp', methods=['POST'])
def forgot_password_send_otp():
    """
    Step 1: Accepts email, finds user, generates 4-digit OTP, sends email,
    and stores/updates OTP in the database.
    """
    data = request.json
    email = data.get('email')

    if not email:
        return jsonify({"msg": "Email is required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        # User enumeration protection: return generic error.
        return jsonify({"msg": "No user found with that email address"}), 404
    
    # Check if a pending OTP already exists
    otp_record = OTP.query.filter_by(user_id=user.id).first()
    
    otp_code = gen_otp(length=4)
    expires_at = datetime.utcnow() + timedelta(minutes=10) # OTP valid for 10 minutes

    if otp_record:
        # Update existing record
        otp_record.code = otp_code
        otp_record.expires_at = expires_at
    else:
        # Create new record
        new_otp = OTP(user_id=user.id, code=otp_code, expires_at=expires_at)
        db.session.add(new_otp)
    
    db.session.commit()

    # Send the email
    subject = "Password Reset OTP"
    body = f"Your 4-digit One-Time Password (OTP) for password reset is: {otp_code}. This code is valid for 10 minutes."
    send_email(user.email, subject, body)

    # Return success, the frontend will navigate to the OTP verification page
    return jsonify({"msg": "Password reset OTP sent to your email address"}), 200


@user_bp.route('/forgot-password/verify-otp', methods=['POST'])
def forgot_password_verify_otp():
    """
    Step 2: Accepts email and OTP, verifies, and returns a temporary password reset token (JWT).
    """
    data = request.json
    email = data.get('email')
    otp_code = data.get('pin')

    if not (email and otp_code):
        return jsonify({"msg": "Email and OTP are required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"msg": "Invalid verification data"}), 401

    otp_record = OTP.query.filter_by(user_id=user.id, code=otp_code).first()

    if not otp_record:
        return jsonify({"msg": "Invalid OTP code"}), 401

    if otp_record.expires_at < datetime.utcnow():
        db.session.delete(otp_record)
        db.session.commit()
        return jsonify({"msg": "OTP expired"}), 401

    # OTP is valid. Generate a temporary, short-lived JWT token for password reset
    reset_token = create_access_token(
        identity=str(user.id), 
        expires_delta=timedelta(minutes=5), # Token only valid for 5 minutes for reset
        additional_claims={"reset_context": True} # Add custom claim for security
    )
    
    # IMPORTANT: Delete the OTP record immediately after successful use
    db.session.delete(otp_record)
    db.session.commit()

    return jsonify({
        "msg": "OTP verified successfully. Proceed to reset password.",
        "reset_token": reset_token
    }), 200


@user_bp.route('/forgot-password/reset', methods=['POST'])
def reset_password():
    """
    Step 3: Accepts email, new password, and the temporary reset token to update the password.
    """
    data = request.json
    email = data.get('email')
    new_password = data.get('password')
    reset_token = data.get('reset_token') # Sent as a data field

    if not (email and new_password and reset_token):
        return jsonify({"msg": "Missing required fields (email, password, or reset token)"}), 400

    # 1. Verify the temporary reset token
    try:
        decoded_token = decode_token(reset_token)
        
        # We expect 'sub' to be the user ID, usually stored as a string by JWT libraries.
        user_id_str = decoded_token.get('sub')
        
        if not user_id_str:
             # This handles if the 'sub' claim is completely missing
             print("Token decoding/validation error: 'sub' claim missing from token.")
             return jsonify({"msg": "Invalid token structure"}), 403
             
        # Explicitly convert to integer for safe comparison against the DB User.id
        try:
            user_id = int(user_id_str)
        except ValueError:
            # This handles the case where 'sub' is a string but not an integer (e.g., 'null' or bad data)
            print("Token decoding/validation error: 'sub' claim is not a valid integer string.")
            return jsonify({"msg": "Invalid or expired password reset token"}), 403


        is_reset_context = decoded_token.get('reset_context')
        
        # Check if the token has the correct context
        if not is_reset_context:
            return jsonify({"msg": "Invalid or unauthorized reset token context"}), 403
            
        # Use the integer ID for the query
        user = User.query.filter_by(id=user_id, email=email).first()
        if not user:
            return jsonify({"msg": "Token user mismatch"}), 403
        
    except Exception as e:
        # This catches general JWT errors (like signature verification failure, expiration, 
        # or the specific "Subject must be a string" error you were seeing).
        print(f"Token decoding/validation error: {e}")
        return jsonify({"msg": "Invalid or expired password reset token"}), 403

    # 2. Update the password
    user.set_password(new_password)
    db.session.commit()

    # 3. Success
    return jsonify({"msg": "Password updated successfully. You can now log in."}), 200

# CHAGE PASSWORD
@user_bp.route('/change-password', methods=['PUT'])
def change_password():
    data = request.json
    print("DATA CHANGE PASSWORD", data)

    email = data.get('email')
    current_password = data.get('current_password')
    set_new_password = data.get('set_new_password')
    confirm_new_password = data.get('confirm_new_password')

    if not (email and current_password and set_new_password and confirm_new_password):
        return jsonify({"msg": "Missing required fields"}), 400

    try:
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({"msg": "User not found"}), 404

        # Verify current password
        if not check_password_hash(user.password_hash, current_password):
            return jsonify({"msg": "Incorrect current password"}), 400

        # Check new + confirm match
        if set_new_password != confirm_new_password:
            return jsonify({"msg": "Set and confirm password do not match"}), 400

        # Update password
        user.set_password(set_new_password)
        db.session.commit()

        return jsonify({"msg": "Password updated successfully!"}), 200

    except Exception as e:
        print("ERRORRRR:", e)
        return jsonify({"msg": "Failed to update password"}), 500




# send otp for delete user account
@user_bp.route('/send-otp-delete-account', methods=['POST'])
@jwt_required()
def send_otp_delete_account():
    data = request.json
    print("DATA EMAIL", data)
    email = data.get('email')

    if not email:
        return jsonify({"msg": "Email is required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        # User enumeration protection: return generic error.
        return jsonify({"msg": "No user found with that email address"}), 404
    
    # Check if a pending OTP already exists
    otp_record = OTP.query.filter_by(user_id=user.id).first()
    
    otp_code = gen_otp(length=4)
    expires_at = (datetime.now(IST) + timedelta(minutes=10)).replace(tzinfo=None) # OTP valid for 10 minutes
        # otp = OTP(user_id=u.id, code=code, expires_at=(datetime.datetime.now(IST) + timedelta(minutes=10)).replace(tzinfo=None))

    if otp_record:
        # Update existing record
        otp_record.code = otp_code
        otp_record.expires_at = expires_at
    else:
        # Create new record
        new_otp = OTP(user_id=user.id, code=otp_code, expires_at=expires_at)
        db.session.add(new_otp)
    
    db.session.commit()

    # Send the email
    subject = "Delete Account OTP"
    body = f"Your 4-digit One-Time Password (OTP) for deleting your account is: {otp_code}. This code is valid for 10 minutes."
    send_email(user.email, subject, body)

    # Return success, the frontend will navigate to the OTP verification page
    return jsonify({"msg": "Password reset OTP sent to your email address"}), 200



@user_bp.route('/verify-otp-delete-account', methods=['POST'])
def verify_otp_delete_account():
    data = request.json
    email = data.get('email')
    code = data.get('pin')
    
    if not email or not code:
        return jsonify({"msg": "Missing email or OTP"}), 400
    
    print("DATA FOR OTP ", data)
    u = User.query.filter_by(email=email).first()
    print("VERIFY OTP USER ", u)
    if not u:
        return jsonify({"msg": "User not found"}), 404

    otp = OTP.query.filter_by(user_id=u.id, code=code).order_by(OTP.expires_at.desc()).first()
    
    if not otp or otp.expires_at < datetime.now():
        return jsonify({"msg": "Invalid or expired OTP"}), 400
    
    # message = Message.query.filter_by(sender_id = u.id, receiver_id = u.id)
    # chatlist = UserChatList.query.filter_by(user_id = u.id, other_user_id = u.id)
    # notification = Notification.query.filter_by(user_id=u.id)
    # friend_request = FriendRequest.query.filter_by(sender_id=u.id, receiver_id=u.id)
    
    Message.query.filter(
        (Message.sender_id == u.id) | (Message.receiver_id == u.id)
    ).delete(synchronize_session=False)

    # 2. Delete all chatlist entries associated with the user
    UserChatList.query.filter(
        (UserChatList.user_id == u.id) | (UserChatList.other_user_id == u.id)
    ).delete(synchronize_session=False)

    # 3. Delete all friend requests involving the user
    FriendRequest.query.filter(
        (FriendRequest.sender_id == u.id) | (FriendRequest.receiver_id == u.id)
    ).delete(synchronize_session=False)
    
    # 4. Delete all notifications for the user
    Notification.query.filter_by(user_id=u.id).delete(synchronize_session=False)
    
    
    db.session.delete(otp)
    db.session.delete(u)
    # db.session.delete(message)
    # db.session.delete(chatlist)
    # db.session.delete(friend_request)
    # db.session.delete(notification)
    db.session.commit()

    return jsonify({"msg": "Your Account Deleted Successfully!"}), 200



# ===== User Profile Routes =====
@user_bp.route('/profile', methods=['GET'])
@jwt_required()
def get_user_profile():
    """Retrieves the current user's profile data."""
    my_id = int(get_jwt_identity())
    user = User.query.get(my_id)

    if not user:
        # Should not happen if JWT is valid, but good for safety
        return jsonify({"msg": "User not found"}), 404

    # Use the new to_dict() method from the User model
    return jsonify(user.to_dict()), 200


# ==============================================================================
# NEW: Cloudinary Media Upload API
# ==============================================================================
@user_bp.route('/profile-image-upload', methods=['POST'])
@jwt_required()
def profile_image_upload():
    """Handles file upload and returns the public URL from Cloudinary."""
    
    if 'file' not in request.files:
        return jsonify({"msg": "No file part in the request"}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"msg": "No selected file"}), 400
    
    # Optional: Check file type and size before upload
    
    try:
        # Use a folder structure based on the user ID for better organization
        user_id = get_jwt_identity()
        
        # Upload the file directly to Cloudinary
        upload_result = cloudinary.uploader.upload(
            file, 
            folder=f"chat_app_profiles/{user_id}",
            # Set a public ID for easy retrieval/management
            public_id=f"profile_image_{user_id}",
            overwrite=True # Always replace the profile image
        )
        
        media_url = upload_result.get('secure_url')
        
        if not media_url:
            raise Exception("Cloudinary upload failed to return a URL.")

        return jsonify({"msg": "File uploaded successfully", "media_url": media_url}), 200

    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return jsonify({"msg": f"Media upload failed: {str(e)}"}), 500


@user_bp.route('/check-username', methods=['GET'])
def check_username():
    name = request.args.get('name')
    if not name:
        return jsonify({"msg": "Username is required"}), 400
    
    exists = User.query.filter_by(name=name).first() is not None
    return jsonify({"available": not exists}), 200



@user_bp.route('/profile', methods=['PUT'])
@jwt_required()
def update_user_profile():
    """Updates the current user's profile fields (name, description, image_url)."""
    my_id = int(get_jwt_identity())
    data = request.json
    
    name = data.get('name')
    description = data.get('description')
    image_url = data.get('image_url')
    
    birthday_str = data.get('birthday')
    print("BIRTHDAY STR", birthday_str)
    
    user = User.query.get(my_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404
        
    # Update fields if provided in the request
    # === CHECK USERNAME BEFORE UPDATING ===
    if name is not None:
        # Check if username already exists (excluding the current user)
        existing_user = User.query.filter(User.name == name, User.id != my_id).first()
        print("EXITING USER", existing_user)
        if existing_user:
            return jsonify({"msg": "Username already exists!"}), 400
        
        user.name = name  # safe to update now

    if description is not None:
        # Check max length before committing
        user.description = description[:500] 
        
    if image_url is not None:
        user.image_url = image_url
        
    if birthday_str is not None:
        if birthday_str == "":
            user.birthday = None # Allow clearing the birthday
        else:
            try:
                # Store it as a date object
                user.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({"msg": "Invalid birthday format. Use YYYY-MM-DD"}), 400
    
    # Note: Email/Password updates should ideally be in separate, more secure routes.
    
    db.session.commit()
    
    # Return the updated profile data
    return jsonify({"msg": "Profile updated successfully", "user": user.to_dict()}), 200


@user_bp.route('/users/suggestions', methods=['GET'])
@jwt_required()
def get_user_suggestions():
    """
    Get all verified users, excluding self, users already in the chat list, 
    and users with a pending friend request (RECEIVED only). Users with SENT requests 
    are kept to show the 'Pending' button on the frontend.
    """
    my_id = int(get_jwt_identity())
    
    # 1. Get IDs of users already in my chat list
    added_ids = [item.other_user_id for item in UserChatList.query.filter_by(user_id=my_id).all()]
    added_ids.append(my_id) # Exclude myself
    
    # 2. Get IDs of users who have sent ME a pending friend request
    # Users I sent a request to will NOT be excluded, allowing the frontend to display the 'Pending' button.
    received_request_ids = [req.sender_id for req in FriendRequest.query.filter_by(receiver_id=my_id).all()]
    
    for user_id in received_request_ids:
        # Add the sender's ID to the exclusion list if not already added 
        if user_id not in added_ids:
            added_ids.append(user_id)
    
    # 3. Get all verified users not in the exclusion list
    users = User.query.filter(User.id.notin_(added_ids), User.verified == True).all()
    
    # --- NEW LOGIC START ---
    # Fetch SENT requests
    sent_request_ids = [req.receiver_id for req in FriendRequest.query.filter_by(sender_id=my_id).all()]
    
    out = []
    for u in users:
        status = "Request"
        if u.id in sent_request_ids:
            status = "Pending"
        
        out.append({
            "id": u.id, 
            "name": u.name, 
            "email": u.email,
            "status": status # Include the status
        })
    # --- NEW LOGIC END ---
    
    return jsonify(out)

# @user_bp.route('/users/chatlist', methods=['POST'])
# @jwt_required()
# def add_user_to_chatlist():
#     """Adds another user to the current user's chat list."""
#     my_id = int(get_jwt_identity())
#     data = request.json
#     other_user_id = data.get('user_id')
    
#     if not other_user_id:
#         return jsonify({"msg": "Missing user_id"}), 400
    
#     other_user_id = int(other_user_id)
    
#     if my_id == other_user_id:
#         return jsonify({"msg": "Cannot add yourself to the chat list"}), 400

#     # Check if the user exists and is verified
#     other_user = User.query.get(other_user_id)
#     if not other_user or not other_user.verified:
#         return jsonify({"msg": "User not found or not verified"}), 404

#     # Check if already added
#     if UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first():
#         return jsonify({"msg": "User already in chat list"}), 400
        
#     # Add to chat list
#     item = UserChatList(user_id=my_id, other_user_id=other_user_id)
#     db.session.add(item)
#     db.session.commit()
    
#     return jsonify({"msg": "User added to chat list", "user": {"id": other_user.id, "name": other_user.name}}), 200


# GET USER CHAT LSIT
# @user_bp.route('/users/chatlist', methods=['GET'])
# @jwt_required()
# def get_chatlist():
#     """Retrieves all users the current user can chat with."""
#     my_id = int(get_jwt_identity())
    
#     # Get the User objects based on the other_user_id in the UserChatList
#     chat_list_items = UserChatList.query.filter_by(user_id=my_id).all()
    
#     chat_users = []
#     for item in chat_list_items:
#         user = User.query.get(item.other_user_id)
#         if user:
#             chat_users.append({"id": user.id, "name": user.name, "email": user.email, "image_url" : user.image_url, "description" : user.description})
            
#     return jsonify(chat_users)


# GET USER CHAT LIST
@user_bp.route('/users/chatlist', methods=['GET'])
@jwt_required()
def get_chatlist():
    my_id = int(get_jwt_identity())

    # Fetch chat list entries for me
    chat_list_items = (UserChatList.query
        .filter_by(user_id=my_id)
        .order_by(
            # pinned first: pin_priority != 0, then ascending (1 on top)
            db.case((UserChatList.pin_priority == 0, 1), else_=0),
            UserChatList.pin_priority.asc(),
            # fallback: by other user's name for stability
            # (join needed)
        ).all())

    chat_users = []
    for item in chat_list_items:
        user = User.query.get(item.other_user_id)
        if user:
            chat_users.append({
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "image_url": user.image_url,
                "description": user.description,
                "pin_priority": item.pin_priority,
                "is_favorite":item.is_favorite
            })
    return jsonify(chat_users)


# POST FOR PINNED CHAT
@user_bp.route('/pin/<int:other_user_id>', methods=['POST'])
@jwt_required()
def pin_chat(other_user_id):
    my_id = int(get_jwt_identity())
    data = request.json or {}
    should_pin = data.get("pin", True)

    entry = UserChatList.query.filter_by(user_id=my_id, other_user_id=other_user_id).first()
    if not entry:
        return jsonify({"msg": "Chat not found in list"}), 404

    # fetch my current pins ordered by priority
    pins = (UserChatList.query
            .filter_by(user_id=my_id)
            .filter(UserChatList.pin_priority > 0)
            .order_by(UserChatList.pin_priority.asc())
            .all())

    if should_pin:
        # if already pinned -> move to top (1) and shift others
        if entry.pin_priority > 0:
            old_pri = entry.pin_priority
            for p in pins:
                if p.id == entry.id:
                    continue
                if p.pin_priority < old_pri:
                    p.pin_priority += 1
            entry.pin_priority = 1
        else:
            # insert at top
            for p in pins:
                p.pin_priority = min(p.pin_priority + 1, 3)
            entry.pin_priority = 1

        # enforce max 3: any pin with pri>3 becomes 0
        overflow = (UserChatList.query
                    .filter_by(user_id=my_id)
                    .filter(UserChatList.pin_priority > 3)
                    .all())
        for p in overflow:
            p.pin_priority = 0
    else:
        # unpin and close the gap (shift lower priorities up)
        removed_pri = entry.pin_priority
        entry.pin_priority = 0
        if removed_pri > 0:
            for p in pins:
                if p.id != entry.id and p.pin_priority > removed_pri:
                    p.pin_priority -= 1

    db.session.commit()

    # prepare minimal payload for client
    new_pins = (UserChatList.query
        .filter_by(user_id=my_id)
        .filter(UserChatList.pin_priority > 0)
        .with_entities(UserChatList.other_user_id, UserChatList.pin_priority)
        .order_by(UserChatList.pin_priority.asc())
        .all())

    payload = {
        "pins": [{"other_user_id": uid, "pin_priority": pri} for (uid, pri) in new_pins]
    }

    # notify this user’s personal room in realtime
    socketio.emit("chat_pins_updated", payload, room=f"user_{my_id}")

    return jsonify({"ok": True, **payload})



# GET MESSAGES
@user_bp.route('/messages/<int:other_user_id>', methods=['GET'])
@jwt_required()
def get_messages(other_user_id):
    """Fetch paginated chat messages between two users."""
    current_user_id = get_jwt_identity()
    
    offset = int(request.args.get('offset', 0))  # how many to skip
    limit = int(request.args.get('limit', 15))   # how many to fetch
    
    # --- Check blocking status ---
    block_by_me = UserChatList.query.filter_by(
        user_id=current_user_id, other_user_id=other_user_id, is_blocked=True
    ).first() is not None

    block_by_them = UserChatList.query.filter_by(
        user_id=other_user_id, other_user_id=current_user_id, is_blocked=True
    ).first() is not None

    # --- Fetch all relevant messages ---
    messages_query = Message.query.filter(
        or_(
            and_(
                Message.sender_id == current_user_id,
                Message.receiver_id == other_user_id,
                Message.is_deleted_for_sender == False
            ),
            and_(
                Message.sender_id == other_user_id,
                Message.receiver_id == current_user_id,
                Message.is_deleted_for_recipient == False
            )
        )
    ).order_by(Message.timestamp.desc())  # latest first

    total_count = messages_query.count()
    messages = messages_query.offset(offset).limit(limit).all()
    output = []
    for msg in messages:
        if msg.is_deleted_for_everyone:
            output.append({
                'id': msg.id,
                'sender_id': msg.sender_id,
                'is_deleted_for_everyone': True,
                'content': 'This message was deleted.',
                'timestamp': msg.timestamp.isoformat(),
            })
        else:
            output.append(msg.to_dict())

    # Return newest first, but UI expects oldest-first order
    output.reverse()

    return jsonify({
        'messages': output,
        'total_count': total_count,
        'is_blocked_by_me': block_by_me,
        'is_blocked_by_them': block_by_them
    }), 200
    
    
@user_bp.route('/messages/search/<int:other_user_id>', methods=['GET'])
@jwt_required()
def search_messages(other_user_id):
    """Search messages between current user and another user by keyword."""
    current_user_id = int(get_jwt_identity())
    query = request.args.get('q', '').strip().lower()

    print("CURRENT USER IN SEARCH MESSAGE", current_user_id)
    print("QUERYY", query)

    if not query:
        return jsonify({"results": []})

    # Step 1️⃣: Get all messages between A and B
    messages = Message.query.filter(
        (
            ((Message.sender_id == current_user_id) & (Message.receiver_id == other_user_id))
            | ((Message.sender_id == other_user_id) & (Message.receiver_id == current_user_id))
        )
        & (Message.is_deleted_for_everyone == False)
    ).order_by(Message.timestamp.asc()).all()

    print(f"Fetched {len(messages)} messages to search")

    # Step 2️⃣: Decrypt + search in memory
    matched_messages = []
    for msg in messages:
        try:
            decrypted_text = decrypt_message(msg.content)
        except Exception as e:
            print("Decryption failed for message:", e)
            continue

        if query in decrypted_text.lower():
            msg_dict = msg.to_dict()
            msg_dict["content"] = decrypted_text  # Replace encrypted with plain text
            matched_messages.append(msg_dict)

    print(f"Matched {len(matched_messages)} messages for query '{query}'")

    return jsonify({"results": matched_messages})


# ===== Media Routes =====
@user_bp.route('/upload-media', methods=['POST'])
@jwt_required()
def upload_media():
    current_user_id = get_jwt_identity()
    
    # 1. Check if a file was sent
    if 'file' not in request.files:
        return jsonify({"msg": "No file part in the request"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"msg": "No selected file"}), 400

    # 2. Upload to Cloudinary
    try:
        # Determine the resource type based on MIME type (approximate)
        mimetype = file.mimetype
        resource_type = 'auto' # Let Cloudinary figure it out

        if mimetype.startswith('image'):
            resource_type = 'image'
        elif mimetype.startswith('video'):
            resource_type = 'video'
        elif 'pdf' in mimetype:
            # We want documents (PDFs) to be treated as 'raw' or 'image' if we need previews.
            # For simplicity and robust downloads, we'll use 'raw' for documents.
            resource_type = 'raw' 
        
        # Use uploader.upload, passing the file object directly
        result = cloudinary.uploader.upload(
            file, 
            folder=f"chat_app/{current_user_id}", # Organized by user ID
            resource_type=resource_type,
            # This allows the file to be accessed and viewed without download, but still secure
            access_mode="public", 
        )
        
        # 3. Return the public URL and resource type
        return jsonify({
            "media_url": result['secure_url'],
            "media_type": result['resource_type'] # Cloudinary's detected type: 'image', 'video', 'raw'
        }), 200

    except Exception as e:
        print(f"Cloudinary Upload Error: {e}")
        return jsonify({"msg": f"Media upload failed: {str(e)}"}), 500


# New route to get initial notifications (friend requests)
@user_bp.route('/notifications', methods=['GET'])
@jwt_required()
def get_notifications():
    """Retrieves all pending friend requests AND historical notifications for the current user."""
    my_id = int(get_jwt_identity())

    # 1. Fetch pending Friend Requests (These are the actionable ones)
    pending_requests = FriendRequest.query.filter_by(receiver_id=my_id).all()

    # Format the pending requests as notifications
    request_notifications = []
    for req in pending_requests:
        request_notifications.append({
            'id': req.id,
            'type': 'friend_request',
            'sender_id': req.sender_id,
            'sender_name': req.sender.name,
            'timestamp': req.timestamp.isoformat(),
            'content': f"{req.sender.name} sent you a friend request."
        })

    # 2. Fetch all persistent Notifications (These are the response and info notifications)
    # Order by timestamp descending (newest first)
    historical_notifications = Notification.query.filter_by(user_id=my_id).order_by(Notification.timestamp.desc()).all()

    # Format historical notifications
    # The frontend logic for rendering is complex, so let's simplify the payload by mapping:

    history_out = []
    for n in historical_notifications:
        payload = {
            'id': n.id,
            'type': n.type, 
            'timestamp': n.timestamp.isoformat(),
            'request_id': n.request_id,
        }
        # Add actor information, which is used by the frontend as the "sender"
        if n.actor:
            payload['sender_id'] = n.actor_id
            payload['sender_name'] = n.actor.name
            
       # Determine specific fields based on notification type
        if n.type == 'request_response' or n.type == 'request_resolved':
            # Extract action (accept/reject) for the frontend to render the response status
            action_word = n.content.split(' ')[1].replace('ed', '').replace('.', '') # Extracts 'accept' or 'reject'
            payload['action'] = action_word
            
        elif n.type == 'new_user_verified':
                # Use 'name' for new user notifications as expected by your frontend
                if n.actor:
                    payload['name'] = n.actor.name
                    
        history_out.append(payload)


    # 3. Combine both lists
    # Pending requests should appear first/mixed with new history, but sorting the combined list is easiest
    all_notifications = request_notifications + history_out

    # Sort by timestamp, newest first (descending)
    all_notifications.sort(key=lambda x: datetime.fromisoformat(x['timestamp'].replace('Z', '+00:00')), reverse=True)

    return jsonify(all_notifications)


@user_bp.route('/block/<int:other_user_id>', methods=['POST'])
@jwt_required()
def toggle_block_user(other_user_id):
    current_user_id = get_jwt_identity()
    data = request.json
    # Expects {'block': true/false}
    should_block = data.get('block', True) 

    # 1. Find or create the UserChatList entry for the current user blocking the other user
    chat_list_entry = UserChatList.query.filter_by(
        user_id=current_user_id, 
        other_user_id=other_user_id
    ).first()

    # If the entry doesn't exist, create it (it must exist for a chat to be open, but safer to check)
    if not chat_list_entry:
        chat_list_entry = UserChatList(
            user_id=current_user_id,
            other_user_id=other_user_id,
        )
        db.session.add(chat_list_entry)

    # 2. Update the block status
    chat_list_entry.is_blocked = should_block
    
    try:
        db.session.commit()

        # 3. Notify the *other user* in real-time about the change
        # This will trigger the disabled chat input on their side.
        from apps.routes.socket import socketio # Import socketio
        
        # Room is the other user's personal room for notifications
        room = f"user_{other_user_id}" 
        
        # 💡 Payload tells the other user *who* blocked them and the new status
        payload = {
            'blocker_id': current_user_id,
            'is_blocked': should_block,
        }
        socketio.emit('block_status_update', payload, room=room)
        
        action = "blocked" if should_block else "unblocked"
        return jsonify({'message': f"User {other_user_id} successfully {action}."}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error during block/unblock: {e}")
        return jsonify({'message': 'Failed to update block status.'}), 500