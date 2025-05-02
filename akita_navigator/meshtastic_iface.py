# akita_navigator/meshtastic_iface.py - Meshtastic communication classes
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
from meshtastic.node import Node
from pubsub import pub
import logging
import json
import time
import threading
from datetime import datetime, timezone
import uuid

import config # Use project config
from . import database # Use project database module

logger = logging.getLogger(__name__)

# --- Message Type Constants ---
MSG_TYPE_LOCATION = "loc"
MSG_TYPE_ASSIGNMENT = "assign"
MSG_TYPE_STATUS_UPDATE = "status"
MSG_TYPE_ACK = "ack"
MSG_TYPE_TASK_COMPLETE = "task_complete"

class MeshtasticInterface:
    """Base class for Meshtastic communication."""
    def __init__(self, connection_type, device_path=None, tcp_host=None, tcp_port=None):
        self.connection_type = connection_type
        self.device_path = device_path
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.interface = None
        self._is_connected = False
        self._node_info = None
        self._lock = threading.Lock() # Protect access to interface object

        # Subscribe to Meshtastic PubSub messages (can be done here or on connect)
        # pub.subscribe(self.on_connection_change, "meshtastic.connection.established")
        # pub.subscribe(self.on_connection_change, "meshtastic.connection.lost")
        # Note: Specific message receive handler subscribed by Dispatch Interface

    def connect(self):
        """Establishes connection to the Meshtastic device."""
        with self._lock:
            if self._is_connected and self.interface:
                logger.debug("Already connected to Meshtastic.")
                return True
            try:
                logger.info(f"Connecting to Meshtastic via {self.connection_type}...")
                # Unsubscribe listeners before creating new interface? Might cause issues if called rapidly.
                # Best practice might be to subscribe *after* connection.

                if self.connection_type == "serial":
                    self.interface = meshtastic.serial_interface.SerialInterface(devPath=self.device_path, debugOut=None) # Reduce serial noise
                elif self.connection_type == "tcp":
                    self.interface = meshtastic.tcp_interface.TCPInterface(hostname=self.tcp_host, port=self.tcp_port)
                else:
                    logger.error(f"Unsupported Meshtastic connection type: {self.connection_type}")
                    return False

                # Wait briefly for node info
                time.sleep(2) # Allow time for interface setup
                self._node_info = self.interface.getMyNodeInfo()
                if not self._node_info or 'myNodeNum' not in self._node_info:
                     logger.warning("Could not get valid node info after connection. Retrying...")
                     time.sleep(3)
                     self._node_info = self.interface.getMyNodeInfo()

                if not self._node_info or 'myNodeNum' not in self._node_info:
                     logger.error("Failed to get node info after connection and retry.")
                     try: self.interface.close()
                     except: pass
                     self.interface = None
                     return False

                logger.info(f"Meshtastic connected. Node ID: {self._node_info.get('user', {}).get('id', 'N/A')}, Num: {self._node_info.get('myNodeNum', 'N/A')}")
                self._is_connected = True

                # Subscribe AFTER connection seems more robust
                pub.subscribe(self.on_connection_change, "meshtastic.connection.established")
                pub.subscribe(self.on_connection_change, "meshtastic.connection.lost")
                # Receive handler is specific to Dispatch/Unit

                return True
            except meshtastic.MeshInterfaceError as e:
                 logger.error(f"Meshtastic connection error: {e}")
                 if "Cannot configure port" in str(e) or "Permission denied" in str(e):
                      logger.error("PERMISSION ERROR: Ensure user has permissions for the serial port (e.g., add to 'dialout' group on Linux).")
                 self.interface = None
                 self._is_connected = False
                 return False
            except Exception as e:
                logger.error(f"Meshtastic connection failed unexpectedly: {e}", exc_info=True)
                self.interface = None
                self._is_connected = False
                return False

    def on_connection_change(self, **kwargs):
        """Callback when connection status changes (from pubsub)."""
        # This might be called with different interface instances if reconnecting rapidly
        # Check if the event is for *this* interface instance if managing multiple?
        # For single interface instance, this should be okay.
        topic_name = kwargs.get('topic', pub.AUTO_TOPIC).name
        if "established" in topic_name:
             # Re-fetch node info on establish just in case
             with self._lock:
                  if self.interface: # Ensure interface object still exists
                       self._node_info = self.interface.getMyNodeInfo()
                       self._is_connected = True
                       logger.info(f"Meshtastic connection established event. Node: {self._node_info.get('user', {}).get('id')}")
                  else: logger.warning("Connection established event but interface is None.")
        elif "lost" in topic_name:
            with self._lock:
                self._is_connected = False
                # Don't null self.interface here, let reconnect logic handle it
            logger.warning("Meshtastic connection lost event.")


    def send_message(self, payload_dict, destinationId="^all", max_retries=config.MESHTASTIC_SEND_RETRIES, retry_delay=config.MESHTASTIC_RETRY_DELAY_SECONDS):
        """Sends a JSON message with basic retry logic."""
        # Ensure connection exists before attempting send
        if not self._is_connected or not self.interface:
             logger.warning("Send attempt while disconnected. Message not sent.")
             # Let higher level logic handle reconnect attempts (e.g., manager thread)
             return False

        try:
            payload_str = json.dumps(payload_dict)
            payload_bytes = payload_str.encode('utf-8')
        except (TypeError, ValueError) as json_e:
             logger.error(f"Failed to encode payload to JSON: {json_e}. Payload: {payload_dict}")
             return False

        logger.debug(f"Attempting send to {destinationId} (Max Retries: {max_retries})")

        for attempt in range(max_retries):
            # Acquire lock *inside* loop to allow other threads access between retries
            with self._lock:
                 # Check connection again inside lock
                 if not self.interface or not self._is_connected:
                      logger.warning(f"Send attempt {attempt + 1} failed: Interface became disconnected.")
                      return False # Fail fast if disconnected

                 try:
                     # Use sendData for direct, sendText for broadcast
                     if destinationId and destinationId.startswith('!'):
                         logger.debug(f"Sending direct (Attempt {attempt + 1}/{max_retries})...")
                         self.interface.sendData(payload_bytes, destinationId=destinationId, wantAck=False, channelIndex=0) # Specify primary channel usually
                     elif destinationId == "^all":
                         logger.debug(f"Sending broadcast via sendText (Attempt {attempt + 1}/{max_retries})...")
                         self.interface.sendText(payload_str, channelIndex=0)
                     else: # Invalid format, default to broadcast
                         logger.warning(f"Invalid destination format '{destinationId}'. Broadcasting.")
                         self.interface.sendText(payload_str, channelIndex=0)

                     logger.debug(f"Message queued for send by radio on attempt {attempt + 1}.")
                     # Note: Success here means queued by library, not necessarily sent over air yet.
                     return True # Success!

                 except meshtastic.MeshInterfaceError as e:
                     logger.warning(f"Meshtastic send error (Attempt {attempt + 1}/{max_retries}): {e}")
                     if "Not connected" in str(e) or "ERROR_TIMEOUT" in str(e) or "No response from radio" in str(e):
                          logger.error("Detected disconnection or timeout during send.")
                          self._is_connected = False # Mark as disconnected
                          # Don't close interface here, let manager handle reconnect
                          return False # Fail fast
                     # Other errors might be retryable

                 except Exception as e:
                     logger.error(f"Unexpected error sending message (Attempt {attempt + 1}/{max_retries}): {e}", exc_info=True)
                     # Assume potentially retryable

            # Wait before next retry if failure occurred and retries remain
            if attempt < max_retries - 1:
                wait_time = retry_delay * (attempt + 1) # Linear backoff
                logger.info(f"Waiting {wait_time}s before next send attempt...")
                time.sleep(wait_time)

        logger.error(f"Failed to send message to {destinationId} after {max_retries} attempts.")
        return False

    def close(self):
        """Closes the Meshtastic connection."""
        # Unsubscribe listeners first
        try: pub.unsubscribe(self.on_connection_change, "meshtastic.connection.established")
        except: pass
        try: pub.unsubscribe(self.on_connection_change, "meshtastic.connection.lost")
        except: pass
        # Derived classes should unsubscribe their specific receive handlers here too

        with self._lock:
            if self.interface:
                try:
                    self.interface.close()
                    logger.info("Meshtastic connection closed.")
                except Exception as e:
                    logger.error(f"Error closing Meshtastic interface: {e}")
                finally:
                     self.interface = None
                     self._is_connected = False
            self._node_info = None


