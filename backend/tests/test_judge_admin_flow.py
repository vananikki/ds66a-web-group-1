from datetime import datetime, timedelta, timezone
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routers.judge_admin as judge_admin
from db.tables_phase1 import (
    Account,
    AccountRole,
    Admin,
    AcademicClass,
    Base,
    ClassJoinRequest,
    ManageClass,
    Student,
    StudentClass,
    Session as UserSession,
)
from db.tables_phase2 import (
    AccessModifier,
    AcademicClassContest,
    AcademicClassProblem,
    Contest,
    ContestProblem,
    Language,
    Problem,
    Submission,
    Testcase as JudgeTestcase,
)
from db.session import get_session
from src.app import app

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()




@pytest.fixture(autouse=True)
def _override_db_session():
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_get_session
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(bind=engine)
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


def _create_account(email: str, role: AccountRole, *, code: str = None, name: str = None):
    with TestingSessionLocal() as db:
        account = Account(
            account_email=email,
            account_code=code or email.split("@")[0],
            account_name=name or email.split("@")[0],
            role=role,
            is_active=True,
        )
        account.set_password("StrongPassword123")
        db.add(account)
        db.commit()
        db.refresh(account)
        return account.account_id


def _set_session(account_id: int, session_id: str = "session-1"):
    with TestingSessionLocal() as db:
        db.add(
            UserSession(
                session_id=session_id,
                account_id=account_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.commit()


def test_admin_can_create_problem_and_class():
    admin_id = _create_account("admin@example.com", AccountRole.ADMIN, code="ADM1", name="Admin")
    _set_session(admin_id, "admin-session")

    client = TestClient(app)

    response = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Sum of Two Numbers",
            "description": "Compute a + b",
            "input_description": "Two integers",
            "output_description": "One integer",
            "data_constraints": "1 <= a, b <= 10^9",
            "time_limit": 1,
            "memory_limit": 256,
            "access_modifier": "public",
        },
        cookies={"session_id": "admin-session"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["problem_name"] == "Sum of Two Numbers"
    assert payload["access_modifier"] == "public"
    assert payload["data_constraints"] == "1 <= a, b <= 10^9"

    update_response = client.patch(
        f"/admin/judge/problems/{payload['problem_id']}",
        json={"data_constraints": "1 <= a, b <= 10^6"},
        cookies={"session_id": "admin-session"},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["data_constraints"] == "1 <= a, b <= 10^6"

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "CS101 Basic Programming"},
        cookies={"session_id": "admin-session"},
    )

    assert class_response.status_code == 200, class_response.text
    class_payload = class_response.json()
    assert class_payload["class_name"] == "CS101 Basic Programming"


def test_super_admin_can_delete_anonymous_problem_and_all_related_data():
    super_admin_id = _create_account(
        "super-delete@example.com",
        AccountRole.SUPER_ADMIN,
        code="SUPERDEL",
        name="Super Admin",
    )
    _set_session(super_admin_id, "super-delete-session")

    with TestingSessionLocal() as db:
        language = Language(name="Python")
        problem = Problem(
            problem_name="Anonymous problem",
            time_limit=1,
            memory_limit=128,
            created_by=None,
        )
        academic_class = AcademicClass(class_name="Test class")
        contest = Contest(
            contest_name="Test contest",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            created_by=super_admin_id,
        )
        db.add_all([language, problem, academic_class, contest])
        db.flush()
        db.add_all(
            [
                JudgeTestcase(
                    problem_id=problem.problem_id,
                    point=100,
                    input_data="1",
                    expected_output="1",
                ),
                AcademicClassProblem(
                    class_id=academic_class.class_id,
                    problem_id=problem.problem_id,
                ),
                ContestProblem(
                    contest_id=contest.contest_id,
                    problem_id=problem.problem_id,
                ),
                Submission(
                    problem_id=problem.problem_id,
                    account_id=super_admin_id,
                    language_id=language.language_id,
                    status="Accepted",
                ),
            ]
        )
        db.commit()
        problem_id = problem.problem_id

    client = TestClient(app)
    listed = client.get(
        "/admin/judge/problems",
        cookies={"session_id": "super-delete-session"},
    )
    assert listed.status_code == 200
    assert next(item for item in listed.json() if item["problem_id"] == problem_id)[
        "is_anonymous"
    ] is True

    response = client.delete(
        f"/admin/judge/problems/{problem_id}",
        cookies={"session_id": "super-delete-session"},
    )

    assert response.status_code == 200, response.text
    with TestingSessionLocal() as db:
        assert db.get(Problem, problem_id) is None
        assert db.query(JudgeTestcase).filter_by(problem_id=problem_id).count() == 0
        assert db.query(AcademicClassProblem).filter_by(problem_id=problem_id).count() == 0
        assert db.query(ContestProblem).filter_by(problem_id=problem_id).count() == 0
        assert db.query(Submission).filter_by(problem_id=problem_id).count() == 0


def test_regular_admin_cannot_delete_problem():
    admin_id = _create_account(
        "regular-delete@example.com",
        AccountRole.ADMIN,
        code="REGDEL",
        name="Regular Admin",
    )
    _set_session(admin_id, "regular-delete-session")
    with TestingSessionLocal() as db:
        problem = Problem(
            problem_name="Protected problem",
            time_limit=1,
            memory_limit=128,
            created_by=None,
        )
        db.add(problem)
        db.commit()
        problem_id = problem.problem_id

    response = TestClient(app).delete(
        f"/admin/judge/problems/{problem_id}",
        cookies={"session_id": "regular-delete-session"},
    )

    assert response.status_code == 403
    with TestingSessionLocal() as db:
        assert db.get(Problem, problem_id) is not None


def test_super_admin_can_delete_class_and_its_memberships_but_keep_shared_data():
    super_admin_id = _create_account(
        "super-class-delete@example.com",
        AccountRole.SUPER_ADMIN,
        code="SUPERCLS",
        name="Super Admin",
    )
    student_account_id = _create_account(
        "class-student@example.com",
        AccountRole.USER,
        code="CLSSTU",
        name="Class Student",
    )
    _set_session(super_admin_id, "super-class-session")

    with TestingSessionLocal() as db:
        student = Student(account_id=student_account_id)
        problem = Problem(
            problem_name="Shared problem",
            time_limit=1,
            memory_limit=128,
            created_by=super_admin_id,
        )
        contest = Contest(
            contest_name="Shared contest",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            created_by=super_admin_id,
        )
        academic_class = AcademicClass(class_name="Removable class")
        db.add_all([student, problem, contest, academic_class])
        db.flush()
        db.add_all(
            [
                StudentClass(
                    class_id=academic_class.class_id,
                    student_id=student.student_id,
                ),
                ClassJoinRequest(
                    class_id=academic_class.class_id,
                    student_id=student.student_id,
                ),
                AcademicClassProblem(
                    class_id=academic_class.class_id,
                    problem_id=problem.problem_id,
                ),
                AcademicClassContest(
                    class_id=academic_class.class_id,
                    contest_id=contest.contest_id,
                ),
            ]
        )
        db.commit()
        class_id = academic_class.class_id
        problem_id = problem.problem_id
        contest_id = contest.contest_id
        student_id = student.student_id

    response = TestClient(app).delete(
        f"/admin/judge/academic-classes/{class_id}",
        cookies={"session_id": "super-class-session"},
    )
    assert response.status_code == 200, response.text

    with TestingSessionLocal() as db:
        assert db.get(AcademicClass, class_id) is None
        assert db.get(Problem, problem_id) is not None
        assert db.get(Contest, contest_id) is not None
        assert db.query(StudentClass).filter_by(student_id=student_id).count() == 0
        assert db.query(ClassJoinRequest).filter_by(class_id=class_id).count() == 0
        assert db.query(AcademicClassProblem).filter_by(class_id=class_id).count() == 0
        assert db.query(AcademicClassContest).filter_by(class_id=class_id).count() == 0


def test_super_admin_can_delete_contest_and_its_submissions():
    super_admin_id = _create_account(
        "super-contest-delete@example.com",
        AccountRole.SUPER_ADMIN,
        code="SUPERCNT",
        name="Super Admin",
    )
    _set_session(super_admin_id, "super-contest-session")

    with TestingSessionLocal() as db:
        language = Language(name="Python")
        problem = Problem(
            problem_name="Kept problem",
            time_limit=1,
            memory_limit=128,
            created_by=super_admin_id,
        )
        academic_class = AcademicClass(class_name="Contest class")
        contest = Contest(
            contest_name="Removable contest",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            created_by=super_admin_id,
        )
        db.add_all([language, problem, academic_class, contest])
        db.flush()
        submission = Submission(
            problem_id=problem.problem_id,
            account_id=super_admin_id,
            contest_id=contest.contest_id,
            language_id=language.language_id,
            status="Accepted",
        )
        db.add_all(
            [
                submission,
                ContestProblem(
                    contest_id=contest.contest_id,
                    problem_id=problem.problem_id,
                ),
                AcademicClassContest(
                    class_id=academic_class.class_id,
                    contest_id=contest.contest_id,
                ),
            ]
        )
        db.commit()
        contest_id = contest.contest_id
        problem_id = problem.problem_id
        submission_id = submission.submission_id
        class_id = academic_class.class_id

    response = TestClient(app).delete(
        f"/admin/judge/contests/{contest_id}",
        cookies={"session_id": "super-contest-session"},
    )
    assert response.status_code == 200, response.text

    with TestingSessionLocal() as db:
        assert db.get(Contest, contest_id) is None
        assert db.get(Problem, problem_id) is not None
        assert db.get(Submission, submission_id) is None
        assert db.query(ContestProblem).filter_by(contest_id=contest_id).count() == 0
        assert db.query(AcademicClassContest).filter_by(class_id=class_id).count() == 0


def test_super_admin_can_delete_submission_but_admin_cannot():
    super_admin_id = _create_account(
        "super-submission-delete@example.com",
        AccountRole.SUPER_ADMIN,
        code="SUPSUB",
        name="Super Admin",
    )
    _set_session(super_admin_id, "super-submission-session")
    with TestingSessionLocal() as db:
        language = Language(name="Python")
        problem = Problem(
            problem_name="Submission problem",
            time_limit=1,
            memory_limit=128,
            created_by=super_admin_id,
        )
        db.add_all([language, problem])
        db.flush()
        submission = Submission(
            problem_id=problem.problem_id,
            account_id=super_admin_id,
            language_id=language.language_id,
            status="Wrong Answer",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.submission_id

    response = TestClient(app).delete(
        f"/admin/judge/submissions/{submission_id}",
        cookies={"session_id": "super-submission-session"},
    )
    assert response.status_code == 200, response.text
    with TestingSessionLocal() as db:
        assert db.get(Submission, submission_id) is None


def test_submission_search_returns_requested_submission():
    super_admin_id = _create_account(
        "super-submission-search@example.com",
        AccountRole.SUPER_ADMIN,
        code="SUPSEARCH",
        name="Super Admin",
    )
    _set_session(super_admin_id, "super-search-session")
    with TestingSessionLocal() as db:
        language = Language(name="Python")
        problem = Problem(
            problem_name="Searchable problem",
            time_limit=1,
            memory_limit=128,
            created_by=super_admin_id,
        )
        db.add_all([language, problem])
        db.flush()
        submission = Submission(
            problem_id=problem.problem_id,
            account_id=super_admin_id,
            language_id=language.language_id,
            status="Accepted",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.submission_id

    response = TestClient(app).get(
        f"/admin/judge/submissions?submission_id={submission_id}",
        cookies={"session_id": "super-search-session"},
    )

    assert response.status_code == 200, response.text
    assert [item["submission_id"] for item in response.json()] == [submission_id]


def test_admin_can_attach_public_problem_to_contest():
    admin_id = _create_account("contest-admin@example.com", AccountRole.ADMIN, code="ADM2", name="Contest Admin")
    _set_session(admin_id, "contest-session")

    client = TestClient(app)
    problem_response = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Prime Check",
            "description": "Return yes/no",
            "input_description": "Integer",
            "output_description": "Result",
            "time_limit": 2,
            "memory_limit": 512,
            "access_modifier": "public",
        },
        cookies={"session_id": "contest-session"},
    )
    problem_id = problem_response.json()["problem_id"]

    contest_response = client.post(
        "/admin/judge/contests",
        json={
            "contest_name": "Week 1 Contest",
            "description": "Warm up contest",
            "start_time": "2026-10-01T10:00:00Z",
            "end_time": "2026-10-01T12:00:00Z",
        },
        cookies={"session_id": "contest-session"},
    )
    contest_id = contest_response.json()["contest_id"]

    attach_response = client.post(
        f"/admin/judge/contests/{contest_id}/problems",
        json={"problem_id": problem_id, "problem_order": 1},
        cookies={"session_id": "contest-session"},
    )

    assert attach_response.status_code == 200, attach_response.text
    assert attach_response.json()["problem_id"] == problem_id

    detail_response = client.get(
        f"/admin/judge/contests/{contest_id}",
        cookies={"session_id": "contest-session"},
    )
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["status"] == "finished"
    assert detail_response.json()["is_out_of_contest"] is True


def test_problem_testcases_can_be_created_and_replaced():
    admin_id = _create_account("testcases@example.com", AccountRole.ADMIN)
    _set_session(admin_id, "testcase-session")
    client = TestClient(app)

    response = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Test case problem",
            "time_limit": 1,
            "memory_limit": 256,
            "testcases": [
                {"input_data": "2 3\n", "expected_output": "5\n", "point": 40},
                {"input_data": "5 7\n", "expected_output": "12\n", "point": 60},
            ],
        },
        cookies={"session_id": "testcase-session"},
    )
    assert response.status_code == 200, response.text
    problem_id = response.json()["problem_id"]

    with TestingSessionLocal() as db:
        testcases = db.scalars(
            select(JudgeTestcase).where(JudgeTestcase.problem_id == problem_id)
        ).all()
        assert [(testcase.input_data, testcase.expected_output, testcase.point) for testcase in testcases] == [
            ("2 3\n", "5\n", 40),
            ("5 7\n", "12\n", 60),
        ]

    update_response = client.patch(
        f"/admin/judge/problems/{problem_id}",
        json={
            "testcases": [
                {"input_data": "8 9\n", "expected_output": "17\n", "point": 100}
            ]
        },
        cookies={"session_id": "testcase-session"},
    )
    assert update_response.status_code == 200, update_response.text

    with TestingSessionLocal() as db:
        testcases = db.scalars(
            select(JudgeTestcase).where(JudgeTestcase.problem_id == problem_id)
        ).all()
        assert [(testcase.input_data, testcase.expected_output, testcase.point) for testcase in testcases] == [
            ("8 9\n", "17\n", 100)
        ]


