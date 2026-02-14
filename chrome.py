import json

with open("chrome-net-export-log.json") as f:
    data = json.load(f)

# Check WebSocket-related event types (480-500 range)
ws_types = [t for t in [481,482,483,484,485,486,487,488,489,490,491,492,493,494,495,498,499,500,501]]

for event in data.get("events", []):
    if event.get("type") in ws_types:
        print(f"TYPE {event['type']}:")
        print(json.dumps(event, indent=2)[:600])
        print("---\n")
        
# Also dump a few of each type to see what they contain
print("\n=== SAMPLE OF EACH WS-RANGE TYPE ===")
seen = {}
for event in data.get("events", []):
    t = event.get("type")
    if t in ws_types and t not in seen:
        seen[t] = True
        print(f"\nTYPE {t}:")
        print(json.dumps(event.get("params", {}), indent=2)[:400])