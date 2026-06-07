#!/usr/bin/env python3
"""
Landing Page Generator — Core Library + CLI
"""
import os, sys, json, re, shutil, requests
from pathlib import Path

IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
STATIC_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ── HTML Templates ────────────────────────────────────────────────────────────

def _html_wrap(body: str, page_title: str, color_theme: str,
               lang: str = "id", local_assets: bool = False,
               template: str = "vanilla") -> str:
    """Bungkus body HTML dengan <head> lengkap. Gunakan asset lokal jika tersedia."""
    if local_assets:
        tw  = '<script src="tailwind.min.js"></script>'
        fa  = '<link rel="stylesheet" href="fa/css/all.min.css">'
        if template == "react":
            react_scripts = (
                '<script src="react/react.production.min.js"></script>\n  '
                '<script src="react/react-dom.production.min.js"></script>\n  '
                '<script src="react/babel.min.js"></script>'
            )
        else:
            react_scripts = ""
    else:
        tw  = '<script src="https://cdn.tailwindcss.com"></script>'
        fa  = ('<link rel="stylesheet" href="https://cdnjs.cloudflare.com'
               '/ajax/libs/font-awesome/6.5.0/css/all.min.css">')
        if template == "react":
            react_scripts = (
                '<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>\n  '
                '<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>\n  '
                '<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>'
            )
        else:
            react_scripts = ""
    extra = f"\n  {react_scripts}" if react_scripts else ""
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{page_title}</title>
  {tw}
  <script>
    tailwind.config = {{
      theme: {{ extend: {{ colors: {{ primary: "{color_theme}" }} }} }}
    }}
  </script>
  {fa}{extra}
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Poppins', sans-serif; }}
    html {{ scroll-behavior: smooth; }}
  </style>
