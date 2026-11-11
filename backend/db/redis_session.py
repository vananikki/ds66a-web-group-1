import secrets

from db.redis_client import redis_client

SESSION_TTL_SECONDS = 24 * 60 * 60


def create_session(account_id: int) -> str:
    session_id = secrets.token_urlsafe(32)

    session_key = f"session:{session_id}"
    account_key = f"account_sessions:{account_id}"

    # Lưu session với TTL 24 giờ
    redis_client.set(
        session_key,
        str(account_id),
        ex=SESSION_TTL_SECONDS,
    )

    # Thêm session vào danh sách của account
    redis_client.sadd(account_key, session_id)

    # Gia hạn TTL của Redis Set mỗi khi có session mới
    redis_client.expire(
        account_key,
        SESSION_TTL_SECONDS,
    )

    return session_id


def get_session(session_id: str) -> int | None:
    account_id = redis_client.get(
        f"session:{session_id}"
    )

    if account_id is None:
        return None

    return int(account_id)


def delete_session(session_id: str) -> None:
    account_id = get_session(session_id)

    # Xóa session
    redis_client.delete(f"session:{session_id}")

    # Xóa session ID khỏi danh sách của account
    if account_id is not None:
        redis_client.srem(
            f"account_sessions:{account_id}",
            session_id,
        )


def delete_account_sessions(account_id: int) -> None:
    account_key = f"account_sessions:{account_id}"

    # Lấy tất cả session ID thuộc account
    session_ids = redis_client.smembers(account_key)

    if session_ids:
        # Xóa tất cả session của account
        session_keys = [
            f"session:{session_id}"
            for session_id in session_ids
        ]

        redis_client.delete(*session_keys)

    # Xóa danh sách session của account
    redis_client.delete(account_key)