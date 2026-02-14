import json
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("THE_SPORTS_ACCESS_USER")
PASSWORD = os.getenv("THE_SPORTS_ACCESS_SECRET")

# TheSports MQTT Topic
# The competition ID for NBA is: 49vjxm8xt4q6odg
# Try different topic patterns to find what works:
# TOPIC = "#"  (everything - use for discovery)
# TOPIC = "basketball/#"  (all basketball)
# TOPIC = "49vjxm8xt4q6odg/#"  (specific NBA competition with wildcard)
# TOPIC = "49vjxm8xt4q6odg"  (just the competition ID)

# Start with everything to discover the correct topic structure
TOPIC = "49vjxm8xt4q6odg"

"""
"id": "l5ergytldj8zr8k",
    "competition_id": "49vjxm8xt4q6odg",
    "home_team_id": "9k82re8td2nrepz",
    "away_team_id": "kjw2r02tdkkqz84",
"""

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected OK")
        # Subscribe with QoS 0 (default). You can pass qos=1 if supported/needed.
        result = client.subscribe(TOPIC)
        print(f"Subscribed to: {TOPIC}")
        print(f"Subscribe result: {result}")
        print("Waiting for messages... (Note: data only flows during live games)")
    elif rc in (4, 5):
        print("Auth failed: check username/key and IP whitelist")
    else:
        print(f"Connection failed, rc={rc}")

def on_message(client, userdata, msg):
    # msg.topic is the MQTT topic (useful because different update types may be split by topic)
    print("\n" + "="*80)
    print("TOPIC:", msg.topic)

    # payload is bytes; sometimes it's JSON, sometimes compressed or plain text
    raw = msg.payload.decode("utf-8", errors="replace")

    # Try JSON parse
    try:
        data = json.loads(raw)
        print("DATA:", json.dumps(data, indent=2))
        
        # Parse expected live data fields
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    match_id = item.get("id")
                    print(f"\nMatch ID: {match_id}")
                    if "score" in item:
                        print(f"  Score data: {item['score']}")
                    if "stats" in item:
                        print(f"  Stats data: {item['stats']}")
                    if "players" in item:
                        print(f"  Players data available")
                    if "tlive" in item:
                        print(f"  Text live data available")
    except json.JSONDecodeError:
        print("RAW:", raw)
    
    print("="*80)

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