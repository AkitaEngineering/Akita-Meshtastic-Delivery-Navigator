# akita_navigator/gps_handler.py - GPS reading logic via gpsd
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import gpsd
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
_gpsd_connected = False

def initialize_gps():
    """Connects to the local gpsd service."""
    global _gpsd_connected
    if _gpsd_connected: return True
    try:
        logger.info("Connecting to gpsd...")
        gpsd.connect()
        # Verify connection with a quick read?
        try:
             packet = gpsd.get_current()
             logger.info(f"GPSD connected. Initial mode: {packet.mode}")
             _gpsd_connected = True
             return True
        except Exception as read_e:
             logger.error(f"Connected to gpsd, but failed initial read: {read_e}. Check gpsd status.")
             try: gpsd.close() # Try to close if initial read failed
             except: pass
             _gpsd_connected = False
             return False
    except Exception as e:
        logger.error(f"Failed to connect to gpsd: {e}")
        logger.error("Ensure gpsd is running and configured.")
        _gpsd_connected = False
        return False

def get_gps_location():
    """Gets current GPS location from gpsd."""
    global _gpsd_connected
    if not _gpsd_connected:
        logger.warning("GPSD not connected. Attempting reconnect...")
        if not initialize_gps(): return None
    try:
        packet = gpsd.get_current()
        # Mode values: 0=NO_FIX, 1=NO_FIX, 2=FIX_2D, 3=FIX_3D
        if packet.mode >= 2:
            ts = packet.time # gpsd typically provides UTC string
            # Ensure timestamp is valid ISO UTC string
            timestamp_iso = None
            if isinstance(ts, str) and len(ts) > 10: # Basic check
                 try:
                      # Parse and ensure UTC timezone awareness
                      dt_obj = datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(timezone.utc)
                      timestamp_iso = dt_obj.isoformat()
                 except ValueError:
                      logger.warning(f"Could not parse GPS time string '{ts}'. Using current UTC.")
            if not timestamp_iso:
                 timestamp_iso = datetime.now(timezone.utc).isoformat()

            location_data = {
                'latitude': packet.lat, 'longitude': packet.lon,
                'altitude': packet.alt if hasattr(packet, 'alt') and packet.mode >= 3 else None, # Need 3D fix for altitude
                'speed': packet.hspeed if hasattr(packet, 'hspeed') else None,
                'timestamp': timestamp_iso
            }
            logger.debug(f"GPS fix: {location_data}")
            return location_data
        else:
            logger.debug(f"Waiting for GPS fix (Mode: {packet.mode})")
            return None
    except gpsd.NoFixError:
         logger.debug("No GPS fix available.")
         return None
    except StopIteration:
         logger.warning("GPSD stream ended unexpectedly. Attempting reconnect.")
         _gpsd_connected = False
         try: gpsd.close()
         except: pass
         return None
    except Exception as e:
        logger.error(f"Error reading from gpsd: {e}", exc_info=True)
        _gpsd_connected = False
        try: gpsd.close()
        except: pass
        return None

def close_gps():
    """Closes the connection to gpsd."""
    global _gpsd_connected
    if _gpsd_connected:
        try:
             gpsd.close()
             logger.info("Closed connection to gpsd.")
        except Exception as e:
             logger.error(f"Error closing gpsd connection: {e}")
        finally:
            _gpsd_connected = False
