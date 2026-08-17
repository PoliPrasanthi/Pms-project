import asyncio
import os
import sys

# Add the parent directory to sys.path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.services.graph_service import get_graph_token, _jit_upsert_user
from requests import get

def sync_all_users():
    print("Acquiring Graph token...")
    try:
        token = get_graph_token()
    except Exception as e:
        print(f"Error acquiring token: {e}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "ConsistencyLevel": "eventual",
    }

    url = "https://graph.microsoft.com/v1.0/users?$select=id,displayName,mail,userPrincipalName"
    total_synced = 0

    with SessionLocal() as db:
        while url:
            print(f"Fetching users from: {url}")
            resp = get(url, headers=headers)
            if resp.status_code != 200:
                print(f"Error fetching users: {resp.status_code} {resp.text}")
                break
            
            data = resp.json()
            users = data.get("value", [])
            for gu in users:
                try:
                    local_id = _jit_upsert_user(db, gu)
                    if local_id:
                        total_synced += 1
                except Exception as e:
                    print(f"Failed to sync user {gu.get('mail')}: {e}")
            
            db.commit()
            url = data.get("@odata.nextLink")
    
    print(f"Sync complete. Total users synced: {total_synced}")

if __name__ == "__main__":
    sync_all_users()