</head>
<body class="bg-white text-gray-800">
{body}
</body>
</html>
"""


def copy_assets_to_project(project_dir: str, template: str = "vanilla") -> bool:
    """
    Salin static assets ke project_dir sesuai template type.
    Returns True jika berhasil. False jika belum di-build (jalankan build_assets.py).
    """
    tw_src = os.path.join(STATIC_DIR, "tailwind.min.js")
    fa_src = os.path.join(STATIC_DIR, "fa")
    if not (os.path.isfile(tw_src) and
            os.path.isfile(os.path.join(fa_src, "css", "all.min.css"))):
        return False

    shutil.copy2(tw_src, os.path.join(project_dir, "tailwind.min.js"))
    fa_dst = os.path.join(project_dir, "fa")
    if os.path.exists(fa_dst):
        shutil.rmtree(fa_dst)
    shutil.copytree(fa_src, fa_dst)

    # Salin React assets jika template react
    if template == "react":
        react_src = os.path.join(STATIC_DIR, "react")
        if os.path.isdir(react_src):
            react_dst = os.path.join(project_dir, "react")
            if os.path.exists(react_dst):
                shutil.rmtree(react_dst)
            shutil.copytree(react_src, react_dst)

    return True

def load_env(filepath=".env"):
    env = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        print(f"[ERROR] File .env tidak ditemukan di: {filepath}")
        sys.exit(1)
    return env

def parse_filename(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def generate_domain_slug(api_key: str, model: str, project_name: str) -> str:
    """
    Gunakan AI untuk generate domain slug yang bersih dari nama proyek.
    Contoh: "Toko Baju Online" → "tokobaju" atau "bajustore"
    Fallback: hapus non-alfanumerik, lowercase, tanpa pemisah.
    """
    system = "You are a domain name generator. Reply with only the slug, nothing else."
    user = (
        f"Create a short, memorable domain slug for a landing page named: '{project_name}'. "
        "Rules: lowercase letters and numbers only (a-z, 0-9), hyphens allowed, "
        "NO underscores, NO spaces, max 20 characters. "
        "Reply ONLY with the slug."
    )
    try:
        raw  = call_ai(api_key, model, system, user, max_tokens=20)
        slug = re.sub(r"[^a-z0-9-]", "", raw.strip().lower())[:20].strip("-")
        if slug:
            return slug
    except Exception:
        pass
    # Fallback: gabung huruf/angka saja
    fallback = re.sub(r"[^a-z0-9]", "", project_name.lower())[:20]
    return fallback or "landingpage"


def is_duplicate(output_dir: str, filename_base: str) -> bool:
    return os.path.isdir(os.path.join(output_dir, filename_base))

def setup_project_dir(output_dir: str, filename_base: str) -> str:
    project_dir = os.path.join(output_dir, filename_base)
    Path(project_dir).mkdir(parents=True, exist_ok=True)
    return project_dir

def copy_photos_to_project(selected: dict, project_dir: str) -> dict:
    Path(project_dir).mkdir(parents=True, exist_ok=True)
    result, copied = {}, {}
    for key in ["hero", "feature_1", "feature_2", "feature_3", "about"]:
        src = selected.get(key) or selected.get("hero", "")
        if not src or not os.path.isfile(src):
            result[key] = result.get("hero", "hero.jpg")
            continue
        if src in copied:
            result[key] = copied[src]; continue
        ext = Path(src).suffix.lower() or ".jpg"
        destname = f"{key}{ext}"
        shutil.copy2(src, os.path.join(project_dir, destname))
        copied[src] = destname
        result[key] = destname
    return result

def save_project_html(html_body: str, project_dir: str,
                      page_title: str = "", color_theme: str = "#3B82F6",
                      lang: str = "id", template: str = "vanilla") -> str:
    Path(project_dir).mkdir(parents=True, exist_ok=True)
    local    = copy_assets_to_project(project_dir, template)
    html     = _html_wrap(html_body, page_title, color_theme, lang,
                          local_assets=local, template=template)
    filepath = os.path.join(project_dir, "index.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    return filepath

def call_ai(api_key: str, model: str, system_prompt: str, user_prompt: str,
            max_tokens: int = 6000) -> str:
    url = "https://apihub.agnes-ai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://landing-page-generator.local",
        "X-Title":       "Landing Page Generator",
    }
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user",   "content": user_prompt}],
        "temperature": 0.7, "max_tokens": max_tokens,
    }
    print(f"  -> AI [{model}] max_tokens={max_tokens} ...", flush=True)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        # Handle 402 (kredit habis) dengan pesan yang jelas
        if resp.status_code == 402:
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:200]
            raise RuntimeError(
                f"Kredit OpenRouter habis (402).\n"
                f"Detail: {detail}\n"
                f"Top-up di: https://openrouter.ai/settings/credits\n"
                f"Atau kurangi MAX_TOKENS di .env (sekarang: {max_tokens})"
            )
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Koneksi gagal: {e}")
    return resp.json()["choices"][0]["message"]["content"]

def analyze_prompt(api_key: str, model: str, description: str, project_name: str) -> dict:
    system = ("You are a helpful assistant that extracts metadata from text. "
              "Always respond with valid JSON only — no markdown, no extra text.")
    user = f'''Extract metadata from this landing page description:
"{description}"
Project name: "{project_name}"
Respond ONLY with this exact JSON (no extra text):
{{
  "page_title": "compelling browser tab title (max 60 chars, match the theme)",
  "color_theme": "#RRGGBB",
  "color_name": "color name in English"
}}'''
    raw = call_ai(api_key, model, system, user, max_tokens=250)
    match = re.search(r"\{[\s\S]*?\}", raw)
    if match:
        try: return json.loads(match.group())
        except json.JSONDecodeError: pass
    return {"page_title": project_name.replace("_"," ").title(),
            "color_theme": "#3B82F6", "color_name": "blue"}

def extract_code(raw: str) -> str:
    # Response lengkap: ada opening dan closing fence
    match = re.search(r"```(?:html)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Response terpotong (token habis): ada opening fence tapi tidak ada closing
    match = re.search(r"```(?:html)?\s*([\s\S]+)", raw, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return raw.strip()

SYSTEM_PROMPT = """Kamu adalah expert Front-End Developer spesialis landing page modern.

TUGAS: Buat HANYA konten di dalam <body> untuk sebuah landing page modern dan profesional.
Assets sudah tersedia: Tailwind CSS, Font Awesome, Google Font 'Poppins'.

ATURAN KETAT:
1.  Output HANYA elemen HTML mulai dari <nav> sampai </footer>.
    DILARANG menulis: <!DOCTYPE>, <html>, <head>, <body>, <script src=...>, <link rel=...>
2.  Tailwind CSS sudah aktif. Gunakan class Tailwind untuk semua styling.
    Warna primary sudah dikonfigurasi — gunakan: bg-primary, text-primary, border-primary.
3.  Font Awesome sudah aktif. Gunakan <i class="fa-solid fa-..."> atau <i class="fa-brands fa-...">.
4.  Font 'Poppins' sudah menjadi default font body.
5.  Boleh satu <script> inline di paling akhir output (hanya untuk navbar scroll effect).
6.  Path gambar RELATIF (file ada di folder SAMA dengan HTML).
    Benar : src="hero.jpg"   atau   style="background-image:url('hero.jpg')"
    Salah : URL https:// apapun.
7.  Section WAJIB — tepat 5, tidak lebih tidak kurang:
    a. <nav>      sticky — transparan → bg-gray-900/95 saat scroll (JS classList toggle)
    b. <section>  hero fullscreen — bg-image + dark overlay + headline + 1 tombol CTA
    c. <section>  features — 3 card dengan <img> masing-masing + teks singkat
    d. <section>  about — <img> about di samping paragraf deskripsi
    e. <footer>   gelap, info kontak fiktif relevan, icon sosmed Font Awesome
