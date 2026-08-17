import os
from datetime import date, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.models.timelog import TimeLog
from app.models.user import User
from app.schemas.external_timesheet import UserWeeklyTimesheetSchema

router = APIRouter()

# API Key security for the external application
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def verify_api_key(api_key: str = Security(api_key_header)):
    # You can set this environment variable in the deployment
    expected_key = os.getenv("EXTERNAL_API_KEY", "technorucs-timesheet-secret-key")
    if api_key != expected_key:
        raise HTTPException(status_code=403, detail="Could not validate credentials")
    return api_key

def format_hours(hours: float) -> str:
    """Format decimal hours to HH:MM string"""
    if not hours:
        return "00:00"
    h = int(hours)
    m = int(round((hours - h) * 60))
    # Handle cases where minutes round up to 60
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}"

def get_previous_week_range():
    """Get the start (Monday) and end (Sunday) dates of the previous week"""
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday

@router.get("/weekly-summary", response_model=List[UserWeeklyTimesheetSchema])
async def get_weekly_summary(
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Returns the time logs for all active users for the previous week.
    This endpoint is designed to be consumed by an external application.
    Requires X-API-Key header for access.
    """
    start_date, end_date = get_previous_week_range()

    # Query active users' timelogs for the previous week
    # We join with User to get user details
    stmt = (
        select(TimeLog, User)
        .join(User, TimeLog.user_id == User.id)
        .where(
            TimeLog.date >= start_date,
            TimeLog.date <= end_date,
            TimeLog.is_deleted == False,
            User.is_deleted == False,
            User.is_active == True
        )
    )
    results = db.execute(stmt).all()

    # Aggregate data
    # user_id -> { "email": "", "name": "", "total_hours": 0.0, "daily": { "YYYY-MM-DD": 0.0 } }
    aggregated = {}

    for timelog, user in results:
        uid = user.id
        if uid not in aggregated:
            aggregated[uid] = {
                "email": user.email,
                "name": f"{user.first_name} {user.last_name or ''}".strip(),
                "total_hours": 0.0,
                "daily": defaultdict(float)
            }
        
        log_hours = float(timelog.daily_log_hours or 0)
        log_date = timelog.date.strftime("%Y-%m-%d")

        aggregated[uid]["total_hours"] += log_hours
        aggregated[uid]["daily"][log_date] += log_hours

    # Format response
    response_data = []
    for uid, data in aggregated.items():
        daily_logs = [
            {
                "Date": d_date,
                "LoggedHours": format_hours(d_hours)
            }
            for d_date, d_hours in sorted(data["daily"].items())
        ]

        response_data.append({
            "Email": data["email"] or "",
            "Name": data["name"],
            "LoggedHours": format_hours(data["total_hours"]),
            "DailyLogs": daily_logs
        })

    # Sort alphabetically by name
    response_data.sort(key=lambda x: x["Name"].lower())
    return response_data
