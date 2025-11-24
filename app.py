import os
os.environ["EVENTLET_NO_GREENDNS"] = "yes"
os.environ["EVENTLET_HUB"] = "poll"
os.environ["EVENTLET_NO_IPV6"] = "1"



import eventlet
eventlet.monkey_patch()

import pymysql
pymysql.install_as_MySQLdb()



from dotenv import load_dotenv
from apps.routes import create_app
from apps.models import db
from flask_jwt_extended import JWTManager
from apps.routes.socket import socketio # Import socketio for running

# Load environment variables from .env file
load_dotenv()

# The application entry point
app = create_app()




with app.app_context():
    try:
        from sqlalchemy import text
        db.session.execute(text("SELECT 1"))
        print("🔥 DB CONNECTION SUCCESS")
    except Exception as e:
        print("❌ DB ERROR:", e)



# IMPORTANT: Get port from environment, default to 5000
PORT = int(os.environ.get('PORT', 5000))
HOST = '0.0.0.0'

if __name__ == '__main__':
    print(f"🚀 Starting Flask app on {HOST}:{PORT}")
    
    # Use socketio.run for production-ready deployment
    socketio.run(
        app,
        host=HOST,
        port=PORT,
        debug=False
    )


# if __name__ == '__main__':
#     # 1. Create the application instance using the factory function
  

#     # 2. Run the application with SocketIO
#     # Note: Using socketio.run instead of app.run
#     # Set FLASK_DEBUG=True in .env for development
#     # socketio.run(
#     #     app,
#     #     host="0.0.0.0",
#     #     debug=os.environ.get('FLASK_DEBUG', 'True') == 'True',
#     #     port=5000,
#     #     allow_unsafe_werkzeug=True # Needed for some dev setups
#     # )
    
#     # if __name__ == '__main__':
    # socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)

    
    