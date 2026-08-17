import os
import csv
from app.core.database import SessionLocal
from sqlalchemy import text

CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"
csv_path = os.path.join(CSV_DIR, "Task.csv")

STATUS_MAP = {
    "Closed": 19, # Completed
    "Open": 16,
    "In Progress": 17,
    "In Review": 18,
    "To be Tested": 18, # In Review
    "Testing": 18, # In Review
    "Completed": 19,
    "UAT Done": 19, # Completed
    "Cancelled": 20,
    "On Hold": 21
}

print("Patching task statuses...")
with SessionLocal() as db:
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        updates = []
        for row in reader:
            task_id = row.get("TaskId")
            if not task_id: continue
            
            status_val = row.get("Status", "").strip()
            status_id = STATUS_MAP.get(status_val)
            
            if status_id is not None:
                updates.append({"pub": f"TSK-{task_id}", "st": status_id})
        
        if updates:
            print(f"Executing {len(updates)} updates...")
            db.execute(
                text("UPDATE tasks SET status_id = :st WHERE public_id = :pub"),
                updates
            )
            db.commit()
            
            # Now rebuild project stats so the view updates
            print("Rebuilding project stats...")
            db.execute(text("CALL sp_rebuild_project_stats()"))
            db.commit()
            print("Done!")
        else:
            print("No tasks found.")
