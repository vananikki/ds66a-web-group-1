import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from db.tables_phase1 import AccountRole

PASSWORD_REQUIREMENTS_MESSAGE = (
    "Mật khẩu phải có ít nhất 8 ký tự, có chữ và số, 1 ký tự đặc biệt và 1 chữ cái in hoa"
)


def validate_password_strength(raw_password: str) -> str:
    if len(raw_password) < 8:
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    if not re.search(r"[A-Za-z]", raw_password):
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    if not re.search(r"\d", raw_password):
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    if not re.search(r"[^A-Za-z0-9]", raw_password):
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    if not re.search(r"[A-Z]", raw_password):
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    return raw_password


# =========================================================
# Register
# =========================================================

class RegisterIn(BaseModel):
    account_name: str = Field(
        min_length=3,
        max_length=100,
    )

    account_email: EmailStr

    password: str = Field(
        max_length=128,
    )

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


# =========================================================
# Login
# =========================================================

class LoginIn(BaseModel):
    account_email: EmailStr

    password: str = Field(
        min_length=1,
        max_length=128,
    )


class AccountRoleUpdateIn(BaseModel):
    role: AccountRole


class AccountNameUpdateIn(BaseModel):
    account_name: str = Field(
        min_length=3,
        max_length=100,
    )


# =========================================================
# Account Update
# =========================================================

class AccountUpdateIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    account_name: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
    )

    account_email: EmailStr | None = None

    @field_validator(
        "account_name",
        "account_email",
    )
    @classmethod
    def not_null_if_sent(cls, v):
        # Client gửi rõ null thì từ chối
        if v is None:
            raise ValueError("Không được để null")

        return v


# =========================================================
# Account Response
# =========================================================

class AccountOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    account_id: int
    account_code: str
    account_name: str
    account_email: EmailStr
    created_at: datetime

    # Không trả password_hash


class JudgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemTestcaseIn(JudgeRequest):
    input_data: str
    expected_output: str
    point: int = Field(default=100, ge=1)


class ProblemCreateIn(JudgeRequest):
    problem_name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    input_description: str | None = None
    output_description: str | None = None
    data_constraints: str | None = None
    time_limit: int = Field(ge=1, le=10)
    memory_limit: int = Field(ge=64, le=1024)
    access_modifier: Literal["private", "public", "hidden"] = "private"
    testcases: list[ProblemTestcaseIn] = Field(default_factory=list, max_length=100)

    @field_validator("problem_name")
    @classmethod
    def strip_problem_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("problem_name không được để trống")
        return value


class ProblemUpdateIn(JudgeRequest):
    problem_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    input_description: str | None = None
    output_description: str | None = None
    data_constraints: str | None = None
    time_limit: int | None = Field(default=None, ge=1, le=10)
    memory_limit: int | None = Field(default=None, ge=64, le=1024)
    access_modifier: Literal["private", "public", "hidden"] | None = None
    testcases: list[ProblemTestcaseIn] | None = Field(default=None, max_length=100)

    @field_validator("problem_name")
    @classmethod
    def strip_optional_problem_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("problem_name không được để trống")
        return value


class AcademicClassCreateIn(JudgeRequest):
    class_name: str = Field(min_length=1, max_length=300)

    @field_validator("class_name")
    @classmethod
    def strip_class_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("class_name không được để trống")
        return value


class AcademicClassProblemIn(JudgeRequest):
    problem_id: int = Field(ge=1)
    is_required: bool = False


class AcademicClassContestIn(JudgeRequest):
    contest_id: int = Field(ge=1)


class AcademicClassStudentIn(JudgeRequest):
    account_id: int = Field(ge=1)


class ClassJoinDecisionIn(JudgeRequest):
    status: Literal["approved", "rejected"]


class StudentSubmissionIn(JudgeRequest):
    language_id: int = Field(ge=1)
    source_code: str = Field(min_length=1, max_length=65_536)
    contest_id: int | None = Field(default=None, ge=1)

    @field_validator("source_code")
    @classmethod
    def source_code_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_code không được để trống")
        return value


class StudentRunCodeIn(JudgeRequest):
    language_id: int = Field(ge=1)
    source_code: str = Field(min_length=1, max_length=65_536)
    stdin: str = Field(default="", max_length=16_384)
    contest_id: int | None = Field(default=None, ge=1)

    @field_validator("source_code")
    @classmethod
    def run_source_code_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_code không được để trống")
        return value


class ContestCreateIn(JudgeRequest):
    contest_name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    start_time: datetime
    end_time: datetime

    @field_validator("contest_name")
    @classmethod
    def strip_contest_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("contest_name không được để trống")
        return value


class ClassContestCreateIn(ContestCreateIn):
    problem_ids: list[int] = Field(min_length=1, max_length=20)

    @field_validator("problem_ids")
    @classmethod
    def unique_problem_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("problem_ids không được trùng lặp")
        return value


class ContestProblemIn(JudgeRequest):
    problem_id: int = Field(ge=1)
    problem_order: int | None = Field(default=None, ge=1, le=20)