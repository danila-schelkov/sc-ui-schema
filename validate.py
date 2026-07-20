#!/usr/bin/env python3

import json
import subprocess
import tomllib
from pathlib import Path

SCHEMA = Path("src/ui.schema.json")


def validate(json_file: Path) -> int:
    return subprocess.run(
        [
            "jsonschema",
            "validate",
            str(SCHEMA),
            str(json_file),
        ]
    ).returncode


def main():
    exit_code = 0

    for ui_file in Path(".").rglob("*.ui"):
        json_file = ui_file.with_suffix(".json")

        try:
            with ui_file.open("rb") as f:
                data = tomllib.load(f)

            with json_file.open("w", encoding="utf-8") as f:
                data["$schema"] = SCHEMA.relative_to(json_file.parent, walk_up=True).as_posix()
                json.dump(data, f, indent=4, ensure_ascii=False)

            print(f"Validating {ui_file}...")
            result = validate(json_file)
            if result != 0:
                exit_code = result
            else:
                json_file.unlink(missing_ok=True)
        except Exception as _e:
            print(f"Error occurred while loading file: {ui_file}")
        finally:
            pass

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
