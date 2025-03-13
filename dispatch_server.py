import meshtastic
import meshtastic.serial_interface
import json
import time
import sqlite3
from flask import Flask, render_template, request, jsonify
import geocoder
import threading
import queue
import math

app = Flask(__name__)

# Database Setup
conn = sqlite3.connect('akita_delivery.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        delivery_id TEXT UNIQUE,
        unit_id TEXT,
        address TEXT,
        latitude REAL,
        longitude REAL,
        status TEXT,
        assigned_channel TEXT,
        arrival_time INTEGER,
        return_latitude REAL,
        return_longitude REAL,
        unit_latitude REAL,
        unit_longitude REAL
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS units (
        unit_id TEXT PRIMARY KEY,
        latitude REAL,
        longitude REAL,
        last_update INTEGER,
        status TEXT
    )
''')
conn.commit()

# Meshtastic Setup
interface = meshtastic.serial_interface.SerialInterface()

# Channel Pool
channel_pool = queue.Queue(maxsize=10)
for i in range(1, 11):
    channel_pool.put(f"delivery-channel-{i}")

def get_available_channel():
    return channel_pool.get()

def release_channel(channel):
    channel_pool.put(channel)

def send_message(node_id, payload, channel_str):
    interface.sendData(payload, node_id, channel=channel_str)

def receive_messages():
    while True:
        packet = interface.receive()
        if packet and 'decoded' in packet and 'portnum' in packet['decoded'] and packet['decoded']['portnum'] == meshtastic.mesh_pb.MeshMsg_PortNum.TEXT_MESSAGE_APP:
            try:
                message = json.loads(packet['decoded']['text'])
                from_id = packet['from']
                if 'type' in message:
                    if message['type'] == 'gps':
                        update_unit_location(from_id, message['latitude'], message['longitude'])
                        if 'delivery_id' in message:
                            update_delivery_gps(message['delivery_id'], message['latitude'], message['longitude'])
                    elif message['type'] == 'arrival':
                        update_delivery_arrival(message['delivery_id'])
                    elif message['type'] == 'return':
                        update_unit_return(from_id, message['latitude'], message['longitude'])
                    elif message['type'] == 'complete':
                        update_delivery_complete(message['delivery_id'])

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error processing message: {e}")
        time.sleep(1)

def update_unit_location(unit_id, latitude, longitude):
    cursor.execute('REPLACE INTO units (unit_id, latitude, longitude, last_update, status) VALUES (?, ?, ?, ?, ?)',
                   (unit_id, latitude, longitude, int(time.time()), "active"))
    conn.commit()

def update_delivery_gps(delivery_id, latitude, longitude):
    cursor.execute('UPDATE deliveries SET unit_latitude = ?, unit_longitude = ? WHERE delivery_id = ?', (latitude, longitude, delivery_id))
    conn.commit()

def update_delivery_arrival(delivery_id):
    cursor.execute('UPDATE deliveries SET status = ?, arrival_time = ? WHERE delivery_id = ?', ("arrived", int(time.time()), delivery_id))
    conn.commit()

def update_unit_return(unit_id, latitude, longitude):
    cursor.execute('UPDATE units SET latitude = ?, longitude = ? WHERE unit_id = ?', (latitude, longitude, unit_id))
    conn.commit()

def update_delivery_complete(delivery_id):
    cursor.execute('UPDATE deliveries SET status = ? WHERE delivery_id = ?', ("completed", delivery_id))
    conn.commit()

@app.route('/')
def index():
    cursor.execute("SELECT * FROM deliveries")
    deliveries = cursor.fetchall()
    cursor.execute("SELECT * FROM units")
    units = cursor.fetchall()

    return render_template('index.html', deliveries=deliveries, units=units)

@app.route('/create_delivery', methods=['POST'])
def create_delivery():
    address = request.form['address']
    unit_id = request.form['unit_id']
    g = geocoder.osm(address)
    if g.latlng:
        latitude, longitude = g.latlng
        delivery_id = str(int(time.time()))
        assigned_channel = get_available_channel()
        return_latitude = 34.0000
        return_longitude = -118.0000

        cursor.execute('INSERT INTO deliveries (delivery_id, unit_id, address, latitude, longitude, status, assigned_channel, return_latitude, return_longitude) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (delivery_id, unit_id, address, latitude, longitude, "assigned", assigned_channel, return_latitude, return_longitude))
        conn.commit()

        message = {
            "type": "delivery",
            "delivery_id": delivery_id,
            "latitude": latitude,
            "longitude": longitude,
            "channel": assigned_channel
        }
        send_message(unit_id, json.dumps(message), assigned_channel)
        return "Delivery created and message sent."
    else:
        return "Address not found."

@app.route('/get_unit_locations')
def get_unit_locations():
    cursor.execute('SELECT * FROM units')
    units = cursor.fetchall()
    unit_list = [{"unit_id": unit[0], "latitude": unit[1], "longitude": unit[2]} for unit in units]
    return jsonify(unit_list)

@app.route('/get_deliveries')
def get_deliveries():
    cursor.execute('SELECT delivery_id, latitude, longitude FROM deliveries')
    deliveries = cursor.fetchall()
    delivery_list = [{"delivery_id": delivery[0], "latitude": delivery[1], "longitude": delivery[2]} for delivery in deliveries]
    return jsonify(delivery_list)

@app.route('/return_unit', methods=['POST'])
def return_unit():
    unit_id = request.form['unit_id']
    delivery_id = request.form['delivery_id']
    cursor.execute('SELECT return_latitude, return_longitude, assigned_channel FROM deliveries WHERE delivery_id = ?', (delivery_id,))
    result = cursor.fetchone()
    if result:
        return_latitude, return_longitude, assigned_channel = result
        message = {
            "type": "return",
            "latitude": return_latitude,
            "longitude": return_longitude
        }
        send_message(unit_id, json.dumps(message), assigned_channel)
        release_channel(assigned_channel)
        return "Return message sent"
    else:
        return "Delivery not found for unit"

threading.Thread(target=receive_messages, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
