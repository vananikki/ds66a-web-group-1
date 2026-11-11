from __future__ import annotations

import base64
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from db.schemas import (
    AcademicClassContestIn,
    ClassContestCreateIn,
    AcademicClassCreateIn,
    AcademicClassProblemIn,
    AcademicClassStudentIn,
    ClassJoinDecisionIn,
    ContestCreateIn,
    ContestProblemIn,
    ProblemCreateIn,
    ProblemUpdateIn,
    StudentSubmissionIn,
)
from db.session import get_session
from db.tables_phase1 import (
    Account,
    AccountRole,
    Admin,
    AcademicClass,
    ClassJoinRequest,
    ManageClass,
    Session as UserSession,
    Student,
    StudentClass,
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
    Testcase,
    add_problem_to_contest,
)

router = APIRouter(prefix="/admin/judge", tags=["judge admin"])
student_router = APIRouter(prefix="/student", tags=["student"])


def _run_judge0(
    source_code: str,
    language_id: int,
    testcase: Testcase,
    time_limit: int,
    memory_limit: int,
    deadline: float | None = None,
) -> dict[str, Any]:
    judge_url = os.getenv("JUDGE0_URL", "http://localhost:2358").rstrip("/")
    deadline = deadline or time.monotonic() + 15
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Judge0 grading exceeded the 45-second request limit")
    encoded_payload = {
        "source_code": base64.b64encode(source_code.encode()).decode(),
        "language_id": language_id,
        "stdin": base64.b64encode(testcase.input_data.encode()).decode(),
        "cpu_time_limit": time_limit,
        "memory_limit": memory_limit * 1024,
    }

    with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
        response = client.post(
            f"{judge_url}/submissions",
            params={"base64_encoded": "true", "wait": "false"},
            json=encoded_payload,
            timeout=max(0.1, min(5.0, remaining)),
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("Judge0 did not return a submission token")

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            result_response = client.get(
                f"{judge_url}/submissions/{token}",
                params={"base64_encoded": "true"},
                timeout=max(0.1, min(2.0, remaining)),
            )
            result_response.raise_for_status()
            result = result_response.json()
            if not isinstance(result, dict) or not isinstance(result.get("status"), dict):
                raise ValueError("Judge0 returned an invalid result")
            status_id = result["status"].get("id")
            if isinstance(status_id, int) and status_id >= 3:
                for field in ("stdout", "stderr", "compile_output"):
                    value = result.get(field)
                    result[field] = (
                        base64.b64decode(value).decode("utf-8", errors="replace")
                        if value
                        else ""
                    )
                return result
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    raise TimeoutError("Judge0 grading exceeded the 45-second request limit")


def _judge0_metric(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        metric = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Judge0 returned an invalid {field} value") from exc
    if not metric.is_finite() or metric < 0:
        raise ValueError(f"Judge0 returned an invalid {field} value")
    return metric


def _metric_as_int(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _require_admin_account(session_id: str | None, session: Session) -> Account:
    account = _require_authenticated_account(session_id, session)
    if not account.is_admin:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý judge")

    admin = session.scalar(select(Admin).where(Admin.account_id == account.account_id))
    if admin is not None and not admin.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản quản trị đã bị vô hiệu hóa")

    return account


def _require_authenticated_account(session_id: str | None, session: Session) -> Account:
    if session_id is None:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")

    user_session = session.get(UserSession, session_id)
    if user_session is None:
        raise HTTPException(status_code=401, detail="Session không hợp lệ")

    expires_at = user_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session đã hết hạn")

    account = session.get(Account, user_session.account_id)
    if account is None:
        raise HTTPException(status_code=401, detail="Tài khoản không tồn tại")
    if not account.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản chưa được kích hoạt")
    return account


def _require_student_account(session_id: str | None, session: Session) -> Account:
    account = _require_authenticated_account(session_id, session)
    if not account.is_user:
        raise HTTPException(status_code=403, detail="Chức năng này dành cho học viên hoặc quản trị viên được mời vào lớp")
    return account


def _class_owner_account_id(class_id: int, session: Session) -> int | None:
    return session.scalar(
        select(Admin.account_id)
        .join(ManageClass, ManageClass.admin_id == Admin.admin_id)
        .where(ManageClass.class_id == class_id)
        .order_by(ManageClass.manage_id)
        .limit(1)
    )


def _require_class_manager(
    account: Account,
    class_id: int,
    session: Session,
) -> bool:
    if session.get(AcademicClass, class_id) is None:
        raise HTTPException(status_code=404, detail="Academic class không tồn tại")
    if account.is_super_admin:
        return _class_owner_account_id(class_id, session) == account.account_id

    admin = session.scalar(select(Admin).where(Admin.account_id == account.account_id))
    if admin is None:
        raise HTTPException(status_code=403, detail="Bạn chưa được cấp quyền quản lý lớp này")
    assignment = session.scalar(
        select(ManageClass).where(
            ManageClass.class_id == class_id,
            ManageClass.admin_id == admin.admin_id,
        )
    )
    if assignment is None:
        raise HTTPException(status_code=403, detail="Bạn chưa được cấp quyền quản lý lớp này")
    return _class_owner_account_id(class_id, session) == account.account_id


def _is_class_manager(account: Account, class_id: int, session: Session) -> bool:
    if not account.is_admin:
        return False
    if account.is_super_admin:
        return _class_owner_account_id(class_id, session) == account.account_id
    return session.scalar(
        select(ManageClass.manage_id)
        .join(Admin, Admin.admin_id == ManageClass.admin_id)
        .where(
            ManageClass.class_id == class_id,
            Admin.account_id == account.account_id,
        )
    ) is not None


def _require_class_contest_viewer(
    account: Account,
    class_id: int,
    contest_id: int,
    session: Session,
) -> bool:
    if session.get(AcademicClassContest, (class_id, contest_id)) is None:
        raise HTTPException(status_code=404, detail="Cuộc thi không thuộc lớp này")

    student = session.scalar(select(Student).where(Student.account_id == account.account_id))
    is_class_member = student is not None and session.scalar(
        select(StudentClass.student_id).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == student.student_id,
        )
    ) is not None
    if is_class_member or _is_class_manager(account, class_id, session):
        return is_class_member
    else:
        raise HTTPException(
            status_code=403,
            detail="Bạn chưa tham gia hoặc được phân công quản lý lớp này",
        )


def _require_class_owner(
    account: Account,
    class_id: int,
    session: Session,
) -> None:
    if not _require_class_manager(account, class_id, session):
        raise HTTPException(
            status_code=403,
            detail="Chỉ admin chính của lớp mới có thể duyệt yêu cầu tham gia",
        )


def _parse_datetime(value: str | None, field_name: str) -> datetime:
    if value is None or value == "":
        raise HTTPException(status_code=422, detail=f"{field_name} là bắt buộc")

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} không hợp lệ") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_problem(problem: Problem) -> dict[str, Any]:
    return {
        "problem_id": problem.problem_id,
        "problem_name": problem.problem_name,
        "description": problem.description,
        "input_description": problem.input_description,
        "output_description": problem.output_description,
        "data_constraints": problem.data_constraints,
        "time_limit": problem.time_limit,
        "memory_limit": problem.memory_limit,
        "access_modifier": problem.access_modifier.value,
        "created_by": problem.created_by,
        "creator_name": problem.creator.account_name if problem.creator else None,
        "is_anonymous": problem.created_by is None,
        "created_at": problem.created_at.isoformat() if problem.created_at else None,
    }


def _serialize_contest(contest: Contest) -> dict[str, Any]:
    return {
        "contest_id": contest.contest_id,
        "contest_name": contest.contest_name,
        "description": contest.description,
        "start_time": _as_utc(contest.start_time).isoformat(),
        "end_time": _as_utc(contest.end_time).isoformat(),
        "created_by": contest.created_by,
        "creator_name": contest.creator.account_name if contest.creator else None,
        "created_at": contest.created_at.isoformat() if contest.created_at else None,
        "status": _contest_status(contest),
    }


def _contest_status(contest: Contest) -> str:
    now = datetime.now(timezone.utc)
    start_time = _as_utc(contest.start_time)
    end_time = _as_utc(contest.end_time)
    if now < start_time:
        return "upcoming"
    if now <= end_time:
        return "active"
    return "finished"


def _require_active_contest(contest: Contest) -> None:
    status = _contest_status(contest)
    if status != "active":
        if status == "upcoming":
            detail = "Cuộc thi chưa bắt đầu"
        else:
            detail = "Cuộc thi đã kết thúc"
        raise HTTPException(status_code=409, detail=detail)


def _contest_hidden_problem_visible_to_student(
    session: Session,
    account: Account,
    contest: Contest,
    problem: Problem,
    class_id: int,
) -> bool:
    if problem.access_modifier != AccessModifier.HIDDEN:
        return True
    if problem.created_by == account.account_id:
        return True
    now = datetime.now(timezone.utc)
    if now < _as_utc(contest.start_time):
        return False
    membership = session.scalar(
        select(StudentClass.student_id).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == select(Student.student_id).where(
                Student.account_id == account.account_id,
            ).scalar_subquery(),
        )
    )
    return membership is not None


