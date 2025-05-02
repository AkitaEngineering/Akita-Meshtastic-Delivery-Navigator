# dispatch_server.py - Main application for the Dispatch Server
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import threading
import logging
import logging.handlers # For rotating file handler
import time
import queue
from waitress import serve
import sys

import config # Project configuration
from akita_navigator.web.app import create_app
from akita_navigator.meshtastic_iface import DispatchMeshtasticInterface
from akita_navigator.database import initialize_database, check_and_update_offline_units

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger() # Get root logger
root_logger.setLevel(config.LOG_LEVEL)

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# File Handler (Optional, Rotating)
if config.LOG_FILE:
    try:
        # Rotate log file, keep 5 backups, max 5MB each
        file_handler = logging.handlers.RotatingFileHandler(
            config.LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
         root_logger.error(f"Could not configure file logging to {config.LOG_FILE}: {e}")


logger = logging.getLogger(__name__) # Get logger for this module
logger.info("Dispatch Server Starting Up...")
logger.info(f"Log Level set to: {logging.getLevelName(config.LOG_LEVEL)}")

# --- Global Shared Resources ---
stop_event = threading.Event()
# Queue for messages received from Meshtastic to be processed by a worker
incoming_message_queue = queue.Queue(maxsize=500) # Limit queue size


# --- Worker Threads ---

def message_processor_worker(mesh_interface):
    """Processes incoming messages from the queue."""
    logger.info("Starting incoming message processor worker...")
    while not stop_event.is_set():
        try:
            message_data, packet = incoming_message_queue.get(block=True, timeout=1.0)
            logger.debug(f"Dequeued message: Type={message_data.get('type')}, From={packet.get('fromId')}")
            try:
                 # Use the internal handler that now expects dequeued messages
                 mesh_interface._handle_incoming_message(message_data, packet)
            except Exception as proc_e:
                 logger.error(f"Error processing dequeued message: {proc_e}", exc_info=True)
            finally:
                 incoming_message_queue.task_done()
        except queue.Empty:
            continue # Timeout, check stop_event
        except Exception as e:
             logger.error(f"Error in message processor worker loop: {e}", exc_info=True)
             time.sleep(1)
    logger.info("Incoming message processor worker stopping.")


def run_meshtastic_service(mesh_interface, incoming_queue):
    """Runs Meshtastic connection logic and queues received messages."""
    logger.info("Starting Meshtastic listener thread...")

    def queued_on_receive(packet, interface):
        """Callback wrapper to put received packets onto the queue."""
        # Basic filtering to avoid noise / own messages
        if packet and 'decoded' in packet and 'payload' in packet['decoded'] and 'fromId' in packet:
             sender_node_id = packet.get('fromId')
             my_node_id = mesh_interface._node_info.get('user',{}).get('id') if mesh_interface._node_info else None
             if sender_node_id and my_node_id and sender_node_id == my_node_id:
                 logger.debug("Ignored own message.")
                 return # Ignore message from self

             payload_bytes = packet['decoded']['payload']
             try:
                 payload_str = payload_bytes.decode('utf-8')
                 message_data = json.loads(payload_str)
                 # Simple validation? Check if it has a 'type'?
                 if not isinstance(message_data, dict) or 'type' not in message_data:
                      logger.warning(f"Received non-standard JSON payload from {sender_node_id}: {payload_str}")
                      return

                 try:
                      incoming_queue.put_nowait((message_data, packet))
                      logger.debug(f"Enqueued message type {message_data.get('type')} from {sender_node_id}")
                 except queue.Full:
                      logger.error(f"Incoming message queue FULL! Message from {sender_node_id} dropped.")
             except (UnicodeDecodeError, json.JSONDecodeError):
                  logger.warning(f"Received non-JSON/undecodable payload from {sender_node_id}")
             except Exception as e:
                  logger.error(f"Error decoding/parsing payload: {e}", exc_info=True)
        elif packet and 'decoded' in packet and packet['decoded'].get('portnum') == 'TEXT_MESSAGE_APP':
             # Optionally handle plain text messages if needed
             logger.debug(f"Received plain text message (ignored): {packet['decoded'].get('text')}")
        else:
             logger.debug(f"Ignoring other packet type: {packet.get('decoded', {}).get('portnum')}")


    mesh_interface.subscribe_receive_handler(queued_on_receive)

    while not stop_event.is_set():
        if not mesh_interface._is_connected:
            logger.info("Meshtastic disconnected in listener thread. Attempting reconnect...")
            if mesh_interface.connect():
                 logger.info("Meshtastic reconnected by listener thread.")
                 # Re-subscribe might be needed if pubsub instance was lost, but handled in connect()
            else:
                 logger.warning("Meshtastic reconnection failed. Will retry in 30s.")
                 stop_event.wait(30)
                 continue
        # Keep thread alive, checking stop event periodically
        stop_event.wait(15) # Check stop event every 15 seconds

    logger.info("Meshtastic listener thread stopping.")
    mesh_interface.close() # Close connection cleanly


def run_offline_checker():
     """Periodically checks for and updates offline units."""
     logger.info("Starting offline unit checker thread...")
     while not stop_event.is_set():
          try:
               logger.debug("Running offline unit check...")
               check_and_update_offline_units()
          except Exception as e:
               logger.error(f"Error in offline unit checker: {e}", exc_info=True)
          # Wait for the next check interval (e.g., 60 seconds)
          # Use stop_event.wait for faster shutdown response
          interval_start = time.monotonic()
          while time.monotonic() - interval_start < 60.0:
                if stop_event.wait(1.0): break # Wait 1s at a time, break if stopped
                if stop_event.is_set(): break
          if stop_event.is_set(): break # Exit outer loop if stopped

     logger.info("Offline unit checker thread stopping.")


def main():
    """Initializes components and starts the server."""
    logger.info("--- Initializing Akita Dispatch Server ---")

    # Initialize Database
    try:
        initialize_database()
    except Exception as db_e:
        logger.critical(f"FATAL: Database initialization failed: {db_e}", exc_info=True)
        return # Cannot continue without DB

    # Create Meshtastic Interface
    mesh_interface = DispatchMeshtasticInterface()
    # Connection attempt happens in listener thread

    # --- Start Worker Threads FIRST ---
    logger.info("Starting worker threads...")
    msg_processor_thread = threading.Thread(target=message_processor_worker, args=(mesh_interface,), name="MsgProcessorThread", daemon=True)
    offline_checker_thread = threading.Thread(target=run_offline_checker, name="OfflineCheckerThread", daemon=True)
    msg_processor_thread.start()
    offline_checker_thread.start()

    # --- Start Meshtastic Listener Thread ---
    logger.info("Starting Meshtastic listener thread...")
    meshtastic_thread = threading.Thread(target=run_meshtastic_service, args=(mesh_interface, incoming_message_queue), name="MeshtasticThread", daemon=True)
    meshtastic_thread.start()

    # --- Restart Pending ACK Timers ---
    logger.info("Scheduling pending ACK timer restart...")
    # Delay slightly to allow interface setup
    def delayed_ack_restart():
         logger.info("Waiting briefly before restarting ACK timers...")
         time.sleep(10) # Adjust delay as needed for interface to stabilize
         if not stop_event.is_set():
              logger.info("Attempting to restart pending ACK timers...")
              try:
                   mesh_interface.restart_pending_ack_timers()
              except Exception as restart_e:
                   logger.error(f"Error restarting pending ACK timers: {restart_e}", exc_info=True)
         else:
              logger.info("Skipping ACK restart, shutdown initiated.")
    restart_thread = threading.Thread(target=delayed_ack_restart, daemon=True)
    restart_thread.start()


    # Create Flask App
    logger.info("Creating Flask web application...")
    try:
         app = create_app(mesh_interface=mesh_interface) # Pass interface if needed by routes
    except Exception as app_e:
         logger.critical(f"FATAL: Failed to create Flask application: {app_e}", exc_info=True)
         # Signal threads to stop?
         stop_event.set()
         # Wait for threads?
         meshtastic_thread.join(timeout=2)
         msg_processor_thread.join(timeout=2)
         offline_checker_thread.join(timeout=2)
         return


    # Run Web Server using Waitress
    logger.info(f"Starting Waitress web server on {config.WEB_SERVER_HOST}:{config.WEB_SERVER_PORT}")
    try:
        serve(app, host=config.WEB_SERVER_HOST, port=config.WEB_SERVER_PORT, threads=8)
    except OSError as os_e:
         if "address already in use" in str(os_e).lower():
              logger.critical(f"FATAL: Port {config.WEB_SERVER_PORT} is already in use. Stop other process or change port in config.py.")
         else:
              logger.critical(f"FATAL: Could not start web server: {os_e}", exc_info=True)
    except Exception as serve_e:
         logger.critical(f"FATAL: Web server failed unexpectedly: {serve_e}", exc_info=True)
    finally:
        # Shutdown initiated (e.g., Ctrl+C on Waitress)
        logger.info("--- Initiating Graceful Shutdown ---")
        stop_event.set()

        logger.info("Waiting for Meshtastic thread...")
        meshtastic_thread.join(timeout=5)
        logger.info("Waiting for Message Processor thread...")
        # Give processor a moment to finish current item if queue isn't empty
        try:
             incoming_message_queue.join() # Wait for queue tasks to be marked done
        except NotImplementedError: pass # Join might not be needed if using task_done correctly
        msg_processor_thread.join(timeout=5)
        logger.info("Waiting for Offline checker thread...")
        offline_checker_thread.join(timeout=5)

        logger.info("--- Akita Dispatch Server Shutdown Complete ---")

if __name__ == "__main__":
    main()
