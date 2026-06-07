#!/usr/bin/env python3
"""
Build / download static assets untuk landing page generator.
Jalankan SEKALI di server: python3 build_assets.py

Assets yang didownload ke static/:
  static/tailwind.min.js              — Tailwind CDN Play script
  static/fa/css/all.min.css           — Font Awesome 6.5.0 CSS
  static/fa/webfonts/*.woff2          — Font Awesome webfonts
  static/react/react.production.min.js     — React 18 (UMD)
  static/react/react-dom.production.min.js — ReactDOM 18 (UMD)
  static/react/babel.min.js                — Babel Standalone (JSX di browser)
  static/alpine.min.js                — Alpine.js 3 (reactive, 15 KB)
  static/gsap.min.js                  — GSAP 3 (animasi premium, 70 KB)

Catatan: Next.js adalah server framework (Node.js + build process).
         Tidak bisa dijadikan static CDN asset — tidak diinclude di sini.
"""

import os
import sys
import requests

STATIC_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
FA_VERSION    = "6.5.0"
FA_BASE       = f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FA_VERSION}"
TW_URL        = "https://cdn.tailwindcss.com"
REACT_VERSION = "18.3.1"
BABEL_VERSION = "7.24.7"
ALPINE_VERSION = "3.14.1"
GSAP_VERSION  = "3.12.5"

REACT_FILES = {
    "react.production.min.js":
        f"https://unpkg.com/react@{REACT_VERSION}/umd/react.production.min.js",
    "react-dom.production.min.js":
        f"https://unpkg.com/react-dom@{REACT_VERSION}/umd/react-dom.production.min.js",
    "babel.min.js":
        f"https://unpkg.com/@babel/standalone@{BABEL_VERSION}/babel.min.js",
}

FA_WEBFONTS = [
    "fa-brands-400.woff2",
    "fa-regular-400.woff2",
    "fa-solid-900.woff2",
    "fa-v4compatibility.woff2",
]


def _get(url: str, label: str) -> bytes:
    print(f"  Downloading {label} ...", end=" ", flush=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    print(f"{len(resp.content) // 1024} KB")
    return resp.content


def download_tailwind():
    dest = os.path.join(STATIC_DIR, "tailwind.min.js")
    if os.path.isfile(dest):
        print(f"  SKIP tailwind.min.js (sudah ada)")
        return
    data = _get(TW_URL, "tailwind.min.js")
    with open(dest, "wb") as f:
        f.write(data)


def download_fontawesome():
    css_path = os.path.join(STATIC_DIR, "fa", "css", "all.min.css")
    if os.path.isfile(css_path):
        print(f"  SKIP Font Awesome (sudah ada)")
        return

    # CSS
    os.makedirs(os.path.dirname(css_path), exist_ok=True)
    css = _get(f"{FA_BASE}/css/all.min.css", "fa/css/all.min.css")
    with open(css_path, "wb") as f:
        f.write(css)

    # Webfonts
    wf_dir = os.path.join(STATIC_DIR, "fa", "webfonts")
    os.makedirs(wf_dir, exist_ok=True)
    for fname in FA_WEBFONTS:
        data = _get(f"{FA_BASE}/webfonts/{fname}", f"fa/webfonts/{fname}")
        with open(os.path.join(wf_dir, fname), "wb") as f:
            f.write(data)


def download_react():
    react_dir = os.path.join(STATIC_DIR, "react")
    if all(os.path.isfile(os.path.join(react_dir, f)) for f in REACT_FILES):
        print("  SKIP React + Babel (sudah ada)")
        return
    os.makedirs(react_dir, exist_ok=True)
    for fname, url in REACT_FILES.items():
        data = _get(url, f"react/{fname}")
        with open(os.path.join(react_dir, fname), "wb") as f:
            f.write(data)


def download_alpine():
    dest = os.path.join(STATIC_DIR, "alpine.min.js")
    if os.path.isfile(dest):
        print("  SKIP alpine.min.js (sudah ada)")
        return
    url  = f"https://unpkg.com/alpinejs@{ALPINE_VERSION}/dist/cdn.min.js"
    data = _get(url, "alpine.min.js")
    with open(dest, "wb") as f:
        f.write(data)


def download_gsap():
    dest = os.path.join(STATIC_DIR, "gsap.min.js")
    if os.path.isfile(dest):
        print("  SKIP gsap.min.js (sudah ada)")
        return
    url  = f"https://cdnjs.cloudflare.com/ajax/libs/gsap/{GSAP_VERSION}/gsap.min.js"
    data = _get(url, "gsap.min.js")
    with open(dest, "wb") as f:
        f.write(data)


def main():
    os.makedirs(STATIC_DIR, exist_ok=True)
    print("\nBuilding static assets...\n")
    try:
        download_tailwind()
        download_fontawesome()
        download_react()
        download_alpine()
        download_gsap()
    except requests.exceptions.RequestException as e:
        print(f"\n[ERROR] Download gagal: {e}")
        sys.exit(1)
    print(f"\nSelesai! Assets tersimpan di: {STATIC_DIR}/")
    print("Folder structure:")
    for root, dirs, files in os.walk(STATIC_DIR):
        lvl = root.replace(STATIC_DIR, "").count(os.sep)
        indent = "  " * lvl
        print(f"{indent}{os.path.basename(root)}/")
        for f in files:
            size = os.path.getsize(os.path.join(root, f)) // 1024
            print(f"{indent}  {f}  ({size} KB)")


if __name__ == "__main__":
    main()
