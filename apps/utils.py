import os
import random
import string
import smtplib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, hmac, padding
import base64
import requests


def send_email(to_email, subject, body):
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")

    url = "https://api.resend.com/email"

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "from": "Chat App <onboarding@resend.dev>",
        "to": [to_email],
        "subject": subject,
        "text": body
    }

    try:
        r = requests.post(url, json=data, headers=headers)
        print("EMAIL SENT RESPONSE:", r.text)
        return True
    except Exception as e:
        print("Email send error:", e)
        return False

# def send_email(to_email, subject, body):
#     host = os.getenv("SMTP_HOST")
#     port = int(os.getenv("SMTP_PORT", 587))
#     user = os.getenv("SMTP_USER")
#     pwd = os.getenv("SMTP_PASS")
#     from_email = os.getenv("FROM_EMAIL", user)
    
#     if not (host and user and pwd):
#         print("SMTP not configured; skipping email send.")
#         print("Would send to:", to_email, subject, body)
#         return False
    
#     try:
#         server = smtplib.SMTP(host, port)
#         server.starttls()
#         server.login(user, pwd)
#         message = f"From: {from_email}\r\nTo: {to_email}\r\nSubject: {subject}\r\n\r\n{body}"
#         server.sendmail(from_email, to_email, message)
#         server.quit()
#         return True
#     except Exception as e:
#         print("Email send error:", e)
#         return False

def gen_otp(length=4):
    """Generates a random N-digit OTP."""
    return ''.join(random.choices(string.digits, k=length))


def get_chat_room_name(user_a_id, user_b_id):
    """Generates a consistent room name for a chat between two users."""
    # Use a combination of IDs to ensure the room name is the same regardless of sender/receiver order
    if user_a_id < user_b_id:
        return f"chat_{user_a_id}_{user_b_id}"
    return f"chat_{user_b_id}_{user_a_id}"



try:
    # Use Flask secret key if available, otherwise fallback to a generated one
    ENCRYPTION_KEY = os.getenv("SECRET_KEY", "a_very_secret_key_that_is_at_least_32_bytes_long").encode('utf-8')[:32]
    # Ensure the key is exactly 32 bytes for AES-256
    if len(ENCRYPTION_KEY) < 32:
        # Pad or use a default secure key if the secret is too short
        ENCRYPTION_KEY = (ENCRYPTION_KEY + b'A' * 32)[:32]
except Exception as e:
    print(f"Error initializing encryption key: {e}")
    ENCRYPTION_KEY = b'B' * 32 # Fallback to a default secure key
    

def encrypt_message(plaintext):
    """
    Encrypts plaintext using AES-256-GCM.
    Returns a base64 encoded string containing the IV, ciphertext, and tag.
    """
    if not plaintext:
        return ""
    
    plaintext_bytes = plaintext.encode('utf-8')

    # Generate a random 12-byte Initialization Vector (IV/Nonce) for GCM
    iv = os.urandom(12) 
    
    # Create the cipher object
    cipher = Cipher(
        algorithms.AES(ENCRYPTION_KEY),
        modes.GCM(iv),
        backend=default_backend()
    )
    
    encryptor = cipher.encryptor()
    
    # Encrypt the data
    ciphertext = encryptor.update(plaintext_bytes) + encryptor.finalize()
    
    # The Authentication Tag (auth_tag) is produced by GCM mode
    tag = encryptor.tag
    
    # Package the IV, ciphertext, and tag for storage/transmission (Base64 for safety)
    # Format: IV (12 bytes) + Ciphertext + Tag (16 bytes)
    encrypted_data = base64.b64encode(iv + ciphertext + tag)
    
    return encrypted_data.decode('utf-8')


def decrypt_message(encrypted_data_b64):
    """
    Decrypts base64 encoded data (IV + Ciphertext + Tag) using AES-256-GCM.
    """
    if not encrypted_data_b64:
        return ""

    try:
        # Decode the base64 string back to bytes
        encrypted_data = base64.b64decode(encrypted_data_b64)
    except:
        return "[Decryption Failed: Invalid Data Format]"

    # Check minimum length (IV: 12 bytes, Tag: 16 bytes = 28 bytes)
    if len(encrypted_data) < 28:
        return "[Decryption Failed: Data Too Short]"

    # Separate the parts: IV (12 bytes), Ciphertext, Tag (16 bytes)
    iv = encrypted_data[:12]
    tag = encrypted_data[-16:]
    ciphertext = encrypted_data[12:-16]

    try:
        # Create the cipher object with the IV and Tag
        cipher = Cipher(
            algorithms.AES(ENCRYPTION_KEY),
            modes.GCM(iv, tag),
            backend=default_backend()
        )
        
        decryptor = cipher.decryptor()
        
        # Decrypt the data and authenticate the tag
        decrypted_bytes = decryptor.update(ciphertext) + decryptor.finalize()
        
        # Decode the bytes back to a UTF-8 string
        return decrypted_bytes.decode('utf-8')

    except Exception as e:
        # Authentication failure means the message was tampered with or the key is wrong
        print(f"Decryption Error (Authentication failure): {e}")
        return "[Decryption Failed: Authentication Error]"