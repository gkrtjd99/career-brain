from career_core.db.base import Base
from career_core.db.migrate import run_env

run_env(Base.metadata)
