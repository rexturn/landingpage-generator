#!/usr/bin/env python3
"""
Caddy JSON Config Manager
==========================
Menambahkan / update file server virtual host di /etc/caddy/caddy.json,
lalu memvalidasi dan mereload Caddy secara otomatis.

Fungsi utama:
    configure_caddy_site(fqdn, web_root, caddy_json_path)
    remove_caddy_site(fqdn, caddy_json_path)
"""

import copy
import json
import os
import re
import subprocess
import tempfile

CADDY_JSON_PATH = "/etc/caddy/caddy.json"
_FQDN_RE        = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$")

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


def _fix_permissions(web_root: str) -> None:
    """
    Pastikan Caddy bisa mengakses web_root beserta semua parent directory-nya.
    - Parent dirs  : sudo chmod o+x  (agar bisa di-traverse)
    - web_root     : sudo chmod -R o+rX  (agar file bisa dibaca)
    Berhenti naik jika sudah sampai /home atau root.
    """
    # chmod -R o+rX pada folder project itu sendiri
    subprocess.run(["sudo", "chmod", "-R", "o+rX", web_root], capture_output=True)

    # chmod o+x pada setiap parent directory hingga /home
    path = os.path.dirname(os.path.abspath(web_root))
    stop_at = os.path.dirname("/home/")  # yaitu '/'
    while path and path != stop_at:
        subprocess.run(["sudo", "chmod", "o+x", path], capture_output=True)
        parent = os.path.dirname(path)
        if parent == path:  # sudah di root
            break
        path = parent


def _is_valid_ssl_fqdn(fqdn: str) -> bool:
    """
    Validasi bahwa FQDN hanya mengandung karakter yang valid untuk SSL/TLS cert.
    Let's Encrypt menolak underscore dan karakter non-DNS.
    """
    if not fqdn or len(fqdn) > 253:
        return False
    return bool(_FQDN_RE.match(fqdn.lower()))


def _reload_caddy() -> None:
    """Reload Caddy. Raises RuntimeError jika gagal."""
    reload_ = subprocess.run(
        ["sudo", "systemctl", "reload", "caddy"],
        capture_output=True, text=True,
    )
    if reload_.returncode != 0:
        output = (reload_.stderr or reload_.stdout).strip()
        raise RuntimeError(f"systemctl reload caddy gagal:\n{output}")


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
        RuntimeError  jika FQDN tidak valid, validate, atau reload gagal.
                      Config di-rollback otomatis jika gagal setelah tulis.
    """
    # ── Validasi FQDN sebelum menyentuh file apa pun ────────────────────────
    if not _is_valid_ssl_fqdn(fqdn):
        raise RuntimeError(
            f"Domain tidak valid untuk SSL: '{fqdn}'.\n"
            "Hanya boleh huruf kecil, angka, dan tanda hubung (-). "
            "Underscore (_) dan karakter khusus lain tidak diizinkan oleh Let's Encrypt."
        )

    config_before = _load_config(caddy_json_path)   # simpan untuk rollback
    config        = copy.deepcopy(config_before)
    server        = _get_server(config)
    routes        = server.setdefault("routes", [])

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
    # Caddy berjalan sebagai user 'caddy'. Untuk bisa baca file di dalam
    # /home/user/..., Caddy butuh execute (o+x) di SETIAP direktori parent.
    # Contoh: /home/rexxy, /home/rexxy/landingpage, .../output harus o+x.
    _fix_permissions(web_root)

    # ── Validate ──────────────────────────────────────────────────────────────
    # Baca CLOUDFLARE_API_TOKEN dari caddy.env agar caddy validate bisa akses
    validate_env = os.environ.copy()
    caddy_env_path = "/etc/caddy/caddy.env"
    if os.path.exists(caddy_env_path):
        with open(caddy_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("CLOUDFLARE_API_TOKEN=") and "=" in _line:
                    validate_env["CLOUDFLARE_API_TOKEN"] = _line.split("=", 1)[1]
                    break
    validate = subprocess.run(
        ["caddy", "validate", "--config", caddy_json_path],
        capture_output=True, text=True, env=validate_env,
    )
    if validate.returncode != 0:
        # Rollback ke config sebelumnya lalu reload
        try:
            _save_config(config_before, caddy_json_path)
            subprocess.run(["sudo", "systemctl", "reload", "caddy"],
                           capture_output=True, text=True)
        except Exception:
            pass
        raw = (validate.stderr or validate.stdout).strip()
        # caddy logs are JSON lines — extract only error/fatal entries
        error_lines = []
        for line in raw.splitlines():
            try:
                entry = json.loads(line)
                if entry.get("level") in ("error", "fatal", "warn"):
                    error_lines.append(
                        entry.get("msg", line)
                        + (f": {entry['err']}" if "err" in entry else "")
                    )
            except (json.JSONDecodeError, TypeError):
                error_lines.append(line)
        output = "\n".join(error_lines) if error_lines else raw
        raise RuntimeError(f"caddy validate gagal (config di-rollback):\n{output}")

    # ── Reload ────────────────────────────────────────────────────────────────
    reload_ = subprocess.run(
        ["sudo", "systemctl", "reload", "caddy"],
        capture_output=True, text=True,
    )
    if reload_.returncode != 0:
        # Rollback dan coba reload sekali lagi dengan config lama
        try:
            _save_config(config_before, caddy_json_path)
            subprocess.run(["sudo", "systemctl", "reload", "caddy"],
                           capture_output=True, text=True)
        except Exception:
            pass
        output = (reload_.stderr or reload_.stdout).strip()
        raise RuntimeError(f"systemctl reload caddy gagal (config di-rollback):\n{output}")

    return {"action": action, "fqdn": fqdn, "web_root": web_root}


# ── Hapus site dari caddy.json ────────────────────────────────────────────────
def remove_caddy_site(
    fqdn: str,
    caddy_json_path: str = CADDY_JSON_PATH,
) -> bool:
    """
    Hapus route untuk fqdn dari caddy.json dan juga hapus dari TLS subjects.
    Berguna untuk membersihkan domain yang gagal mendapatkan SSL cert.

    Returns:
        True  jika route ditemukan dan berhasil dihapus
        False jika route tidak ditemukan (tidak ada yang perlu dihapus)

    Raises:
        RuntimeError jika reload Caddy gagal setelah penghapusan.
    """
    config = _load_config(caddy_json_path)
    server = _get_server(config)
    routes = server.get("routes", [])

    new_routes = [
        r for r in routes
        if fqdn not in [h for m in r.get("match", []) for h in m.get("host", [])]
    ]
    if len(new_routes) == len(routes):
        return False   # fqdn tidak ada di config

    server["routes"] = new_routes

    # Hapus juga dari TLS subjects
    try:
        policies = config["apps"]["tls"]["automation"]["policies"]
        if policies and "subjects" in policies[0]:
            policies[0]["subjects"] = [
                s for s in policies[0]["subjects"] if s != fqdn
            ]
    except (KeyError, IndexError, TypeError):
        pass

    _save_config(config, caddy_json_path)

    reload_ = subprocess.run(
        ["sudo", "systemctl", "reload", "caddy"],
        capture_output=True, text=True,
    )
    if reload_.returncode != 0:
        output = (reload_.stderr or reload_.stdout).strip()
        raise RuntimeError(f"Caddy reload gagal setelah hapus '{fqdn}':\n{output}")

    return True
