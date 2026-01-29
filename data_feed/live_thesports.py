import json
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("THE_SPORTS_ACCESS_USER")
PASSWORD = os.getenv("THE_SPORTS_ACCESS_SECRET")

# You must replace this with the actual American football topic from their docs.
# Examples of what it might look like:
# TOPIC = "american_football/#"
# TOPIC = "sports/american_football/#"
# TOPIC = "af/#"
TOPIC = "jw2r00b212orz84"

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected OK")
        # Subscribe with QoS 0 (default). You can pass qos=1 if supported/needed.
        client.subscribe(TOPIC)
        print(f"Subscribed to: {TOPIC}")
    elif rc in (4, 5):
        print("Auth failed: check username/key and IP whitelist")
    else:
        print(f"Connection failed, rc={rc}")

def on_message(client, userdata, msg):
    # msg.topic is the MQTT topic (useful because different update types may be split by topic)
    print("TOPIC:", msg.topic)

    # payload is bytes; sometimes it's JSON, sometimes compressed or plain text
    raw = msg.payload.decode("utf-8", errors="replace")

    # Try JSON parse
    try:
        data = json.loads(raw)
        print("DATA:", data)
    except json.JSONDecodeError:
        print("RAW:", raw)

if __name__ == "__main__":
    client = mqtt.Client(transport="websockets")

    # TLS for port 443
    client.tls_set()

    # Auth
    client.username_pw_set(USERNAME, PASSWORD)

    # Callbacks
    client.on_connect = on_connect
    client.on_message = on_message

    # Some MQTT brokers require an explicit WS path, commonly "/mqtt".
    # paho-mqtt lets you set it like this (ONLY if needed):
    # client.ws_set_options(path="/mqtt")

    client.connect("mq.thesports.com", 443, keepalive=60)
    client.loop_forever()