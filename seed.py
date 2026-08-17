import sys
import os

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.core.seeding import seed_all

if __name__ == "__main__":
    reset = "--reset" in sys.argv
    seed_all(reset=reset)
