from career_core.db.migrate import run_env

from personal_api.identity import PersonalBase

run_env(PersonalBase.metadata)
