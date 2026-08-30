import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

# Mapping of port IDs to (latitude, longitude)
PORT_COORDINATES = {
    "Haldia": (22.03, 88.06),
    "Paradip": (20.26, 86.67),
    "Vizag": (17.68, 83.28),
    "Gangavaram": (17.62, 83.24),
    "Gopalpur": (19.30, 84.97),
    "Dhamra": (20.80, 86.96),
    "Sagar_Sandheads": (21.65, 88.05),
}

ROUTES_TO_CHECK = [
    ("HALDIA_PARADIP", "Haldia", "Paradip"),
    ("HALDIA_VIZAG", "Haldia", "Vizag"),
    ("PARADIP_VIZAG", "Paradip", "Vizag")
]

def get_midpoint(coord1, coord2):
    return ((coord1[0] + coord2[0]) / 2, (coord1[1] + coord2[1]) / 2)

def fetch_marine_weather(lat, lon, start_date_str, end_date_str):
    url = f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}&hourly=wave_height&start_date={start_date_str}&end_date={end_date_str}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"Error fetching data for lat={lat}, lon={lon}: {e}")
        return None

def build_weather_events():
    today = datetime.now(UTC).date()
    end_date = today + timedelta(days=7) # Next 7 days
    
    start_date_str = today.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    
    weather_events = []
    
    WAVE_HEIGHT_THRESHOLD_M = 1.5 # Using a low threshold to ensure we get some events for demo purposes
    
    for route_id, origin, dest in ROUTES_TO_CHECK:
        coord1 = PORT_COORDINATES.get(origin)
        coord2 = PORT_COORDINATES.get(dest)
        
        if not coord1 or not coord2:
            continue
            
        mid_lat, mid_lon = get_midpoint(coord1, coord2)
        print(f"Fetching weather for {route_id} at midpoint ({mid_lat:.2f}, {mid_lon:.2f})...")
        
        data = fetch_marine_weather(mid_lat, mid_lon, start_date_str, end_date_str)
        if not data or "hourly" not in data or "wave_height" not in data["hourly"]:
            continue
            
        times = data["hourly"]["time"]
        wave_heights = data["hourly"]["wave_height"]
        
        in_event = False
        event_start = None
        
        for i, (t_str, wh) in enumerate(zip(times, wave_heights)):
            if wh is None:
                continue
                
            if wh >= WAVE_HEIGHT_THRESHOLD_M and not in_event:
                in_event = True
                event_start = t_str
            elif wh < WAVE_HEIGHT_THRESHOLD_M and in_event:
                in_event = False
                event_end = t_str
                
                # Create an event
                event = {
                    "event_id": f"EVT_{route_id}_{event_start.replace(':', '').replace('-', '')}",
                    "route": route_id,
                    "start_time": event_start,
                    "end_time": event_end,
                    "delay_hours": 12, # Static delay logic
                    "severity": "GALE",
                    "max_wave_height_m": max([h for h in wave_heights[times.index(event_start):i] if h is not None])
                }
                weather_events.append(event)
                
        # Handle ongoing event at end of series
        if in_event:
            event = {
                "event_id": f"EVT_{route_id}_{event_start.replace(':', '').replace('-', '')}",
                "route": route_id,
                "start_time": event_start,
                "end_time": times[-1],
                "delay_hours": 12,
                "severity": "GALE",
                "max_wave_height_m": max([h for h in wave_heights[times.index(event_start):] if h is not None])
            }
            weather_events.append(event)
            
    print("\n--- Generated Weather Events ---")
    print(json.dumps(weather_events, indent=2))
    
    # Save to a file in src/data
    import os
    os.makedirs("../data", exist_ok=True)
    with open("../data/weather_events.json", "w") as f:
        json.dump(weather_events, f, indent=2)
    print("\nSaved to src/data/weather_events.json")

if __name__ == "__main__":
    build_weather_events()
