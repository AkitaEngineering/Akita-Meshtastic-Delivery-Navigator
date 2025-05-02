# akita_navigator/geocoder_util.py - Geocoding helper
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
# Licensed under GPLv3. See LICENSE file for details.

import geocoder
import logging
import config
import time
# from requests.exceptions import RequestException, Timeout, ConnectionError # Optional specific imports

logger = logging.getLogger(__name__)

def geocode_address(address):
    """Geocodes an address string with retry logic."""
    logger.info(f"Geocoding address: '{address}' using provider: {config.GEOCODER_PROVIDER}")
    if not address:
        logger.error("Geocode attempt failed: Address is empty.")
        return None, None

    for attempt in range(config.GEOCODER_RETRIES):
        try:
            # Delay before request (especially for OSM)
            # Apply delay even on first attempt to respect global rate limits
            delay = config.GEOCODER_RETRY_BASE_DELAY_SECONDS * (2 ** attempt) # Exponential backoff (starts at base delay)
            time.sleep(delay)

            g = geocoder.osm(address) # Assuming OSM based on config default
            # TODO: Add dynamic provider selection based on config.GEOCODER_PROVIDER if needed

            if g.ok:
                latitude = g.latlng[0]
                longitude = g.latlng[1]
                logger.info(f"Geocoding successful (Attempt {attempt + 1}/{config.GEOCODER_RETRIES}): ({latitude}, {longitude})")
                return latitude, longitude
            else:
                logger.warning(f"Geocoding attempt {attempt + 1} failed for '{address}'. Status: {g.status}")
                if g.status == "ZERO_RESULTS":
                     logger.error(f"Zero results found for '{address}'. No retries needed.")
                     return None, None
                # Continue to retry for other errors (like OVER_QUERY_LIMIT, network issues)

        except ImportError:
             logger.error("Geocoder library not found. Install 'geocoder'.")
             return None, None # Cannot retry
        # except (RequestException, Timeout, ConnectionError) as e: # Catch specific network errors if requests is direct dependency
        #      logger.warning(f"Network error during geocoding attempt {attempt + 1}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error during geocoding attempt {attempt + 1} for '{address}': {e}")

        # If loop continues, it means failure occurred and retries remain

    logger.error(f"Geocoding failed for address '{address}' after {config.GEOCODER_RETRIES} attempts.")
    return None, None
