import os
# os.environ["EVENTLET_NO_GREENDNS"] = "yes"
# os.environ["EVENTLET_HUB"] = "poll"
# os.environ["EVENTLET_NO_IPV6"] = "1"



# import os
from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager

# Import extensions and blueprints
from apps.models import db
from apps.routes.user import user_bp
from apps.routes.socket import socketio, register_socket_handlers, check_and_send_birthday_notifications
from flask_migrate import Migrate

from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from datetime import datetime, timedelta

migrate = Migrate()

# Initialize JWTManager globally
jwt = JWTManager()

def create_app(test_config=None):
    """
    The Application Factory function. 
    It creates, configures, and returns the Flask application instance.
    """
    # --- Template path resolution ---
    # Current Directory: .../apps/routes
    current_dir = os.path.abspath(os.path.dirname(__file__))
    # Project Root: .../chat_app_project
    project_root = os.path.abspath(os.path.join(current_dir, '..', '..')) 
    template_folder_path = os.path.join(project_root, 'templates')
    
    app = Flask(__name__, 
                static_folder=os.path.join(project_root, 'static'), 
                template_folder=template_folder_path)
    
    # ===============================
    # 1. Configuration Setup
    # ===============================
    if test_config is None:
        # Load configuration from environment variables (provided by load_dotenv in app.py)
        app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SQLALCHEMY_DATABASE_URI')
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-dev-secret-key')
        app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-default')
        app.config['JWT_TOKEN_LOCATION'] = ['headers', 'cookies'] # Common config for JWT
        # ✅ Token valid for 72 hours (3 days)
        from datetime import timedelta
        app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=72)
    else:
        app.config.update(test_config)
    
    # Check for critical configuration
    if not app.config['SQLALCHEMY_DATABASE_URI']:
        print("❌ CRITICAL ERROR: DATABASE_URL not found.")
        
    # ===============================
    # 2. Initialize Extensions
    # ===============================
    db.init_app(app)
    jwt.init_app(app)
    
    migrate.init_app(app, db)
    
    # Initialize SocketIO
    socketio.init_app(app, cors_allowed_origins="*", async_mode="eventlet")

    register_socket_handlers(app) # Register the handlers defined in socket.py
    
    # ===============================
    # 3. Enable CORS
    # ===============================
    CORS(app, supports_credentials=True, resources={r"/api/*": {"origins": "*"}})

    # ===============================
    # 4. Register Blueprints
    # ===============================
    # All API routes are now under the 'user' blueprint
    app.register_blueprint(user_bp, url_prefix='/api')
    
    # Return the initialized application
    @app.route("/")
    def home():
        return {"status": "running", "message": "Backend is live!"}

    
    # Also register the root URL for the index.html
    # The '/' route is defined in user.py as well, so it maps to /api/ (oops)
    # Let's adjust: The web route '/' should be at the root, not under /api.
    # We will adjust user.py to have a separate blueprint or use the default app route.
    # For simplicity, we'll keep it as-is for now, assuming the frontend accesses 
    # the index.html at the root via app.py or a web server, and all APIs are under /api.
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        try:
            scheduler = BackgroundScheduler()
            
            run_date = datetime.now() + timedelta(minutes=1)
            print(f"Scheduling birthday check to run for testing at: {run_date.isoformat()}")

            scheduler.add_job(
                func=check_and_send_birthday_notifications, 
                trigger="date", 
                run_date=run_date,
                args=[app],
                id='birthday_checker',
                replace_existing=True
            )
            scheduler.start()
            atexit.register(lambda: scheduler.shutdown())
            print("✅ Scheduler started successfully")
        except Exception as e:
            print(f"⚠️ Scheduler setup failed: {e}")
    
    # ===============================
    # 5. Create tables and check database connection
    # ===============================
    with app.app_context():
        if app.debug:  # only for local developement
            db.create_all()
            print("Tables created in development.")
        else:
            print("Skipping db.create_all() in production.")

    # Return the initialized application, jwt, and db instance
    return app