def test_problem_ownership_and_public_practice_are_enforced(monkeypatch):
    owner_id = _create_account("problem-owner@example.com", AccountRole.ADMIN)
    other_admin_id = _create_account("problem-other-admin@example.com", AccountRole.ADMIN)
    student_id = _create_account("problem-practice-student@example.com", AccountRole.USER)
    _set_session(owner_id, "problem-owner-session")
    _set_session(other_admin_id, "problem-other-admin-session")
    _set_session(student_id, "problem-practice-student-session")
    with TestingSessionLocal() as db:
        db.add(Language(language_id=72, name="Python (3.8.1)"))
        db.commit()
    monkeypatch.setattr(
        judge_admin,
        "_run_judge0",
        lambda source_code, language_id, testcase, time_limit, memory_limit, **kwargs: {
            "status": {"id": 3, "description": "Accepted"},
            "stdout": testcase.expected_output,
        },
    )
    client = TestClient(app)
    testcase = [
        {"input_data": "secret test input\n", "expected_output": "practice output\n", "point": 100}
    ]
    private_problem = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Owner-only private problem",
            "time_limit": 1,
            "memory_limit": 256,
            "testcases": testcase,
        },
        cookies={"session_id": "problem-owner-session"},
    ).json()
    public_problem_response = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Public practice problem A + B",
            "data_constraints": "1 <= n <= 10^5",
            "time_limit": 1,
            "memory_limit": 256,
            "access_modifier": "public",
            "testcases": testcase,
        },
        cookies={"session_id": "problem-owner-session"},
    )
    assert public_problem_response.status_code == 200, public_problem_response.text
    public_problem = public_problem_response.json()
    public_statement = client.get(
        f"/student/problems/{public_problem['problem_id']}",
        cookies={"session_id": "problem-practice-student-session"},
    )
    assert public_statement.status_code == 200, public_statement.text
    assert public_statement.json()["data_constraints"] == "1 <= n <= 10^5"
    assert "testcases" not in public_statement.json()
    assert client.get(
        f"/student/problems/{private_problem['problem_id']}",
        cookies={"session_id": "problem-practice-student-session"},
    ).status_code == 404

    owner_problem = client.get(
        f"/admin/judge/problems/{private_problem['problem_id']}",
        cookies={"session_id": "problem-owner-session"},
    )
    assert owner_problem.status_code == 200, owner_problem.text
    assert owner_problem.json()["testcases"][0]["input_data"] == "secret test input\n"
    assert client.get(
        f"/admin/judge/problems/{private_problem['problem_id']}",
        cookies={"session_id": "problem-other-admin-session"},
    ).status_code == 403
    assert client.patch(
        f"/admin/judge/problems/{private_problem['problem_id']}",
        json={"problem_name": "Changed by another admin"},
        cookies={"session_id": "problem-other-admin-session"},
    ).status_code == 403


    assert client.patch(
        f"/admin/judge/problems/{public_problem['problem_id']}",
        json={"problem_name": "Changed public problem by another admin"},
        cookies={"session_id": "problem-other-admin-session"},
    ).status_code == 403

    student_cookies = {"session_id": "problem-practice-student-session"}
    public_list = client.get("/student/problems", cookies=student_cookies)
    assert public_list.status_code == 200, public_list.text
    assert [item["problem_id"] for item in public_list.json()] == [
        public_problem["problem_id"]
    ]
    assert "testcases" not in public_list.json()[0]
    search_by_title = client.get(
        "/student/problems?q=practice",
        cookies=student_cookies,
    )
    assert search_by_title.status_code == 200, search_by_title.text
    assert [item["problem_id"] for item in search_by_title.json()] == [
        public_problem["problem_id"]
    ]
    search_by_punctuation = client.get(
        "/student/problems?q=A%2BB",
        cookies=student_cookies,
    )
    assert search_by_punctuation.status_code == 200, search_by_punctuation.text
    assert [item["problem_id"] for item in search_by_punctuation.json()] == [
        public_problem["problem_id"]
    ]
    assert client.get(
        "/student/problems?q=A%2BZ",
        cookies=student_cookies,
    ).json() == []
    search_by_constraints = client.get(
        "/student/problems?q=10%5E5",
        cookies=student_cookies,
    )
    assert search_by_constraints.status_code == 200, search_by_constraints.text
    assert search_by_constraints.json() == []
    assert client.get(
        "/student/problems?q=owner-only",
        cookies=student_cookies,
    ).json() == []
    owner_search = client.get(
        "/admin/judge/problems?q=owner-only",
        cookies={"session_id": "problem-owner-session"},
    )
    assert owner_search.status_code == 200, owner_search.text
    assert [item["problem_id"] for item in owner_search.json()] == [
        private_problem["problem_id"]
    ]
    assert client.get(
        f"/student/problems?q={'x' * 101}",
        cookies=student_cookies,
    ).status_code == 422
    assert client.post(
        f"/student/problems/{private_problem['problem_id']}/submissions",
        json={"language_id": 72, "source_code": "print('private')"},
        cookies=student_cookies,
    ).status_code == 404

    public_submission = client.post(
        f"/student/problems/{public_problem['problem_id']}/submissions",
        json={"language_id": 72, "source_code": "print('practice')"},
        cookies=student_cookies,
    )
    assert public_submission.status_code == 200, public_submission.text
    assert public_submission.json()["status"] == "Accepted"
    assert public_submission.json()["score"] == 100
    assert "input_data" not in public_submission.json()["testcases"][0]


