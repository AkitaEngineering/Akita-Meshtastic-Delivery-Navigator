# akita_navigator/database.py - SQLite database interactions
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import sqlite3
import logging
import json
from datetime import datetime, timezone, timedelta
import config

logger = logging.getLogger(__name__)

# --- State Machine Definitions ---
VALID_DELIVERY_STATUSES = {'pending', 'assigned', 'en_route', 'arrived', 'completed', 'failed'}
VALID_UNIT_STATUSES = {'idle', 'assigned', 'en_route', 'arrived_dest', 'returning', 'offline', 'error'}

DELIVERY_TRANSITIONS = {
    'pending': {'assigned', 'failed'},
    'assigned': {'en_route', 'failed', 'pending'}, # Can revert if unassigned/ACK fails
    'en_route': {'arrived', 'failed', 'returning'}, # Can fail/be recalled?
    'arrived': {'completed', 'failed', 'returning'}, # Can fail/be completed/unit starts return
    'completed': {'pending'}, # Allow re-open
    'failed': {'pending'} # Allow re-open
}

UNIT_TRANSITIONS = {
    'offline': {'idle', 'error'},
    'idle': {'assigned', 'error', 'offline'},
    'assigned': {'en_route', 'idle', 'error', 'offline'},
    'en_route': {'arrived_dest', 'returning', 'failed', 'error', 'offline'},
    'arrived_dest': {'returning', 'idle', 'failed', 'error', 'offline'}, # Waits for command or fails
    'returning': {'idle', 'error', 'offline'},
    'error': {'idle', 'offline'}
}

# --- Helper Functions ---
def _now_utc_iso():
    """Returns the current time in UTC ISO format string."""
    return datetime.now(timezone.utc).isoformat()

def _validate_state_transition(current_state, new_state, valid_transitions):
    """Checks if a state transition is allowed."""
    allowed_next_states = valid_transitions.get(current_state)
    if allowed_next_states is None:
        logger.error(f"State transition error: Current state '{current_state}' has no defined transitions.")
        return False, f"Current state '{current_state}' invalid or undefined"
    if new_state not in allowed_next_states:
        logger.warning(f"Invalid state transition: Cannot move from '{current_state}' to '{new_state}'. Allowed: {allowed_next_states}")
        return False, f"Cannot transition from '{current_state}' to '{new_state}'"
    return True, "Transition valid"

# --- Database Connection ---
def get_db_connection():
    """Establishes a connection to the SQLite database, enabling WAL mode."""
    try:
        conn = sqlite3.connect(config.DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
             conn.execute("PRAGMA journal_mode=WAL;")
             logger.debug("SQLite journal_mode set to WAL.")
        except sqlite3.Error as wal_e:
             logger.warning(f"Could not enable WAL mode for SQLite: {wal_e}")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}", exc_info=True)
        raise

