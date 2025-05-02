# Akita Meshtastic Delivery Navigator - Configuration
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import logging
from datetime import timezone # For timezone object if needed

# --- Base Settings ---
# Coordinates (Latitude, Longitude) for the return base location
# Port Colborne, ON approx coords: 42.8860, -79.2493
RETURN_BASE_COORDS = (42.8860, -79.2493)
ARRIVAL_PROXIMITY_METERS = 50

# --- Flask Security ---
# IMPORTANT: Generate a strong, random secret key for production!
# Use: python -c "import secrets; print(secrets.token_hex(32))"
# Store this securely (e.g., environment variable), not directly in committed code if possible.
FLASK_SECRET_KEY = 'generate_a_real_secret_key_here_and_store_safely'

# Example storing hashed passwords (better in DB or secrets manager)
# Generate hash using: python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your_chosen_password'))"
ADMIN_USERS = {
    'admin': {
        # Replace with the actual generated hash for your chosen password
        'password_hash': 'pbkdf2:sha256:600000$exampleSalt$exampleHashValue...', # Example Hash - REPLACE THIS
        'roles': ['admin']
    }
}

# --- Meshtastic Settings ---
MESHTASTIC_CONNECTION_TYPE = "serial" # or "tcp"
MESHTASTIC_DEVICE_PATH = "/dev/ttyUSB0" # Ignored if type is "tcp"
MESHTASTIC_TCP_HOST = "localhost"    # Ignored if type is "serial"
MESHTASTIC_TCP_PORT = 4403           # Ignored if type is "serial"

# Node IDs of the delivery units (must start with '!'). Get from Meshtastic device info.
MESHTASTIC_TARGET_NODE_IDS = ["!YOUR_UNIT_NODE_ID_1", "!YOUR_UNIT_NODE_ID_2"]

# Optional: Specify a channel URL/name for broadcast messages if direct messaging fails.
MESHTASTIC_BROADCAST_CHANNEL = None # Example: "LongFast" or channel URL

# Unit Meshtastic connection (can be same as dispatch if running on same Pi)
UNIT_MESHTASTIC_CONNECTION_TYPE = "serial"
UNIT_MESHTASTIC_DEVICE_PATH = "/dev/ttyUSB0"
UNIT_MESHTASTIC_TCP_HOST = "localhost"
UNIT_MESHTASTIC_TCP_PORT = 4403

# --- Dispatch Server / Web UI Settings ---
WEB_SERVER_HOST = '0.0.0.0'
WEB_SERVER_PORT = 5000
DATABASE_PATH = 'akita_delivery.db' # Path to the SQLite database file
LOG_LEVEL = logging.INFO     # DEBUG, INFO, WARNING, ERROR
LOG_FILE = 'dispatch_server.log' # Set to None to log only to console

# --- Delivery Unit Settings ---
GPS_UPDATE_INTERVAL_SECONDS = 30

# --- Geocoding Settings ---
GEOCODER_PROVIDER = 'osm' # OpenStreetMap/Nominatim
# GEOCODER_API_KEY = "YOUR_API_KEY_IF_NEEDED"

# --- Error Handling & Retries ---
MESHTASTIC_SEND_RETRIES = 3         # Basic send attempts
MESHTASTIC_RETRY_DELAY_SECONDS = 2  # Delay between basic send retries
GEOCODER_RETRIES = 3
GEOCODER_RETRY_BASE_DELAY_SECONDS = 1 # Initial delay for geocoder retries

# --- State Management & Timeouts ---
ASSIGNMENT_ACK_TIMEOUT_SECONDS = 45 # How long to wait for assignment ACK
MAX_ASSIGNMENT_RETRIES = 3        # How many times to resend assignment if no ACK
UNIT_OFFLINE_TIMEOUT_SECONDS = 300 # (5 minutes) Mark unit offline if no update
UNIT_MAX_GPS_FAILURES = 10       # Consecutive GPS fails before marking unit 'error'

# --- Map Settings (for Web UI) ---
MAP_DEFAULT_CENTER_LAT = RETURN_BASE_COORDS[0]
MAP_DEFAULT_CENTER_LON = RETURN_BASE_COORDS[1]
MAP_DEFAULT_ZOOM = 13

# --- Timezone ---
# Store dates in UTC in the database. Display in local time in UI via browser JS.
TIMEZONE = 'UTC'