def test_public_code_runner_uses_manual_input_without_saving_submission(monkeypatch):
    owner_id = _create_account("runner-owner@example.com", AccountRole.ADMIN)
    student_id = _create_account("runner-student@example.com", AccountRole.USER)
    _set_session(owner_id, "runner-owner-session")
    _set_session(student_id, "runner-student-session")
    with TestingSessionLocal() as db:
        db.add(Language(language_id=71, name="Python (3.8.1)"))
        db.commit()

    received_input = []

    def run_judge0(source_code, language_id, testcase, time_limit, memory_limit, **kwargs):
        received_input.append(testcase.input_data)
        return {
            "status": {"id": 3, "description": "Accepted"},
            "stdout": f"output for {testcase.input_data}",
            "stderr": "",
            "compile_output": "",
            "time": "0.025",
            "memory": 1024,
        }

    monkeypatch.setattr(judge_admin, "_run_judge0", run_judge0)
    client = TestClient(app)
    problem_response = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Manual input runner",
            "time_limit": 1,
            "memory_limit": 256,
            "access_modifier": "public",
            "testcases": [
                {
                    "input_data": "hidden input",
                    "expected_output": "hidden output",
                    "point": 100,
                }
            ],
        },
        cookies={"session_id": "runner-owner-session"},
    )
    assert problem_response.status_code == 200, problem_response.text
    problem_id = problem_response.json()["problem_id"]

    response = client.post(
        f"/student/problems/{problem_id}/run",
        json={
            "language_id": 71,
            "source_code": "print(input())",
            "stdin": "my manual input\n",
        },
        cookies={"session_id": "runner-student-session"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "Accepted",
        "status_id": 3,
        "stdout": "output for my manual input\n",
        "stderr": "",
        "compile_output": "",
        "runtime_ms": 25,
        "memory_kb": 1024,
    }
    assert received_input == ["my manual input\n"]
    with TestingSessionLocal() as db:
        student = Student(account_id=student_id)
        academic_class = AcademicClass(class_name="Manual runner class")
        db.add_all([student, academic_class])
        db.flush()
        db.add_all(
            [
                StudentClass(
                    class_id=academic_class.class_id,
                    student_id=student.student_id,
                ),
                AcademicClassProblem(
                    class_id=academic_class.class_id,
                    problem_id=problem_id,
                ),
            ]
        )
        class_id = academic_class.class_id
        db.commit()
    class_response = client.post(
        f"/student/classes/{class_id}/problems/{problem_id}/run",
        json={
            "language_id": 71,
            "source_code": "print(input())",
            "stdin": "class input\n",
        },
        cookies={"session_id": "runner-student-session"},
    )
    assert class_response.status_code == 200, class_response.text
    assert class_response.json()["stdout"] == "output for class input\n"
    assert received_input == ["my manual input\n", "class input\n"]
    with TestingSessionLocal() as db:
        assert db.query(Submission).count() == 0

    private_problem = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Private runner problem",
            "time_limit": 1,
            "memory_limit": 256,
        },
        cookies={"session_id": "runner-owner-session"},
    ).json()
    denied = client.post(
        f"/student/problems/{private_problem['problem_id']}/run",
        json={
            "language_id": 71,
            "source_code": "print(input())",
            "stdin": "secret",
        },
        cookies={"session_id": "runner-student-session"},
    )
    assert denied.status_code == 404


def test_hidden_contest_problem_is_only_visible_during_active_contest():
    owner_id = _create_account("hidden-owner@example.com", AccountRole.ADMIN)
    student_id = _create_account("hidden-student@example.com", AccountRole.USER)
    _set_session(owner_id, "hidden-owner-session")
    _set_session(student_id, "hidden-student-session")

    client = TestClient(app)
    response = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Hidden contest problem",
            "time_limit": 1,
            "memory_limit": 256,
            "access_modifier": "hidden",
            "testcases": [{"input_data": "7\n", "expected_output": "7\n", "point": 100}],
        },
        cookies={"session_id": "hidden-owner-session"},
    )
    assert response.status_code == 200, response.text
    problem_id = response.json()["problem_id"]

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Hidden contest class"},
        cookies={"session_id": "hidden-owner-session"},
    )
    class_id = class_response.json()["class_id"]
    assert client.post(
        f"/admin/judge/academic-classes/{class_id}/students",
        json={"account_id": student_id},
        cookies={"session_id": "hidden-owner-session"},
    ).status_code == 200

    attach_to_class_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/problems",
        json={"problem_id": problem_id, "is_required": False},
        cookies={"session_id": "hidden-owner-session"},
    )
    assert attach_to_class_response.status_code == 200, attach_to_class_response.text

    now = datetime.now(timezone.utc)
    contest_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Hidden contest",
            "description": "Contest with hidden problem",
            "start_time": (now + timedelta(minutes=30)).isoformat(),
            "end_time": (now + timedelta(minutes=90)).isoformat(),
            "problem_ids": [problem_id],
        },
        cookies={"session_id": "hidden-owner-session"},
    )
    assert contest_response.status_code == 200, contest_response.text
    contest_id = contest_response.json()["contest_id"]

    assert client.get(
        "/student/problems",
        cookies={"session_id": "hidden-student-session"},
    ).json() == []
    assert client.get(
        f"/student/problems/{problem_id}",
        cookies={"session_id": "hidden-student-session"},
    ).status_code == 404

    contest_detail = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}",
        cookies={"session_id": "hidden-student-session"},
    )
    assert contest_detail.status_code == 409
    with TestingSessionLocal() as db:
        contest = db.get(Contest, contest_id)
        contest.start_time = now - timedelta(minutes=5)
        contest.end_time = now + timedelta(minutes=30)
        db.commit()
    contest_detail = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}",
        cookies={"session_id": "hidden-student-session"},
    )
    assert contest_detail.status_code == 200, contest_detail.text
    assert [
        item["problem_id"] for item in contest_detail.json()["problems"]
    ] == [problem_id]

    owner_detail = client.get(
        f"/admin/judge/problems/{problem_id}",
        cookies={"session_id": "hidden-owner-session"},
    )
    assert owner_detail.status_code == 200, owner_detail.text
    assert owner_detail.json()["access_modifier"] == "hidden"


