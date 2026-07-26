import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/lumina.db")

#SQLite creates the .db file but not its parent directory
#Ensure the directory exits so a fresh clone runs without manual setup

if DATABASE_URL.startswith("sqlite:///"):
    db_path = Path(DATABASE_URL.removeprefix("sqlite:///"))
    db_path.parent.mkdir(parents=True, exist_ok = True)

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autoflush= False)

class Base(DeclarativeBase):
    """All Lumina models inherit from this base"""  