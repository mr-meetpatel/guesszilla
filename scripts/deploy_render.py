"""Deploy one tested commit to Render. Uses only the Python standard library."""

import json
import os
import re
import sys
import time
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.render.com/v1"
SERVICES = ("API", "WORKER", "SCHEDULER", "ADMIN")
FAILED = {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}


class DeploymentError(Exception):
    pass


def configuration(environment: Mapping[str, str]) -> tuple[str, str, list[tuple[str, str]]]:
    required = ["RENDER_API_KEY", "RENDER_COMMIT_SHA"] + [
        f"RENDER_{name}_SERVICE_ID" for name in SERVICES
    ]
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        raise DeploymentError("Set required deployment variables: " + ", ".join(missing))
    commit = environment["RENDER_COMMIT_SHA"].strip()
    if not re.fullmatch(r"[a-fA-F0-9]{40}", commit):
        raise DeploymentError("RENDER_COMMIT_SHA must be a full Git commit SHA")
    services = [(name, environment[f"RENDER_{name}_SERVICE_ID"].strip()) for name in SERVICES]
    if any(not re.fullmatch(r"srv-[a-zA-Z0-9]+", identifier) for _, identifier in services):
        raise DeploymentError("Service IDs must start with srv- and contain letters/digits")
    if len({identifier for _, identifier in services}) != len(SERVICES):
        raise DeploymentError("Each Render service must have a different service ID")
    return environment["RENDER_API_KEY"].strip(), commit, services


class RenderClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(self, path: str, body: dict | None = None) -> dict:
        request = Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        # Retry read-only polling, but never retry a deploy POST: the first
        # request may already have been accepted even if its response was lost.
        attempts = 3 if body is None else 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=30) as response:
                    value = json.load(response)
                if not isinstance(value, dict):
                    raise DeploymentError("Unexpected Render API response")
                return value
            except HTTPError as error:
                retry = error.code == 429 or error.code >= 500
                if not retry or attempt == attempts - 1:
                    raise DeploymentError(f"Render API returned HTTP {error.code}") from None
            except URLError, TimeoutError:
                if attempt == attempts - 1:
                    raise DeploymentError(
                        "Cannot reach Render API; inspect Render before retrying a deployment"
                    ) from None
            except ValueError, OSError:
                raise DeploymentError("Cannot read Render API response") from None
            time.sleep(10)
        raise DeploymentError("Render API unavailable")

    def deploy(self, name: str, service: str, commit: str, timeout: float = 900) -> None:
        result = self.request(f"/services/{service}/deploys", {"commitId": commit})
        deploy_id = result.get("id", "")
        if not isinstance(deploy_id, str) or not re.fullmatch(r"dep-[a-zA-Z0-9]+", deploy_id):
            raise DeploymentError(f"{name}: Render did not return a deployment ID")
        print(f"{name}: deployment {deploy_id} for {commit}", flush=True)
        deadline = time.monotonic() + timeout
        previous = None
        while time.monotonic() < deadline:
            result = self.request(f"/services/{service}/deploys/{deploy_id}")
            status = result.get("status")
            if not isinstance(status, str):
                raise DeploymentError(f"{name}: missing deployment status")
            if status != previous:
                # Log only known status vocabulary, never arbitrary API bodies.
                safe_status = status if re.fullmatch(r"[a-z_]+", status) else "unknown"
                print(f"{name}: {safe_status}", flush=True)
                previous = status
            if status == "live":
                if result.get("commit", {}).get("id") != commit:
                    raise DeploymentError(f"{name}: live deployment does not match tested commit")
                return
            if status in FAILED:
                raise DeploymentError(f"{name}: deployment failed; check Render deploy logs")
            time.sleep(10)
        raise DeploymentError(f"{name}: timed out; check Render before retrying")


def main() -> int:
    try:
        token, commit, services = configuration(os.environ)
        client = RenderClient(token)
        # API startup applies locked migrations before new workers start.
        # Migrations must remain compatible with the previous running release.
        for name, service in services:
            client.deploy(name, service, commit)
        print("All Render services are live at the tested commit.", flush=True)
    except DeploymentError as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