def _search_problems(stmt, query: str | None):
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return stmt

    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", normalized_query)))
    normalized_name = func.lower(Problem.problem_name)
    phrase_match = normalized_name.contains(normalized_query, autoescape=True)
    token_matches = [
        normalized_name.contains(token, autoescape=True)
        for token in tokens
    ]
    relevance = case(
        (normalized_name == normalized_query, 100),
        (normalized_name.startswith(normalized_query, autoescape=True), 80),
        (phrase_match, 60),
        else_=0,
    )
    for matched in token_matches:
        relevance = relevance + case(
            (matched, 12),
            else_=0,
        )

    if token_matches:
        search_match = or_(phrase_match, and_(*token_matches))
    else:
        search_match = phrase_match
    return stmt.where(search_match).order_by(relevance.desc(), Problem.problem_id.desc())


@router.get("/problems")
def list_problems(
    q: str | None = Query(default=None, max_length=100),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    stmt = select(Problem).options(selectinload(Problem.creator))
    if account.role != AccountRole.SUPER_ADMIN:
        stmt = stmt.where(
            or_(
                Problem.created_by == account.account_id,
                Problem.access_modifier == AccessModifier.PUBLIC,
            )
        )

    stmt = _search_problems(stmt, q)
    if not q or not q.strip():
        stmt = stmt.order_by(Problem.problem_id.desc())
    problems = session.scalars(stmt).all()
    return [_serialize_problem(problem) for problem in problems]


@router.get("/problems/{problem_id}")
def get_problem(
    problem_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    problem = session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem không tồn tại")
    if account.role != AccountRole.SUPER_ADMIN and problem.created_by != account.account_id:
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể xem bài tập của chính mình")
    return {
        **_serialize_problem(problem),
        "class_ids": [item.class_id for item in problem.class_problems],
        "testcases": [
            {
                "input_data": testcase.input_data,
                "expected_output": testcase.expected_output,
                "point": testcase.point,
            }
            for testcase in sorted(problem.testcases, key=lambda item: item.testcase_id)
        ],
    }


@router.post("/problems")
def create_problem(
    payload: ProblemCreateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    problem = Problem(
        problem_name=payload.problem_name,
        description=payload.description or None,
        input_description=payload.input_description or None,
        output_description=payload.output_description or None,
        data_constraints=payload.data_constraints or None,
        time_limit=payload.time_limit,
        memory_limit=payload.memory_limit,
        created_by=account.account_id,
        access_modifier=AccessModifier(payload.access_modifier),
        testcases=[
            Testcase(
                input_data=testcase.input_data,
                expected_output=testcase.expected_output,
                point=testcase.point,
            )
            for testcase in payload.testcases
        ],
    )

    session.add(problem)
    session.commit()
    session.refresh(problem)
    return _serialize_problem(problem)


@router.patch("/problems/{problem_id}")
def update_problem(
    problem_id: int,
    payload: ProblemUpdateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    problem = session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem không tồn tại")

    if account.role != AccountRole.SUPER_ADMIN and problem.created_by != account.account_id:
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể sửa problem của chính mình")

    updates = payload.model_dump(exclude_unset=True)
    if "problem_name" in updates and updates["problem_name"] is not None:
        problem.problem_name = updates["problem_name"]
    if "description" in updates:
        problem.description = updates["description"] or None
    if "input_description" in updates:
        problem.input_description = updates["input_description"] or None
    if "output_description" in updates:
        problem.output_description = updates["output_description"] or None
    if "data_constraints" in updates:
        problem.data_constraints = updates["data_constraints"] or None
    if "time_limit" in updates and updates["time_limit"] is not None:
        problem.time_limit = updates["time_limit"]
    if "memory_limit" in updates and updates["memory_limit"] is not None:
        problem.memory_limit = updates["memory_limit"]
    if "access_modifier" in updates and updates["access_modifier"] is not None:
        problem.access_modifier = AccessModifier(updates["access_modifier"])
    if "testcases" in updates and updates["testcases"] is not None:
        problem.testcases = [
            Testcase(
                input_data=testcase["input_data"],
                expected_output=testcase["expected_output"],
                point=testcase["point"],
            )
            for testcase in updates["testcases"]
        ]

    session.commit()
    session.refresh(problem)
    return _serialize_problem(problem)


@router.delete("/problems/{problem_id}")
def delete_problem(
    problem_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    if account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Chỉ super admin mới có thể xóa problem",
        )

    problem = session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem không tồn tại")

    session.execute(
        delete(Submission).where(Submission.problem_id == problem_id)
    )
    session.execute(
        delete(Testcase).where(Testcase.problem_id == problem_id)
    )
    session.execute(
        delete(AcademicClassProblem).where(
            AcademicClassProblem.problem_id == problem_id
        )
    )
    session.execute(
        delete(ContestProblem).where(ContestProblem.problem_id == problem_id)
    )
    session.delete(problem)
    session.commit()

    return {"message": "Đã xóa problem và dữ liệu liên quan", "problem_id": problem_id}


@router.delete("/academic-classes/{class_id}")
def delete_academic_class(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    if account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ super admin mới có thể xóa lớp")

    academic_class = session.get(AcademicClass, class_id)
    if academic_class is None:
        raise HTTPException(status_code=404, detail="Academic class không tồn tại")

    session.execute(delete(ClassJoinRequest).where(ClassJoinRequest.class_id == class_id))
    session.execute(delete(StudentClass).where(StudentClass.class_id == class_id))
    session.execute(delete(ManageClass).where(ManageClass.class_id == class_id))
    session.execute(
        delete(AcademicClassProblem).where(AcademicClassProblem.class_id == class_id)
    )
    session.execute(
        delete(AcademicClassContest).where(AcademicClassContest.class_id == class_id)
    )
    session.delete(academic_class)
    session.commit()
    return {"message": "Đã xóa lớp và các liên kết phụ thuộc", "class_id": class_id}


@router.get("/academic-classes")
def list_academic_classes(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    query = select(AcademicClass).order_by(AcademicClass.class_id.desc())
    if account.role != AccountRole.SUPER_ADMIN:
        query = (
            query.outerjoin(ManageClass, ManageClass.class_id == AcademicClass.class_id)
            .outerjoin(Admin, Admin.admin_id == ManageClass.admin_id)
            .where(
                or_(
                    Admin.account_id == account.account_id,
                    ManageClass.manage_id.is_(None),
                )
            )
        )
    classes = session.scalars(query).unique().all()
    return [
        {
            "class_id": academic_class.class_id,
            "class_name": academic_class.class_name,
            "is_owner": _class_owner_account_id(academic_class.class_id, session) == account.account_id,
            "is_claimable": _class_owner_account_id(academic_class.class_id, session) is None,
        }
        for academic_class in classes
    ]


@router.post("/academic-classes/{class_id}/claim")
def claim_unowned_class(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    if account.role not in {AccountRole.ADMIN, AccountRole.SUPER_ADMIN}:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên lớp mới có thể nhận lớp chưa có chủ")

    academic_class = session.scalar(
        select(AcademicClass)
        .where(AcademicClass.class_id == class_id)
        .with_for_update()
    )
    if academic_class is None:
        raise HTTPException(status_code=404, detail="Academic class không tồn tại")
    if _class_owner_account_id(class_id, session) is not None:
        raise HTTPException(status_code=409, detail="Lớp này đã có người quản lý")

    admin = session.scalar(select(Admin).where(Admin.account_id == account.account_id))
    if admin is None:
        admin = Admin(account_id=account.account_id, is_active=True)
        session.add(admin)
        session.flush()
    session.add(ManageClass(class_id=class_id, admin_id=admin.admin_id))
    session.commit()
    return {"class_id": class_id, "class_name": academic_class.class_name}


@router.get("/academic-classes/{class_id}")
def get_academic_class_detail(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    academic_class = session.scalar(
        select(AcademicClass)
        .options(
            selectinload(AcademicClass.student_classes)
            .selectinload(StudentClass.student)
            .selectinload(Student.account)
        )
        .where(AcademicClass.class_id == class_id)
    )
    if academic_class is None:
        raise HTTPException(status_code=404, detail="Academic class không tồn tại")
    is_owner = _require_class_manager(account, class_id, session)

    problems = []
    for item in sorted(academic_class.class_problems, key=lambda x: x.problem_id):
        problem = item.problem
        problems.append(
            {
                "problem_id": problem.problem_id,
                "problem_name": problem.problem_name,
                "description": problem.description,
                "input_description": problem.input_description,
                "output_description": problem.output_description,
                "data_constraints": problem.data_constraints,
                "time_limit": problem.time_limit,
                "memory_limit": problem.memory_limit,
                "access_modifier": problem.access_modifier.value,
                "is_required": item.is_required,
            }
        )

    contests = []
    for item in sorted(academic_class.class_contests, key=lambda x: x.contest_id):
        contest = item.contest
        contests.append(
            {
                "contest_id": contest.contest_id,
                "contest_name": contest.contest_name,
                "description": contest.description,
                "start_time": _as_utc(contest.start_time).isoformat(),
                "end_time": _as_utc(contest.end_time).isoformat(),
                "status": _contest_status(contest),
                "problem_ids": [
                    item.problem_id
                    for item in sorted(
                        contest.contest_problems,
                        key=lambda problem: problem.problem_order,
                    )
                ],
            }
        )

    students = [
        {
            "student_id": item.student.student_id,
            "account_id": item.student.account_id,
            "account_name": item.student.account.account_name,
            "account_email": item.student.account.account_email,
            "account_role": item.student.account.role.value,
        }
        for item in sorted(academic_class.student_classes, key=lambda x: x.student_id)
    ]

    return {
        "class_id": academic_class.class_id,
        "class_name": academic_class.class_name,
        "problems": problems,
        "contests": contests,
        "students": students,
        "is_owner": is_owner,
        "owner_account_id": _class_owner_account_id(class_id, session),
    }


@router.post("/academic-classes")
def create_academic_class(
    payload: AcademicClassCreateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    academic_class = AcademicClass(class_name=payload.class_name)
    session.add(academic_class)
    admin = session.scalar(select(Admin).where(Admin.account_id == account.account_id))
    if admin is None:
        admin = Admin(account_id=account.account_id, is_active=True)
        session.add(admin)
        session.flush()
    academic_class.manage_classes.append(ManageClass(admin=admin))
    session.commit()
    session.refresh(academic_class)
    return {"class_id": academic_class.class_id, "class_name": academic_class.class_name}


@router.get("/academic-classes/{class_id}/managers")
def list_class_managers(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_manager(account, class_id, session)
    assignments = session.scalars(
        select(ManageClass)
        .options(selectinload(ManageClass.admin).selectinload(Admin.account))
        .where(ManageClass.class_id == class_id)
        .order_by(ManageClass.manage_id)
    ).all()
    return [
        {
            "account_id": item.admin.account_id,
            "account_name": item.admin.account.account_name,
            "account_email": item.admin.account.account_email,
            "is_owner": item.admin.account_id == _class_owner_account_id(class_id, session),
        }
        for item in assignments
    ]


@router.get("/academic-classes/{class_id}/available-managers")
def list_available_class_managers(
    class_id: int,
    search: str = Query(min_length=2, max_length=100),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    is_owner = _require_class_manager(account, class_id, session)
    if not is_owner and account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Chỉ chủ lớp mới có thể mời quản trị viên",
        )
    search = search.strip()
    if len(search) < 2:
        raise HTTPException(
            status_code=422,
            detail="Nhập ít nhất 2 ký tự để tìm quản trị viên",
        )

    assigned_admins = (
        select(ManageClass.manage_id)
        .join(Admin, Admin.admin_id == ManageClass.admin_id)
        .where(
            ManageClass.class_id == class_id,
            Admin.account_id == Account.account_id,
        )
    )
    candidates = session.scalars(
        select(Account)
        .outerjoin(Admin, Admin.account_id == Account.account_id)
        .where(
            Account.role == AccountRole.ADMIN,
            Account.is_active.is_(True),
            or_(Admin.admin_id.is_(None), Admin.is_active.is_(True)),
            or_(
                Account.account_name.ilike(f"%{search}%"),
                Account.account_email.ilike(f"%{search}%"),
            ),
            ~assigned_admins.exists(),
        )
        .order_by(Account.account_name, Account.account_email)
        .limit(10)
    ).all()
    return [
        {
            "account_id": candidate.account_id,
            "account_name": candidate.account_name,
            "account_email": candidate.account_email,
        }
        for candidate in candidates
    ]


@router.post("/academic-classes/{class_id}/managers")
def add_class_manager(
    class_id: int,
    payload: AcademicClassStudentIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    is_owner = _require_class_manager(account, class_id, session)
    if not is_owner and account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ chủ lớp mới có thể thay đổi quyền quản lý")

    manager_account = session.get(Account, payload.account_id)
    if manager_account is None or manager_account.role != AccountRole.ADMIN:
        raise HTTPException(status_code=404, detail="Chỉ có thể thêm tài khoản quản trị đang hoạt động")
    if not manager_account.is_active:
        raise HTTPException(status_code=409, detail="Tài khoản quản trị chưa được kích hoạt")

    admin = session.scalar(select(Admin).where(Admin.account_id == manager_account.account_id))
    if admin is None:
        admin = Admin(account_id=manager_account.account_id, is_active=True)
        session.add(admin)
        session.flush()
    if not admin.is_active:
        raise HTTPException(status_code=409, detail="Tài khoản quản trị không có quyền hoạt động")
    if session.scalar(
        select(ManageClass).where(
            ManageClass.class_id == class_id,
            ManageClass.admin_id == admin.admin_id,
        )
    ):
        raise HTTPException(status_code=409, detail="Quản trị viên đã có quyền trong lớp")

    session.add(ManageClass(class_id=class_id, admin_id=admin.admin_id))
    session.commit()
    return {"class_id": class_id, "account_id": manager_account.account_id}


@router.delete("/academic-classes/{class_id}/managers/{account_id}")
def remove_class_manager(
    class_id: int,
    account_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    is_owner = _require_class_manager(account, class_id, session)
    if not is_owner and account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ chủ lớp mới có thể thay đổi quyền quản lý")
    if (
        account.role != AccountRole.SUPER_ADMIN
        and account_id == _class_owner_account_id(class_id, session)
    ):
        raise HTTPException(status_code=409, detail="Không thể gỡ quyền của chủ lớp")

    admin = session.scalar(select(Admin).where(Admin.account_id == account_id))
    assignment = session.scalar(
        select(ManageClass).where(
            ManageClass.class_id == class_id,
            ManageClass.admin_id == admin.admin_id if admin else False,
        )
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Quản trị viên không quản lý lớp này")
    session.delete(assignment)
    session.commit()
    return {"class_id": class_id, "account_id": account_id}


@student_router.get("/classes")
def list_student_classes(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    student = session.scalar(select(Student).where(Student.account_id == account.account_id))
    if student is None:
        return []
    memberships = session.scalars(
        select(StudentClass)
        .options(selectinload(StudentClass.academic_class))
        .where(StudentClass.student_id == student.student_id)
        .order_by(StudentClass.class_id)
    ).all()
    return [
        {
            "class_id": item.class_id,
            "class_name": item.academic_class.class_name,
        }
        for item in memberships
    ]


@student_router.get("/available-classes")
def list_available_classes(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    student = session.scalar(select(Student).where(Student.account_id == account.account_id))
    memberships = set()
    requests = {}
    if student is not None:
        memberships = set(
            session.scalars(
                select(StudentClass.class_id).where(StudentClass.student_id == student.student_id)
            ).all()
        )
        requests = {
            item.class_id: item.status
            for item in session.scalars(
                select(ClassJoinRequest).where(ClassJoinRequest.student_id == student.student_id)
            ).all()
        }

    classes = session.scalars(select(AcademicClass).order_by(AcademicClass.class_name)).all()
    return [
        {
            "class_id": item.class_id,
            "class_name": item.class_name,
            "membership_status": (
                "joined" if item.class_id in memberships else requests.get(item.class_id)
            ),
        }
        for item in classes
    ]


@student_router.post("/classes/{class_id}/join-requests")
def request_to_join_class(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    if session.get(AcademicClass, class_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học")

    student = session.scalar(select(Student).where(Student.account_id == account.account_id))
    if student is None:
        student = Student(account_id=account.account_id)
        session.add(student)
        session.flush()
    if session.scalar(
        select(StudentClass).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == student.student_id,
        )
    ):
        raise HTTPException(status_code=409, detail="Bạn đã là thành viên của lớp")

    join_request = session.scalar(
        select(ClassJoinRequest).where(
            ClassJoinRequest.class_id == class_id,
            ClassJoinRequest.student_id == student.student_id,
        )
    )
    if join_request is not None:
        if join_request.status == "pending":
            raise HTTPException(status_code=409, detail="Yêu cầu tham gia đang chờ duyệt")
        join_request.status = "pending"
        join_request.requested_at = datetime.now(timezone.utc)
    else:
        join_request = ClassJoinRequest(class_id=class_id, student_id=student.student_id)
        session.add(join_request)
    session.commit()
    return {"class_id": class_id, "status": join_request.status}


@student_router.get("/classes/{class_id}")
def get_student_class(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    student = session.scalar(select(Student).where(Student.account_id == account.account_id))
    membership = session.scalar(
        select(StudentClass).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == student.student_id if student else False,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Bạn chưa tham gia lớp này")
    academic_class = session.scalar(
        select(AcademicClass)
        .options(
            selectinload(AcademicClass.class_problems).selectinload(AcademicClassProblem.problem),
            selectinload(AcademicClass.class_contests).selectinload(AcademicClassContest.contest),
            selectinload(AcademicClass.student_classes)
            .selectinload(StudentClass.student)
            .selectinload(Student.account),
        )
        .where(AcademicClass.class_id == class_id)
    )
    visible_problems = []
    for item in academic_class.class_problems:
        if (
            item.problem.access_modifier == AccessModifier.HIDDEN
            and item.problem.created_by != account.account_id
        ):
            continue
        visible_problems.append(
            {
                "problem_id": item.problem.problem_id,
                "problem_name": item.problem.problem_name,
                "description": item.problem.description,
                "input_description": item.problem.input_description,
                "output_description": item.problem.output_description,
                "data_constraints": item.problem.data_constraints,
                "time_limit": item.problem.time_limit,
                "memory_limit": item.problem.memory_limit,
                "is_required": item.is_required,
            }
        )
    return {
        "class_id": academic_class.class_id,
        "class_name": academic_class.class_name,
        "problems": visible_problems,
        "students": [
            {
                "account_id": item.student.account_id,
                "account_name": item.student.account.account_name,
                "account_role": item.student.account.role.value,
            }
            for item in sorted(academic_class.student_classes, key=lambda member: member.student_id)
        ],
        "contests": [
            {
                "contest_id": item.contest.contest_id,
                "contest_name": item.contest.contest_name,
                "start_time": _as_utc(item.contest.start_time).isoformat(),
                "end_time": _as_utc(item.contest.end_time).isoformat(),
                "status": _contest_status(item.contest),
            }
            for item in academic_class.class_contests
        ],
    }


@student_router.get("/languages")
def list_submission_languages(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    _require_authenticated_account(session_id, session)
    return [
        {"language_id": item.language_id, "name": item.name}
        for item in session.scalars(select(Language).order_by(Language.name)).all()
    ]


@student_router.get("/classes/{class_id}/contests/{contest_id}")
def get_student_contest(
    class_id: int,
    contest_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    is_class_member = _require_class_contest_viewer(
        account, class_id, contest_id, session
    )
    contest = session.scalar(
        select(Contest)
        .options(
            selectinload(Contest.contest_problems).selectinload(ContestProblem.problem)
        )
        .where(Contest.contest_id == contest_id)
    )
    if contest is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc thi")
    is_class_manager = _is_class_manager(account, class_id, session)
    if not is_class_manager and _contest_status(contest) == "upcoming":
        _require_active_contest(contest)
    status = _contest_status(contest)
    now = datetime.now(timezone.utc)
    can_view_all_submissions = is_class_manager
    visible_problems = []
    for item in contest.contest_problems:
        if (
            item.problem.access_modifier == AccessModifier.HIDDEN
            and item.problem.created_by != account.account_id
            and is_class_member
            and not is_class_manager
            and not _contest_hidden_problem_visible_to_student(
                session,
                account,
                contest,
                item.problem,
                class_id,
            )
        ):
            continue
        visible_problems.append(
            {
                "problem_id": item.problem_id,
                "problem_name": item.problem.problem_name,
                "description": item.problem.description,
                "input_description": item.problem.input_description,
                "output_description": item.problem.output_description,
                "data_constraints": item.problem.data_constraints,
                "time_limit": item.problem.time_limit,
                "memory_limit": item.problem.memory_limit,
                "problem_order": item.problem_order,
                "is_solved": bool(
                    session.scalar(
                        select(Submission.submission_id).where(
                            Submission.account_id == account.account_id,
                            Submission.contest_id == contest_id,
                            Submission.problem_id == item.problem_id,
                            Submission.status == "Accepted",
                        ).limit(1)
                    )
                ),
            }
        )
    return {
        "contest_id": contest.contest_id,
        "contest_name": contest.contest_name,
        "description": contest.description,
        "start_time": _as_utc(contest.start_time).isoformat(),
        "end_time": _as_utc(contest.end_time).isoformat(),
        "status": status,
        "can_submit": status == "active" and is_class_member,
        "can_view_all_submissions": can_view_all_submissions,
        "can_manage": is_class_manager,
        "problems": visible_problems,
        "server_time": now.isoformat(),
    }


@student_router.get("/classes/{class_id}/contests/{contest_id}/standings")
def get_student_contest_standings(
    class_id: int,
    contest_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    _require_class_contest_viewer(account, class_id, contest_id, session)
    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc thi")
    if not _is_class_manager(account, class_id, session) and _contest_status(contest) == "upcoming":
        _require_active_contest(contest)

    contest_problem_rows = session.execute(
        select(
            ContestProblem.problem_id,
            ContestProblem.problem_order,
            Problem.problem_name,
            func.coalesce(func.sum(Testcase.point), 0).label("max_score"),
        )
        .join(Problem, Problem.problem_id == ContestProblem.problem_id)
        .outerjoin(Testcase, Testcase.problem_id == ContestProblem.problem_id)
        .where(ContestProblem.contest_id == contest_id)
        .group_by(
            ContestProblem.problem_id,
            ContestProblem.problem_order,
            Problem.problem_name,
        )
        .order_by(ContestProblem.problem_order, ContestProblem.problem_id)
    ).all()
    contest_problems = [
        {
            "problem_id": problem_id,
            "problem_order": problem_order,
            "problem_name": problem_name,
            "max_score": max_score,
        }
        for problem_id, problem_order, problem_name, max_score in contest_problem_rows
    ]

    rows = session.execute(
        select(Submission.account_id, Account.account_name, Submission.problem_id,
               Submission.status, Submission.score, Submission.submitted_at)
        .join(Account, Account.account_id == Submission.account_id)
        .where(Submission.contest_id == contest_id)
        .order_by(Submission.submitted_at)
    ).all()
    standings: dict[int, dict[str, Any]] = {}
    for account_id, account_name, problem_id, status, score, submitted_at in rows:
        participant = standings.setdefault(
            account_id,
            {
                "account_id": account_id,
                "account_name": account_name,
                "solved": 0,
                "score": 0,
                "penalty": 0,
                "problems": {
                    item["problem_id"]: {
                        "attempts": 0,
                        "solved": False,
                        "score": 0,
                        "max_score": item["max_score"],
                    }
                    for item in contest_problems
                },
            },
        )
        problem_score = participant["problems"].setdefault(
            problem_id,
            {"attempts": 0, "solved": False, "score": 0, "max_score": 0},
        )
        if not problem_score["solved"]:
            problem_score["attempts"] += 1
        problem_score["score"] = max(problem_score["score"], score or 0)
        if status == "Accepted" and not problem_score["solved"]:
            problem_score["solved"] = True
            participant["penalty"] += problem_score["attempts"] - 1

    for participant in standings.values():
        participant["solved"] = sum(
            int(problem["solved"]) for problem in participant["problems"].values()
        )
        participant["score"] = sum(
            problem["score"] for problem in participant["problems"].values()
        )
        participant["problems"] = [
            {
                "problem_id": item["problem_id"],
                "problem_order": item["problem_order"],
                "problem_name": item["problem_name"],
                **participant["problems"][item["problem_id"]],
            }
            for item in contest_problems
        ]

    result = sorted(
        standings.values(),
        key=lambda item: (-item["score"], item["penalty"], item["account_name"].casefold()),
    )
    for rank, item in enumerate(result, start=1):
        item["rank"] = rank
        item["is_current_user"] = item["account_id"] == account.account_id
    return {
        "contest_id": contest_id,
        "contest_name": contest.contest_name,
        "problems": contest_problems,
        "standings": result,
    }


@student_router.get("/classes/{class_id}/submissions")
def list_student_submissions(
    class_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    contest_id: int | None = Query(default=None, ge=1),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    if contest_id is not None:
        _require_class_contest_viewer(account, class_id, contest_id, session)
    else:
        student = session.scalar(select(Student).where(Student.account_id == account.account_id))
        if student is None or session.scalar(
            select(StudentClass).where(
                StudentClass.class_id == class_id,
                StudentClass.student_id == student.student_id,
            )
        ) is None:
            raise HTTPException(status_code=403, detail="Bạn chưa tham gia lớp này")
    if contest_id is not None:
        contest = session.get(Contest, contest_id)
        if contest is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy cuộc thi")
        if not _is_class_manager(account, class_id, session) and _contest_status(contest) == "upcoming":
            _require_active_contest(contest)
    submission_filters = [Submission.class_id == class_id]
    if contest_id is not None:
        submission_filters.append(Submission.contest_id == contest_id)
    else:
        submission_filters.append(Submission.contest_id.is_(None))
    rows = session.execute(
        select(
            Submission,
            Problem.problem_name,
            Contest.contest_name,
            Account.account_name,
        )
        .join(Problem, Problem.problem_id == Submission.problem_id)
        .outerjoin(Contest, Contest.contest_id == Submission.contest_id)
        .outerjoin(Account, Account.account_id == Submission.account_id)
        .where(*submission_filters)
        .order_by(Submission.submitted_at.desc(), Submission.submission_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "submission_id": submission.submission_id,
            "problem_id": submission.problem_id,
            "problem_name": problem_name,
            "contest_id": submission.contest_id,
            "contest_name": contest_name,
            "account_name": account_name or "Ẩn danh",
            "is_mine": submission.account_id == account.account_id,
            "status": submission.status,
            "score": submission.score,
            "passed_testcases": submission.passed_testcases,
            "total_testcases": submission.total_testcases,
            "runtime_ms": submission.runtime_ms,
            "memory_kb": submission.memory_kb,
            "submitted_at": _as_utc(submission.submitted_at).isoformat(),
        }
        for submission, problem_name, contest_name, account_name in rows
    ]


@student_router.post("/classes/{class_id}/problems/{problem_id}/submissions")
def submit_solution(
    class_id: int,
    problem_id: int,
    payload: StudentSubmissionIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    student = session.scalar(select(Student).where(Student.account_id == account.account_id))
    if student is None or session.scalar(
        select(StudentClass).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == student.student_id,
        )
    ) is None:
        raise HTTPException(status_code=403, detail="Bạn chưa tham gia lớp này")

    if session.get(AcademicClassProblem, (class_id, problem_id)) is None:
        raise HTTPException(status_code=404, detail="Bài tập không thuộc lớp này")
    problem = session.get(Problem, problem_id)
    contest = None
    if payload.contest_id is not None:
        if session.get(AcademicClassContest, (class_id, payload.contest_id)) is None:
            raise HTTPException(status_code=404, detail="Cuộc thi không thuộc lớp này")
        if session.get(ContestProblem, (payload.contest_id, problem_id)) is None:
            raise HTTPException(status_code=404, detail="Bài tập không thuộc cuộc thi này")
        contest = session.get(Contest, payload.contest_id)
        now = datetime.now(timezone.utc)
        if not (_as_utc(contest.start_time) <= now <= _as_utc(contest.end_time)):
            raise HTTPException(status_code=409, detail="Cuộc thi chưa bắt đầu hoặc đã kết thúc")

    return _grade_and_store_submission(
        account, problem, payload, contest, session, class_id=class_id
    )


def _grade_and_store_submission(
    account: Account,
    problem: Problem,
    payload: StudentSubmissionIn,
    contest: Contest | None,
    session: Session,
    class_id: int | None = None,
) -> dict[str, Any]:
    language = session.get(Language, payload.language_id)
    if language is None:
        raise HTTPException(status_code=422, detail="Ngôn ngữ lập trình không hợp lệ")
    testcases = session.scalars(
        select(Testcase)
        .where(Testcase.problem_id == problem.problem_id)
        .order_by(Testcase.testcase_id)
    ).all()
    if not testcases:
        raise HTTPException(status_code=409, detail="Bài tập chưa có test case để chấm")
    if len(testcases) > 100:
        raise HTTPException(status_code=409, detail="Bài tập có quá nhiều test case để chấm")

    testcase_results = []
    score = 0
    passed_testcases = 0
    total_runtime_ms = Decimal(0)
    has_runtime_measurement = False
    peak_memory_kb: Decimal | None = None
    grading_deadline = time.monotonic() + 45
    try:
        for testcase in testcases:
            if time.monotonic() >= grading_deadline:
                raise TimeoutError("Judge0 grading exceeded the 45-second request limit")
            result = _run_judge0(
                payload.source_code,
                language.language_id,
                testcase,
                problem.time_limit,
                problem.memory_limit,
                deadline=grading_deadline,
            )
            status = result.get("status", {})
            status_id = status.get("id")
            passed = (
                status_id == 3
                and str(result.get("stdout") or "").rstrip()
                == testcase.expected_output.rstrip()
            )
            if passed:
                score += testcase.point
                passed_testcases += 1
            case_status = (
                "Accepted"
                if passed
                else (
                    "Wrong Answer"
                    if status_id == 3
                    else status.get("description", "Unknown")
                )
            )
            runtime = _judge0_metric(result.get("time"), "runtime")
            memory = _judge0_metric(result.get("memory"), "memory")
            if runtime is not None and status_id != 5:
                total_runtime_ms += runtime * 1000
                has_runtime_measurement = True
            if memory is not None:
                peak_memory_kb = max(peak_memory_kb or memory, memory)
            testcase_results.append(
                {
                    "status": case_status,
                    "passed": passed,
                    "runtime_ms": _metric_as_int(runtime * 1000)
                    if runtime is not None and status_id != 5
                    else None,
                    "memory_kb": _metric_as_int(memory),
                }
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dịch vụ chấm bài Judge0 hiện không khả dụng",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Phản hồi từ Judge0 không hợp lệ") from exc

    all_passed = passed_testcases == len(testcases)
    failed_statuses = [
        item["status"] for item in testcase_results if not item["passed"]
    ]
    submission_status = "Accepted" if all_passed else max(
        dict.fromkeys(failed_statuses),
        key=failed_statuses.count,
    )
    runtime_ms = (
        _metric_as_int(total_runtime_ms) if has_runtime_measurement else None
    )
    memory_kb = _metric_as_int(peak_memory_kb)
    submission = Submission(
        problem_id=problem.problem_id,
        account_id=account.account_id,
        language_id=language.language_id,
        contest_id=contest.contest_id if contest else None,
        class_id=class_id,
        status=submission_status,
        score=score,
        passed_testcases=passed_testcases,
        total_testcases=len(testcases),
        runtime_ms=runtime_ms,
        memory_kb=memory_kb,
        source_code=base64.b64encode(payload.source_code.encode("utf-8")).decode(
            "ascii"
        ),
        source_code_encoded=True,
    )
    session.add(submission)
    session.commit()
    session.refresh(submission)
    return {
        "submission_id": submission.submission_id,
        "status": submission.status,
        "score": score,
        "passed_testcases": passed_testcases,
        "total_testcases": len(testcases),
        "runtime_ms": runtime_ms,
        "memory_kb": memory_kb,
        "testcases": testcase_results,
    }


@student_router.get("/problems")
def list_public_problems(
    q: str | None = Query(default=None, max_length=100),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    _require_authenticated_account(session_id, session)
    stmt = select(Problem).where(Problem.access_modifier == AccessModifier.PUBLIC)
    stmt = _search_problems(stmt, q)
    if not q or not q.strip():
        stmt = stmt.order_by(Problem.problem_id.desc())
    problems = session.scalars(stmt).all()
    return [
        {
            "problem_id": problem.problem_id,
            "problem_name": problem.problem_name,
            "description": problem.description,
            "input_description": problem.input_description,
            "output_description": problem.output_description,
            "data_constraints": problem.data_constraints,
            "time_limit": problem.time_limit,
            "memory_limit": problem.memory_limit,
        }
        for problem in problems
    ]


@student_router.get("/submissions")
def list_student_submission_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    problem_id: int | None = Query(default=None, ge=1),
    account_id: int | None = Query(default=None, ge=1),
    problem_name: str | None = Query(default=None, max_length=100),
    account_name: str | None = Query(default=None, max_length=100),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_authenticated_account(session_id, session)
    filters = []
    if problem_id is not None:
        filters.append(Submission.problem_id == problem_id)
    if account_id is not None:
        filters.append(Submission.account_id == account_id)
    if problem_name and problem_name.strip():
        filters.append(
            Problem.problem_name.icontains(problem_name.strip(), autoescape=True)
        )
    if account_name and account_name.strip():
        filters.append(
            func.coalesce(Account.account_name, "Ẩn danh").icontains(
                account_name.strip(),
                autoescape=True,
            )
        )
    rows = session.execute(
        select(
            Submission,
            Problem.problem_name,
            Problem.access_modifier,
            Contest.contest_name,
            Account.account_name,
        )
        .join(Problem, Problem.problem_id == Submission.problem_id)
        .outerjoin(Contest, Contest.contest_id == Submission.contest_id)
        .outerjoin(Account, Account.account_id == Submission.account_id)
        .where(*filters)
        .order_by(Submission.submitted_at.desc(), Submission.submission_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "submission_id": submission.submission_id,
            "problem_id": submission.problem_id,
            "problem_name": problem_name,
            "access_modifier": access_modifier.value,
            "contest_id": submission.contest_id,
            "contest_name": contest_name,
            "account_name": account_name or "Ẩn danh",
            "is_mine": submission.account_id == account.account_id,
            "status": submission.status,
            "score": submission.score,
            "passed_testcases": submission.passed_testcases,
            "total_testcases": submission.total_testcases,
            "runtime_ms": submission.runtime_ms,
            "memory_kb": submission.memory_kb,
            "submitted_at": _as_utc(submission.submitted_at).isoformat(),
        }
        for submission, problem_name, access_modifier, contest_name, account_name in rows
    ]


@student_router.get("/submissions/{submission_id}/source")
def get_own_submission_source(
    submission_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_authenticated_account(session_id, session)
    submission = session.get(Submission, submission_id)
    if submission is None or submission.account_id != account.account_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài nộp")
    return {
        "submission_id": submission.submission_id,
        "source_code": _decode_submission_source(submission),
    }


def _decode_submission_source(submission: Submission) -> str:
    if not submission.source_code:
        raise HTTPException(status_code=404, detail="Bài nộp không có mã nguồn")
    try:
        return (
            base64.b64decode(submission.source_code, validate=True).decode("utf-8")
            if submission.source_code_encoded
            else submission.source_code
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Không thể giải mã mã nguồn của bài nộp",
        ) from exc


@student_router.get(
    "/classes/{class_id}/contests/{contest_id}/submissions/{submission_id}/source"
)
def get_class_manager_contest_submission_source(
    class_id: int,
    contest_id: int,
    submission_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_student_account(session_id, session)
    _require_class_contest_viewer(account, class_id, contest_id, session)
    if not _is_class_manager(account, class_id, session):
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên lớp mới được xem mã bài nộp")

    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc thi")
    if not _is_class_manager(account, class_id, session):
        _require_active_contest(contest)
    submission = session.scalar(
        select(Submission).where(
            Submission.submission_id == submission_id,
            Submission.class_id == class_id,
            Submission.contest_id == contest_id,
        )
    )
    if submission is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài nộp trong cuộc thi")
    return {
        "submission_id": submission.submission_id,
        "source_code": _decode_submission_source(submission),
    }


@student_router.get("/problems/{problem_id}")
def get_public_problem(
    problem_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    _require_authenticated_account(session_id, session)
    problem = session.get(Problem, problem_id)
    if problem is None or problem.access_modifier != AccessModifier.PUBLIC:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài tập public")
    return {
        "problem_id": problem.problem_id,
        "problem_name": problem.problem_name,
        "description": problem.description,
        "input_description": problem.input_description,
        "output_description": problem.output_description,
        "data_constraints": problem.data_constraints,
        "time_limit": problem.time_limit,
        "memory_limit": problem.memory_limit,
    }


@student_router.post("/problems/{problem_id}/submissions")
def submit_public_solution(
    problem_id: int,
    payload: StudentSubmissionIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_authenticated_account(session_id, session)
    problem = session.get(Problem, problem_id)
    if problem is None or problem.access_modifier != AccessModifier.PUBLIC:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài tập public")
    if payload.contest_id is not None:
        raise HTTPException(status_code=422, detail="Bài luyện tập public không thuộc cuộc thi")
    return _grade_and_store_submission(account, problem, payload, None, session)


@router.get("/academic-classes/{class_id}/join-requests")
def list_class_join_requests(
    class_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_owner(account, class_id, session)
    requests = session.scalars(
        select(ClassJoinRequest)
        .options(selectinload(ClassJoinRequest.student).selectinload(Student.account))
        .where(ClassJoinRequest.class_id == class_id, ClassJoinRequest.status == "pending")
        .order_by(ClassJoinRequest.requested_at)
    ).all()
    return [
        {
            "join_request_id": item.join_request_id,
            "account_id": item.student.account_id,
            "account_name": item.student.account.account_name,
            "account_email": item.student.account.account_email,
            "requested_at": item.requested_at.isoformat(),
        }
        for item in requests
    ]


@router.patch("/academic-classes/{class_id}/join-requests/{request_id}")
def decide_class_join_request(
    class_id: int,
    request_id: int,
    payload: ClassJoinDecisionIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_owner(account, class_id, session)
    join_request = session.get(ClassJoinRequest, request_id)
    if join_request is None or join_request.class_id != class_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu tham gia")
    if join_request.status != "pending":
        raise HTTPException(status_code=409, detail="Yêu cầu này đã được xử lý")

    join_request.status = payload.status
    if payload.status == "approved":
        membership = session.scalar(
            select(StudentClass).where(
                StudentClass.class_id == class_id,
                StudentClass.student_id == join_request.student_id,
            )
        )
        if membership is None:
            session.add(
                StudentClass(class_id=class_id, student_id=join_request.student_id)
            )
    session.commit()
    return {"class_id": class_id, "status": join_request.status}


@router.post("/academic-classes/{class_id}/problems")
def attach_problem_to_class(
    class_id: int,
    payload: AcademicClassProblemIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    _require_class_manager(account, class_id, session)

    problem = session.get(Problem, payload.problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem không tồn tại")

    if account.role != AccountRole.SUPER_ADMIN and problem.created_by != account.account_id:
        if problem.access_modifier != AccessModifier.PUBLIC:
            raise HTTPException(status_code=403, detail="Bạn chỉ có thể gắn problem public hoặc problem của chính mình")

    existing = session.get(AcademicClassProblem, (class_id, payload.problem_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Problem đã nằm trong academic class")

    relation = AcademicClassProblem(
        class_id=class_id,
        problem_id=payload.problem_id,
        is_required=payload.is_required,
    )
    session.add(relation)
    session.commit()
    return {"class_id": class_id, "problem_id": payload.problem_id, "is_required": relation.is_required}


@router.post("/academic-classes/{class_id}/problems/create")
def create_problem_for_class(
    class_id: int,
    payload: ProblemCreateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_manager(account, class_id, session)
    problem = Problem(
        problem_name=payload.problem_name,
        description=payload.description or None,
        input_description=payload.input_description or None,
        output_description=payload.output_description or None,
        data_constraints=payload.data_constraints or None,
        time_limit=payload.time_limit,
        memory_limit=payload.memory_limit,
        created_by=account.account_id,
        access_modifier=AccessModifier(payload.access_modifier),
        testcases=[
            Testcase(
                input_data=testcase.input_data,
                expected_output=testcase.expected_output,
                point=testcase.point,
            )
            for testcase in payload.testcases
        ],
    )
    session.add(problem)
    session.flush()
    session.add(
        AcademicClassProblem(
            class_id=class_id,
            problem_id=problem.problem_id,
            is_required=False,
        )
    )
    session.commit()
    session.refresh(problem)
    return _serialize_problem(problem)


@router.get("/academic-classes/{class_id}/available-students")
def list_available_class_students(
    class_id: int,
    search: str = Query(min_length=2, max_length=100),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_manager(account, class_id, session)
    search = search.strip()
    if len(search) < 2:
        raise HTTPException(status_code=422, detail="Nhập ít nhất 2 ký tự để tìm thành viên")

    assigned_students = (
        select(StudentClass.student_id)
        .join(Student, Student.student_id == StudentClass.student_id)
        .where(
            StudentClass.class_id == class_id,
            Student.account_id == Account.account_id,
        )
        .correlate(Account)
    )
    candidates = session.scalars(
        select(Account)
        .where(
            Account.is_active.is_(True),
            Account.role.in_([AccountRole.USER, AccountRole.ADMIN, AccountRole.SUPER_ADMIN]),
            or_(
                Account.account_name.ilike(f"%{search}%"),
                Account.account_email.ilike(f"%{search}%"),
            ),
            ~assigned_students.exists(),
        )
        .order_by(Account.account_name, Account.account_email)
        .limit(10)
    ).all()
    return [
        {
            "account_id": candidate.account_id,
            "account_name": candidate.account_name,
            "account_email": candidate.account_email,
            "role": candidate.role.value,
        }
        for candidate in candidates
    ]


@router.post("/academic-classes/{class_id}/students")
def add_student_to_class(
    class_id: int,
    payload: AcademicClassStudentIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    _require_class_manager(account, class_id, session)

    invited_account = session.get(Account, payload.account_id)
    if invited_account is None or invited_account.role not in {
        AccountRole.USER,
        AccountRole.ADMIN,
        AccountRole.SUPER_ADMIN,
    }:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản thành viên với account_id này")
    if not invited_account.is_active:
        raise HTTPException(status_code=409, detail="Tài khoản thành viên chưa được kích hoạt")

    student = session.scalar(
        select(Student)
        .options(selectinload(Student.account))
        .where(Student.account_id == invited_account.account_id)
    )
    if student is None:
        student = Student(account_id=invited_account.account_id)
        session.add(student)
        session.flush()

    existing = session.scalar(
        select(StudentClass).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == student.student_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Thành viên đã thuộc academic class")

    session.add(StudentClass(class_id=class_id, student_id=student.student_id))
    session.commit()
    return {
        "class_id": class_id,
        "student_id": student.student_id,
        "account_id": student.account_id,
        "account_name": student.account.account_name,
    }


@router.delete("/academic-classes/{class_id}/students/{account_id}")
def remove_student_from_class(
    class_id: int,
    account_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    _require_class_manager(account, class_id, session)

    student = session.scalar(
        select(Student).where(Student.account_id == account_id)
    )
    if student is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy học viên với account_id này")

    relation = session.scalar(
        select(StudentClass).where(
            StudentClass.class_id == class_id,
            StudentClass.student_id == student.student_id,
        )
    )
    if relation is None:
        raise HTTPException(status_code=404, detail="Học viên không thuộc academic class")

    session.delete(relation)
    session.commit()
    return {"class_id": class_id, "account_id": account_id}


@router.get("/contests")
def list_contests(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    _require_admin_account(session_id, session)
    contests = session.scalars(
        select(Contest).options(selectinload(Contest.creator)).order_by(Contest.contest_id.desc())
    ).all()
    return [_serialize_contest(contest) for contest in contests]


@router.delete("/contests/{contest_id}")
def delete_contest(
    contest_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    if account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ super admin mới có thể xóa contest")

    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest không tồn tại")

    session.execute(delete(Submission).where(Submission.contest_id == contest_id))
    session.execute(
        delete(AcademicClassContest).where(
            AcademicClassContest.contest_id == contest_id
        )
    )
    session.execute(
        delete(ContestProblem).where(ContestProblem.contest_id == contest_id)
    )
    session.delete(contest)
    session.commit()
    return {"message": "Đã xóa contest và dữ liệu liên quan", "contest_id": contest_id}


@router.get("/submissions")
def list_submissions(
    submission_id: int | None = Query(default=None, ge=1),
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    if account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Chỉ super admin mới có thể quản lý bài nộp",
        )

    query = (
        select(Submission, Problem.problem_name, Account.account_name)
        .join(Problem, Problem.problem_id == Submission.problem_id)
        .outerjoin(Account, Account.account_id == Submission.account_id)
        .order_by(Submission.submitted_at.desc(), Submission.submission_id.desc())
    )
    if submission_id is not None:
        rows = session.execute(
            query.where(Submission.submission_id == submission_id)
        ).all()
    else:
        rows = session.execute(query.limit(500)).all()
    return [
        {
            "submission_id": submission.submission_id,
            "problem_id": submission.problem_id,
            "problem_name": problem_name,
            "account_id": submission.account_id,
            "account_name": account_name or "Ẩn danh",
            "contest_id": submission.contest_id,
            "status": submission.status,
            "score": submission.score,
            "passed_testcases": submission.passed_testcases,
            "total_testcases": submission.total_testcases,
            "runtime_ms": submission.runtime_ms,
            "memory_kb": submission.memory_kb,
            "submitted_at": _as_utc(submission.submitted_at).isoformat(),
        }
        for submission, problem_name, account_name in rows
    ]


@router.delete("/submissions/{submission_id}")
def delete_submission(
    submission_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    if account.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Chỉ super admin mới có thể xóa bài nộp",
        )

    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Bài nộp không tồn tại")
    session.delete(submission)
    session.commit()
    return {"message": "Đã xóa bài nộp", "submission_id": submission_id}


@router.get("/contests/{contest_id}")
def get_contest_detail(
    contest_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    _require_admin_account(session_id, session)

    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest không tồn tại")

    problems = []
    for item in contest.contest_problems:
        problems.append(
            {
                "problem_id": item.problem_id,
                "problem_name": item.problem.problem_name,
                "problem_order": item.problem_order,
                "access_modifier": item.problem.access_modifier.value,
            }
        )

    now = datetime.now(timezone.utc)
    status = _contest_status(contest)
    return {
        "contest_id": contest.contest_id,
        "contest_name": contest.contest_name,
        "description": contest.description,
        "start_time": _as_utc(contest.start_time).isoformat(),
        "end_time": _as_utc(contest.end_time).isoformat(),
        "created_by": contest.created_by,
        "creator_name": contest.creator.account_name if contest.creator else None,
        "created_at": contest.created_at.isoformat() if contest.created_at else None,
        "status": status,
        "is_active": status == "active",
        "is_out_of_contest": now > _as_utc(contest.end_time),
        "problem_count": len(problems),
        "problems": problems,
    }


@router.post("/contests")
def create_contest(
    payload: ContestCreateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    start_time = _parse_datetime(payload.start_time.isoformat(), "start_time")
    end_time = _parse_datetime(payload.end_time.isoformat(), "end_time")
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="end_time phải lớn hơn start_time")

    contest = Contest(
        contest_name=payload.contest_name,
        description=payload.description or None,
        start_time=start_time,
        end_time=end_time,
        created_by=account.account_id,
    )
    session.add(contest)
    session.commit()
    session.refresh(contest)
    return _serialize_contest(contest)


@router.post("/contests/{contest_id}/problems")
def add_problem_to_contest_route(
    contest_id: int,
    payload: ContestProblemIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)

    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest không tồn tại")

    problem_id = payload.problem_id
    problem = session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem không tồn tại")

    if account.role != AccountRole.SUPER_ADMIN and problem.created_by != account.account_id:
        if problem.access_modifier != AccessModifier.PUBLIC:
            raise HTTPException(status_code=403, detail="Bạn chỉ có thể thêm problem public hoặc problem của chính mình")

    try:
        item = add_problem_to_contest(
            session,
            contest_id,
            problem_id,
            payload.problem_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return {"contest_id": contest_id, "problem_id": problem_id, "problem_order": item.problem_order}


@router.post("/academic-classes/{class_id}/contests")
def attach_contest_to_class(
    class_id: int,
    payload: AcademicClassContestIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_manager(account, class_id, session)

    contest = session.get(Contest, payload.contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest không tồn tại")

    existing = session.get(AcademicClassContest, (class_id, payload.contest_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Contest đã nằm trong academic class")

    relation = AcademicClassContest(class_id=class_id, contest_id=payload.contest_id)
    session.add(relation)
    session.commit()
    return {"class_id": class_id, "contest_id": payload.contest_id}


@router.post("/academic-classes/{class_id}/contests/create")
def create_class_contest(
    class_id: int,
    payload: ClassContestCreateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_manager(account, class_id, session)
    start_time = _parse_datetime(payload.start_time.isoformat(), "start_time")
    end_time = _parse_datetime(payload.end_time.isoformat(), "end_time")
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="Thời điểm kết thúc phải sau thời điểm bắt đầu")

    class_problem_ids = set(
        session.scalars(
            select(AcademicClassProblem.problem_id).where(
                AcademicClassProblem.class_id == class_id
            )
        ).all()
    )
    if not set(payload.problem_ids).issubset(class_problem_ids):
        raise HTTPException(status_code=422, detail="Chỉ có thể thêm bài tập đã thuộc lớp")

    contest = Contest(
        contest_name=payload.contest_name,
        description=payload.description or None,
        start_time=start_time,
        end_time=end_time,
        created_by=account.account_id,
    )
    session.add(contest)
    session.flush()
    for problem_order, problem_id in enumerate(payload.problem_ids, start=1):
        add_problem_to_contest(session, contest.contest_id, problem_id, problem_order)
    session.add(AcademicClassContest(class_id=class_id, contest_id=contest.contest_id))
    session.commit()
    session.refresh(contest)
    return _serialize_contest(contest)


@router.patch(
    "/academic-classes/{class_id}/contests/{contest_id}",
)
def update_class_contest(
    class_id: int,
    contest_id: int,
    payload: ClassContestCreateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    account = _require_admin_account(session_id, session)
    _require_class_manager(account, class_id, session)
    if session.get(AcademicClassContest, (class_id, contest_id)) is None:
        raise HTTPException(status_code=404, detail="Cuộc thi không thuộc lớp này")

    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest không tồn tại")

    start_time = _parse_datetime(payload.start_time.isoformat(), "start_time")
    end_time = _parse_datetime(payload.end_time.isoformat(), "end_time")
    if end_time <= start_time:
        raise HTTPException(
            status_code=422,
            detail="Thời điểm kết thúc phải sau thời điểm bắt đầu",
        )

    class_problem_ids = set(
        session.scalars(
            select(AcademicClassProblem.problem_id).where(
                AcademicClassProblem.class_id == class_id
            )
        ).all()
    )
    if not set(payload.problem_ids).issubset(class_problem_ids):
        raise HTTPException(status_code=422, detail="Chỉ có thể thêm bài tập đã thuộc lớp")

    contest.contest_name = payload.contest_name
    contest.description = payload.description or None
    contest.start_time = start_time
    contest.end_time = end_time
    session.execute(
        delete(ContestProblem).where(ContestProblem.contest_id == contest_id)
    )
    session.flush()
    for problem_order, problem_id in enumerate(payload.problem_ids, start=1):
        add_problem_to_contest(session, contest_id, problem_id, problem_order)

    session.commit()
    session.refresh(contest)
    return _serialize_contest(contest)


@router.get("/contests/{contest_id}/standings")
def contest_standings(
    contest_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    _require_admin_account(session_id, session)

    contest = session.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="Contest không tồn tại")

    rows = session.scalars(
        select(Submission, Account.account_name)
        .join(Account, Account.account_id == Submission.account_id)
        .where(Submission.contest_id == contest_id)
        .order_by(Submission.submitted_at.asc())
    ).all()

    standings: dict[int, dict[str, Any]] = {}
    for submission, account_name in rows:
        participant = standings.setdefault(
            submission.account_id,
            {
                "account_id": submission.account_id,
                "account_name": account_name,
                "solved": 0,
                "attempts": 0,
                "score": 0,
            },
        )
        participant["attempts"] += 1
        if submission.status == "Accepted":
            participant["solved"] += 1
            participant["score"] = max(participant["score"], submission.score or 0)

    result = sorted(standings.values(), key=lambda item: (-item["solved"], item["attempts"], -item["score"]))
    return {"contest_id": contest_id, "contest_name": contest.contest_name, "standings": result}


@router.get("/health")
def health_check():
    return {"status": "ok"}