def test_class_contest_preserves_selected_problem_order():
    admin_id = _create_account("ordered-contest-admin@example.com", AccountRole.ADMIN)
    student_id = _create_account("ordered-contest-student@example.com", AccountRole.USER)
    _set_session(admin_id, "ordered-contest-admin-session")
    _set_session(student_id, "ordered-contest-student-session")
    client = TestClient(app)
    cookies = {"session_id": "ordered-contest-admin-session"}

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Ordered contest class"},
        cookies=cookies,
    )
    assert class_response.status_code == 200, class_response.text
    class_id = class_response.json()["class_id"]

    problem_ids = []
    for problem_name in ("First added", "Second added"):
        problem_response = client.post(
            f"/admin/judge/academic-classes/{class_id}/problems/create",
            json={
                "problem_name": problem_name,
                "time_limit": 1,
                "memory_limit": 256,
                "access_modifier": "public",
            },
            cookies=cookies,
        )
        assert problem_response.status_code == 200, problem_response.text
        problem_ids.append(problem_response.json()["problem_id"])

    invite_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/students",
        json={"account_id": student_id},
        cookies=cookies,
    )
    assert invite_response.status_code == 200, invite_response.text

    now = datetime.now(timezone.utc)
    contest_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Ordered contest",
            "start_time": (now - timedelta(minutes=30)).isoformat(),
            "end_time": (now + timedelta(minutes=90)).isoformat(),
            "problem_ids": list(reversed(problem_ids)),
        },
        cookies=cookies,
    )
    assert contest_response.status_code == 200, contest_response.text
    contest_id = contest_response.json()["contest_id"]

    detail_response = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}",
        cookies={"session_id": "ordered-contest-student-session"},
    )
    assert detail_response.status_code == 200, detail_response.text
    assert [
        (item["problem_id"], item["problem_order"])
        for item in detail_response.json()["problems"]
    ] == [(problem_ids[1], 1), (problem_ids[0], 2)]


