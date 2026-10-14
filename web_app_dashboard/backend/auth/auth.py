from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import datetime, timedelta
from typing import Annotated

from web_app_dashboard.backend.auth.schemas import Token, TokenData, User, UserInDB, LoginRequest

router = APIRouter()

# Placeholder for a very simple user database
fake_users_db = {
    "testuser": {
        "username": "testuser",
        "full_name": "Test User",
        "email": "test@example.com",
        "hashed_password": "$2b$12$Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z.Z", # Placeholder for a hashed password
        "disabled": False,
    }
}

# Placeholder for a very simple function to get user from DB
def get_user(username: str):
    if username in fake_users_db:
        user_dict = fake_users_db[username]
        return UserInDB(**user_dict)

# Placeholder for creating an access token
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    # In a real app, you would use a proper JWT encoding library
    return "fake-jwt-token"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

@router.post("/login", response_model=Token)
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    username = form_data.username
    password = form_data.password # In a real app, hash and compare

    # In a real app, validate credentials against a user authentication service
    if username == "testuser" and password == "password": # Placeholder validation
        access_token_expires = timedelta(minutes=30)
        access_token = create_access_token(
            data={"sub": username}, expires_delta=access_token_expires
        )
        return {"access_token": access_token, "token_type": "bearer"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

@router.post("/logout")
async def logout():
    # In a real app, invalidate the token/session
    return {"message": "Successfully logged out"}

@router.get("/refresh_token", response_model=Token)
async def refresh_token():
    # In a real app, refresh the token
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": "placeholder_user"}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=User)
async def read_users_me(token: Annotated[str, Depends(oauth2_scheme)]):
    # In a real app, decode token and retrieve user info
    if token == "fake-jwt-token": # Placeholder check
        return User(username="testuser", email="test@example.com", full_name="Test User")
    raise HTTPException(status_code=400, detail="Invalid token")
