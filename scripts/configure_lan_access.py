#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import socket
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/lan-access.json"
DEFAULT_CIDRS = ["127.0.0.0/8", "::1/128", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def ensure_config(reset: bool = False) -> dict:
    if CONFIG_PATH.is_file() and not reset:
        try:
            current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if current.get("enabled") and current.get("access_code"):
                return current
        except (OSError, json.JSONDecodeError):
            pass
    config = {
        "schema_version": "1.0.0",
        "enabled": True,
        "port": 8765,
        "access_code": secrets.token_urlsafe(9),
        "allowed_cidrs": DEFAULT_CIDRS,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(CONFIG_PATH)
    CONFIG_PATH.chmod(0o600)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure private LAN access for the studio workbench")
    parser.add_argument("--reset", action="store_true", help="Generate a new shared access code")
    args = parser.parse_args()
    config = ensure_config(reset=args.reset)
    addresses = local_ipv4_addresses()
    print("工作室访问码：" + config["access_code"])
    if addresses:
        for address in addresses:
            print(f"工作台地址：http://{address}:{config.get('port', 8765)}/workbench")
    else:
        print("工作台地址：请在系统网络设置中查看主电脑局域网地址")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