def test_submission_history_includes_private_and_public_with_each_status(monkeypatch):
    student_id = _create_account("history-student@example.com", AccountRole.USER)
    other_student_id = _create_account("other-history-student@example.com", AccountRole.USER)
    _set_session(student_id, "history-student-session")
    _set_session(other_student_id, "other-history-student-session")
    with TestingSessionLocal() as db:
        student = Student(account_id=student_id)
        other_student = Student(account_id=other_student_id)
        first_class = AcademicClass(class_name="History class one")
        second_class = AcademicClass(class_name="History class two")
        public_problem = Problem(
            problem_name="Shared public history problem",
            time_limit=1,
            memory_limit=128,
            access_modifier=AccessModifier.PUBLIC,
        )
        private_problem = Problem(
            problem_name="Private history problem",
            time_limit=1,
            memory_limit=128,
            access_modifier=AccessModifier.PRIVATE,
        )
        db.add_all(
            [
                student,
                other_student,
                first_class,
                second_class,
                public_problem,
                private_problem,
                Language(language_id=72, name="Python (3.8.1)"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StudentClass(class_id=first_class.class_id, student_id=student.student_id),
                StudentClass(class_id=second_class.class_id, student_id=student.student_id),
                StudentClass(
                    class_id=first_class.class_id,
                    student_id=other_student.student_id,
                ),
                AcademicClassProblem(
                    class_id=first_class.class_id,
                    problem_id=public_problem.problem_id,
                ),
                AcademicClassProblem(
                    class_id=second_class.class_id,
                    problem_id=public_problem.problem_id,
                ),
                AcademicClassProblem(
                    class_id=first_class.class_id,
                    problem_id=private_problem.problem_id,
                ),
                JudgeTestcase(
                    problem_id=public_problem.problem_id,
                    point=100,
                    input_data="",
                    expected_output="ok\n",
                ),
                JudgeTestcase(
                    problem_id=private_problem.problem_id,
                    point=100,
                    input_data="",
                    expected_output="ok\n",
                ),
            ]
        )
        first_class_id = first_class.class_id
        second_class_id = second_class.class_id
        public_problem_id = public_problem.problem_id
        private_problem_id = private_problem.problem_id
        db.commit()

    monkeypatch.setattr(
        judge_admin,
        "_run_judge0",
        lambda source_code, language_id, testcase, time_limit, memory_limit, **kwargs: (
            {
                "status": {"id": 3, "description": "Accepted"},
                "stdout": testcase.expected_output,
            }
            if source_code == "print('ok')"
            else {
                "status": {"id": 3, "description": "Accepted"},
                "stdout": "wrong output",
            }
        ),
    )
    client = TestClient(app)
    student_cookies = {"session_id": "history-student-session"}
    submission_payload = {"language_id": 72, "source_code": "print('ok')"}

    class_public_submission = client.post(
        f"/student/classes/{first_class_id}/problems/{public_problem_id}/submissions",
        json=submission_payload,
        cookies=student_cookies,
    )
    assert class_public_submission.status_code == 200, class_public_submission.text
    public_submission = client.post(
        f"/student/problems/{public_problem_id}/submissions",
        json={**submission_payload, "source_code": "print('wrong')"},
        cookies=student_cookies,
    )
    assert public_submission.status_code == 200, public_submission.text
    private_submission = client.post(
        f"/student/classes/{first_class_id}/problems/{private_problem_id}/submissions",
        json=submission_payload,
        cookies=student_cookies,
    )
    assert private_submission.status_code == 200, private_submission.text
    other_class_submission = client.post(
        f"/student/classes/{first_class_id}/problems/{public_problem_id}/submissions",
        json={**submission_payload, "source_code": "print('other')"},
        cookies={"session_id": "other-history-student-session"},
    )
    assert other_class_submission.status_code == 200, other_class_submission.text

    submission_history = client.get("/student/submissions", cookies=student_cookies)
    assert submission_history.status_code == 200, submission_history.text
    assert [item["problem_id"] for item in submission_history.json()] == [
        public_problem_id,
        private_problem_id,
        public_problem_id,
        public_problem_id,
    ]
    assert [item["status"] for item in submission_history.json()] == [
        "Wrong Answer",
        "Accepted",
        "Wrong Answer",
        "Accepted",
    ]
    assert [item["access_modifier"] for item in submission_history.json()] == [
        "public",
        "private",
        "public",
        "public",
    ]
    assert [item["is_mine"] for item in submission_history.json()] == [
        False,
        True,
        True,
        True,
    ]
    assert submission_history.json()[0]["account_name"] == "other-history-student"
    assert all("source_code" not in item for item in submission_history.json())
    public_problem_history = client.get(
        f"/student/submissions?problem_id={public_problem_id}&limit=2",
        cookies=student_cookies,
    )
    assert public_problem_history.status_code == 200, public_problem_history.text
    assert len(public_problem_history.json()) == 2
    assert all(
        item["problem_id"] == public_problem_id
        for item in public_problem_history.json()
    )
    other_account_history = client.get(
        f"/student/submissions?account_id={other_student_id}",
        cookies=student_cookies,
    )
    assert other_account_history.status_code == 200, other_account_history.text
    assert len(other_account_history.json()) == 1
    assert other_account_history.json()[0]["account_name"] == "other-history-student"
    assert other_account_history.json()[0]["is_mine"] is False
    problem_name_history = client.get(
        "/student/submissions?problem_name=SHARED%20PUBLIC",
        cookies=student_cookies,
    )
    assert problem_name_history.status_code == 200, problem_name_history.text
    assert len(problem_name_history.json()) == 3
    assert all(
        item["problem_name"] == "Shared public history problem"
        for item in problem_name_history.json()
    )
    account_name_history = client.get(
        "/student/submissions?account_name=OTHER-HISTORY",
        cookies=student_cookies,
    )
    assert account_name_history.status_code == 200, account_name_history.text
    assert len(account_name_history.json()) == 1
    assert account_name_history.json()[0]["account_name"] == "other-history-student"
    named_combined_history = client.get(
        "/student/submissions?problem_name=history&account_name=other",
        cookies=student_cookies,
    )
    assert named_combined_history.status_code == 200, named_combined_history.text
    assert len(named_combined_history.json()) == 1
    assert named_combined_history.json()[0]["problem_id"] == public_problem_id
    assert client.get(
        f"/student/submissions?problem_name={'x' * 101}",
        cookies=student_cookies,
    ).status_code == 422
    combined_history = client.get(
        f"/student/submissions?problem_id={public_problem_id}&account_id={other_student_id}",
        cookies=student_cookies,
    )
    assert combined_history.status_code == 200, combined_history.text
    assert len(combined_history.json()) == 1
    assert combined_history.json()[0]["problem_id"] == public_problem_id
    assert client.get(
        "/student/submissions?problem_id=0",
        cookies=student_cookies,
    ).status_code == 422
    assert client.get(
        "/student/submissions?account_id=0",
        cookies=student_cookies,
    ).status_code == 422
    assert client.get("/student/submissions").status_code == 401
    other_history = client.get(
        "/student/submissions",
        cookies={"session_id": "other-history-student-session"},
    )
    assert other_history.status_code == 200
    assert len(other_history.json()) == 4
    assert other_history.json()[0]["is_mine"] is True
    assert client.get(
        f"/student/submissions?limit=1&offset=1",
        cookies=student_cookies,
    ).json()[0]["is_mine"] is True
    assert client.get(
        f"/student/submissions/{other_class_submission.json()['submission_id']}/source",
        cookies=student_cookies,
    ).status_code == 404
    own_source = client.get(
        f"/student/submissions/{public_submission.json()['submission_id']}/source",
        cookies=student_cookies,
    )
    assert own_source.status_code == 200, own_source.text
    assert own_source.json()["source_code"] == "print('wrong')"

    first_class_history = client.get(
        f"/student/classes/{first_class_id}/submissions",
        cookies=student_cookies,
    )
    second_class_history = client.get(
        f"/student/classes/{second_class_id}/submissions",
        cookies=student_cookies,
    )
    assert first_class_history.status_code == 200, first_class_history.text
    assert second_class_history.status_code == 200, second_class_history.text
    assert client.get(
        f"/student/classes/{second_class_id}/submissions",
        cookies={"session_id": "other-history-student-session"},
    ).status_code == 403
    assert {item["problem_id"] for item in first_class_history.json()} == {
        public_problem_id,
        private_problem_id,
    }
    assert len(first_class_history.json()) == 3
    assert {item["account_name"] for item in first_class_history.json()} == {
        "history-student",
        "other-history-student",
    }
    assert second_class_history.json() == []
    assert all(
        "source_code" not in item
        for item in first_class_history.json() + second_class_history.json()
    )
    with TestingSessionLocal() as db:
        assert db.query(Submission).filter_by(account_id=student_id).count() == 3


def test_submission_stores_aggregate_verdict_and_judge0_metrics(monkeypatch):
    student_id = _create_account("metrics-student@example.com", AccountRole.USER)
    _set_session(student_id, "metrics-student-session")
    results = iter(
        [
            {
                "status": {"id": 3, "description": "Accepted"},
                "stdout": "expected 1",
                "time": "0.01",
                "memory": 100,
            },
            {
                "status": {"id": 4, "description": "Wrong Answer"},
                "stdout": "wrong",
                "time": "0.02",
                "memory": 120,
            },
            {
                "status": {"id": 7, "description": "Runtime Error (SIGSEGV)"},
                "time": "0.5",
                "memory": 140,
            },
            {
                "status": {"id": 7, "description": "Runtime Error (SIGSEGV)"},
                "time": "0.03",
                "memory": 130,
            },
            {
                "status": {"id": 5, "description": "Time Limit Exceeded"},
                "time": "1.0",
                "memory": 150,
            },
        ]
    )
    monkeypatch.setattr(
        judge_admin,
        "_run_judge0",
        lambda *args, **kwargs: next(results),
    )
    with TestingSessionLocal() as db:
        db.add(Language(language_id=74, name="Python (metrics test)"))
        problem = Problem(
            problem_name="Submission metrics",
            time_limit=1,
            memory_limit=128,
            access_modifier=AccessModifier.PUBLIC,
        )
        db.add(problem)
        db.flush()
        db.add_all(
            [
                JudgeTestcase(
                    problem_id=problem.problem_id,
                    point=20,
                    input_data=str(index),
                    expected_output="expected 1" if index == 0 else "expected",
                )
                for index in range(5)
            ]
        )
        problem_id = problem.problem_id
        db.commit()

    client = TestClient(app)
    response = client.post(
        f"/student/problems/{problem_id}/submissions",
        json={"language_id": 74, "source_code": "print('test')"},
        cookies={"session_id": "metrics-student-session"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "Runtime Error (SIGSEGV)"
    assert result["score"] == 20
    assert result["passed_testcases"] == 1
    assert result["total_testcases"] == 5
    assert result["runtime_ms"] == 560
    assert result["memory_kb"] == 150
    assert [case["status"] for case in result["testcases"]] == [
        "Accepted",
        "Wrong Answer",
        "Runtime Error (SIGSEGV)",
        "Runtime Error (SIGSEGV)",
        "Time Limit Exceeded",
    ]

    history = client.get(
        "/student/submissions",
        cookies={"session_id": "metrics-student-session"},
    )
    assert history.status_code == 200, history.text
    assert {
        key: history.json()[0][key]
        for key in (
            "passed_testcases",
            "total_testcases",
            "runtime_ms",
            "memory_kb",
        )
    } == {
        "passed_testcases": 1,
        "total_testcases": 5,
        "runtime_ms": 560,
        "memory_kb": 150,
    }
    with TestingSessionLocal() as db:
        submission = db.query(Submission).one()
        assert submission.status == "Runtime Error (SIGSEGV)"
        assert submission.score == 20
        assert submission.passed_testcases == 1
        assert submission.total_testcases == 5
        assert submission.runtime_ms == 560
        assert submission.memory_kb == 150


def test_class_managers_attach_existing_owned_or_public_problems():
    owner_id = _create_account("class-problem-owner@example.com", AccountRole.ADMIN)
    other_admin_id = _create_account("class-problem-other@example.com", AccountRole.ADMIN)
    _set_session(owner_id, "class-problem-owner-session")
    _set_session(other_admin_id, "class-problem-other-session")
    client = TestClient(app)
    owner_cookies = {"session_id": "class-problem-owner-session"}
    other_cookies = {"session_id": "class-problem-other-session"}

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Attach existing problems"},
        cookies=owner_cookies,
    )
    assert class_response.status_code == 200, class_response.text
    class_id = class_response.json()["class_id"]

    def create_problem(name, cookies, access_modifier="private", data_constraints=None):
        response = client.post(
            "/admin/judge/problems",
            json={
                "problem_name": name,
                "data_constraints": data_constraints,
                "time_limit": 1,
                "memory_limit": 256,
                "access_modifier": access_modifier,
            },
            cookies=cookies,
        )
        assert response.status_code == 200, response.text
        return response.json()["problem_id"]

    own_private_id = create_problem(
        "Owner private",
        owner_cookies,
        data_constraints="1 <= n <= 10^4",
    )
    public_id = create_problem("Shared public", other_cookies, "public")
    other_private_id = create_problem("Other private", other_cookies)

    library = client.get("/admin/judge/problems", cookies=owner_cookies)
    assert library.status_code == 200, library.text
    listed_problem_ids = {item["problem_id"] for item in library.json()}
    assert own_private_id in listed_problem_ids
    assert public_id in listed_problem_ids
    assert other_private_id not in listed_problem_ids

    attach_url = f"/admin/judge/academic-classes/{class_id}/problems"
    assert client.post(
        attach_url,
        json={"problem_id": own_private_id},
        cookies=owner_cookies,
    ).status_code == 200
    assert client.post(
        attach_url,
        json={"problem_id": public_id},
        cookies=owner_cookies,
    ).status_code == 200
    assert client.post(
        attach_url,
        json={"problem_id": own_private_id},
        cookies=owner_cookies,
    ).status_code == 409
    assert client.post(
        attach_url,
        json={"problem_id": other_private_id},
        cookies=owner_cookies,
    ).status_code == 403
    class_detail = client.get(
        f"/admin/judge/academic-classes/{class_id}",
        cookies=owner_cookies,
    )
    assert class_detail.status_code == 200, class_detail.text
    attached_problem = next(
        item for item in class_detail.json()["problems"]
        if item["problem_id"] == own_private_id
    )
    assert attached_problem["data_constraints"] == "1 <= n <= 10^4"


def test_admin_can_manage_class_students_without_schema_changes():
    admin_id = _create_account("class-admin@example.com", AccountRole.ADMIN)
    student_account_id = _create_account("class-student@example.com", AccountRole.USER)
    searchable_account_id = _create_account(
        "searchable-member@example.com",
        AccountRole.USER,
        name="Searchable Member",
    )
    _set_session(admin_id, "class-session")
    client = TestClient(app)

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "CS 101"},
        cookies={"session_id": "class-session"},
    )
    assert class_response.status_code == 200, class_response.text
    class_id = class_response.json()["class_id"]

    search_response = client.get(
        f"/admin/judge/academic-classes/{class_id}/available-students?search=Searchable",
        cookies={"session_id": "class-session"},
    )
    assert search_response.status_code == 200, search_response.text
    assert searchable_account_id in [
        item["account_id"] for item in search_response.json()
    ]

    add_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/students",
        json={"account_id": student_account_id},
        cookies={"session_id": "class-session"},
    )
    assert add_response.status_code == 200, add_response.text

    detail_response = client.get(
        f"/admin/judge/academic-classes/{class_id}",
        cookies={"session_id": "class-session"},
    )
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["students"][0]["account_id"] == student_account_id
    assert detail_response.json()["students"][0]["account_role"] == "USER"
    assert student_account_id not in [
        item["account_id"]
        for item in client.get(
            f"/admin/judge/academic-classes/{class_id}/available-students?search=class",
            cookies={"session_id": "class-session"},
        ).json()
    ]

    remove_response = client.delete(
        f"/admin/judge/academic-classes/{class_id}/students/{student_account_id}",
        cookies={"session_id": "class-session"},
    )
    assert remove_response.status_code == 200, remove_response.text


