# akita_navigator/web/routes.py - Flask Routes and API Endpoints
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

from flask import (Flask, render_template, request, jsonify, current_app, abort,
                   redirect, url_for, flash, session)
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import check_password_hash
from urllib.parse import urlparse, urljoin
import logging
from datetime import datetime # For year in footer

from .app import get_meshtastic_interface, User # Import User model from app
from .. import database as db # Use shorter alias
from .. import geocoder_util as geocoder
import config

logger = logging.getLogger(__name__)
app = current_app # Use Flask's current_app proxy

# --- Helper Functions ---
def is_safe_url(target):
    """Checks if a redirect target URL is safe (within the same host)."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

# --- Context Processor ---
@app.context_processor
def inject_template_config():
    """Injects configuration into templates."""
    return dict(config=current_app.config.get('TEMPLATE_CONFIG', {}),
                current_year=datetime.now().year)

# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        # Load user and hashed password (example using config)
        user_record = config.ADMIN_USERS.get(username)
        hashed_password = user_record.get('password_hash') if user_record else None

        if hashed_password and check_password_hash(hashed_password, password):
            user_obj = User(username)
            login_user(user_obj, remember=remember)
            logger.info(f"User '{username}' logged in.")
            next_page = request.args.get('next')
            if not next_page or not is_safe_url(next_page): next_page = url_for('index')
            return redirect(next_page)
        else:
            logger.warning(f"Failed login for '{username}'.")
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logger.info(f"User '{current_user.id}' logged out.")
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# --- Main Application Routes ---
@app.route('/')
@login_required
def index():
    """Renders the main dispatch UI."""
    return render_template('index.html') # Config injected by context processor

# --- API Endpoints (Protected) ---
@app.route('/api/state', methods=['GET'])
@login_required
def get_state():
    """Returns the current state of all deliveries and units."""
    logger.debug(f"API Request: /api/state by user '{current_user.id}'")
    try:
        deliveries = db.get_all_deliveries()
        units = db.get_all_units()
        return jsonify({"deliveries": deliveries, "units": units})
    except Exception as e:
        logger.error(f"API Error getting state: {e}", exc_info=True)
        return jsonify({"error": "Server error fetching state"}), 500

@app.route('/api/deliveries', methods=['POST'])
@login_required
def create_delivery():
    """Creates a new delivery."""
    logger.debug(f"API Request: POST /api/deliveries by user '{current_user.id}'")
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    address = data.get('address')
    if not address: return jsonify({"error": "Missing 'address' field"}), 400

    try:
        latitude, longitude = geocoder.geocode_address(address)
        if latitude is None or longitude is None:
            logger.warning(f"Geocoding failed for address: {address}")
            return jsonify({"error": "Geocoding failed", "details": f"Could not find coordinates for: {address}"}), 400

        delivery_id = db.add_delivery(address, latitude, longitude)
        if delivery_id:
            new_delivery = db.get_delivery(delivery_id) # Fetch newly created delivery
            return jsonify({"message": "Delivery created", "delivery": new_delivery}), 201
        else:
            logger.error("Database failed to add delivery after geocoding.")
            return jsonify({"error": "Database error", "details": "Failed to save delivery."}), 500
    except Exception as e:
         logger.error(f"API Error creating delivery: {e}", exc_info=True)
         return jsonify({"error": "Server error", "details": "An unexpected error occurred creating delivery."}), 500

@app.route('/api/assign', methods=['POST'])
@login_required
def assign_unit():
    """Assigns a delivery to a unit."""
    logger.debug(f"API Request: POST /api/assign by user '{current_user.id}'")
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    delivery_id = data.get('delivery_id')
    unit_id = data.get('unit_id')
    if not delivery_id or not unit_id: return jsonify({"error": "Missing 'delivery_id' or 'unit_id'"}), 400

    try:
        # 1. Update DB State
        db_success, db_message = db.assign_delivery_to_unit(delivery_id, unit_id)
        if not db_success:
            status_code = 400 if "not found" in db_message or "not idle" in db_message or "status changed" in db_message else 500
            return jsonify({"error": "Assignment failed (DB)", "details": db_message}), status_code

        # 2. Trigger Meshtastic Send (ACK tracking handled by interface)
        delivery = db.get_delivery(delivery_id)
        if not delivery: return jsonify({"error": "Server error", "details": "Delivery missing after DB assignment."}), 500

        mesh_interface = get_meshtastic_interface()
        if not mesh_interface:
             # Critical: Assignment in DB, but cannot notify unit. Requires manual intervention.
             logger.error("Meshtastic interface missing after DB assignment! Manual check needed.")
             return jsonify({"error": "Server configuration error", "details": "Assignment saved, but cannot contact Meshtastic device."}), 500

        initial_send_success, send_status_msg = mesh_interface.send_assignment(
            unit_id=unit_id, delivery_id=delivery_id, latitude=delivery['latitude'],
            longitude=delivery['longitude'], address=delivery['address'])

        if initial_send_success:
            # Status 202 Accepted: processing initiated (waiting for ACK)
            return jsonify({"message": "Assignment initiated", "details": send_status_msg}), 202
        else:
            # Initial send failed after retries. Revert DB changes.
            logger.error(f"Reverting assignment due to initial send failure for delivery {delivery_id}.")
            db.update_delivery_status(delivery_id, 'pending', failure_reason="Failed to send assignment")
            db.update_unit_status(unit_id, 'idle') # Set unit back to idle
            return jsonify({"error": "Assignment Failed", "details": f"Could not send command via Meshtastic: {send_status_msg}"}), 500

    except Exception as e:
         logger.error(f"API Error assigning unit: {e}", exc_info=True)
         return jsonify({"error": "Server error", "details": "An unexpected error occurred during assignment."}), 500

@app.route('/api/delivery/<int:delivery_id>/status', methods=['POST'])
@login_required
def manual_update_delivery_status(delivery_id):
    """Manually updates a delivery's status (completed, failed, pending)."""
    logger.debug(f"API Request: POST /api/delivery/{delivery_id}/status by user '{current_user.id}'")
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    new_status = data.get('status')
    reason = data.get('reason') # Optional reason for failure
    if not new_status or new_status not in ['completed', 'failed', 'pending']:
        return jsonify({"error": "Invalid or missing 'status'. Must be 'completed', 'failed', or 'pending'."}), 400

    try:
        delivery = db.get_delivery(delivery_id)
        if not delivery: abort(404, description=f"Delivery {delivery_id} not found")
        assigned_unit_id = delivery.get('assigned_unit_id')

        # Update delivery status in DB
        db_success, db_message = db.update_delivery_status(delivery_id, new_status, failure_reason=reason)

        if not db_success:
            status_code = 400 if "Invalid transition" in db_message else 500
            return jsonify({"error": "Update failed (DB)", "details": db_message}), status_code

        # Post-update actions
        response_message = f"Delivery status updated to {new_status}"
        mesh_interface = get_meshtastic_interface()

        if new_status == 'completed' and assigned_unit_id:
            if mesh_interface:
                logger.info(f"Sending Task Complete command to unit {assigned_unit_id} for delivery {delivery_id}")
                cmd_success = mesh_interface.send_task_complete(assigned_unit_id, delivery_id)
                if not cmd_success:
                     response_message += " (Warning: Failed to notify unit)"
                     logger.error(f"Failed to send Task Complete command to {assigned_unit_id}.")
            else:
                 response_message += " (Error: Mesh interface unavailable)"
                 logger.error("Cannot send Task Complete: Mesh interface missing.")

        elif new_status == 'failed' and assigned_unit_id:
            # If failed manually, set unit back to returning/idle
            logger.info(f"Delivery {delivery_id} marked failed. Setting unit {assigned_unit_id} to returning/idle.")
            # Try returning first, fallback to idle
            unit_ok, _ = db.update_unit_status(assigned_unit_id, 'returning')
            if not unit_ok: db.update_unit_status(assigned_unit_id, 'idle')

        elif new_status == 'pending' and assigned_unit_id:
             # If re-opened, ensure assigned unit goes back to idle
             logger.info(f"Delivery {delivery_id} re-opened. Setting unit {assigned_unit_id} to idle.")
             db.update_unit_status(assigned_unit_id, 'idle')

        updated_delivery = db.get_delivery(delivery_id)
        return jsonify({"message": response_message, "delivery": updated_delivery}), 200

    except Exception as e:
         logger.error(f"API Error updating delivery status: {e}", exc_info=True)
         return jsonify({"error": "Server error", "details": "An unexpected error occurred updating status."}), 500
