import meshtastic
import meshtastic.serial_interface
import json
import time
import threading
import gpsd
import math

interface = meshtastic.serial_interface.SerialInterface()
delivery_id = None
delivery_latitude = None
delivery_longitude = None
delivery_channel = None
return_latitude = None
return_longitude = None

gpsd.connect()

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) * math.sin(dlat / 2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) * math.sin(dlon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance

def send_gps():
    while True:
        try:
            packet = gpsd.get_current()
            latitude = packet.lat
            longitude = packet.lon
            if latitude is not None and longitude is not None:
                message = {
                    "type": "gps",
                    "latitude": latitude,
                    "longitude": longitude
                }
                if delivery_id:
                    message['delivery_id'] = delivery_id
                    if delivery_latitude and delivery_longitude:
                        distance = calculate_distance(latitude, longitude, delivery_latitude, delivery_longitude)
                        print(f"Distance to destination: {distance:.2f} km")
                interface.sendData(json.dumps(message), channel=delivery_channel if delivery_channel else None)
        except Exception as e:
            print(f"GPS Error: {e}")

        time.sleep(30)

def receive_messages():
    global delivery_id, delivery_latitude, delivery_longitude, delivery_channel, return_latitude, return_longitude
    while True:
        packet = interface.receive()
        if packet and 'decoded' in packet and 'portnum' in packet['decoded'] and packet['decoded']['portnum'] == meshtastic.mesh_pb.MeshMsg_PortNum.TEXT_MESSAGE_APP:
            try:
                message = json.loads(packet['decoded']['text'])
                if message['type'] == 'delivery':
                    delivery_id = message['delivery_id']
                    delivery_latitude = message['latitude']
                    delivery_longitude = message['longitude']
                    delivery_channel = message['channel']
                    print(f"Delivery assigned: {delivery_id}")
                elif message['type'] == 'return':
                    return_latitude = message['latitude']
                    return_longitude = message['longitude']
                    print(f"Return to: {return_latitude}, {return_longitude}")
                    delivery_id = None #Clear delivery information.
                    delivery_latitude = None
                    delivery_longitude = None
                    delivery_channel = None
                elif message['type'] == 'arrival':
                    message = {"type":"complete", "delivery_id":delivery_id}
                    interface.sendData(json.dumps(message), channel=delivery_channel)
                    print("Arrival Message Received, Complete Message Sent")

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error processing message: {e}")
        time.sleep(1)

threading.Thread(target=send_gps, daemon=True).start()
threading.Thread(target=receive_messages, daemon=True).start()

while True:
    time.sleep(10)
