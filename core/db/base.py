"""
SQLAlchemy DeclarativeBase shared by all ORM models.
pgvector's Vector type is registered here so it is available project-wide.
"""
from sqlalchemy.orm import DeclarativeBase
from pgvector.sqlalchemy import Vector  # noqa: F401 — registers the type


class Base(DeclarativeBase):
    pass
