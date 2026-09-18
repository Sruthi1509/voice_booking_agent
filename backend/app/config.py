import os
from dotenv import load_dotenv

load_dotenv()

FRONTEND_ORIGINS = [
    o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

