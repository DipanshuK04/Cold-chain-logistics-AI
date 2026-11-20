import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

database_url = URL.create(
    drivername="mysql+pymysql",
    username=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    host=os.environ["DB_HOST"],
    port=int(os.environ["DB_PORT"]),
    database=os.environ["DB_NAME"],
)

# Creates SQLAlchemy's database engine
engine = create_engine(database_url, pool_pre_ping=True)