def test_admin_can_be_invited_as_student_member_without_management_rights():
    owner_id = _create_account("owner-member@example.com", AccountRole.ADMIN)
    other_admin_id = _create_account("member-admin@example.com", AccountRole.ADMIN)
    _set_session(owner_id, "owner-member-session")
    _set_session(other_admin_id, "member-admin-session")
    client = TestClient(app)

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Member admin class"},
        cookies={"session_id": "owner-member-session"},
    )
    assert class_response.status_code == 200, class_response.text
    class_id = class_response.json()["class_id"]

    invite_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/students",
        json={"account_id": other_admin_id},
        cookies={"session_id": "owner-member-session"},
    )
    assert invite_response.status_code == 200, invite_response.text

    owner_detail = client.get(
        f"/admin/judge/academic-classes/{class_id}",
        cookies={"session_id": "owner-member-session"},
    )
    assert owner_detail.status_code == 200, owner_detail.text
    assert owner_detail.json()["students"][0]["account_role"] == "ADMIN"

    member_class_list = client.get(
        "/student/classes",
        cookies={"session_id": "member-admin-session"},
    )
    assert member_class_list.status_code == 200, member_class_list.text
    assert any(item["class_id"] == class_id for item in member_class_list.json())
    available_classes = client.get(
        "/student/available-classes",
        cookies={"session_id": "member-admin-session"},
    )
    assert available_classes.status_code == 200, available_classes.text
    assert next(
        item for item in available_classes.json() if item["class_id"] == class_id
    )["membership_status"] == "joined"

    member_detail = client.get(
        f"/student/classes/{class_id}",
        cookies={"session_id": "member-admin-session"},
    )
    assert member_detail.status_code == 200, member_detail.text

    admin_detail = client.get(
        f"/admin/judge/academic-classes/{class_id}",
        cookies={"session_id": "member-admin-session"},
    )
    assert admin_detail.status_code == 403, admin_detail.text


def test_class_manager_can_view_active_contest_without_student_membership():
    admin_id = _create_account("contest-view-manager@example.com", AccountRole.ADMIN)
    outsider_id = _create_account("contest-view-outsider@example.com", AccountRole.USER)
    contestant_id = _create_account("contest-view-contestant@example.com", AccountRole.USER)
    _set_session(admin_id, "contest-view-manager-session")
    _set_session(outsider_id, "contest-view-outsider-session")
    _set_session(contestant_id, "contest-view-contestant-session")
    client = TestClient(app)
    admin_cookies = {"session_id": "contest-view-manager-session"}

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Contest observer class"},
        cookies=admin_cookies,
    )
    assert class_response.status_code == 200, class_response.text
    class_id = class_response.json()["class_id"]
    problem_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/problems/create",
        json={
            "problem_name": "Contest observer problem",
            "time_limit": 1,
            "memory_limit": 256,
            "access_modifier": "public",
        },
        cookies=admin_cookies,
    )
    assert problem_response.status_code == 200, problem_response.text
    problem_id = problem_response.json()["problem_id"]
    invite_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/students",
        json={"account_id": contestant_id},
        cookies=admin_cookies,
    )
    assert invite_response.status_code == 200, invite_response.text
    contest_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Active contest for manager",
            "start_time": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "end_time": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "problem_ids": [problem_id],
        },
        cookies=admin_cookies,
    )
    assert contest_response.status_code == 200, contest_response.text
    contest_id = contest_response.json()["contest_id"]
    source_code = "print('contest submission')"
    with TestingSessionLocal() as db:
        db.add(Language(language_id=97, name="Test language"))
        db.add(
            Submission(
                problem_id=problem_id,
                account_id=contestant_id,
                language_id=97,
                contest_id=contest_id,
                class_id=class_id,
                status="Accepted",
                score=100,
                passed_testcases=1,
                total_testcases=1,
                source_code=base64.b64encode(source_code.encode()).decode("ascii"),
                source_code_encoded=True,
            )
        )
        db.commit()

    detail = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}",
        cookies=admin_cookies,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["can_submit"] is False
    assert detail.json()["can_view_all_submissions"] is True
    assert detail.json()["problems"][0]["problem_name"] == "Contest observer problem"

    standings = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}/standings",
        cookies=admin_cookies,
    )
    assert standings.status_code == 200, standings.text
    assert len(standings.json()["standings"]) == 1
    assert standings.json()["standings"][0]["account_name"] == "contest-view-contestant"
    assert standings.json()["standings"][0]["score"] == 100
    history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={contest_id}",
        cookies=admin_cookies,
    )
    assert history.status_code == 200, history.text
    assert len(history.json()) == 1
    assert history.json()[0]["account_name"] == "contest-view-contestant"
    submission_id = history.json()[0]["submission_id"]
    source = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}/submissions/{submission_id}/source",
        cookies=admin_cookies,
    )
    assert source.status_code == 200, source.text
    assert source.json()["source_code"] == source_code

    outsider_cookies = {"session_id": "contest-view-outsider-session"}
    assert client.get(
        f"/student/classes/{class_id}/contests/{contest_id}",
        cookies=outsider_cookies,
    ).status_code == 403
    assert client.get(
        f"/student/classes/{class_id}/contests/{contest_id}/standings",
        cookies=outsider_cookies,
    ).status_code == 403
    assert client.get(
        f"/student/classes/{class_id}/submissions?contest_id={contest_id}",
        cookies=outsider_cookies,
    ).status_code == 403
    assert client.get(
        f"/student/classes/{class_id}/contests/{contest_id}/submissions/{submission_id}/source",
        cookies=outsider_cookies,
    ).status_code == 403
    assert client.get(
        f"/student/classes/{class_id}/contests/{contest_id}/submissions/{submission_id}/source",
        cookies={"session_id": "contest-view-contestant-session"},
    ).status_code == 403


def test_class_manager_can_view_edit_and_review_contests_outside_schedule():
    admin_id = _create_account("contest-edit-manager@example.com", AccountRole.ADMIN)
    student_id = _create_account("contest-edit-student@example.com", AccountRole.USER)
    outsider_id = _create_account("contest-edit-outsider@example.com", AccountRole.ADMIN)
    _set_session(admin_id, "contest-edit-manager-session")
    _set_session(student_id, "contest-edit-student-session")
    _set_session(outsider_id, "contest-edit-outsider-session")
    client = TestClient(app)
    admin_cookies = {"session_id": "contest-edit-manager-session"}
    student_cookies = {"session_id": "contest-edit-student-session"}

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Contest edit permissions"},
        cookies=admin_cookies,
    )
    class_id = class_response.json()["class_id"]
    problem_ids = []
    for problem_name in ("Original contest problem", "Replacement contest problem"):
        response = client.post(
            f"/admin/judge/academic-classes/{class_id}/problems/create",
            json={
                "problem_name": problem_name,
                "time_limit": 1,
                "memory_limit": 256,
                "access_modifier": "public",
            },
            cookies=admin_cookies,
        )
        assert response.status_code == 200, response.text
        problem_ids.append(response.json()["problem_id"])

    assert client.post(
        f"/admin/judge/academic-classes/{class_id}/students",
        json={"account_id": student_id},
        cookies=admin_cookies,
    ).status_code == 200
    contest_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Finished contest to edit",
            "description": "Original description",
            "start_time": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "end_time": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "problem_ids": [problem_ids[0]],
        },
        cookies=admin_cookies,
    )
    assert contest_response.status_code == 200, contest_response.text
    contest_id = contest_response.json()["contest_id"]
    with TestingSessionLocal() as db:
        db.add(Language(language_id=98, name="Contest edit test language"))
        db.add(
            Submission(
                problem_id=problem_ids[0],
                account_id=student_id,
                language_id=98,
                contest_id=contest_id,
                class_id=class_id,
                status="Accepted",
                score=100,
                passed_testcases=1,
                total_testcases=1,
                source_code=base64.b64encode(b"print('history')").decode("ascii"),
                source_code_encoded=True,
            )
        )
        db.commit()

    contest_url = f"/student/classes/{class_id}/contests/{contest_id}"
    detail = client.get(contest_url, cookies=admin_cookies)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "finished"
    assert detail.json()["can_submit"] is False
    assert detail.json()["can_manage"] is True
    assert client.get(
        f"{contest_url}/standings",
        cookies=admin_cookies,
    ).status_code == 200
    history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={contest_id}",
        cookies=admin_cookies,
    )
    assert history.status_code == 200, history.text
    assert len(history.json()) == 1
    source = client.get(
        f"{contest_url}/submissions/{history.json()[0]['submission_id']}/source",
        cookies=admin_cookies,
    )
    assert source.status_code == 200, source.text

    update = client.patch(
        f"/admin/judge/academic-classes/{class_id}/contests/{contest_id}",
        json={
            "contest_name": "Updated contest",
            "description": "Updated description",
            "start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "end_time": (datetime.now(timezone.utc) + timedelta(days=1, hours=2)).isoformat(),
            "problem_ids": [problem_ids[1]],
        },
        cookies=admin_cookies,
    )
    assert update.status_code == 200, update.text
    assert update.json()["contest_name"] == "Updated contest"
    assert update.json()["status"] == "upcoming"

    updated_detail = client.get(contest_url, cookies=admin_cookies)
    assert updated_detail.status_code == 200, updated_detail.text
    assert updated_detail.json()["problems"][0]["problem_id"] == problem_ids[1]
    updated_history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={contest_id}",
        cookies=admin_cookies,
    )
    assert updated_history.status_code == 200, updated_history.text
    assert len(updated_history.json()) == 1
    assert client.get(
        f"{contest_url}/standings",
        cookies=admin_cookies,
    ).status_code == 200
    assert client.get(
        f"{contest_url}/submissions/{updated_history.json()[0]['submission_id']}/source",
        cookies=admin_cookies,
    ).status_code == 200

    assert client.get(contest_url, cookies=student_cookies).status_code == 409
    assert client.get(
        f"{contest_url}/standings",
        cookies=student_cookies,
    ).status_code == 409
    assert client.get(
        f"/student/classes/{class_id}/submissions?contest_id={contest_id}",
        cookies=student_cookies,
    ).status_code == 409
    assert client.patch(
        f"/admin/judge/academic-classes/{class_id}/contests/{contest_id}",
        json={
            "contest_name": "Forbidden edit",
            "description": None,
            "start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "end_time": (datetime.now(timezone.utc) + timedelta(days=1, hours=1)).isoformat(),
            "problem_ids": [problem_ids[0]],
        },
        cookies={"session_id": "contest-edit-outsider-session"},
    ).status_code == 403


