# delivery_unit.py - Main application run on the delivery unit hardware
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import time
import threading
import logging
import logging.handlers
import math
from datetime import datetime, timezone
import sys

import config # Use shared config (primarily for GPS interval, base coords etc)
from akita_navigator.meshtastic_iface import UnitMeshtasticInterface
from akita_navigator import gps_handler

# --- Unit Specific Config ---
# !!! CRITICAL: SET THIS FOR EACH UNIT !!!
DELIVERY_UNIT_ID = "Unit-Alpha"
# !!! CRITICAL: SET THIS FOR EACH UNIT !!!

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
unit_logger = logging.getLogger() # Root logger for unit
unit_logger.setLevel(config.LOG_LEVEL) # Use level from main config

# Console Handler
unit_console_handler = logging.StreamHandler(sys.stdout)
unit_console_handler.setFormatter(log_formatter)
unit_logger.addHandler(unit_console_handler)

# File Handler (Optional) - Use unit-specific log file
unit_log_file = f'{DELIVERY_UNIT_ID.lower()}.log'
try:
    unit_file_handler = logging.handlers.RotatingFileHandler(
        unit_log_file, maxBytes=2*1024*1024, backupCount=3, encoding='utf-8' # Smaller log for unit
    )
    unit_file_handler.setFormatter(log_formatter)
    unit_logger.addHandler(unit_file_handler)
except Exception as e:
     unit_logger.error(f"Could not configure file logging to {unit_log_file}: {e}")

logger = logging.getLogger(__name__) # Logger for this module
logger.info(f"--- Starting Delivery Unit: {DELIVERY_UNIT_ID} ---")
logger.info(f"Log Level set to: {logging.getLevelName(config.LOG_LEVEL)}")

# --- Global State ---
current_assignment = { "delivery_id": None, "latitude": None, "longitude": None, "address": None }
unit_status = "offline" # Start as offline, will change on connect/GPS fix
stop_event = threading.Event()
last_location = None
consecutive_gps_failures = 0
# Make mesh_interface global for easier access in callbacks
mesh_interface = None

