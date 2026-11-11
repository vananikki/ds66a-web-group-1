# Database schema

Tài liệu này mô tả schema SQLAlchemy đang dùng bởi backend. Nguồn khai báo
chính thức là `backend/db/tables_phase1.py` và `backend/db/tables_phase2.py`.
Backend kết nối cơ sở dữ liệu qua `DATABASE_URL` trong `backend/.env`; không
đưa URL hoặc thông tin xác thực vào tài liệu này.

> Khi thay đổi model, chạy các lệnh khởi tạo/migration trong [README.md](./README.md) trước khi
> khởi động backend. Đặc biệt, `initialize_tables.py` tạo bảng còn thiếu nhưng
> không tự thêm cột mới vào bảng đã tồn tại; `migrate_judge_admin.py` xử lý
> các thay đổi bổ sung như testcase metadata, dữ liệu nguồn Base64, metrics
> chấm bài và ngữ cảnh lớp của submission.

## Entity relationship diagram

```mermaid
erDiagram
    ACCOUNT ||--o| STUDENT : "student profile"
    ACCOUNT ||--o| ADMIN : "admin profile"
    ACCOUNT o|--o{ PROBLEM : "creates"
    ACCOUNT o|--o{ CONTEST : "creates"
    ACCOUNT o|--o{ SUBMISSION : "submits"
    ACADEMIC_CLASS o|--o{ SUBMISSION : "submission context"

    STUDENT ||--o{ STUDENT_CLASS : "membership"
    ACADEMIC_CLASS ||--o{ STUDENT_CLASS : "has students"
    ADMIN ||--o{ MANAGE_CLASS : "manages"
    ACADEMIC_CLASS ||--o{ MANAGE_CLASS : "has managers"
    STUDENT ||--o{ CLASS_JOIN_REQUEST : "requests"
    ACADEMIC_CLASS ||--o{ CLASS_JOIN_REQUEST : "receives"

    PROBLEM ||--o{ TESTCASE : "is judged with"
    PROBLEM ||--o{ SUBMISSION : "receives"
    LANGUAGE ||--o{ SUBMISSION : "used for"
    CONTEST o|--o{ SUBMISSION : "may contain"

    ACADEMIC_CLASS ||--o{ ACADEMIC_CLASS_PROBLEM : "assigns"
    PROBLEM ||--o{ ACADEMIC_CLASS_PROBLEM : "assigned to"
    ACADEMIC_CLASS ||--o{ ACADEMIC_CLASS_CONTEST : "hosts"
    CONTEST ||--o{ ACADEMIC_CLASS_CONTEST : "hosted in"
    CONTEST ||--o{ CONTEST_PROBLEM : "contains"
    PROBLEM ||--o{ CONTEST_PROBLEM : "included in"

    ACCOUNT {
        int account_id PK
        string account_email UK
        string account_code UK
        string account_name
        string password_hash
        boolean is_active
        enum role
        datetime created_at
    }
    STUDENT {
        int student_id PK
        int account_id FK
    }
    ADMIN {
        int admin_id PK
        int account_id FK
        boolean is_active
    }
    ACADEMIC_CLASS {
        int class_id PK
        string class_name
    }
    MANAGE_CLASS {
        int manage_id PK
        int class_id FK
        int admin_id FK
    }
    STUDENT_CLASS {
        int student_class_id PK
        int class_id FK
        int student_id FK
    }
    CLASS_JOIN_REQUEST {
        int join_request_id PK
        int class_id FK
        int student_id FK
        string status
        datetime requested_at
    }
    PROBLEM {
        int problem_id PK
        int created_by FK
        string problem_name
        text description
        text input_description
        text output_description
        text data_constraints
        int time_limit
        int memory_limit
        enum access_modifier
        datetime created_at
    }
    TESTCASE {
        int testcase_id PK
        int problem_id FK
        int point
        text input_data
        text expected_output
    }
    LANGUAGE {
        int language_id PK
        string name UK
    }
    CONTEST {
        int contest_id PK
        int created_by FK
        string contest_name
        text description
        datetime start_time
        datetime end_time
        datetime created_at
    }
    CONTEST_PROBLEM {
        int contest_id PK, FK
        int problem_id PK, FK
        int problem_order
    }
    ACADEMIC_CLASS_PROBLEM {
        int class_id PK, FK
        int problem_id PK, FK
        datetime added_at
        boolean is_required
    }
    ACADEMIC_CLASS_CONTEST {
        int class_id PK, FK
        int contest_id PK, FK
        datetime added_at
    }
    SUBMISSION {
        int submission_id PK
        int problem_id FK
        int account_id FK
        int contest_id FK
        int class_id FK
        int language_id FK
        datetime submitted_at
        string status
        text source_code "Base64 encoded"
        boolean source_code_encoded
        int score
        int passed_testcases
        int total_testcases
        int runtime_ms
        int memory_kb
    }
```

