import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine, text

from app.config import DATABASE_URL

def test_db_connection():
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
    )

    query = text("""
        SELECT
            table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)

    try:
        with engine.connect() as connection:
            result = connection.execute(query)

            print("PostgreSQL connection successful.")
            print("\nTables in public schema:")

            tables = result.fetchall()

            if not tables:
                print("No tables found.")
            else:
                for row in tables:
                    print(f"- {row[0]}")

    except Exception as exc:
        print("PostgreSQL connection failed.")
        print(f"Error: {exc}")

    finally:
        engine.dispose()


if __name__ == "__main__":
    test_db_connection()