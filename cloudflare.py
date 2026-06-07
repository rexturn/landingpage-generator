#!/usr/bin/env python3
"""
Cloudflare DNS Helper
Membuat / update DNS record subdomain secara otomatis.
"""

import re
import requests

CF_API = "https://api.cloudflare.com/client/v4"


def _headers(api_token: str) -> dict:
    token = api_token.strip() if api_token else ""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


def _cf(method: str, url: str, api_token: str, **kwargs) -> dict:
    """
    Buat request ke Cloudflare API.
    Selalu parse JSON response dan cek field 'success' sebelum raise.
    Memberikan pesan error yang jelas jika API gagal.
    """
    token = (api_token or "").strip()
    if not token:
        raise RuntimeError(
            "CF_API_TOKEN kosong di .env. "
            "Buat API Token di: https://dash.cloudflare.com/profile/api-tokens"
        )

    kwargs["headers"] = _headers(token)
    kwargs.setdefault("timeout", 15)

    resp = getattr(requests, method)(url, **kwargs)

    # Parse JSON dulu — Cloudflare selalu reply JSON bahkan untuk error
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        return {}

    if not data.get("success", True):
        errors = data.get("errors", [])
        parts  = []
        for e in errors:
            code = e.get("code", "?")
            msg  = e.get("message", "unknown error")
            if code == 9106:
                parts.append(
                    f"[{code}] Token tidak dikirim — pastikan CF_API_TOKEN terisi di .env"
                )
            elif code == 1000:
                parts.append(
                    f"[{code}] Token tidak valid / expired. "
                    "Buat token baru di: https://dash.cloudflare.com/profile/api-tokens"
                )
            elif code == 10000:
                parts.append(
                    f"[{code}] Token tidak punya izin DNS:Edit. "
                    "Tambahkan permission Zone → DNS → Edit saat buat token."
                )
            else:
                parts.append(f"[{code}] {msg}")
        raise RuntimeError("Cloudflare: " + " | ".join(parts))

    return data


# ── Auto-detect public IP server ─────────────────────────────────────────────
def get_public_ip() -> str | None:
    """Deteksi public IP server secara otomatis."""
    endpoints = [
        ("https://api.ipify.org?format=json",      "ip"),
        ("https://api4.my-ip.io/v2/ip.json",       "ip"),
        ("https://api.seeip.org/jsonip",            "ip"),
    ]
    for url, key in endpoints:
        try:
            resp = requests.get(url, timeout=5)
            ip = resp.json().get(key, "")
            if ip and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                return ip
        except Exception:
            continue
    return None


# ── Deteksi tipe record ───────────────────────────────────────────────────────
def is_ip_address(s: str) -> bool:
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s.strip()))


# ── Zone lookup ───────────────────────────────────────────────────────────────
def get_zone_id(api_token: str, domain: str, account_id: str = None) -> str:
    """
    Ambil Zone ID Cloudflare untuk domain (misal: qtl.web.id).
    Raises RuntimeError jika tidak ditemukan.
    """
    params = {"name": domain, "status": "active"}
    if account_id:
        params["account.id"] = account_id

    data    = _cf("get", f"{CF_API}/zones", api_token, params=params)
    results = data.get("result", [])
    if not results:
        raise RuntimeError(
            f"Zone tidak ditemukan untuk domain '{domain}'. "
            "Pastikan domain sudah ditambahkan di Cloudflare dashboard."
        )
    return results[0]["id"]


# ── Cek apakah record sudah ada ──────────────────────────────────────────────
def find_existing_record(api_token: str, zone_id: str, fqdn: str) -> str | None:
    """Kembalikan record_id jika DNS record untuk fqdn sudah ada, else None."""
    data    = _cf("get", f"{CF_API}/zones/{zone_id}/dns_records",
                  api_token, params={"name": fqdn})
    results = data.get("result", [])
    return results[0]["id"] if results else None


# ── Buat atau update DNS record ───────────────────────────────────────────────
def create_or_update_dns(
    api_token:   str,
    zone_id:     str,
    subdomain:   str,
    base_domain: str,
    target:      str,
    proxied:     bool = True,
) -> dict:
    """
    Buat atau update A/CNAME record di Cloudflare.

    Parameters
    ----------
    api_token   : Cloudflare API token (perlu permission DNS:Edit)
    zone_id     : Zone ID dari base_domain
    subdomain   : nama subdomain saja, misal "toko_baju"
    base_domain : base domain, misal "qtl.web.id"
    target      : IP address (→ A record) atau hostname (→ CNAME record)
    proxied     : True = lewat Cloudflare CDN/proxy (ikon oranye)

    Returns
    -------
    dict dengan key: action, type, name, content, proxied
    """
    fqdn        = f"{subdomain}.{base_domain}"
    record_type = "A" if is_ip_address(target) else "CNAME"
    payload = {
        "type":    record_type,
        "name":    fqdn,
        "content": target,
        "ttl":     1,        # 1 = automatic
        "proxied": proxied,
    }

    existing_id = find_existing_record(api_token, zone_id, fqdn)

    if existing_id:
        data   = _cf("put",
                     f"{CF_API}/zones/{zone_id}/dns_records/{existing_id}",
                     api_token, json=payload)
        action = "diperbarui"
    else:
        data   = _cf("post",
                     f"{CF_API}/zones/{zone_id}/dns_records",
                     api_token, json=payload)
        action = "dibuat"

    result = data["result"]
    return {
        "action":  action,
        "type":    result["type"],
        "name":    result["name"],
        "content": result["content"],
        "proxied": result.get("proxied", proxied),
        "fqdn":    fqdn,
    }


# ── High-level helper (dipanggil dari bot.py) ─────────────────────────────────
def setup_subdomain(
    api_token:   str,
    account_id:  str,
    zone_id:     str,       # bisa "" → auto-lookup dari base_domain
    subdomain:   str,
    base_domain: str,
    target:      str = "",  # "" → auto-detect public IP
    proxied:     bool = False,
) -> dict:
    """
    One-call wrapper:
    1. Resolve target IP jika kosong
    2. Resolve zone_id jika kosong
    3. Buat/update DNS record
    Kembalikan dict hasil atau raise RuntimeError.
    """
    # 1. Target
    if not target:
        target = get_public_ip()
        if not target:
            raise RuntimeError(
                "Tidak bisa mendeteksi public IP server. "
                "Set CF_DNS_TARGET di .env secara manual."
            )

    # 2. Zone ID
    if not zone_id:
        zone_id = get_zone_id(api_token, base_domain, account_id or None)

    # 3. Buat/update
    result = create_or_update_dns(
        api_token, zone_id, subdomain, base_domain, target, proxied
    )
    result["target_used"] = target
    result["zone_id"]     = zone_id
    return result
