"""Load optional local environment configuration before app initialization."""
from pathlib import Path
from dotenv import load_dotenv


def load_environment():
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / '.env', override=False)