def test_students_request_class_access_and_owner_approves():
    owner_id = _create_account("owner@example.com", AccountRole.ADMIN)
    student_id = _create_account("requester@example.com", AccountRole.USER)
    _set_session(owner_id, "owner-session")
    _set_session(student_id, "student-session")
    client = TestClient(app)

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Algorithms"},
        cookies={"session_id": "owner-session"},
    )
    class_id = class_response.json()["class_id"]

    request_response = client.post(
        f"/student/classes/{class_id}/join-requests",
        cookies={"session_id": "student-session"},
    )
    assert request_response.status_code == 200, request_response.text
    assert request_response.json()["status"] == "pending"
    available_classes = client.get(
        "/student/available-classes",
        cookies={"session_id": "student-session"},
    )
    assert next(
        item for item in available_classes.json() if item["class_id"] == class_id
    )["membership_status"] == "pending"
    assert client.get(
        f"/student/classes/{class_id}",
        cookies={"session_id": "student-session"},
    ).status_code == 403

    pending_response = client.get(
        f"/admin/judge/academic-classes/{class_id}/join-requests",
        cookies={"session_id": "owner-session"},
    )
    assert pending_response.status_code == 200, pending_response.text
    request_id = pending_response.json()[0]["join_request_id"]

    decision_response = client.patch(
        f"/admin/judge/academic-classes/{class_id}/join-requests/{request_id}",
        json={"status": "approved"},
        cookies={"session_id": "owner-session"},
    )
    assert decision_response.status_code == 200, decision_response.text
    assert client.get(
        f"/student/classes/{class_id}",
        cookies={"session_id": "student-session"},
    ).status_code == 200
    assert client.get(
        "/student/classes",
        cookies={"session_id": "student-session"},
    ).json()[0]["class_id"] == class_id


def test_admins_only_manage_owned_classes_and_students_cannot_create_them():
    owner_id = _create_account("owner2@example.com", AccountRole.ADMIN)
    other_admin_id = _create_account("other-admin@example.com", AccountRole.ADMIN)
    candidate_admin_id = _create_account(
        "invite-me@example.com",
        AccountRole.ADMIN,
        code="INVITE1",
        name="Invite Me",
    )
    student_id = _create_account("ordinary-student@example.com", AccountRole.USER)
    _set_session(owner_id, "owner2-session")
    _set_session(other_admin_id, "other-admin-session")
    _set_session(student_id, "ordinary-student-session")
    client = TestClient(app)

    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Owned class"},
        cookies={"session_id": "owner2-session"},
    )
    class_id = class_response.json()["class_id"]
    available_response = client.get(
        f"/admin/judge/academic-classes/{class_id}/available-managers?search=Invite",
        cookies={"session_id": "owner2-session"},
    )
    assert available_response.status_code == 200
    assert [
        item["account_id"] for item in available_response.json()
    ] == [candidate_admin_id]
    email_search_response = client.get(
        f"/admin/judge/academic-classes/{class_id}/available-managers?search=other-admin@example.com",
        cookies={"session_id": "owner2-session"},
    )
    assert [
        item["account_id"] for item in email_search_response.json()
    ] == [other_admin_id]
    assert client.get(
        f"/admin/judge/academic-classes/{class_id}/available-managers?search=%20%20",
        cookies={"session_id": "owner2-session"},
    ).status_code == 422

    invite_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/managers",
        json={"account_id": candidate_admin_id},
        cookies={"session_id": "owner2-session"},
    )
    assert invite_response.status_code == 200, invite_response.text
    assert candidate_admin_id in [
        item["account_id"]
        for item in client.get(
            f"/admin/judge/academic-classes/{class_id}/managers",
            cookies={"session_id": "owner2-session"},
        ).json()
    ]
    assert candidate_admin_id not in [
        item["account_id"]
        for item in client.get(
            f"/admin/judge/academic-classes/{class_id}/available-managers?search=Invite",
            cookies={"session_id": "owner2-session"},
        ).json()
    ]

    assert [item["class_id"] for item in client.get(
        "/admin/judge/academic-classes",
        cookies={"session_id": "other-admin-session"},
    ).json()] == []
    assert client.get(
        f"/admin/judge/academic-classes/{class_id}",
        cookies={"session_id": "other-admin-session"},
    ).status_code == 403
    assert client.get(
        f"/admin/judge/academic-classes/{class_id}/available-managers?search=Invite",
        cookies={"session_id": "other-admin-session"},
    ).status_code == 403
    assert client.post(
        f"/admin/judge/academic-classes/{class_id}/problems/create",
        json={
            "problem_name": "Unauthorized",
            "time_limit": 1,
            "memory_limit": 256,
        },
        cookies={"session_id": "other-admin-session"},
    ).status_code == 403
    assert client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Should not exist"},
        cookies={"session_id": "ordinary-student-session"},
    ).status_code == 403

    with TestingSessionLocal() as db:
        other_admin = Admin(account_id=other_admin_id, is_active=True)
        db.add(other_admin)
        db.commit()
        db.refresh(other_admin)
        db.add(ManageClass(class_id=class_id, admin_id=other_admin.admin_id))
        db.commit()

    assert client.get(
        f"/admin/judge/academic-classes/{class_id}",
        cookies={"session_id": "other-admin-session"},
    ).status_code == 200

    request_response = client.post(
        f"/student/classes/{class_id}/join-requests",
        cookies={"session_id": "ordinary-student-session"},
    )
    assert request_response.status_code == 200, request_response.text
    with TestingSessionLocal() as db:
        join_request = db.scalar(select(ClassJoinRequest))
        request_id = join_request.join_request_id

    assert client.get(
        f"/admin/judge/academic-classes/{class_id}/join-requests",
        cookies={"session_id": "other-admin-session"},
    ).status_code == 403
    assert client.patch(
        f"/admin/judge/academic-classes/{class_id}/join-requests/{request_id}",
        json={"status": "approved"},
        cookies={"session_id": "other-admin-session"},
    ).status_code == 403
    assert client.get(
        f"/student/classes/{class_id}",
        cookies={"session_id": "ordinary-student-session"},
    ).status_code == 403
    owner_requests = client.get(
        f"/admin/judge/academic-classes/{class_id}/join-requests",
        cookies={"session_id": "owner2-session"},
    )
    assert owner_requests.status_code == 200
    assert client.patch(
        f"/admin/judge/academic-classes/{class_id}/join-requests/{request_id}",
        json={"status": "approved"},
        cookies={"session_id": "owner2-session"},
    ).status_code == 200
    assert client.get(
        f"/student/classes/{class_id}",
        cookies={"session_id": "ordinary-student-session"},
    ).status_code == 200


