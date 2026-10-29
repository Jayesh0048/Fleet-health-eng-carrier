import os
from dotenv import load_dotenv

load_dotenv()

SAMSARA_API_TOKEN = os.getenv("SAMSARA_API_TOKEN")

def get_fleet_assets():
    url = "https://api.samsara.com/assets"
    
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {SAMSARA_API_TOKEN}"
    }
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json().get("data", [])
        print(f"Successfully connected! Found {len(data)} assets.")
        
        for asset in data[:5]: # Print the first 5 assets
            asset_type = asset.get("type")
            name = asset.get("name")
            make = asset.get("make", "Unknown Make")
            
            print(f"[{asset_type.upper()}] Unit: {name} | Make: {make}")
    else:
        print(f"Connection Failed: {response.status_code} - {response.text}")

if __name__ == "__main__":
    get_fleet_assets()