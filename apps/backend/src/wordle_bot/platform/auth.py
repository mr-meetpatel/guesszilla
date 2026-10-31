"""Cookie JWTs with database-backed revocation and atomic refresh rotation."""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from functools import wraps
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from flask import Blueprint, Response, abort, g, jsonify, request
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.platform.models import Admin, AdminSession, Event, LoginBucket, identifier, utcnow

passwords = PasswordHasher()
_dummy_hash = passwords.hash("not-a-real-admin-password")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(hashed: str, supplied: str) -> bool:
    try:
        return passwords.verify(hashed or _dummy_hash, supplied)
    except VerificationError:
        return False


def require_password(password: object) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        abort(422, description="Passwords must contain 12–256 characters")
    return password


def audit(
    session: Session,
    action: str,
    *,
    actor: str | None = None,
    target: str | None = None,
    workspace: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        Event(
            category="audit",
            action=action,
            actor=actor,
            target=target,
            workspace_id=workspace,
            details=details or {},
        )
    )


def protected(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    @jwt_required()
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if get_jwt().get("must_change") and request.path != "/api/v1/auth/password":
            abort(403, description="Change your temporary password first")
        g.admin_id = str(get_jwt_identity())
        return function(*args, **kwargs)

    return wrapped


def register_auth(app: Any, settings: Settings, engine: Engine) -> None:
    app.config.update(
        JWT_SECRET_KEY=settings.jwt_secret_key.get_secret_value(),
        JWT_TOKEN_LOCATION=["cookies"],
        JWT_COOKIE_SECURE=settings.cookie_secure,
        JWT_COOKIE_SAMESITE="Lax",
        JWT_COOKIE_CSRF_PROTECT=True,
        JWT_ACCESS_COOKIE_PATH="/api/",
        JWT_REFRESH_COOKIE_PATH="/api/v1/auth/",
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=5),
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=7),
        JWT_ENCODE_ISSUER="wordle-platform",
        JWT_DECODE_ISSUER="wordle-platform",
        JWT_ENCODE_AUDIENCE="wordle-admin",
        JWT_DECODE_AUDIENCE="wordle-admin",
    )
    jwt = JWTManager(app)

    @jwt.token_in_blocklist_loader
    def revoked(header: dict[str, Any], claims: dict[str, Any]) -> bool:
        with Session(engine) as db:
            current = db.get(AdminSession, claims.get("sid", ""))
            admin = db.get(Admin, claims.get("sub", ""))
            return not (
                current
                and admin
                and admin.active
                and not current.revoked
                and current.admin_id == admin.id
                and current.expires_at > utcnow()
            )

    blueprint = Blueprint("auth", __name__, url_prefix="/api/v1/auth")

    def body() -> dict[str, Any]:
        if not request.is_json:
            abort(415)
        if request.headers.get("Origin") not in {None, settings.public_url.rstrip("/")}:
            abort(403)
        value = request.get_json()
        if not isinstance(value, dict):
            abort(422)
        return value

    def tokens(admin: Admin, current: AdminSession) -> Response:
        claims = {"sid": current.id, "must_change": admin.must_change_password}
        access = create_access_token(identity=admin.id, additional_claims=claims)
        refresh = create_refresh_token(
            identity=admin.id, additional_claims=claims, expires_delta=current.expires_at - utcnow()
        )
        current.refresh_digest = digest(decode_token(refresh)["jti"])
        response = jsonify(
            id=admin.id,
            username=admin.username,
            must_change_password=admin.must_change_password,
            session_id=current.id,
        )
        set_access_cookies(response, access)
        set_refresh_cookies(response, refresh)
        return response

    @blueprint.post("/login")
    def login() -> Response:
        data = body()
        username, password = (
            str(data.get("username", "")).strip().casefold(),
            str(data.get("password", "")),
        )
        if len(username) > 80 or len(password) > 256:
            abort(401, description="Invalid credentials")
        limited = False
        valid = False
        response = None
        with Session(engine) as db, db.begin():
            for key, maximum in (
                (digest("user:" + username), 10),
                (digest("ip:" + (request.remote_addr or "unknown")), 100),
            ):
                db.execute(
                    insert(LoginBucket)
                    .values(key=key, attempts=0, expires_at=utcnow() + timedelta(minutes=15))
                    .on_conflict_do_nothing()
                )
                bucket = db.scalar(
                    select(LoginBucket).where(LoginBucket.key == key).with_for_update()
                )
                assert bucket is not None
                if bucket.expires_at <= utcnow():
                    bucket.attempts = 0
                    bucket.expires_at = utcnow() + timedelta(minutes=15)
                bucket.attempts += 1
                limited |= bucket.attempts > maximum
            admin = db.scalar(select(Admin).where(Admin.username == username))
            valid = not limited and verify_password(admin.password_hash if admin else "", password)
            if valid and admin and admin.active:
                current = AdminSession(
                    id=identifier(),
                    admin_id=admin.id,
                    refresh_digest="",
                    expires_at=utcnow() + timedelta(days=7),
                )
                db.add(current)
                response = tokens(admin, current)
                audit(db, "auth.login", actor=admin.id)
            else:
                audit(db, "auth.login_failed")
        if response is None:
            abort(
                429 if limited else 401,
                description="Too many attempts" if limited else "Invalid credentials",
            )
        return response

    @blueprint.post("/refresh")
    @jwt_required(refresh=True)
    def refresh() -> Response:
        replay = False
        response = None
        with Session(engine) as db, db.begin():
            current = db.scalar(
                select(AdminSession).where(AdminSession.id == get_jwt()["sid"]).with_for_update()
            )
            assert current is not None
            admin = db.get(Admin, current.admin_id)
            assert admin is not None
            if current.revoked or current.refresh_digest != digest(get_jwt()["jti"]):
                current.revoked = True
                replay = True
                audit(db, "auth.refresh_replay", actor=admin.id)
            else:
                response = tokens(admin, current)
        if replay or response is None:
            abort(401, description="Session revoked")
        return response

    @blueprint.get("/me")
    @jwt_required()
    def me() -> Response:
        with Session(engine) as db:
            admin = db.get(Admin, get_jwt_identity())
            assert admin is not None
            return jsonify(
                id=admin.id,
                username=admin.username,
                must_change_password=admin.must_change_password,
            )

    @blueprint.post("/logout")
    @jwt_required(verify_type=False)
    def logout() -> Response:
        with Session(engine) as db, db.begin():
            db.execute(
                update(AdminSession).where(AdminSession.id == get_jwt()["sid"]).values(revoked=True)
            )
            audit(db, "auth.logout", actor=str(get_jwt_identity()))
        response = jsonify(ok=True)
        unset_jwt_cookies(response)
        return response

    @blueprint.post("/password")
    @jwt_required()
    def change_password() -> Response:
        data = body()
        password = require_password(data.get("password"))
        with Session(engine) as db, db.begin():
            admin = db.scalar(select(Admin).where(Admin.id == get_jwt_identity()).with_for_update())
            assert admin is not None
            if not verify_password(admin.password_hash, str(data.get("current_password", ""))):
                abort(403, description="Incorrect current password")
            admin.password_hash = passwords.hash(password)
            admin.must_change_password = False
            db.execute(
                update(AdminSession).where(AdminSession.admin_id == admin.id).values(revoked=True)
            )
            audit(db, "auth.password_changed", actor=admin.id)
        response = jsonify(ok=True)
        unset_jwt_cookies(response)
        return response

    app.register_blueprint(blueprint)


def bootstrap_admin(engine: Engine, username: str, password: str) -> None:
    if not 1 <= len(username.strip()) <= 80:
        raise ValueError("Use a 1–80 character username")
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Use a 12–256 character password")
    with Session(engine) as db, db.begin():
        if db.scalar(select(func.count()).select_from(Admin)):
            raise ValueError("Bootstrap is only allowed on an empty admin database")
        db.add(Admin(username=username.strip().casefold(), password_hash=passwords.hash(password)))
        audit(db, "auth.bootstrap")


def reset_admin(engine: Engine, username: str, password: str) -> None:
    if not 12 <= len(password) <= 256:
        raise ValueError("Use a 12–256 character password")
    with Session(engine) as db, db.begin():
        admin = db.scalar(
            select(Admin).where(Admin.username == username.strip().casefold()).with_for_update()
        )
        if not admin:
            raise ValueError("Admin not found")
        admin.password_hash = passwords.hash(password)
        admin.must_change_password = True
        db.execute(
            update(AdminSession).where(AdminSession.admin_id == admin.id).values(revoked=True)
        )
        audit(db, "auth.cli_password_reset", target=admin.id)
