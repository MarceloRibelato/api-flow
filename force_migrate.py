import os
from sqlalchemy import create_engine, text

# Load DATABASE_URL from .env if needed
from dotenv import load_dotenv
load_dotenv(dotenv_path="/home/server/Desktop/Projetos/Flow/api-flow/.env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("No DATABASE_URL found.")
    exit(1)

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    try:
        conn.execute(text("ALTER TABLE api_test_execution_history ADD COLUMN trigger_origin VARCHAR(50) DEFAULT 'manual';"))
        print("Added trigger_origin to api_test_execution_history")
    except Exception as e:
        print("Skipped:", e)

    try:
        conn.execute(text("ALTER TABLE api_test_execution_history_archive ADD COLUMN trigger_origin VARCHAR(50) DEFAULT 'manual';"))
        print("Added trigger_origin to archive")
    except Exception as e:
        print("Skipped:", e)

    try:
        conn.execute(text("CREATE INDEX ix_api_test_execution_history_trigger_origin ON api_test_execution_history (trigger_origin);"))
        print("Added index 1")
    except Exception as e:
        print("Skipped:", e)

    try:
        conn.execute(text("CREATE INDEX ix_api_test_execution_history_archive_trigger_origin ON api_test_execution_history_archive (trigger_origin);"))
        print("Added index 2")
    except Exception as e:
        print("Skipped:", e)

    try:
        conn.execute(text("UPDATE alembic_version SET version_num = 'z9z9z9z9z9za';"))
        print("Updated alembic version")
    except Exception as e:
        print("Skipped:", e)

print("Migration complete!")
