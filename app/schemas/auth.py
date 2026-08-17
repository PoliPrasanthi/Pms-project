from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any

class MSCallbackRequest(BaseModel):

    code: str
    redirect_uri: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    public_id: str
    email: str
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    display_name: Optional[str] = None
    role: Optional[Dict[str, Any]] = None
    is_external: bool = False
    is_synced: bool = False

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None