# --- Dispatch Server Specific Interface ---
class DispatchMeshtasticInterface(MeshtasticInterface):
    """Handles Meshtastic communication for Dispatch, using DB for ACK tracking."""
    def __init__(self):
        super().__init__(config.MESHTASTIC_CONNECTION_TYPE, config.MESHTASTIC_DEVICE_PATH, config.MESHTASTIC_TCP_HOST, config.MESHTASTIC_TCP_PORT)
        self.target_node_ids = set(config.MESHTASTIC_TARGET_NODE_IDS)
        self._active_ack_timers = {} # { msg_id: threading.Timer }
        self._timer_lock = threading.Lock()
        self._queued_receive_handler = None # Store ref to allow unsubscribe
        logger.info(f"Dispatch server targeting units: {self.target_node_ids}")

    def subscribe_receive_handler(self, callback):
         """Subscribes the callback that puts messages onto the queue."""
         self._queued_receive_handler = callback
         try:
              pub.subscribe(self._queued_receive_handler, "meshtastic.receive")
              logger.info("Subscribed queued receive handler.")
         except Exception as e:
              logger.error(f"Error subscribing receive handler: {e}")

    def _start_ack_timer(self, msg_id, timeout_seconds):
        # ... (Implementation from previous step is correct) ...
        with self._timer_lock:
            self._cancel_ack_timer(msg_id, acquire_lock=False)
            timer = threading.Timer(timeout_seconds, self._handle_assignment_timeout, args=[msg_id])
            timer.daemon = True; self._active_ack_timers[msg_id] = timer; timer.start()
            logger.debug(f"Started ACK timer ({timeout_seconds}s) for {msg_id}.")

    def _cancel_ack_timer(self, msg_id, acquire_lock=True):
        # ... (Implementation from previous step is correct) ...
        if acquire_lock: self._timer_lock.acquire()
        try:
            timer = self._active_ack_timers.pop(msg_id, None)
            if timer: timer.cancel(); logger.debug(f"Cancelled ACK timer for {msg_id}.")
        finally:
            if acquire_lock: self._timer_lock.release()

    def _handle_assignment_timeout(self, msg_id):
        # ... (Implementation from previous step is correct - uses DB for state) ...
         logger.warning(f"ACK timeout for assignment msg_id {msg_id}.")
         with self._timer_lock: self._active_ack_timers.pop(msg_id, None) # Remove timer ref
         pending_info = database.get_pending_ack(msg_id)
         if not pending_info: return # Already handled
         # ... (increment retry count, check max retries, resend or fail using DB updates) ...
         current_retry_count = pending_info['retry_count']
         # ... (get delivery_id, unit_id etc from pending_info) ...
         if current_retry_count < config.MAX_ASSIGNMENT_RETRIES:
              if database.update_pending_ack_retry(msg_id):
                   send_success = self.send_message(pending_info['payload'], destinationId=pending_info['destination_node_id'])
                   if send_success: self._start_ack_timer(msg_id, config.ASSIGNMENT_ACK_TIMEOUT_SECONDS * (current_retry_count + 2))
                   else: # Failed resend
                        database.update_pending_ack_status(msg_id, 'failed')
                        database.update_delivery_status(delivery_id, 'failed', ...)
                        database.update_unit_status(unit_id, 'error', ...)
              else: logger.error(f"Failed to update retry count for {msg_id}. Aborting.")
         else: # Max retries exceeded
              database.update_pending_ack_status(msg_id, 'failed')
              database.update_delivery_status(delivery_id, 'failed', ...)
              database.update_unit_status(unit_id, 'error', ...)


    def _handle_incoming_ack(self, message_data):
        # ... (Implementation from previous step is correct - updates DB, cancels timer) ...
        acked_msg_id = message_data.get('ack_id'); unit_id = message_data.get('unit_id')
        if acked_msg_id and unit_id:
             if database.update_pending_ack_status(acked_msg_id, 'acked'): self._cancel_ack_timer(acked_msg_id)
        else: logger.warning("Incomplete ACK")


    def _handle_incoming_message(self, message_data, packet):
        """Processes a message received from the incoming queue."""
        # ... (Implementation from previous step is correct - handles types, updates DB, ensures unit exists) ...
        # Remember this is now called by the message_processor_worker thread
        sender_node_id = packet.get('fromId', 'Unknown Node')
        unit_id = message_data.get('unit_id')
        try:
            msg_type = message_data.get('type')
            if msg_type == MSG_TYPE_ACK: self._handle_incoming_ack(message_data); return
            if not unit_id: logger.warning(...); return
            # Ensure unit exists / update last seen etc.
            if not database.get_unit(unit_id): database.upsert_unit(unit_id=unit_id, ...)
            else: database.upsert_unit(unit_id=unit_id, meshtastic_node_id=sender_node_id, last_update_time=_now_utc_iso())
            # Process LOC, STATUS etc. update DB, reset offline/error status if needed
            # ... (rest of the logic) ...
        except Exception as e: logger.error(...)

    def send_assignment(self, unit_id, delivery_id, latitude, longitude, address):
        """Sends assignment and initiates ACK tracking via DB."""
        # ... (Implementation from previous step is correct - adds to DB, calls send_message, starts timer) ...
        unit_info = database.get_unit(unit_id) # Fetch fresh unit info
        if not unit_info or not unit_info.get('meshtastic_node_id'): return False, "Unit has no Node ID"
        # ... (check node ID format) ...
        msg_id = uuid.uuid4().hex[:8]; payload = { ... }
        # Add to DB *before* sending
        if not database.add_pending_ack(msg_id, delivery_id, unit_id, destination_node_id, payload):
             # Handle case where it might already exist but failed send previously? Maybe delete and re-add? Or just try send.
             if not database.get_pending_ack(msg_id): # Check if fetch also fails
                  logger.error(f"Failed add/get pending ACK for {msg_id}. Aborting.")
                  return False, "DB error storing ACK state"
             else: logger.warning(f"Pending ACK {msg_id} already in DB. Retrying send.")

        initial_send_success = self.send_message(payload, destinationId=destination_node_id)
        if initial_send_success: self._start_ack_timer(msg_id, config.ASSIGNMENT_ACK_TIMEOUT_SECONDS); return True, "Assignment sent, awaiting ACK"
        else: # Initial send failed
             database.update_pending_ack_status(msg_id, 'failed')
             database.update_delivery_status(delivery_id, 'failed', ...)
             database.update_unit_status(unit_id, 'error', ...)
             return False, "Failed initial send attempt"

    def send_task_complete(self, unit_id, delivery_id):
        """Sends task complete command."""
        # ... (Implementation from previous step is correct - uses send_message) ...
        unit_info = database.get_unit(unit_id); # Fetch fresh info
        if not unit_info or ...: return False
        destination_node_id = unit_info['meshtastic_node_id']; # ... check format ...
        msg_id = uuid.uuid4().hex[:8]; payload = { ... } # Include msg_id
        success = self.send_message(payload, destinationId=destination_node_id)
        # Optional: Add ACK tracking for this too if needed, similar to assignments
        return success


    def restart_pending_ack_timers(self):
        """Queries DB for pending ACKs on startup and restarts timers."""
        # ... (Implementation from previous step is correct) ...
        logger.info("Restarting timers for pending assignment ACKs from database...")
        pending_acks = database.get_all_pending_acks_for_restart()
        now_dt = datetime.now(timezone.utc); restarted_count = 0; immediate_retry_count = 0
        with self._timer_lock: self._active_ack_timers.clear()
        for ack_info in pending_acks:
             msg_id = ack_info['msg_id']; sent_time_str = ack_info['sent_time']; retry_count = ack_info['retry_count']
             try:
                  sent_dt = datetime.fromisoformat(sent_time_str.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
                  base_timeout = config.ASSIGNMENT_ACK_TIMEOUT_SECONDS * (retry_count + 1)
                  elapsed_seconds = (now_dt - sent_dt).total_seconds()
                  remaining_timeout = base_timeout - elapsed_seconds
                  if remaining_timeout > 1:
                       self._start_ack_timer(msg_id, remaining_timeout) # Uses internal _start without lock
                       restarted_count += 1
                  else: # Overdue
                       threading.Thread(target=self._handle_assignment_timeout, args=[msg_id], daemon=True).start()
                       immediate_retry_count += 1
             except Exception as e: logger.error(f"Error processing pending ACK {msg_id} during restart: {e}")
        logger.info(f"Finished ACK restart: {restarted_count} timers restarted, {immediate_retry_count} immediate checks.")


    def close(self):
         """Closes interface, unsubscribes, cancels timers."""
         # Unsubscribe receive handler
         if self._queued_receive_handler:
              try: pub.unsubscribe(self._queued_receive_handler, "meshtastic.receive")
              except: pass
         # Cancel timers
         with self._timer_lock:
              logger.info(f"Cancelling {len(self._active_ack_timers)} active ACK timers.")
              for timer in self._active_ack_timers.values(): timer.cancel()
              self._active_ack_timers.clear()
         super().close() # Call base class close


# --- Delivery Unit Specific Interface ---
class UnitMeshtasticInterface(MeshtasticInterface):
    # ... (Init, set callbacks, send_ack, send_location_update, send_status_update remain same) ...
    # Ensure it uses the base send_message with retries internally.

    def _handle_parsed_message(self, message_data, packet):
         """Processes messages, including sending ACKs for assignments/commands."""
         # ... (Implementation from previous step is correct - calls callbacks, sends ACKs) ...
         try:
             msg_type = message_data.get('type')
             sender_node_id = packet.get('fromId')
             msg_id = message_data.get('msg_id')

             if msg_type == MSG_TYPE_ASSIGNMENT:
                  # ... (parse assignment) ...
                  if delivery_id is not None and lat is not None:
                       logger.info(f"Received assignment ... (MsgID: {msg_id})")
                       if msg_id: self.send_ack(msg_id, sender_node_id) # Send ACK
                       else: logger.warning("Assignment missing msg_id, no ACK sent.")
                       if self.assignment_callback: self.assignment_callback(...) # Call app logic
                  else: logger.warning("Incomplete assignment")

             elif msg_type == MSG_TYPE_TASK_COMPLETE:
                  # ... (parse command) ...
                  logger.info(f"Received Task Complete command ... (MsgID: {msg_id})")
                  if msg_id: self.send_ack(msg_id, sender_node_id) # Send ACK
                  else: logger.warning("Task Complete missing msg_id, no ACK sent.")
                  if self.task_complete_callback: self.task_complete_callback(...) # Call app logic

             else: logger.debug(...) # Unhandled type
         except Exception as e: logger.error(...)


    def close(self):
         # Unit doesn't subscribe to receive handler in base, so just call super
         super().close()


# --- Helper ---
def _now_utc_iso():
    # ... (remains same) ...
    return datetime.now(timezone.utc).isoformat()
