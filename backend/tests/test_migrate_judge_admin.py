import base64

from sqlalchemy import create_engine, inspect, text

from db.migrate_judge_admin import migrate_judge_schema


def test_migration_adds_contest_link_to_existing_submission_table():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE contest ("
                "contest_id INTEGER PRIMARY KEY, created_by INTEGER)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE submission ("
                "submission_id INTEGER PRIMARY KEY, account_id INTEGER)"
            )
        )

    migrate_judge_schema(engine)
    migrate_judge_schema(engine)

    submission_columns = {
        column["name"] for column in inspect(engine).get_columns("submission")
    }
    assert "contest_id" in submission_columns
    assert "class_id" in submission_columns
    assert "source_code" in submission_columns
    assert "source_code_encoded" in submission_columns
    assert {
        "passed_testcases",
        "total_testcases",
        "runtime_ms",
        "memory_kb",
    }.issubset(submission_columns)
    assert inspect(engine).has_table("class_join_request")
    assert "data_constraints" in {
        column["name"] for column in inspect(engine).get_columns("problem")
    }

    foreign_keys = inspect(engine).get_foreign_keys("submission")
    assert any(foreign_key["referred_table"] == "contest" for foreign_key in foreign_keys)
    assert any(
        foreign_key["referred_table"] == "academic_class"
        for foreign_key in foreign_keys
    )


def test_migration_encodes_legacy_source_code_only_once():
    engine = create_engine("sqlite://")
    legacy_source = "print('xin chào')\n"
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE submission ("
                "submission_id INTEGER PRIMARY KEY, "
                "account_id INTEGER, "
                "source_code TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO submission (submission_id, account_id, source_code) "
                "VALUES (1, 1, :source_code)"
            ),
            {"source_code": legacy_source},
        )

    migrate_judge_schema(engine)
    encoded_source = base64.b64encode(legacy_source.encode("utf-8")).decode("ascii")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT source_code, source_code_encoded FROM submission "
                "WHERE submission_id = 1"
            )
        ).one()
    assert row == (encoded_source, True)

    second_legacy_source = "print('legacy row')"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO submission "
                "(submission_id, account_id, source_code, source_code_encoded) "
                "VALUES (2, 1, :source_code, FALSE)"
            ),
            {"source_code": second_legacy_source},
        )
    migrate_judge_schema(engine)
    with engine.connect() as connection:
        stored_sources = connection.execute(
            text(
                "SELECT source_code, source_code_encoded FROM submission "
                "ORDER BY submission_id"
            )
        ).all()
    assert stored_sources == [
        (encoded_source, True),
        (
            base64.b64encode(second_legacy_source.encode("utf-8")).decode("ascii"),
            True,
        ),
    ]


def test_migration_adds_data_constraints_to_existing_problem_table():
    engine = create_engine("sqlite://")
    migrate_judge_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE problem DROP COLUMN data_constraints"))

    migrate_judge_schema(engine)

    problem_columns = {
        column["name"] for column in inspect(engine).get_columns("problem")
    }
    assert "data_constraints" in problem_columns


def test_migration_drops_legacy_solution_table():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE solution ("
                "solution_id INTEGER PRIMARY KEY, content TEXT NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO solution (solution_id, content) VALUES (1, 'legacy')")
        )

    migrate_judge_schema(engine)
    migrate_judge_schema(engine)

    assert not inspect(engine).has_table("solution")


def test_migration_drops_legacy_authentication_session_table():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                'CREATE TABLE "session" ('
                "session_id VARCHAR(64) PRIMARY KEY, "
                "account_id INTEGER NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "expires_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                'INSERT INTO "session" '
                "(session_id, account_id, created_at, expires_at) "
                "VALUES ('legacy-session', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    migrate_judge_schema(engine)
    migrate_judge_schema(engine)

    assert not inspect(engine).has_table("session")
