# Akita Meshtastic Delivery Navigator

This project provides a system for delivery and dispatch using Meshtastic, enabling real-time tracking and management of delivery units.

## Features

* **Delivery Management:**
    * Create and assign deliveries with address geocoding.
    * Track delivery status (assigned, arrived, completed).
    * Assign delivery units to specific deliveries.
* **Real-Time Tracking:**
    * Monitor unit locations on a map in real-time.
    * Track delivery progress and arrival.
    * Track return to base.
* **Meshtastic Integration:**
    * Uses Meshtastic for communication between the dispatch server and delivery units.
    * Efficient channel management with a channel pool.
* **Navigation Assistance:**
    * Provides basic navigation cues (distance to destination) to delivery units.
    * Display delivery locations on a map.
* **Database Storage:**
    * Stores delivery and unit information in an SQLite database.

## Requirements

* **Python 3.6+**
* **Meshtastic Devices**
* **GPS Module (for delivery units)**
* **Python Libraries:**
    * `meshtastic`
    * `flask`
    * `geocoder`
    * `pyserial`
    * `gpsd-clients`

## Installation

1.  **Clone the repository:**

    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **Install Python dependencies:**

    ```bash
    pip install meshtastic flask geocoder pyserial gpsd-clients
    ```

3.  **Ensure Meshtastic Setup:**

    * Connect your Meshtastic devices.
    * Verify Meshtastic communication is working.
    * Ensure gpsd is installed and running on the delivery unit devices.

## Usage

1.  **Run the Dispatch Server:**

    ```bash
    python dispatch_server.py
    ```

2.  **Run the Delivery Unit:**

    ```bash
    python delivery_unit.py
    ```

    * Run this script on the device carried by the delivery person.
    * Ensure gpsd is running on this device.

3.  **Access the Web Interface:**

    * Open a web browser and go to `http://<server_ip>:5000`.
    * Create deliveries, assign units, and monitor progress through the web interface.

## File Structure

* `dispatch_server.py`: The Flask application for the dispatch server.
* `delivery_unit.py`: The Python script for the delivery unit.
* `templates/index.html`: The HTML template for the web interface.
* `akita_delivery.db`: The SQLite database.
* `README.md`: This file.

## Configuration

* Modify the return coordinates in `dispatch_server.py` to match your base location.
* Adjust the channel pool size in `dispatch_server.py` as needed.
* The webserver runs on port 5000, this can be changed within the dispatch_server.py file.
* The gps update interval is 30 seconds, this can be changed within the delivery_unit.py file.

## Further Enhancements

* Implement more advanced navigation cues (bearing, turn-by-turn).
* Add error handling and retry mechanisms for Meshtastic communication.
* Improve the user interface for both the dispatch server and the delivery unit.
* Add security features (encryption, authentication).
* Add better map functionality, such as route planning.
* Create a Meshtastic plugin for the delivery unit, so that a computer is not required.

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues.
