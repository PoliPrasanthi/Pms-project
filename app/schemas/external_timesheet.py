from pydantic import BaseModel, Field
from typing import List

class DailyLogSchema(BaseModel):
    Date: str = Field(..., description="Date of the log in YYYY-MM-DD format")
    LoggedHours: str = Field(..., description="Total hours logged on this date in HH:MM format")

class UserWeeklyTimesheetSchema(BaseModel):
    Email: str = Field(..., description="Email of the user")
    Name: str = Field(..., description="Full name of the user")
    LoggedHours: str = Field(..., description="Total logged hours for the week in HH:MM format")
    DailyLogs: List[DailyLogSchema] = Field(..., description="List of daily logs")
