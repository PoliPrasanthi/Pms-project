import os
import sys
from dotenv import load_dotenv

backend_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(backend_dir, ".env"))
sys.path.append(backend_dir)

from seed import seed_all

if __name__ == "__main__":
    print("Running seed_all()...")
    seed_all(reset=False)
    print("Done.")
