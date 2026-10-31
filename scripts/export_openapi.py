"""Export the API contract without accessing a database or reading deployment secrets."""

import argparse
import json
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import SecretStr
from wordle_bot.config import Settings
from wordle_bot.http import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql://unused:unused@localhost/unused"),
        admin_panel_enabled=True,
        jwt_secret_key=SecretStr("contract-export-not-a-deployment-secret"),
        credential_encryption_keys=SecretStr(Fernet.generate_key().decode()),
        persist_logs=False,
    )
    app = create_app(settings)
    spec = app.extensions["admin_api"].spec.to_dict()
    # Auth routes use the JWT extension directly; include their contracts explicitly.
    for route, method in (
        ("login", "post"),
        ("refresh", "post"),
        ("me", "get"),
        ("logout", "post"),
        ("password", "post"),
    ):
        spec["paths"]["/api/v1/auth/" + route] = {
            method: {
                "operationId": "auth_" + route,
                "responses": {"200": {"description": "Authenticated session"}},
                **({"security": []} if route == "login" else {}),
            }
        }
    value = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    target = Path(__file__).resolve().parents[1] / "infra" / "openapi.json"
    if args.check:
        if not target.exists() or target.read_text() != value:
            raise SystemExit("OpenAPI contract drift. Run make api-client.")
    else:
        target.write_text(value)
