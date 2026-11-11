
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


from db.tables_phase2 import Language


class Base(DeclarativeBase):
    pass


# =====================================================
# 1. Load environment variables
# =====================================================

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

DATABASE_URL = os.getenv("DATABASE_URL")
JUDGE0_URL = os.getenv(
    "JUDGE0_URL",
    "http://localhost:2358",
).rstrip("/")


# =====================================================
# 2. Validate configuration
# =====================================================

if not DATABASE_URL:
    raise ValueError(
        f"Không tìm thấy DATABASE_URL trong {ENV_PATH}"
    )


# =====================================================
# 3. Fetch languages from Judge0
# =====================================================

def fetch_languages():
    return [
        {"language_id": 54, "name": "C++ (GCC 9.2.0)"},
        {"language_id": 62, "name": "Java (OpenJDK 13.0.1)"},
        {"language_id": 71, "name": "Python (3.8.1)"},
        {"language_id": 50, "name": "C (GCC 9.2.0)"},
        {"language_id": 63, "name": "JavaScript (Node.js 12.14.0)"},
    ]


# =====================================================
# 4. Save languages to PostgreSQL using ORM
# =====================================================

def initialize_languages():
    engine = create_engine(DATABASE_URL)

    try:
        # Chỉ tạo bảng Language nếu chưa tồn tại.
        # Không xóa bảng hoặc dữ liệu hiện có.
        Language.__table__.create(
            bind=engine,
            checkfirst=True,
        )

        languages = fetch_languages()

        with Session(engine) as session:
            statement = insert(Language).values(languages)

            statement = statement.on_conflict_do_update(
                index_elements=[Language.language_id],
                set_={
                    "name": statement.excluded.name,
                },
            )

            session.execute(statement)
            session.commit()

        print(
            f"Đã đồng bộ thành công {len(languages)} "
            "ngôn ngữ từ Judge0 vào PostgreSQL."
        )

        with Session(engine) as session:
            saved_languages = (
                session.query(Language)
                .order_by(Language.language_id)
                .all()
            )

            for language in saved_languages:
                print(
                    f"ID = {language.language_id:<4} "
                    f"| {language.name}"
                )

    finally:
        engine.dispose()


# =====================================================
# 5. Main
# =====================================================

if __name__ == "__main__":
    try:
        initialize_languages()

    except requests.RequestException as error:
        print(f"Lỗi kết nối Judge0: {error}")

    except Exception as error:
        print(f"Lỗi khi khởi tạo bảng Language: {error}")
        raise
