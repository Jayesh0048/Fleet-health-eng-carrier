import os
from dotenv import load_dotenv

load_dotenv()

SAMSARA_API_TOKEN = os.getenv("SAMSARA_API_TOKEN")

def get_live_vehicle_stats():
    # 1. Fetch the master asset list to get the internal Samsara IDs
    assets_url = "https://api.samsara.com/assets"
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {SAMSARA_API_TOKEN}"
    }
    
    assets_resp = requests.get(assets_url, headers=headers)
    if assets_resp.status_code != 200:
        print("Failed to get assets.")
        return

    # Filter out trailers, keep only vehicles
    vehicles = [a for a in assets_resp.json().get("data", []) if a.get("type") == "vehicle"]
    
    # Get the first 5 vehicle IDs to test
    vehicle_ids = [str(a.get("id")) for a in vehicles[:5]]
    ids_string = ",".join(vehicle_ids)
    
    print(f"Fetching live fault codes and odometer readings...\n")

    # 2. Query the stats feed for faultCodes and obdOdometerMeters
    stats_url = f"https://api.samsara.com/fleet/vehicles/stats/feed?types=faultCodes,obdOdometerMeters&vehicleIds={ids_string}"
    
    stats_resp = requests.get(stats_url, headers=headers)
    if stats_resp.status_code != 200:
        print(f"Failed to get stats: {stats_resp.text}")
        return

    # 3. Parse and display the data safely
    stats_data = stats_resp.json().get("data", [])
    
    for stat in stats_data:
        v_name = stat.get("name")
        odometer_data = stat.get("obdOdometerMeters")
        
        # Safely handle the odometer data whether it is a list or dictionary
        if isinstance(odometer_data, list) and len(odometer_data) > 0:
            odometer_meters = odometer_data[0].get("value")
        elif isinstance(odometer_data, dict):
            odometer_meters = odometer_data.get("value")
        else:
            odometer_meters = None
            
        # Convert meters to miles
        miles = round(odometer_meters * 0.000621371) if odometer_meters else "Unknown"
        print(f"Truck: {v_name} | Odometer: {miles} mi")
        
        # Extract J1939 fault codes safely
        fault_codes_data = stat.get("faultCodes")
        active_faults = []
        
        if isinstance(fault_codes_data, list) and len(fault_codes_data) > 0:
            j1939_faults = fault_codes_data[0].get("value", {}).get("j1939", [])
            active_faults = [f for f in j1939_faults if f.get("isActive")]
        elif isinstance(fault_codes_data, dict):
            j1939_faults = fault_codes_data.get("j1939", [])
            active_faults = [f for f in j1939_faults if f.get("isActive")]
        
        if not active_faults:
            print("  -> Status: HEALTHY (No active J1939 faults)")
        else:
            print(f"  -> Status: {len(active_faults)} ACTIVE FAULTS DETECTED:")
            for f in active_faults:
                print(f"     [!] SPN {f.get('spn')} FMI {f.get('fmi')} - {f.get('description')}")
        print("-" * 40)

if __name__ == "__main__":
    get_live_vehicle_stats()