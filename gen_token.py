import jwt
from datetime import datetime, timedelta

SECRET_KEY = "7f0ee1c5d225de46bf357e6a"
ALGORITHM = "HS256"

expire = datetime.utcnow() + timedelta(minutes=100)
to_encode = {"sub": "1", "exp": expire}
encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
print(encoded_jwt)
