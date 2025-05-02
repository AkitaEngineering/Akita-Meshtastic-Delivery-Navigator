# akita_navigator/web/app.py - Flask App Creation and Configuration
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

from flask import Flask
from flask_login import LoginManager, UserMixin
import logging
import config # Project configuration

logger = logging.getLogger(__name__)
meshtastic_interface_instance = None

# --- User Model & Authentication Setup ---
# Uses user definition from config.py for simplicity
# In production, replace with a proper database user store
class User(UserMixin):
    """Simple User class for Flask-Login."""
    def __init__(self, id):
        self.id = id
        # In a real app, add roles, names, etc.

    # In a real app, get password hash from DB based on self.id
    def get_password_hash(self):
        user_record = config.ADMIN_USERS.get(self.id)
        return user_record.get('password_hash') if user_record else None

login_manager = LoginManager()
login_manager.login_view = 'login' # Function name of the login route
login_manager.login_message_category = 'info'
login_manager.login_message = "Please log in to access this page."

@login_manager.user_loader
def load_user(user_id):
    """Flask-Login hook to load a user by ID."""
    # Check if user exists in our config store
    if user_id in config.ADMIN_USERS:
        return User(user_id)
    return None
# --- End User Model ---

def create_app(mesh_interface=None):
    """Creates and configures the Flask application."""
    global meshtastic_interface_instance
    meshtastic_interface_instance = mesh_interface

    # Use instance_relative_config=True if using instance folder for secrets
    app = Flask(__name__,
                template_folder='templates',
                static_folder='../../static', # Point to static folder at project root
                instance_relative_config=False)

    # --- Configuration ---
    app.config['SECRET_KEY'] = config.FLASK_SECRET_KEY
    if not app.config['SECRET_KEY'] or app.config['SECRET_KEY'] == 'generate_a_real_secret_key_here_and_store_safely':
         logger.critical("FATAL: FLASK_SECRET_KEY is not set or is set to the default placeholder! Application will not run securely.")
         raise ValueError("FLASK_SECRET_KEY is not configured securely in config.py")

    # Pass relevant config to templates (use selectively)
    app.config['TEMPLATE_CONFIG'] = {
        'map_default_lat': config.MAP_DEFAULT_CENTER_LAT,
        'map_default_lon': config.MAP_DEFAULT_CENTER_LON,
        'map_default_zoom': config.MAP_DEFAULT_ZOOM,
        'base_lat': config.RETURN_BASE_COORDS[0],
        'base_lon': config.RETURN_BASE_COORDS[1],
        'gps_update_interval_seconds': config.GPS_UPDATE_INTERVAL_SECONDS,
        'unit_offline_timeout_seconds': config.UNIT_OFFLINE_TIMEOUT_SECONDS,
    }

    # Initialize Flask-Login
    login_manager.init_app(app)

    # --- Register Blueprints or Routes ---
    with app.app_context():
        from . import routes # Import and register routes
        # If using Blueprints:
        # from .routes import bp as main_blueprint
        # app.register_blueprint(main_blueprint)

    logger.info("Flask app created and configured.")
    return app

def get_meshtastic_interface():
    """Provides access to the shared Meshtastic interface instance."""
    # This is a simple way for routes to access the instance.
    # Consider dependency injection frameworks for larger apps.
    return meshtastic_interface_instance
