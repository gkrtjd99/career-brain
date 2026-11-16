from pathlib import Path

from career_core.db.migrate import MigrationChain

PERSONAL_CHAIN = MigrationChain(
    name="personal", script_location=Path(__file__).resolve().parent, schema="personal"
)