def test_enrolled_student_can_submit_source_code_for_class_problem(monkeypatch):
    admin_id = _create_account("submit-admin@example.com", AccountRole.ADMIN)
    student_id = _create_account("submit-student@example.com", AccountRole.USER)
    _set_session(admin_id, "submit-admin-session")
    _set_session(student_id, "submit-student-session")
    with TestingSessionLocal() as db:
        db.add(Language(language_id=71, name="Python (3.8.1)"))
        db.commit()
    monkeypatch.setattr(
        judge_admin,
        "_run_judge0",
        lambda source_code, language_id, testcase, time_limit, memory_limit, **kwargs: {
            "status": {"id": 3, "description": "Accepted"},
            "stdout": "5\n",
        },
    )
    client = TestClient(app)

    class_id = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Submission class"},
        cookies={"session_id": "submit-admin-session"},
    ).json()["class_id"]
    problem = client.post(
        f"/admin/judge/academic-classes/{class_id}/problems/create",
        json={
            "problem_name": "Add two numbers",
            "time_limit": 1,
            "memory_limit": 256,
            "testcases": [
                {"input_data": "2 3\n", "expected_output": "5\n", "point": 2},
            ],
        },
        cookies={"session_id": "submit-admin-session"},
    )
    assert problem.status_code == 200, problem.text
    problem = problem.json()
    problem_id = problem["problem_id"]

    now = datetime.now(timezone.utc)
    invalid_contest = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Invalid contest",
            "start_time": (now - timedelta(minutes=5)).isoformat(),
            "end_time": (now + timedelta(minutes=30)).isoformat(),
            "problem_ids": [999],
        },
        cookies={"session_id": "submit-admin-session"},
    )
    assert invalid_contest.status_code == 422
    contest_response = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Live contest",
            "description": "A timed test",
            "start_time": (now - timedelta(minutes=5)).isoformat(),
            "end_time": (now + timedelta(minutes=30)).isoformat(),
            "problem_ids": [problem_id],
        },
        cookies={"session_id": "submit-admin-session"},
    )
    assert contest_response.status_code == 200, contest_response.text
    contest_id = contest_response.json()["contest_id"]

    submit_url = f"/student/classes/{class_id}/problems/{problem_id}/submissions"
    submission_body = {
        "language_id": 71,
        "source_code": "print(sum(map(int, input().split())))",
        "contest_id": contest_id,
    }
    assert client.post(
        submit_url,
        json=submission_body,
        cookies={"session_id": "submit-student-session"},
    ).status_code == 403

    request_id_response = client.post(
        f"/student/classes/{class_id}/join-requests",
        cookies={"session_id": "submit-student-session"},
    )
    request_id = client.get(
        f"/admin/judge/academic-classes/{class_id}/join-requests",
        cookies={"session_id": "submit-admin-session"},
    ).json()[0]["join_request_id"]
    assert request_id_response.status_code == 200
    assert client.patch(
        f"/admin/judge/academic-classes/{class_id}/join-requests/{request_id}",
        json={"status": "approved"},
        cookies={"session_id": "submit-admin-session"},
    ).status_code == 200

    student_cookies = {"session_id": "submit-student-session"}
    class_detail = client.get(
        f"/student/classes/{class_id}",
        cookies=student_cookies,
    )
    assert class_detail.status_code == 200, class_detail.text
    assert "testcases" not in class_detail.json()["problems"][0]
    assert any(
        member["account_id"] == student_id
        for member in class_detail.json()["students"]
    )
    assert client.post(
        f"/admin/judge/academic-classes/{class_id}/problems/create",
        json={
            "problem_name": "Student-created problem",
            "time_limit": 1,
            "memory_limit": 256,
            "testcases": [
                {"input_data": "1\n", "expected_output": "1\n", "point": 100},
            ],
        },
        cookies=student_cookies,
    ).status_code == 403
    assert client.patch(
        f"/admin/judge/problems/{problem_id}",
        json={
            "problem_name": "Student-edited problem",
            "testcases": [
                {"input_data": "1\n", "expected_output": "999\n", "point": 100},
            ],
        },
        cookies=student_cookies,
    ).status_code == 403

    future_contest = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Future contest",
            "start_time": (now + timedelta(days=1)).isoformat(),
            "end_time": (now + timedelta(days=1, hours=1)).isoformat(),
            "problem_ids": [problem_id],
        },
        cookies={"session_id": "submit-admin-session"},
    )
    assert future_contest.status_code == 200, future_contest.text
    future_contest_id = future_contest.json()["contest_id"]
    assert client.get(
        f"/student/classes/{class_id}/contests/{future_contest_id}",
        cookies={"session_id": "submit-student-session"},
    ).status_code == 409
    assert client.get(
        f"/student/classes/{class_id}/contests/{future_contest_id}/standings",
        cookies={"session_id": "submit-student-session"},
    ).status_code == 409
    assert client.post(
        submit_url,
        json={
            **submission_body,
            "contest_id": future_contest_id,
        },
        cookies={"session_id": "submit-student-session"},
    ).status_code == 409
    assert client.get(
        f"/student/classes/{class_id}/submissions?contest_id={future_contest_id}",
        cookies={"session_id": "submit-student-session"},
    ).status_code == 409

    finished_contest = client.post(
        f"/admin/judge/academic-classes/{class_id}/contests/create",
        json={
            "contest_name": "Finished contest",
            "start_time": (now - timedelta(hours=2)).isoformat(),
            "end_time": (now - timedelta(hours=1)).isoformat(),
            "problem_ids": [problem_id],
        },
        cookies={"session_id": "submit-admin-session"},
    )
    assert finished_contest.status_code == 200, finished_contest.text
    finished_contest_id = finished_contest.json()["contest_id"]
    finished_detail = client.get(
        f"/student/classes/{class_id}/contests/{finished_contest_id}",
        cookies={"session_id": "submit-student-session"},
    )
    assert finished_detail.status_code == 200, finished_detail.text
    assert finished_detail.json()["status"] == "finished"
    assert finished_detail.json()["can_submit"] is False
    finished_standings = client.get(
        f"/student/classes/{class_id}/contests/{finished_contest_id}/standings",
        cookies={"session_id": "submit-student-session"},
    )
    assert finished_standings.status_code == 200, finished_standings.text
    finished_history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={finished_contest_id}",
        cookies={"session_id": "submit-student-session"},
    )
    assert finished_history.status_code == 200, finished_history.text

    response = client.post(
        submit_url,
        json=submission_body,
        cookies={"session_id": "submit-student-session"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "Accepted"
    assert response.json()["score"] == 2
    with TestingSessionLocal() as db:
        db.add(
            Submission(
                problem_id=problem_id,
                account_id=student_id,
                language_id=71,
                contest_id=finished_contest_id,
                class_id=class_id,
                status="Accepted",
                score=2,
                passed_testcases=1,
                total_testcases=1,
                source_code=base64.b64encode(b"print('finished history')").decode("ascii"),
                source_code_encoded=True,
            )
        )
        db.commit()
    finished_history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={finished_contest_id}",
        cookies={"session_id": "submit-student-session"},
    )
    assert len(finished_history.json()) == 1
    own_source = client.get(
        f"/student/submissions/{finished_history.json()[0]['submission_id']}/source",
        cookies={"session_id": "submit-student-session"},
    )
    assert own_source.status_code == 200, own_source.text
    assert own_source.json()["source_code"] == "print('finished history')"

    contest_detail = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}",
        cookies={"session_id": "submit-student-session"},
    )
    assert contest_detail.status_code == 200, contest_detail.text
    assert contest_detail.json()["can_submit"] is True
    assert contest_detail.json()["problems"][0]["is_solved"] is True
    standings = client.get(
        f"/student/classes/{class_id}/contests/{contest_id}/standings",
        cookies={"session_id": "submit-student-session"},
    )
    assert standings.status_code == 200, standings.text
    assert standings.json()["standings"][0]["is_current_user"] is True
    assert standings.json()["standings"][0]["score"] == 2
    assert standings.json()["problems"] == [
        {
            "problem_id": problem_id,
            "problem_order": 1,
            "problem_name": "Add two numbers",
            "max_score": 2,
        }
    ]
    assert standings.json()["standings"][0]["problems"] == [
        {
            "problem_id": problem_id,
            "problem_order": 1,
            "problem_name": "Add two numbers",
            "attempts": 1,
            "solved": True,
            "score": 2,
            "max_score": 2,
        }
    ]
    history = client.get(
        f"/student/classes/{class_id}/submissions",
        cookies={"session_id": "submit-student-session"},
    )
    assert history.status_code == 200, history.text
    assert history.json() == []
    contest_history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={contest_id}",
        cookies={"session_id": "submit-student-session"},
    )
    assert contest_history.status_code == 200, contest_history.text
    assert len(contest_history.json()) == 1
    assert contest_history.json()[0]["contest_id"] == contest_id
    assert "source_code" not in contest_history.json()[0]
    unrelated_contest_history = client.get(
        f"/student/classes/{class_id}/submissions?contest_id={future_contest.json()['contest_id']}",
        cookies={"session_id": "submit-student-session"},
    )
    assert unrelated_contest_history.status_code == 409
    assert client.get(
        "/student/languages",
        cookies={"session_id": "submit-student-session"},
    ).json()[0]["language_id"] == 71
    with TestingSessionLocal() as db:
        submission = db.query(Submission).filter_by(contest_id=contest_id).one()
        assert submission.source_code == base64.b64encode(
            submission_body["source_code"].encode("utf-8")
        ).decode("ascii")
        assert submission.source_code_encoded is True
        assert submission.contest_id == contest_id


def test_problem_and_contest_inputs_are_validated():
    admin_id = _create_account("validation-admin@example.com", AccountRole.ADMIN)
    _set_session(admin_id, "validation-session")
    client = TestClient(app)

    invalid_problem = client.post(
        "/admin/judge/problems",
        json={
            "problem_name": "Invalid limits",
            "time_limit": 0,
            "memory_limit": 256,
        },
        cookies={"session_id": "validation-session"},
    )
    assert invalid_problem.status_code == 422

    invalid_contest = client.post(
        "/admin/judge/contests",
        json={
            "contest_name": "Invalid schedule",
            "start_time": "2026-10-01T12:00:00Z",
            "end_time": "2026-10-01T10:00:00Z",
        },
        cookies={"session_id": "validation-session"},
    )
    assert invalid_contest.status_code == 422
    class_response = client.post(
        "/admin/judge/academic-classes",
        json={"class_name": "Invalid contest schedule"},
        cookies={"session_id": "validation-session"},
    )
    assert class_response.status_code == 200, class_response.text
    invalid_class_contest = client.post(
        f"/admin/judge/academic-classes/{class_response.json()['class_id']}/contests/create",
        json={
            "contest_name": "Invalid class contest",
            "start_time": "2026-10-01T12:00:00Z",
            "end_time": "2026-10-01T10:00:00Z",
            "problem_ids": [1],
        },
        cookies={"session_id": "validation-session"},
    )
    assert invalid_class_contest.status_code == 422