# --- Helper Functions ---
def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in meters using Haversine formula."""
    R = 6371000 # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def set_unit_status(new_status, delivery_id=None, force_send=False):
    """Updates local unit status and sends update via Meshtastic."""
    global unit_status, mesh_interface # Access global interface
    if new_status not in {'idle', 'assigned', 'en_route', 'arrived_dest', 'returning', 'error', 'offline'}:
         logger.error(f"Attempted to set invalid local status: {new_status}")
         return False

    status_changed = (new_status != unit_status)
    current_delivery = delivery_id if delivery_id else current_assignment.get("delivery_id")

    if status_changed:
        logger.info(f"Local unit status changing from '{unit_status}' to '{new_status}' (Delivery: {current_delivery})")
        unit_status = new_status # Update local state

        # Report status change via Meshtastic
        if mesh_interface and mesh_interface._is_connected:
            logger.info(f"Reporting status update '{unit_status}'...")
            send_success = mesh_interface.send_status_update(
                status=unit_status,
                delivery_id=current_delivery
            )
            if not send_success: logger.error(f"Failed to report status update '{unit_status}'.")
            return send_success
        elif mesh_interface:
             logger.warning(f"Cannot report status '{unit_status}': Meshtastic connected but flag is false?")
             return False
        else:
             logger.warning(f"Cannot report status '{unit_status}': Meshtastic interface not available.")
             return False
    elif force_send:
         if mesh_interface and mesh_interface._is_connected:
              logger.info(f"Forcing send of current status '{unit_status}'...")
              return mesh_interface.send_status_update(status=unit_status, delivery_id=current_delivery)
         else: return False # Cannot force send if not connected
    else:
        logger.debug(f"Local unit status remains '{unit_status}'")
        return True # No change needed, considered success

# --- Callbacks ---
def handle_incoming_assignment(delivery_id, lat, lon, address):
    """Callback for new assignment message."""
    global current_assignment, unit_status
    logger.info(f"Received new assignment via Meshtastic: Delivery {delivery_id} to '{address}'")
    # Overwrite current assignment
    current_assignment["delivery_id"] = delivery_id
    current_assignment["latitude"] = lat
    current_assignment["longitude"] = lon
    current_assignment["address"] = address
    # Status change (to 'assigned' or 'en_route') is handled by main loop / GPS loop
    if unit_status == 'idle':
         logger.info("Setting status to 'assigned' based on new assignment.")
         # Need mesh_interface here to report status change
         set_unit_status('assigned', delivery_id=delivery_id)

def handle_task_complete(completed_delivery_id):
    """Callback when dispatch signals task completion."""
    global unit_status, current_assignment
    logger.info(f"Received task complete command for delivery {completed_delivery_id}.")
    # Check if we are arrived for *this* delivery
    if unit_status == 'arrived_dest' and current_assignment.get("delivery_id") == completed_delivery_id:
        logger.info("Task confirmed complete. Transitioning to 'returning' state.")
        set_unit_status('returning') # Reports status change
    else:
        logger.warning(f"Received task complete for {completed_delivery_id}, but current state is '{unit_status}' (Assignment: {current_assignment.get('delivery_id')}). Ignoring.")


# --- Main Logic Threads ---
def gps_update_loop():
    """Periodically gets GPS data, updates state, and sends updates."""
    global last_location, unit_status, consecutive_gps_failures, mesh_interface
    logger.info("Starting GPS update loop...")
    gps_handler.initialize_gps() # Ensure gpsd connection attempted

    while not stop_event.is_set():
        start_time = time.monotonic()
        location = None
        try:
            location = gps_handler.get_gps_location()
            assigned_delivery_id = current_assignment.get("delivery_id") # Get current task

            if location and location.get('latitude') is not None:
                if consecutive_gps_failures > 0: logger.info("GPS fix acquired.")
                consecutive_gps_failures = 0
                last_location = location
                logger.debug(f"GPS Loc: ({location['latitude']:.4f}, {location['longitude']:.4f}), Spd: {location.get('speed', 0):.1f} m/s")

                # Report location update via Meshtastic (if connected)
                if mesh_interface and mesh_interface._is_connected:
                    send_loc_success = mesh_interface.send_location_update(
                        location['latitude'], location['longitude'], location['timestamp'])
                    if not send_loc_success: logger.error("Failed to send location update.")
                elif not mesh_interface: logger.warning("No mesh interface for loc update.")
                elif not mesh_interface._is_connected: logger.debug("Mesh not connected for loc update.")

                # --- State Logic based on GPS and Assignment ---
                if assigned_delivery_id is not None: # We have a task
                    if unit_status == 'assigned':
                         # Move to en_route once assigned and GPS is good (or maybe if speed > threshold?)
                         # Simple: Move immediately if assigned and GPS OK.
                         logger.info("Assignment active, GPS OK. Setting status to 'en_route'.")
                         set_unit_status('en_route', delivery_id=assigned_delivery_id)

                    elif unit_status == 'en_route':
                         dest_lat = current_assignment["latitude"]; dest_lon = current_assignment["longitude"]
                         distance_m = haversine(location['latitude'], location['longitude'], dest_lat, dest_lon)
                         logger.debug(f"Dist to Dest ({assigned_delivery_id}): {distance_m:.1f} m")
                         if distance_m <= config.ARRIVAL_PROXIMITY_METERS:
                              logger.info(f"Arrived at destination for delivery {assigned_delivery_id}. Reporting status.")
                              set_unit_status('arrived_dest', delivery_id=assigned_delivery_id)
                              # Wait here for task_complete command

                    elif unit_status == 'arrived_dest':
                         # Do nothing based on location, wait for command
                         logger.debug("Status arrived_dest, awaiting command.")

                    elif unit_status == 'returning':
                         base_lat = config.RETURN_BASE_COORDS[0]; base_lon = config.RETURN_BASE_COORDS[1]
                         distance_m = haversine(location['latitude'], location['longitude'], base_lat, base_lon)
                         logger.debug(f"Dist to Base: {distance_m:.1f} m")
                         if distance_m <= config.ARRIVAL_PROXIMITY_METERS:
                              logger.info("Arrived back at base.")
                              set_unit_status('idle')
                              # Clear completed assignment info
                              current_assignment.update({k: None for k in current_assignment})
                    # Other states (idle, error, offline) handled elsewhere or by commands

                # No assignment, not returning -> should be idle
                elif unit_status not in ['idle', 'returning', 'error', 'offline']:
                    logger.info(f"No active task/return. Setting status 'idle'. Current: {unit_status}")
                    set_unit_status('idle')

            else: # No valid GPS fix
                consecutive_gps_failures += 1
                if unit_status != 'error': # Only warn if not already in error
                    logger.warning(f"Waiting for GPS fix... (Failures: {consecutive_gps_failures}/{config.UNIT_MAX_GPS_FAILURES})")
                if consecutive_gps_failures >= config.UNIT_MAX_GPS_FAILURES and unit_status != 'error':
                    logger.error(f"GPS fix lost for {config.UNIT_MAX_GPS_FAILURES} checks. Setting unit status to 'error'.")
                    set_unit_status('error')

        except Exception as e:
            logger.error(f"Error in GPS loop: {e}", exc_info=True)
            consecutive_gps_failures += 1 # Count errors as failures
            if consecutive_gps_failures >= config.UNIT_MAX_GPS_FAILURES and unit_status != 'error':
                 set_unit_status('error')

        # Interval timing
        elapsed = time.monotonic() - start_time
        wait_time = max(0, config.GPS_UPDATE_INTERVAL_SECONDS - elapsed)
        # Use stop_event.wait for faster shutdown response
        if stop_event.wait(wait_time): break # Exit loop if stop event set

    logger.info("GPS update loop stopped.")
    gps_handler.close_gps()


def meshtastic_connection_manager():
     """Manages Meshtastic connection and resends status on reconnect."""
     global mesh_interface, unit_status
     logger.info("Starting Meshtastic connection manager thread...")
     initial_connect_attempted = False
     while not stop_event.is_set():
          if not mesh_interface:
               logger.error("Meshtastic interface not initialized yet in manager.")
               stop_event.wait(5)
               continue

          if not mesh_interface._is_connected:
               logger.info("Meshtastic disconnected. Attempting connect...")
               if mesh_interface.connect():
                    logger.info("Meshtastic connected by manager.")
                    initial_connect_attempted = True
                    # On successful (re)connect, send current status
                    set_unit_status(unit_status, force_send=True) # Force send
                    if last_location: # Also resend last known location
                         logger.info("Resending last known location after reconnect.")
                         mesh_interface.send_location_update(last_location['latitude'], last_location['longitude'], last_location['timestamp'])
               else:
                    if not initial_connect_attempted:
                         logger.warning("Initial Meshtastic connection failed. Will retry.")
                         initial_connect_attempted = True # Avoid spamming log
                    else:
                         logger.warning("Meshtastic reconnection failed. Will retry later.")
                    # Wait longer if connection fails
                    stop_event.wait(30)
                    continue # Skip normal wait

          # If connected, wait normally
          stop_event.wait(15) # Check connection status periodically

     logger.info("Meshtastic connection manager thread stopping.")
     if mesh_interface: mesh_interface.close() # Ensure close on exit

def main():
    """Initializes components and starts the delivery unit loops."""
    global mesh_interface # Allow modification by this function

    # --- Initialize Interfaces ---
    logger.info("Initializing Meshtastic interface...")
    mesh_interface = UnitMeshtasticInterface(
        unit_id=DELIVERY_UNIT_ID,
        connection_type=config.UNIT_MESHTASTIC_CONNECTION_TYPE,
        device_path=config.UNIT_MESHTASTIC_DEVICE_PATH,
        tcp_host=config.UNIT_MESHTASTIC_TCP_HOST,
        tcp_port=config.UNIT_MESHTASTIC_TCP_PORT
    )
    mesh_interface.set_assignment_callback(handle_incoming_assignment)
    mesh_interface.set_task_complete_callback(handle_task_complete)
    # Connection attempt happens in manager thread

    # --- Start Background Threads ---
    logger.info("Starting background threads...")
    # GPS Loop (Must run after mesh_interface is created)
    gps_thread = threading.Thread(target=gps_update_loop, name="GPSThread", daemon=True)
    # Meshtastic Connection Manager
    mesh_manager_thread = threading.Thread(target=meshtastic_connection_manager, name="MeshManagerThread", daemon=True)

    mesh_manager_thread.start() # Start mesh manager first to handle connections
    time.sleep(1) # Give manager a moment
    gps_thread.start()

    logger.info(f"--- Delivery Unit {DELIVERY_UNIT_ID} Running --- (Press Ctrl+C to exit)")
    try:
        # Keep main thread alive
        while not stop_event.is_set():
            # Check for main loop tasks if any (e.g., check error state, report battery?)
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\nCtrl+C received. Shutting down...")
    finally:
        logger.info("Setting stop event for threads...")
        stop_event.set()

        logger.info("Waiting for GPS thread...")
        gps_thread.join(timeout=5)
        logger.info("Waiting for Meshtastic Manager thread...")
        mesh_manager_thread.join(timeout=5)

        # Close interfaces (manager thread already closes mesh iface)
        # gps_handler.close_gps() # Closed by GPS thread

        logger.info(f"--- Delivery Unit {DELIVERY_UNIT_ID} Shutdown Complete ---")

if __name__ == "__main__":
    main()