8.  Kode RINGKAS: gunakan class Tailwind, hindari style inline berulang.
9.  IMAJINASIKAN bila perlu menambahkan komponen lain agar terkesan tidak monoton.
"""

SYSTEM_PROMPT_REACT = """Kamu adalah expert React Developer spesialis landing page modern.

TUGAS: Buat HANYA dua elemen berikut untuk sebuah landing page modern dan profesional:
  1. <div id="root"></div>
  2. <script type="text/babel"> ... ReactDOM.createRoot(document.getElementById('root')).render(<App />) </script>

Assets sudah tersedia: React 18, ReactDOM 18, Babel Standalone, Tailwind CSS, Font Awesome, Poppins.

ATURAN KETAT:
1.  Output HANYA dua elemen di atas. DILARANG menulis apapun di luar itu.
    DILARANG: <!DOCTYPE>, <html>, <head>, <body>, <script src=...>, <link rel=...>
2.  WAJIB: Baris PERTAMA di dalam <script type="text/babel"> harus selalu:
    const { useState, useEffect, useRef } = React;
    React hooks (useState, useEffect, useRef, dsb) TIDAK tersedia sebagai global.
    Selalu destructure dari React di awal script, BUKAN di dalam komponen.
3.  Gunakan React functional components dengan useState dan useEffect.
4.  Tailwind CSS: gunakan className (BUKAN class) untuk semua styling.
    Warna primary sudah dikonfigurasi — gunakan: bg-primary, text-primary.
5.  Font Awesome: gunakan <i className="fa-solid fa-..."> langsung di JSX.
6.  Font 'Poppins' sudah menjadi default font.
7.  Path gambar RELATIF.
    Benar: src="hero.jpg"   style={{backgroundImage:"url('hero.jpg')"}}
    Salah: URL https:// apapun.
8.  Komponen WAJIB dalam <App> — tepat 5, tidak lebih:
    a. <Navbar>   sticky — transparan → bg-gray-900/95 saat scroll (useEffect + scroll listener)
    b. <Hero>     fullscreen — bg-image + dark overlay + headline + 1 tombol CTA
    c. <Features> 3 card dengan <img> masing-masing + teks singkat
    d. <About>    <img> about di samping paragraf deskripsi
    e. <Footer>   gelap, info kontak fiktif relevan, icon sosmed
9.  Kode RINGKAS: 1 file, semua komponen dalam 1 <script type="text/babel">.
10. IMAJINASIKAN bila perlu menambahkan komponen lain agar terkesan tidak monoton.
"""


def get_system_prompt(template: str = "vanilla") -> str:
    """Kembalikan system prompt sesuai template type."""
    return SYSTEM_PROMPT_REACT if template == "react" else SYSTEM_PROMPT

def build_html_prompt(raw_name, description, page_title, color_theme, color_name,
                      photo_filenames, language="id"):
    lang_note = ("Semua teks konten dalam Bahasa Indonesia."
                 if language == "id" else "All content text in English.")
    hero = photo_filenames.get("hero", "hero.jpg")
    f1   = photo_filenames.get("feature_1", hero)
    f2   = photo_filenames.get("feature_2", hero)
    f3   = photo_filenames.get("feature_3", hero)
    ab   = photo_filenames.get("about", hero)
    return f"""Buat konten body landing page untuk proyek berikut:

NAMA PROYEK : {raw_name}
DESKRIPSI   : {description}
WARNA UTAMA : {color_theme} ({color_name}) → gunakan bg-primary / text-primary

PATH GAMBAR LOKAL (file ada di folder SAMA dengan HTML, gunakan PERSIS):
- hero   : {hero}
- fitur1 : {f1}
- fitur2 : {f2}
- fitur3 : {f3}
- about  : {ab}

