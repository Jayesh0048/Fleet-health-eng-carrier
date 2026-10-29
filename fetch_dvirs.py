import os
from dotenv import load_dotenv

load_dotenv()

SAMSARA_API_TOKEN = os.getenv("SAMSARA_API_TOKEN")

def get_recent_dvirs():
    # Fetch DVIRs submitted in the last 7 days
    start_time = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    url = f"https://api.samsara.com/dvirs/stream?startTime={start_time}"
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {SAMSARA_API_TOKEN}"
    }
    
    print(f"Fetching driver inspection reports since {start_time}...\n")
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        print(f"Failed to fetch DVIRs: {response.text}")
        return
        
    dvirs = response.json().get("data", [])
    if not dvirs:
        print("No recent DVIRs found.")
        return
        
    for dvir in dvirs:
        vehicle_name = dvir.get("trailer", {}).get("name") or dvir.get("vehicle", {}).get("name", "Unknown Unit")
        safety_status = dvir.get("safetyStatus", "unspecified")
        
        # Extract notes from the driver or mechanic
        mechanic_notes = dvir.get("mechanicNotes", "")
        
        print(f"Unit: {vehicle_name} | Safety Status: {safety_status.upper()}")
        
        # Check for specific defects reported
        defects = dvir.get("defects", [])
        if defects:
            for defect in defects:
                comment = defect.get("comment", "")
                defect_type = defect.get("defectType", "General Defect")
                print(f"  -> Defect Logged ({defect_type}): '{comment}'")
        
        if mechanic_notes:
            print(f"  -> Mechanic Notes: '{mechanic_notes}'")
            
        print("-" * 40)

if __name__ == "__main__":
    get_recent_dvirs()