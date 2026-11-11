import base64

from sqlalchemy import Engine, inspect, text

from db.session import engine
from db.tables_phase1 import Base
import db.tables_phase2  # noqa: F401


def migrate_judge_schema(database_engine: Engine) -> None:
    Base.metadata.create_all(bind=database_engine)

    with database_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS solution"))
        connection.execute(text('DROP TABLE IF EXISTS "session"'))

        problem_columns = {
            column["name"]
            for column in inspect(connection).get_columns("problem")
        }
        if "data_constraints" not in problem_columns:
            connection.execute(
                text("ALTER TABLE problem ADD COLUMN data_constraints TEXT")
            )

        submission_columns = {
            column["name"]
            for column in inspect(connection).get_columns("submission")
        }
        if "contest_id" not in submission_columns:
            connection.execute(
                text(
                    "ALTER TABLE submission "
                    "ADD COLUMN contest_id INTEGER "
                    "REFERENCES contest(contest_id) ON DELETE SET NULL"
                )
            )
        if "class_id" not in submission_columns:
            connection.execute(
                text(
                    "ALTER TABLE submission "
                    "ADD COLUMN class_id INTEGER "
                    "REFERENCES academic_class(class_id) ON DELETE SET NULL"
                )
            )
        if "source_code" not in submission_columns:
            connection.execute(
                text("ALTER TABLE submission ADD COLUMN source_code TEXT")
            )
        for column_name in (
            "passed_testcases",
            "total_testcases",
            "runtime_ms",
            "memory_kb",
        ):
            if column_name not in submission_columns:
                connection.execute(
                    text(f"ALTER TABLE submission ADD COLUMN {column_name} INTEGER")
                )
        if "source_code_encoded" not in submission_columns:
            connection.execute(
                text(
                    "ALTER TABLE submission "
                    "ADD COLUMN source_code_encoded BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
        legacy_submissions = connection.execute(
            text(
                "SELECT submission_id, source_code FROM submission "
                "WHERE source_code IS NOT NULL AND source_code_encoded = FALSE"
            )
        ).all()
        for submission_id, source_code in legacy_submissions:
            encoded_source = base64.b64encode(
                source_code.encode("utf-8")
            ).decode("ascii")
            connection.execute(
                text(
                    "UPDATE submission SET source_code = :source_code, "
                    "source_code_encoded = TRUE "
                    "WHERE submission_id = :submission_id"
                ),
                {
                    "source_code": encoded_source,
                    "submission_id": submission_id,
                },
            )
        legacy_contest_submissions = connection.execute(
            text(
                "SELECT submission_id, contest_id FROM submission "
                "WHERE class_id IS NULL AND contest_id IS NOT NULL"
            )
        ).all()
        for submission_id, contest_id in legacy_contest_submissions:
            class_ids = connection.execute(
                text(
                    "SELECT class_id FROM academic_class_contest "
                    "WHERE contest_id = :contest_id"
                ),
                {"contest_id": contest_id},
            ).scalars().all()
            if len(class_ids) == 1:
                connection.execute(
                    text(
                        "UPDATE submission SET class_id = :class_id "
                        "WHERE submission_id = :submission_id"
                    ),
                    {"class_id": class_ids[0], "submission_id": submission_id},
                )

        for table_name, column_name in (
            ("problem", "created_by"),
            ("contest", "created_by"),
            ("submission", "account_id"),
        ):
            columns = {
                column["name"]: column
                for column in inspect(connection).get_columns(table_name)
            }
            if not columns[column_name]["nullable"]:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ALTER COLUMN {column_name} DROP NOT NULL"
                    )
                )


if __name__ == "__main__":
    migrate_judge_schema(engine)
    print("Judge-admin tables, class requests, and submission fields are ready.")
