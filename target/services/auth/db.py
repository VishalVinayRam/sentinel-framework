"""Database connection pool for auth-service.

BUG (tracked: SENT-1041): pool_size=5 with max_overflow=0 exhausts under
peak login load. Any burst above 5 concurrent DB ops raises:
  sqlalchemy.exc.OperationalError: QueuePool limit of size 5 overflow 0
  reached, connection timed out, timeout 30
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

DB_URL = os.environ.get("DATABASE_URL", "postgresql://sentinel:sentinel@localhost/auth")

# FIXME: pool_size and max_overflow are dangerously low.
# Under peak load (>5 concurrent logins) all connections saturate and
# new requests block for pool_timeout seconds before failing.
engine = create_engine(
    DB_URL,
    pool_size=5,          # only 5 persistent connections — too small
    max_overflow=0,       # ← no burst capacity; raise to 10
    pool_timeout=30,      # seconds to wait before raising OperationalError
    pool_pre_ping=False,  # ← stale connections not recycled; set to True
    pool_recycle=3600,
)

Session = sessionmaker(bind=engine)


def get_session():
    return Session()
