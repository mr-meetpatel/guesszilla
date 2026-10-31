"""Validate the deployment Blueprint against Render's current official schema."""

import json
from pathlib import Path
from urllib.request import urlopen

import jsonschema
import yaml

if __name__ == "__main__":
    with urlopen("https://render.com/schema/render.yaml.json", timeout=15) as response:
        schema = json.load(response)
    root = Path(__file__).resolve().parents[1]
    blueprint = yaml.safe_load((root / "render.yaml").read_text())
    jsonschema.validate(blueprint, schema)
    print("Render Blueprint is valid.")
