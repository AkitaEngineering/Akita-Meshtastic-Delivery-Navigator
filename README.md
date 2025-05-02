# Akita Meshtastic Delivery Navigator
# Copyright (C) 2025 Akita Engineering <http://www.akitaengineering.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version. See the LICENSE file for details.

This Akita Engineering project provides a system for delivery and dispatch using Meshtastic, enabling real-time tracking and management of delivery units.

**NOTE:** This code requires configuration, testing, and potentially further refinement for production use.

## Features

* **Delivery Management:** Create deliveries via web UI, geocode addresses automatically.
* **Status Tracking:** Track deliveries through stages: `pending`, `assigned`, `en_route`, `arrived`, `completed`, `failed` with state validation.
* **Unit Assignment:** Assign deliveries to idle units via web UI modal.
* **Real-Time Tracking:** Monitor unit locations and statuses on an interactive map (Leaflet.js) with timestamps indicating data freshness.
* **Meshtastic Integration:** Uses JSON messages over Meshtastic. Includes ACK/Retry for critical assignment messages stored persistently.
* **Refined Unit Logic:** Hybrid approach - units auto-detect arrival, dispatcher manually confirms completion via UI, triggering unit return/idle state.
* **Database Storage:** Stores delivery, unit, status, and pending ACK information in an SQLite database (WAL mode enabled).
* **Web UI/UX:** Improved interface using Pico.css and custom styles, featuring tables, client-side sorting/filtering, modals, and non-blocking notifications.
* **Web Security:** Basic user authentication implemented using Flask-Login (requires secure password/secret key setup).
* **Scalability Features:** Includes persistent ACKs and decoupled incoming message processing via a queue to improve resilience and responsiveness.

## Requirements

* Python 3.7+
* Meshtastic Devices (Configured with desired channel settings)
* GPS Module (for delivery units) + `gpsd` service running
* Python Libraries (see `requirements.txt`)
* Network access for the Dispatch Server (Web UI, Geocoding)
* A securely generated `FLASK_SECRET_KEY`.
* A securely generated *hashed* password for the admin user.

## Project Structure

(See structure diagram provided in previous responses or infer from file list)

## Installation

1.  **Clone:** `git clone <repository_url>` & `cd Akita-Meshtastic-Delivery-Navigator`
2.  **Virtual Env:** `python -m venv venv` & `source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
3.  **Dependencies:** `pip install -r requirements.txt`
4.  **Hardware Setup:** Configure Meshtastic radios (see Security section below). Setup and verify `gpsd`. Note your unit Node IDs.

## Configuration (CRITICAL)

1.  **Edit `config.py`:**
    * **`FLASK_SECRET_KEY`:** Generate a strong random key (e.g., `python -c "import secrets; print(secrets.token_hex(32))"`) and set its value. **Store securely!**
    * **`ADMIN_USERS`:**
        * Choose a strong password for the 'admin' user.
        * Generate its hash: Run `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YOUR_CHOSEN_PASSWORD'))"` in your activated venv.
        * Replace the placeholder `password_hash` value in `config.py` with the generated hash string (starts with `pbkdf2:sha256...`).
    * Set `RETURN_BASE_COORDS` to your actual base location (Default: Port Colborne, ON).
    * Update `MESHTASTIC_TARGET_NODE_IDS` with the *actual* Meshtastic Node IDs (e.g., `!aabbccdd`) of your delivery units.
    * Configure `MESHTASTIC_CONNECTION_TYPE`, `MESHTASTIC_DEVICE_PATH` / `MESHTASTIC_TCP_HOST`/`PORT` for the dispatch radio.
    * Configure `UNIT_MESHTASTIC_CONNECTION_TYPE` / `_DEVICE_PATH` / `_TCP_HOST` / `_TCP_PORT` (often same as dispatch if using serial).
    * Review other settings (timeouts, intervals, geocoder).
2.  **Edit `delivery_unit.py`:**
    * Set the `DELIVERY_UNIT_ID` variable near the top to a unique, *human-readable* identifier for *each* physical unit (e.g., "Truck-01", "Bike-A").

## Usage

1.  **Run Dispatch Server:**
    ```bash
    python dispatch_server.py
    ```
    * Access the web UI at `http://<server_ip>:<WEB_SERVER_PORT>` (Default: `http://<your_ip>:5000`).
    * Login using the username (`admin`) and the **plain-text password** you chose (the one you hashed for the config).
2.  **Run Delivery Unit:**
    * Ensure `gpsd` is running and has a fix.
    * Ensure `DELIVERY_UNIT_ID` is set correctly in the script.
    * Run on the unit hardware:
        ```bash
        python delivery_unit.py
        ```

## Security Considerations

* **Web Authentication:** Default credentials (`admin`/`password`) **MUST BE CHANGED** via hashing in `config.py`. The Flask `SECRET_KEY` **MUST BE SET SECURELY**. Implement proper user management and password policies for production. Consider HTTPS.
* **Meshtastic Channel Encryption (Highly Recommended):** Configure your Meshtastic devices (Dispatch & Units) to use an **encrypted channel with a strong Pre-Shared Key (PSK)** using Meshtastic tools (Web UI, CLI, app). This application *does not* handle the PSK; it relies on the device's configuration. **Do not transmit sensitive data over unencrypted channels.**

## Scalability Considerations

* **Current Features:** Persistent ACKs (DB), Decoupled Message Input (Queue), DB WAL Mode, Basic Retries.
* **Future Enhancements:** For very large scale, consider migrating to Celery/RabbitMQ, PostgreSQL, Asyncio architecture, or multiple Meshtastic gateways. Optimize API calls (pagination) and consider WebSockets for UI updates.

## Testing (CRITICAL)

This application requires **extensive testing** across various scenarios before reliable use. Please refer to detailed testing steps outlined in development discussions or create your own comprehensive test plan covering:
* Startup/Shutdown & Restarts (including ACK timer recovery)
* Full Delivery Lifecycle (Create, Assign, ACK, En Route, Arrive, Complete Command, Return, Idle)
* Failure Cases (No ACK, Geocoding Fail, Manual Fail, Unit Offline, GPS Fail, Invalid State Changes)
* UI Functionality (Login, Filtering, Sorting, Modals, Map Interaction, Notifications, Responsiveness)
* Multi-Unit Interaction

## Contributing

Contributions welcome! Please submit pull requests or open issues. Remember this project is licensed under GPLv3.

## License

This project is licensed under the GNU General Public License, Version 3 (GPLv3). See the [LICENSE](LICENSE) file for the full license text.
---
Copyright (c) 2025 Akita Engineering (http://www.akitaengineering.com)