PENTING:
- Hero: style="background-image:url('{hero}')" fullscreen + overlay gelap.
- Features: <img src="{f1}">, <img src="{f2}">, <img src="{f3}"> di masing-masing card.
- About: <img src="{ab}">.{lang_note}
"""

def scan_photos(photos_dir: str) -> list:
    photos_dir = os.path.expanduser(photos_dir)
    if not os.path.isdir(photos_dir): return []
    photos = []
    for f in sorted(Path(photos_dir).iterdir()):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
            photos.append({"name": f.name, "path": str(f),
                           "size_kb": f.stat().st_size // 1024})
    return photos

def select_photos_cli(photos: list, photos_dir: str) -> dict:
    print(f"  Foto tersedia di {os.path.expanduser(photos_dir)}:\n")
    for i, p in enumerate(photos, 1):
        print(f"    [{i:2}]  {p['name']}  ({p['size_kb']} KB)")
    print()
    def pick(prompt_text, required=True):
        while True:
            raw = input(f"  >> {prompt_text}: ").strip()
            if not raw:
                if required: print("       [!] Wajib diisi.\n"); continue
                return None
            if not raw.isdigit() or not (1 <= int(raw) <= len(photos)):
                print(f"       [!] Angka 1-{len(photos)}.\n"); continue
            return photos[int(raw)-1]["path"]
    print("  Pilih foto dengan mengetik nomornya.\n")
    hero = pick("Foto UTAMA / Hero (wajib)", required=True)
    print()
    print("  Foto Fitur & About — opsional (Enter = pakai foto utama):")
    f1 = pick("Foto Fitur 1 (Enter = hero)", required=False) or hero
    f2 = pick("Foto Fitur 2 (Enter = hero)", required=False) or hero
    f3 = pick("Foto Fitur 3 (Enter = hero)", required=False) or hero
    ab = pick("Foto About/Gallery (Enter = hero)", required=False) or hero
    return {"hero": hero, "feature_1": f1, "feature_2": f2, "feature_3": f3, "about": ab}

def main():
    env        = load_env(".env")
    api_key    = env.get("OPENROUTER_API_KEY", "")
    model      = env.get("AI_MODEL", "google/gemini-2.5-flash")
    language   = env.get("LANGUAGE", "id")
    output_dir = env.get("OUTPUT_DIR", "output")
    app_name   = env.get("APP_NAME", "Landing Page Generator")
    photos_dir = env.get("PHOTOS_DIR", "~/Pictures")
    max_tokens    = int(env.get("MAX_TOKENS", "6000"))
    template_type = env.get("TEMPLATE_TYPE", "vanilla").strip().lower()
    if not api_key:
        print("[ERROR] OPENROUTER_API_KEY tidak ditemukan di .env"); sys.exit(1)
    print()
    print("=" * 60)
    print(f"  {app_name}")
    print(f"  Model : {model}")
    print(f"  Template: {template_type}")
    print("=" * 60 + "\n")

    print("STEP 1 — Nama Proyek\n")
    while True:
        raw_name = input("  >> Nama proyek: ").strip()
        if not raw_name: print("     [!] Tidak boleh kosong.\n"); continue
        filename_base = parse_filename(raw_name)
        if not filename_base: print("     [!] Nama tidak valid.\n"); continue
        if is_duplicate(output_dir, filename_base):
            print(f"     [!] Folder '{filename_base}/' sudah ada.\n"); continue
        print(f"     OK  Folder: {output_dir}/{filename_base}/\n"); break

    print("STEP 2 — Pilih Foto Lokal")
    photos = scan_photos(photos_dir)
    if not photos:
        print(f"  [!] Tidak ada foto di '{photos_dir}'. Ubah PHOTOS_DIR di .env"); sys.exit(1)
    selected_paths = select_photos_cli(photos, photos_dir)
    print()

    print("STEP 3 — Deskripsi Landing Page\n")
    while True:
        description = input("  >> Deskripsi: ").strip()
        if description: break
        print("     [!] Tidak boleh kosong.\n")

    print("\nSTEP 4 — Analisis Tema ...")
    meta        = analyze_prompt(api_key, model, description, filename_base)
    page_title  = meta.get("page_title", raw_name)
    color_theme = meta.get("color_theme", "#3B82F6")
    color_name  = meta.get("color_name", "blue")
    print(f"     OK  Judul : {page_title}")
    print(f"     OK  Warna : {color_theme} ({color_name})")

    print("\nSTEP 5 — Setup Folder & Salin Foto ...")
    project_dir     = setup_project_dir(output_dir, filename_base)
    photo_filenames = copy_photos_to_project(selected_paths, project_dir)
    for k, v in photo_filenames.items():
        print(f"     OK  {k:10}: {v}")

    print("\nSTEP 6 — Generate Landing Page ...")
    prompt    = build_html_prompt(raw_name, description, page_title,
                                  color_theme, color_name, photo_filenames, language)
    raw_out   = call_ai(api_key, model, get_system_prompt(template_type), prompt,
                        max_tokens=max_tokens)
    html_code = extract_code(raw_out)
    filepath  = save_project_html(html_code, project_dir, page_title, color_theme,
                                  language, template_type)

    print()
    print("=" * 60)
    print("  SELESAI!")
    print(f"  Folder : {project_dir}/")
    print(f"  HTML   : {filepath}")
    print(f"  Title  : {page_title}")
    print("=" * 60)

    if input("\nBuka di browser? (y/n): ").strip().lower() == "y":
        import subprocess
        abs_path = os.path.abspath(filepath)
        try: subprocess.Popen(["xdg-open", abs_path])
        except Exception:
            try: subprocess.Popen(["open", abs_path])
            except Exception: print(f"Buka manual: file://{abs_path}")

if __name__ == "__main__":
    main()