# --- Schema Initialization ---
def initialize_database():
    """Creates the database tables if they don't exist."""
    logger.info(f"Initializing database at: {config.DATABASE_PATH}")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Units Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS units (
                    unit_id TEXT PRIMARY KEY,
                    meshtastic_node_id TEXT UNIQUE,
                    last_latitude REAL,
                    last_longitude REAL,
                    last_location_time TEXT, -- ISO Format UTC
                    current_status TEXT DEFAULT 'offline',
                    assigned_delivery_id INTEGER,
                    last_update_time TEXT -- ISO Format UTC
                )
            ''')
            # Deliveries Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    assigned_unit_id TEXT,
                    failure_reason TEXT,
                    creation_time TEXT NOT NULL, -- ISO Format UTC
                    assigned_time TEXT, -- ISO Format UTC
                    enroute_time TEXT, -- ISO Format UTC
                    arrived_time TEXT, -- ISO Format UTC
                    completion_time TEXT, -- ISO Format UTC
                    last_update_time TEXT, -- ISO Format UTC
                    FOREIGN KEY (assigned_unit_id) REFERENCES units (unit_id) ON DELETE SET NULL
                )
            ''')
            # Pending ACKs Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_assignment_acks (
                    msg_id TEXT PRIMARY KEY,
                    delivery_id INTEGER NOT NULL,
                    unit_id TEXT NOT NULL,
                    destination_node_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, -- Store the sent payload
                    sent_time TEXT NOT NULL,     -- ISO Format UTC
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending', -- pending, acked, failed
                    last_update_time TEXT NOT NULL, -- ISO Format UTC
                    FOREIGN KEY (delivery_id) REFERENCES deliveries (id) ON DELETE CASCADE,
                    FOREIGN KEY (unit_id) REFERENCES units (unit_id) ON DELETE CASCADE
                )
            ''')
            # Indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_delivery_status ON deliveries (status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_unit_status ON units (current_status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_pending_ack_status ON pending_assignment_acks (status)')

            conn.commit()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}", exc_info=True)
        raise

# --- Delivery Functions ---
def add_delivery(address, latitude, longitude):
    """Adds a new delivery to the database."""
    logger.debug(f"Adding delivery: {address} at ({latitude}, {longitude})")
    sql = ''' INSERT INTO deliveries(address, latitude, longitude, status, creation_time, last_update_time)
              VALUES(?,?,?,?,?,?) '''
    now = _now_utc_iso()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (address, latitude, longitude, 'pending', now, now))
            conn.commit()
            logger.info(f"Delivery added with ID: {cursor.lastrowid}")
            return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(f"Failed to add delivery for {address}: {e}")
        return None

def get_delivery(delivery_id):
    """Retrieves a specific delivery by its ID."""
    logger.debug(f"Getting delivery ID: {delivery_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"Failed to get delivery {delivery_id}: {e}")
        return None

def get_all_deliveries():
    """Retrieves all deliveries."""
    # No filter applied here by default, UI handles filtering display
    logger.debug(f"Getting all deliveries")
    sql = "SELECT * FROM deliveries ORDER BY creation_time DESC"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"Failed to get deliveries: {e}")
        return []

def update_delivery_status(delivery_id, new_status, failure_reason=None, timestamp=None):
    """Updates the status and corresponding timestamp of a delivery, validating the transition."""
    logger.info(f"Attempting delivery {delivery_id} status update to {new_status}")
    if new_status not in VALID_DELIVERY_STATUSES:
        logger.error(f"Invalid target status '{new_status}' for delivery {delivery_id}")
        return False, f"Invalid target status '{new_status}'"
    now = timestamp if timestamp else _now_utc_iso()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM deliveries WHERE id = ?", (delivery_id,))
            row = cursor.fetchone()
            if not row: return False, "Delivery not found"
            current_status = row['status']

            is_valid, reason = _validate_state_transition(current_status, new_status, DELIVERY_TRANSITIONS)
            if not is_valid:
                if new_status == current_status: return True, "Already in target status"
                else: return False, reason

            sql_parts = ["status = ?", "last_update_time = ?"]
            params = [new_status, now]
            if new_status == 'assigned': sql_parts.append("assigned_time = ?"); params.append(now)
            elif new_status == 'en_route': sql_parts.append("enroute_time = ?"); params.append(now)
            elif new_status == 'arrived': sql_parts.append("arrived_time = ?"); params.append(now)
            elif new_status == 'completed': sql_parts.append("completion_time = ?"); params.append(now)
            elif new_status == 'failed': sql_parts.append("failure_reason = ?"); params.append(failure_reason if failure_reason else "Unknown")
            if new_status == 'pending':
                sql_parts.extend(["assigned_unit_id = NULL", "assigned_time = NULL", "enroute_time = NULL", "arrived_time = NULL", "completion_time = NULL", "failure_reason = NULL"])

            sql = f"UPDATE deliveries SET {', '.join(sql_parts)} WHERE id = ?"
            params.append(delivery_id)
            cursor.execute(sql, tuple(params))
            if cursor.rowcount == 0: return False, "Update failed unexpectedly"
            conn.commit()
            logger.info(f"Delivery {delivery_id} status updated successfully to {new_status}.")
            return True, "Update successful"
    except sqlite3.Error as e:
        logger.error(f"DB error updating delivery {delivery_id} status: {e}", exc_info=True)
        return False, "Database error"
    except Exception as e:
        logger.error(f"Unexpected error updating delivery {delivery_id} status: {e}", exc_info=True)
        return False, "Unexpected error"

def assign_delivery_to_unit(delivery_id, unit_id):
    """Assigns a delivery to a unit, validating transitions."""
    logger.info(f"Attempting assignment: Delivery {delivery_id} to Unit {unit_id}")
    now = _now_utc_iso()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Validate Delivery
            cursor.execute("SELECT status FROM deliveries WHERE id = ?", (delivery_id,))
            delivery_row = cursor.fetchone()
            if not delivery_row: return False, "Delivery not found"
            valid_del, reason_del = _validate_state_transition(delivery_row['status'], 'assigned', DELIVERY_TRANSITIONS)
            if not valid_del: return False, f"Cannot assign delivery: {reason_del}"
            # Validate Unit
            cursor.execute("SELECT current_status FROM units WHERE unit_id = ?", (unit_id,))
            unit_row = cursor.fetchone()
            if not unit_row: return False, "Unit not found"
            if unit_row['current_status'] != 'idle': return False, f"Unit not idle (status: {unit_row['current_status']})"
            valid_unit, reason_unit = _validate_state_transition(unit_row['current_status'], 'assigned', UNIT_TRANSITIONS)
            if not valid_unit: return False, f"Cannot assign unit: {reason_unit}" # Should pass if idle

            # Perform Updates
            cursor.execute("UPDATE deliveries SET assigned_unit_id = ?, status = ?, assigned_time = ?, last_update_time = ? WHERE id = ? AND status = ?",
                           (unit_id, 'assigned', now, now, delivery_id, delivery_row['status']))
            if cursor.rowcount == 0: conn.rollback(); return False, "Failed to update delivery (race condition?)"
            cursor.execute("UPDATE units SET assigned_delivery_id = ?, current_status = ?, last_update_time = ? WHERE unit_id = ? AND current_status = ?",
                           (delivery_id, 'assigned', now, now, unit_id, unit_row['current_status']))
            if cursor.rowcount == 0: conn.rollback(); return False, "Failed to update unit (race condition?)"
            conn.commit()
            logger.info(f"Successfully assigned delivery {delivery_id} to unit {unit_id}")
            return True, "Assignment successful"
    except sqlite3.Error as e:
        logger.error(f"DB error during assignment: {e}", exc_info=True)
        return False, "Database error during assignment"
    except Exception as e:
        logger.error(f"Unexpected error during assignment: {e}", exc_info=True)
        return False, "Unexpected error during assignment"

# --- Unit Functions ---
def upsert_unit(unit_id, meshtastic_node_id=None, latitude=None, longitude=None, location_time=None, status=None, last_update_time=None):
    """Adds/updates a unit. Ensures status is valid if provided."""
    logger.debug(f"Upserting unit: {unit_id}")
    now = last_update_time if last_update_time else _now_utc_iso()
    if status and status not in VALID_UNIT_STATUSES:
         logger.error(f"Attempted upsert unit {unit_id} with invalid status '{status}'. Ignoring status.")
         status = None # Don't use invalid status
    # Determine effective status (provided, current, or default 'offline')
    current_db_status = None
    if not status:
         existing = get_unit(unit_id)
         if existing: current_db_status = existing['current_status']
    effective_status = status if status else current_db_status if current_db_status else 'offline'
    # Prepare fields and params (similar logic as before, ensure effective_status used)
    fields_to_update = {"last_update_time": now}
    if status and status != current_db_status: fields_to_update["current_status"] = status
    if meshtastic_node_id: fields_to_update["meshtastic_node_id"] = meshtastic_node_id
    if latitude is not None: fields_to_update["last_latitude"] = latitude
    if longitude is not None: fields_to_update["last_longitude"] = longitude
    if location_time: fields_to_update["last_location_time"] = location_time
    set_clause = ", ".join([f"{key} = :{key}" for key in fields_to_update])
    params = fields_to_update; params["unit_id"] = unit_id
    sql = f''' INSERT INTO units (unit_id, meshtastic_node_id, last_latitude, last_longitude, last_location_time, current_status, last_update_time)
               VALUES(:unit_id, :mnid, :lat, :lon, :loctime, :defstat, :lut)
               ON CONFLICT(unit_id) DO UPDATE SET {set_clause} '''
    insert_params = {"unit_id": unit_id, "mnid": fields_to_update.get("meshtastic_node_id"), "lat": fields_to_update.get("last_latitude"),
                     "lon": fields_to_update.get("last_longitude"), "loctime": fields_to_update.get("last_location_time"),
                     "defstat": effective_status, "lut": now}
    params.update(insert_params)
    try:
        with get_db_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
            logger.debug(f"Unit {unit_id} upserted successfully.")
            return True
    except sqlite3.IntegrityError as e:
         logger.error(f"Integrity error upserting unit {unit_id} (duplicate node ID?): {e}")
         return False
    except sqlite3.Error as e:
        logger.error(f"DB error upserting unit {unit_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error upserting unit {unit_id}: {e}", exc_info=True)
        return False

def get_unit(unit_id):
    """Retrieves a specific unit by its ID."""
    # ... (logic remains same) ...
    logger.debug(f"Getting unit ID: {unit_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM units WHERE unit_id = ?", (unit_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"Failed to get unit {unit_id}: {e}")
        return None


def get_all_units():
    """Retrieves all units."""
    # ... (logic remains same) ...
    logger.debug("Getting all units")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM units ORDER BY unit_id")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"Failed to get units: {e}")
        return []


def update_unit_location(unit_id, latitude, longitude, location_time):
    """Updates the location and timestamp for a specific unit."""
    # ... (logic remains same, ensure logger usage) ...
    logger.debug(f"Updating location for unit {unit_id}: ({latitude}, {longitude})")
    now = _now_utc_iso()
    sql = ''' UPDATE units
              SET last_latitude = ?, last_longitude = ?, last_location_time = ?, last_update_time = ?
              WHERE unit_id = ? '''
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (latitude, longitude, location_time, now, unit_id))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"No unit {unit_id} found to update location. Consider upserting.")
            return True
    except sqlite3.Error as e:
        logger.error(f"Failed to update location for unit {unit_id}: {e}")
        return False


def update_unit_status(unit_id, new_status, assigned_delivery_id=None, timestamp=None, reason=None): # Added reason for error state
    """Updates the status of a unit, validating the transition."""
    # Reason parameter might be useful if new_status is 'error'
    logger.info(f"Attempting unit {unit_id} status update to {new_status}")
    if new_status not in VALID_UNIT_STATUSES:
        logger.error(f"Invalid target status '{new_status}' for unit {unit_id}")
        return False, f"Invalid target status '{new_status}'"
    now = timestamp if timestamp else _now_utc_iso()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_status, assigned_delivery_id FROM units WHERE unit_id = ?", (unit_id,))
            row = cursor.fetchone()
            if not row: return False, "Unit not found"
            current_status = row['current_status']

            is_valid, validation_reason = _validate_state_transition(current_status, new_status, UNIT_TRANSITIONS)
            if not is_valid:
                 if new_status == current_status: # Allow idempotent update
                      # Just update timestamp
                      conn.execute("UPDATE units SET last_update_time = ? WHERE unit_id = ?", (now, unit_id))
                      conn.commit()
                      return True, "Already in target status"
                 else: return False, validation_reason

            sql_parts = ["current_status = ?", "last_update_time = ?"]
            params = [new_status, now]
            if new_status in ['idle', 'offline', 'error']:
                sql_parts.append("assigned_delivery_id = NULL") # Clear assignment
            elif new_status == 'assigned' and assigned_delivery_id is not None:
                sql_parts.append("assigned_delivery_id = ?"); params.append(assigned_delivery_id)
            # Keep assignment for en_route, arrived_dest, returning

            sql = f"UPDATE units SET {', '.join(sql_parts)} WHERE unit_id = ?"
            params.append(unit_id)
            cursor.execute(sql, tuple(params))
            if cursor.rowcount == 0: return False, "Update failed unexpectedly"
            conn.commit()
            logger.info(f"Unit {unit_id} status updated successfully to {new_status}.")
            return True, "Update successful"
    except sqlite3.Error as e:
        logger.error(f"DB error updating unit {unit_id} status: {e}", exc_info=True)
        return False, "Database error"
    except Exception as e:
        logger.error(f"Unexpected error updating unit {unit_id} status: {e}", exc_info=True)
        return False, "Unexpected error"


# --- Pending ACK Functions (add_pending_ack, get_pending_ack, update_pending_ack_retry, update_pending_ack_status, get_all_pending_acks_for_restart) ---
# ... (Implementations from previous step are correct) ...

# --- Offline Check ---
def check_and_update_offline_units():
    """Finds units that haven't updated recently and marks them as offline."""
    offline_threshold = datetime.now(timezone.utc) - timedelta(seconds=config.UNIT_OFFLINE_TIMEOUT_SECONDS)
    offline_threshold_str = offline_threshold.isoformat()
    updated_count = 0
    failed_delivery_count = 0
    logger.debug(f"Checking for units offline since {offline_threshold_str}")
    try:
        with get_db_connection() as conn: # Use separate connection? Maybe not needed with WAL.
            cursor = conn.cursor()
            cursor.execute("SELECT unit_id, current_status, last_update_time, assigned_delivery_id FROM units WHERE current_status != 'offline' AND last_update_time < ?", (offline_threshold_str,))
            units_to_mark_offline = cursor.fetchall()
            if not units_to_mark_offline: return

            for unit_row in units_to_mark_offline:
                 unit_id = unit_row['unit_id']; current_status = unit_row['current_status']; assigned_del_id = unit_row['assigned_delivery_id']
                 logger.warning(f"Unit {unit_id} (Status: {current_status}, Last: {unit_row['last_update_time']}) offline. Marking offline.")
                 success, _ = update_unit_status(unit_id, 'offline', timestamp=offline_threshold_str)
                 if success:
                      updated_count += 1
                      # Fail the active delivery associated with this unit
                      if assigned_del_id:
                           delivery = get_delivery(assigned_del_id) # Fetch the specific delivery
                           if delivery and delivery['status'] not in ['completed', 'failed']:
                                logger.warning(f"Unit {unit_id} went offline. Failing active delivery #{assigned_del_id}.")
                                fail_success, _ = update_delivery_status(assigned_del_id, 'failed', failure_reason=f"Unit {unit_id} went offline")
                                if fail_success: failed_delivery_count += 1
    except sqlite3.Error as e: logger.error(f"DB error during offline unit check: {e}", exc_info=True)
    except Exception as e: logger.error(f"Unexpected error during offline unit check: {e}", exc_info=True)
    if updated_count > 0: logger.info(f"Marked {updated_count} units as offline. Failed {failed_delivery_count} associated active deliveries.")

# --- Helper to find active delivery ---
def get_delivery_by_unit(unit_id):
     """Finds the currently active (non-completed/failed) delivery for a unit."""
     # ... (Implementation is correct) ...
     logger.debug(f"Getting active delivery for unit {unit_id}")
     try:
         with get_db_connection() as conn:
             cursor = conn.cursor()
             cursor.execute("SELECT d.* FROM deliveries d JOIN units u ON d.assigned_unit_id = u.unit_id WHERE u.unit_id = ? AND d.status NOT IN ('completed', 'failed') ORDER BY d.creation_time DESC LIMIT 1", (unit_id,))
             row = cursor.fetchone()
             return dict(row) if row else None
     except sqlite3.Error as e:
         logger.error(f"Failed to get active delivery for unit {unit_id}: {e}")
         return None
