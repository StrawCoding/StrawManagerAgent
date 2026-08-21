"""Network expose: CloudflareTunnel / CustomDNS / LAN_mDNS."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from sma.paths import ensure_home, sma_home

NetworkMode = Literal["cloudflare", "custom_dns", "lan_mdns"]


@dataclass
class NetworkConfig:
    mode: NetworkMode = "lan_mdns"
    bind: str = "0.0.0.0"
    port: int = 8741
    public_hostname: str | None = None
    cloudflare_tunnel_name: str | None = None


def detect_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_network_config(root: Path | None = None) -> NetworkConfig:
    ensure_home(root)
    cfg_path = sma_home(root) / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    net = data.get("network") or {}
    return NetworkConfig(
        mode=net.get("mode", "lan_mdns"),
        bind=net.get("bind", "0.0.0.0"),
        port=int(net.get("port", 8741)),
        public_hostname=net.get("public_hostname"),
        cloudflare_tunnel_name=net.get("cloudflare_tunnel_name"),
    )


def save_network_config(cfg: NetworkConfig, root: Path | None = None) -> Path:
    ensure_home(root)
    cfg_path = sma_home(root) / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    data["network"] = {
        "mode": cfg.mode,
        "bind": cfg.bind,
        "port": cfg.port,
        "public_hostname": cfg.public_hostname,
        "cloudflare_tunnel_name": cfg.cloudflare_tunnel_name,
    }
    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return cfg_path


def describe_setup(cfg: NetworkConfig) -> dict:
    lan = detect_lan_ip()
    local = f"http://{lan}:{cfg.port}"
    if cfg.mode == "lan_mdns":
        return {
            "mode": "lan_mdns",
            "urls": [local, f"http://sma.local:{cfg.port}"],
            "notes": "Bind 0.0.0.0; optional mDNS hostname sma.local. This is LAN expose, not DHCP.",
            "scripts": [],
        }
    if cfg.mode == "custom_dns":
        host = cfg.public_hostname or "sma.example.com"
        return {
            "mode": "custom_dns",
            "urls": [f"https://{host}"],
            "notes": f"Create A/CNAME for {host} → {lan}; reverse-proxy to 127.0.0.1:{cfg.port}",
            "scripts": [
                f"# nginx snippet\n"
                f"server {{\n  server_name {host};\n"
                f"  location / {{ proxy_pass http://127.0.0.1:{cfg.port}; }}\n}}"
            ],
        }
    # cloudflare
    return {
        "mode": "cloudflare",
        "urls": [f"https://{cfg.public_hostname or 'sma.example.com'}"],
        "notes": "Requires CLOUDFLARE_API_TOKEN in ~/.sma/.env; create tunnel + route DNS.",
        "scripts": [
            "cloudflared tunnel create sma",
            f"cloudflared tunnel route dns sma {cfg.public_hostname or 'sma.example.com'}",
            f"cloudflared tunnel run --url http://127.0.0.1:{cfg.port} sma",
        ],
    }