`o|` represents an optional relationship, `||` exactly one, and `o{` zero or
many. `UK` means a unique constraint. In particular, owner/account references
on `PROBLEM`, `CONTEST`, and `SUBMISSION` are nullable so judge history can
remain after an account is removed.

## Tables

All IDs described as generated are integer identity/auto-increment primary keys
unless stated otherwise. `created_at`, `requested_at`, `added_at`, and
`submitted_at` use a database timestamp default where applicable.

### Accounts and access

| Table | Columns |
|---|---|
| `account` | `account_id` integer PK; `account_email` varchar(100) required, unique; `is_active` boolean required, default `true`; `account_code` varchar(20) required, unique; `account_name` varchar(100) required; `password_hash` varchar(255) required; `created_at` timezone-aware timestamp required, default current time; `role` enum `account_role` required (`USER`, `ADMIN`, `SUPER_ADMIN`), application default `USER`. |
| `student` | `student_id` integer PK; `account_id` integer required FK → `account.account_id`, unique. An account can have at most one student profile. |
| `admin` | `admin_id` integer PK; `account_id` integer required FK → `account.account_id`, unique; `is_active` boolean required, default `true`. An account can have at most one admin profile. |

Roles live on `account`; the `student` and `admin` tables are separate role
profiles. Authentication sessions are stored in Redis, not in PostgreSQL; see
the Redis configuration in [backend.md](./backend.md).

### Classes and membership

| Table | Columns and constraints |
|---|---|
| `academic_class` | `class_id` integer PK; `class_name` varchar(300) required. |
| `manage_class` | `manage_id` integer PK; `class_id` required FK → `academic_class.class_id`; `admin_id` required FK → `admin.admin_id`; unique (`class_id`, `admin_id`). |
| `student_class` | `student_class_id` integer PK; `class_id` required FK → `academic_class.class_id`; `student_id` required FK → `student.student_id`; unique (`class_id`, `student_id`). |
| `class_join_request` | `join_request_id` integer PK; `class_id` required FK → `academic_class.class_id` with `ON DELETE CASCADE`; `student_id` required FK → `student.student_id` with `ON DELETE CASCADE`; `status` varchar(20) required, default `pending` (application states: `pending`, `approved`, `rejected`); `requested_at` timezone-aware timestamp required, default current time; unique (`class_id`, `student_id`). |

`manage_class` and `student_class` are join tables: admins and students can each
be associated with multiple classes. `class_join_request` tracks a student's
request separately from an accepted `student_class` membership.

### Problems, judging, and languages

| Table | Columns and constraints |
|---|---|
| `problem` | `problem_id` integer PK; `problem_name` varchar(255) required; `description`, `input_description`, `output_description`, `data_constraints` nullable text; `time_limit` integer required, check `0 < time_limit <= 10` (seconds); `memory_limit` integer required, check `0 < memory_limit <= 1024` (MB); `created_by` nullable integer FK → `account.account_id`; `created_at` timezone-aware timestamp required, default current time; `access_modifier` enum `access_modifier_enum` required (`private`, `public`, `hidden`), default `private`. |
| `testcase` | `testcase_id` integer PK; `problem_id` integer required FK → `problem.problem_id`; `point` integer required; `input_data` and `expected_output` required text. |
| `language` | `language_id` integer PK (ID synced from Judge0); `name` varchar(50) required, unique. |
| `submission` | `submission_id` integer PK; `problem_id` integer required FK → `problem.problem_id`; `account_id` nullable integer FK → `account.account_id`; `contest_id` nullable integer FK → `contest.contest_id` with `ON DELETE SET NULL`; `class_id` nullable integer FK → `academic_class.class_id` with `ON DELETE SET NULL`; `language_id` integer required FK → `language.language_id`; `submitted_at` timezone-aware timestamp required, default current time; `status` varchar(50) required; `source_code` nullable text containing Base64-encoded source; `source_code_encoded` boolean required, default `true`; `score`, `passed_testcases`, `total_testcases`, `runtime_ms`, and `memory_kb` nullable integers. |

The database stores problem statements and judging limits, while the
`testcase` table stores private judge data. Problem access is controlled by
`access_modifier`; application authorization determines who may read or edit
private problem details and test cases.

