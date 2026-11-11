
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Identity,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

if TYPE_CHECKING:
    from db.tables_phase2 import Contest, Problem, Submission


# =========================================================
# Base
# =========================================================

class Base(DeclarativeBase):
    pass


# =========================================================
# Password
# =========================================================

ph = PasswordHasher()


# =========================================================
# Enum
# =========================================================

class AccountRole(PyEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


# =========================================================
# Account
# =========================================================

class Account(Base):
    __tablename__ = "account"

    account_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    # Email dùng để đăng nhập
    account_email: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    # Trạng thái kích hoạt tài khoản; cần xác thực email trước khi đăng nhập
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )

    # Mã tài khoản
    account_code: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
    )

    # Tên hiển thị
    account_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Mật khẩu đã hash
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # =====================================================
    # Role
    # =====================================================

    role: Mapped[AccountRole] = mapped_column(
        SAEnum(
            AccountRole,
            name="account_role",
        ),
        default=AccountRole.USER,
        nullable=False,
    )

    # =====================================================
    # Relationships
    # =====================================================

    # Account 1 - 1 Student
    student: Mapped["Student | None"] = relationship(
        back_populates="account",
        uselist=False,
    )

    # Account 1 - 1 Admin
    admin: Mapped["Admin | None"] = relationship(
        back_populates="account",
        uselist=False,
    )

    # Relationship assignments are attached below after the phase-two models
    # are imported so SQLAlchemy can resolve the foreign-key targets safely.

    # =====================================================
    # Password methods
    # =====================================================

    def set_password(self, raw_password: str) -> None:
        self.password_hash = ph.hash(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        if len(raw_password) < 8:
            return False

        try:
            ph.verify(
                self.password_hash,
                raw_password,
            )
        except VerificationError:
            return False

        if ph.check_needs_rehash(self.password_hash):
            self.set_password(raw_password)

        return True

    @property
    def is_user(self) -> bool:
        return self.role in {
            AccountRole.USER,
            AccountRole.ADMIN,
            AccountRole.SUPER_ADMIN,
        }

    @property
    def is_admin(self) -> bool:
        return self.role in {
            AccountRole.ADMIN,
            AccountRole.SUPER_ADMIN,
        }

    @property
    def is_super_admin(self) -> bool:
        return self.role == AccountRole.SUPER_ADMIN
    


# =========================================================
# Student
# =========================================================

class Student(Base):
    __tablename__ = "student"

    student_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.account_id"),
        unique=True,
        nullable=False,
    )

    # Student N - 1 Account
    account: Mapped["Account"] = relationship(
        back_populates="student",
    )

    # Student N - N AcademicClass
    student_classes: Mapped[list["StudentClass"]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
    )


# =========================================================
# Admin
# =========================================================

class Admin(Base):
    __tablename__ = "admin"

    admin_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.account_id"),
        unique=True,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )

    # Admin N - 1 Account
    account: Mapped["Account"] = relationship(
        back_populates="admin",
    )

    # Admin N - N AcademicClass
    manage_classes: Mapped[list["ManageClass"]] = relationship(
        back_populates="admin",
        cascade="all, delete-orphan",
    )


# =========================================================
# Academic Class
# =========================================================

class AcademicClass(Base):
    __tablename__ = "academic_class"

    class_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    class_name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    # AcademicClass N - N Admin
    manage_classes: Mapped[list["ManageClass"]] = relationship(
        back_populates="academic_class",
        cascade="all, delete-orphan",
    )

    # AcademicClass N - N Student
    student_classes: Mapped[list["StudentClass"]] = relationship(
        back_populates="academic_class",
        cascade="all, delete-orphan",
    )


# =========================================================
# ManageClass
# Admin <-> AcademicClass
# =========================================================

class ManageClass(Base):
    __tablename__ = "manage_class"

    manage_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    class_id: Mapped[int] = mapped_column(
        ForeignKey("academic_class.class_id"),
        nullable=False,
    )

    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admin.admin_id"),
        nullable=False,
    )

    # Không cho phép một Admin quản lý cùng một Class 2 lần
    __table_args__ = (
        UniqueConstraint(
            "class_id",
            "admin_id",
            name="uq_manage_class",
        ),
    )

    # ManageClass N - 1 AcademicClass
    academic_class: Mapped["AcademicClass"] = relationship(
        back_populates="manage_classes",
    )

    # ManageClass N - 1 Admin
    admin: Mapped["Admin"] = relationship(
        back_populates="manage_classes",
    )


# =========================================================
# StudentClass
# Student <-> AcademicClass
# =========================================================

class StudentClass(Base):
    __tablename__ = "student_class"

    student_class_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    class_id: Mapped[int] = mapped_column(
        ForeignKey("academic_class.class_id"),
        nullable=False,
    )

    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.student_id"),
        nullable=False,
    )

    # Không cho phép một Student tham gia cùng một Class 2 lần
    __table_args__ = (
        UniqueConstraint(
            "class_id",
            "student_id",
            name="uq_student_class",
        ),
    )

    # StudentClass N - 1 AcademicClass
    academic_class: Mapped["AcademicClass"] = relationship(
        back_populates="student_classes",
    )

    # StudentClass N - 1 Student
    student: Mapped["Student"] = relationship(
        back_populates="student_classes",
    )


class ClassJoinRequest(Base):
    __tablename__ = "class_join_request"

    join_request_id: Mapped[int] = mapped_column(
        Identity(),
        primary_key=True,
    )

    class_id: Mapped[int] = mapped_column(
        ForeignKey("academic_class.class_id", ondelete="CASCADE"),
        nullable=False,
    )

    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.student_id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        server_default="pending",
        nullable=False,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    academic_class: Mapped["AcademicClass"] = relationship()
    student: Mapped["Student"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "class_id",
            "student_id",
            name="uq_class_join_request",
        ),
    )


try:
    from db.tables_phase2 import (
        AcademicClassContest,
        AcademicClassProblem,
        Contest,
        Problem,
        Submission,
    )
except ModuleNotFoundError:
    from tables_phase2 import (
        AcademicClassContest,
        AcademicClassProblem,
        Contest,
        Problem,
        Submission,
    )

Account.created_problems = relationship(
    "Problem",
    foreign_keys="Problem.created_by",
    back_populates="creator",
)
Account.created_contests = relationship(
    "Contest",
    foreign_keys="Contest.created_by",
    back_populates="creator",
)
Account.submissions = relationship(
    "Submission",
    foreign_keys="Submission.account_id",
    back_populates="account",
)

AcademicClass.class_problems = relationship(
    "AcademicClassProblem",
    back_populates="academic_class",
    cascade="all, delete-orphan",
)
AcademicClass.class_contests = relationship(
    "AcademicClassContest",
    back_populates="academic_class",
    cascade="all, delete-orphan",
)