#!/usr/bin/env python3
"""
Caddy JSON Config Manager
==========================
Menambahkan / update file server virtual host di /etc/caddy/caddy.json,
lalu memvalidasi dan mereload Caddy secara otomatis.

Fungsi utama:
    configure_caddy_site(fqdn, web_root, caddy_json_path)
"""

import copy
import json
import os
import subprocess
import tempfile

CADDY_JSON_PATH = "/etc/caddy/caddy.json"

# Skeleton config minimal jika caddy.json belum ada
_BASE_CONFIG: dict = {
    "apps": {
        "http": {
            "servers": {
                "main": {
                    "listen": [":80", ":443"],
                    "routes": [],
                }
            }
        }
    }
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config(path: str) -> dict:
    """Baca caddy.json; kembalikan base config jika file tidak ada / kosong."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return json.loads(content)
    return copy.deepcopy(_BASE_CONFIG)


def _save_config(config: dict, path: str) -> None:
    """
    Tulis caddy.json dengan indentasi yang rapi.
    Karena /etc/caddy/ hanya bisa ditulis root, tulis dulu ke temp file
    lalu salin dengan 'sudo cp'.
    """
    content = json.dumps(config, indent=2, ensure_ascii=False) + "\n"

    # Jika path di bawah /etc atau tidak bisa ditulis langsung → sudo cp
    try:
        # Coba tulis langsung dulu (berguna saat testing)
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        # Tulis ke temp file, lalu salin dengan sudo
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                ["sudo", "cp", tmp_path, path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Gagal menulis {path} (sudo cp):\n"
                    f"{(result.stderr or result.stdout).strip()}"
                )
        finally:
            os.unlink(tmp_path)


def _get_server(config: dict) -> dict:
    """
    Ambil server pertama dari config apps.http.servers.
    Jika belum ada, buat skeleton baru secara otomatis.
    """
    servers = (
        config
        .setdefault("apps", {})
        .setdefault("http", {})
        .setdefault("servers", {})
    )
    if not servers:
        servers["main"] = {"listen": [":80", ":443"], "routes": []}
    return servers[next(iter(servers))]


def _add_tls_subject(config: dict, fqdn: str) -> None:
    """
    Tambahkan fqdn ke subjects di tls.automation.policies[0] jika sudah ada.
    Jika belum ada sama sekali, biarkan — Caddy akan auto-issue via default policy.
    """
    try:
        policies = config["apps"]["tls"]["automation"]["policies"]
        if policies:
            subjects = policies[0].setdefault("subjects", [])
            if fqdn not in subjects:
                subjects.append(fqdn)
    except (KeyError, IndexError, TypeError):
        pass  # Tidak ada TLS automation config — lewati


# ── Public API ────────────────────────────────────────────────────────────────

def configure_caddy_site(
    fqdn: str,
    web_root: str,
    caddy_json_path: str = CADDY_JSON_PATH,
) -> dict:
    """
    Tambah atau update route di caddy.json:
        {fqdn}  →  file_server root {web_root}

    Setelah mengubah file, otomatis menjalankan:
        caddy validate --config <caddy_json_path>
        sudo systemctl reload caddy

    Returns:
        {
            "action"   : "added" | "updated",
            "fqdn"     : str,
            "web_root" : str,
        }

    Raises:
        RuntimeError  jika validate atau reload gagal.
    """
    config = _load_config(caddy_json_path)
    server = _get_server(config)
    routes = server.setdefault("routes", [])

    # Bangun route baru
    new_route = {
        "match":    [{"host": [fqdn]}],
        "handle":   [{"handler": "file_server", "root": web_root}],
        "terminal": True,
    }

    # Cek apakah fqdn sudah ada → update; jika tidak → append
    action = "added"
    for i, route in enumerate(routes):
        hosts = [h for m in route.get("match", []) for h in m.get("host", [])]
        if fqdn in hosts:
            routes[i] = new_route
            action = "updated"
            break
    else:
        routes.append(new_route)

    # Tambahkan ke TLS subjects agar Caddy issue sertifikat otomatis
    _add_tls_subject(config, fqdn)

    _save_config(config, caddy_json_path)

    # ── Izin baca web root untuk user Caddy ───────────────────────────────────
    # Caddy berjalan sebagai user 'caddy'; pastikan folder output bisa dibaca.
    subprocess.run(
        ["sudo", "chmod", "-R", "o+rX", web_root],
        capture_output=True,
    )

    # ── Validate ──────────────────────────────────────────────────────────────
    validate = subprocess.run(
        ["caddy", "validate", "--config", caddy_json_path],
        capture_output=True, text=True,
    )
    if validate.returncode != 0:
        output = (validate.stderr or validate.stdout).strip()
        raise RuntimeError(f"caddy validate gagal:\n{output}")

    # ── Reload ────────────────────────────────────────────────────────────────
    reload_ = subprocess.run(
        ["sudo", "systemctl", "reload", "caddy"],
        capture_output=True, text=True,
    )
    if reload_.returncode != 0:
        output = (reload_.stderr or reload_.stdout).strip()
        raise RuntimeError(f"systemctl reload caddy gagal:\n{output}")

    return {"action": action, "fqdn": fqdn, "web_root": web_root}