`source_code_encoded` records whether `source_code` is Base64-encoded so legacy
rows can be decoded safely. Base64 is an encoding format, not encryption.
`passed_testcases` and `total_testcases` record judging progress;
`runtime_ms` is the summed runtime for testcases that did not time out, and
`memory_kb` is the peak memory reported by Judge0. Metrics may be `NULL` for
legacy submissions or when Judge0 does not report a measurement.

`class_id` records the class context for a class submission. Public-practice
submissions have no class context. Because legacy submissions may not have
reliable class provenance, only submissions with a recorded `class_id` appear
in class-scoped history.

### Contests and class assignments

| Table | Columns and constraints |
|---|---|
| `contest` | `contest_id` integer PK; `contest_name` varchar(255) required; `description` nullable text; `start_time` and `end_time` timezone-aware timestamps required; `created_by` nullable integer FK → `account.account_id`; `created_at` timezone-aware timestamp required, default current time; check `end_time > start_time`. |
| `contest_problem` | Composite PK (`contest_id`, `problem_id`); `contest_id` FK → `contest.contest_id` with `ON DELETE CASCADE`; `problem_id` FK → `problem.problem_id`; nullable integer `problem_order`; unique (`contest_id`, `problem_order`); check order is null or between 1 and 20. A contest is limited to 20 problems by application validation as well. |
| `academic_class_problem` | Composite PK (`class_id`, `problem_id`); `class_id` FK → `academic_class.class_id` and `problem_id` FK → `problem.problem_id`, both with `ON DELETE CASCADE`; `added_at` timezone-aware timestamp required, default current time; `is_required` boolean required, default `false`. |
| `academic_class_contest` | Composite PK (`class_id`, `contest_id`); `class_id` FK → `academic_class.class_id` and `contest_id` FK → `contest.contest_id`, both with `ON DELETE CASCADE`; `added_at` timezone-aware timestamp required, default current time. |

`academic_class_problem` and `academic_class_contest` implement many-to-many
assignments. A problem can be reused in multiple classes and contests without
copying its statement or test cases.

## Relationship summary

- `account` 1 — 0..1 `student`; `account` 1 — 0..1 `admin`.
- `account` 1 — many owned `problem` and `contest`; these ownership links are
  optional (`created_by` can be `NULL`).
- `account` 1 — many `submission`; the submitter link is optional so a
  submission can be retained without its account.
- `academic_class` 1 — many `submission`; the class context is optional and is
  set to `NULL` when its class is deleted.
- `admin` many — many `academic_class` through `manage_class`.
- `student` many — many `academic_class` through `student_class`; join requests
  are stored in `class_join_request`.
- `problem` 1 — many `testcase` and `submission`.
- `language` 1 — many `submission`.
- `contest` many — many `problem` through `contest_problem`.
- `academic_class` many — many `problem` through `academic_class_problem`.
- `academic_class` many — many `contest` through `academic_class_contest`.
- `contest` 1 — many `submission`; deleting a contest sets the submission's
  `contest_id` to `NULL` at the database level.

## Deletion and migration notes

- Authentication sessions are stored in Redis with a 24-hour TTL. The legacy
  SQL `session` table, if present from an earlier deployment, is no longer
  queried; existing SQL sessions are not migrated and users must sign in again.

- Database `ON DELETE` actions are explicitly configured only on the foreign
  keys listed above. Other cleanup behavior is performed by ORM cascades or
  application routes; do not assume every relationship is deleted by the
  database alone.
- The account deletion flow clears nullable owner/submitter references so
  problem, contest, and submission records can be preserved.
- Source code for new submissions is stored as Base64 text; this does not
  encrypt the code. The judge migration performs the one-time conversion of
  older plain-text rows and preserves their encoding state.
- The repeatable judge migration drops the deprecated `solution` table. This is
  destructive; any existing rows in that table are permanently removed.
- Administrative deletion routes explicitly remove dependent test cases,
  submissions, and class/contest links as appropriate.
- Run `PYTHONPATH=. .venv/bin/python db/initialize_tables.py` for missing
  tables, then `PYTHONPATH=. .venv/bin/python db/migrate_judge_admin.py` to
  apply incremental judge schema changes and remove the deprecated `solution`
  table. The migration is intended to be repeatable.
- `backend/db/initialize/creating.py` contains older duplicate `Problem` and
  `Contest` declarations. The active application metadata is assembled from
  `tables_phase1.py` and `tables_phase2.py`; use those files when changing the
  live schema.
