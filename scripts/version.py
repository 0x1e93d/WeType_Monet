#!/usr/bin/env python3

"""模块版本管理命令。"""

import argparse
import json
import re
from pathlib import Path


MODULE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "module.json"
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def bump_patch(config_path: Path = MODULE_CONFIG_PATH) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    match = VERSION_PATTERN.fullmatch(str(config.get("version", "")))
    if not match:
        raise ValueError("module.json 的 version 必须为 MAJOR.MINOR.PATCH 格式")
    major, minor, patch = (int(value) for value in match.groups())
    config["version"] = f"{major}.{minor}.{patch + 1}"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return config["version"]


def main():
    parser = argparse.ArgumentParser(description="管理模块展示版本")
    parser.add_argument("command", choices=["bump-patch"])
    args = parser.parse_args()
    if args.command == "bump-patch":
        print(bump_patch())


if __name__ == "__main__":
    main()
