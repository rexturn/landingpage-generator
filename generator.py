#!/usr/bin/env python3
"""
Landing Page Generator — Core Library + CLI
"""
import os, sys, json, re, shutil, requests
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

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

def save_project_html(html_code: str, project_dir: str) -> str:
    Path(project_dir).mkdir(parents=True, exist_ok=True)
    filepath = os.path.join(project_dir, "index.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_code)
    return filepath

def call_ai(api_key: str, model: str, system_prompt: str, user_prompt: str,
            max_tokens: int = 6000) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
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
    match = re.search(r"```(?:html)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    return match.group(1).strip() if match else raw.strip()

SYSTEM_PROMPT = """Kamu adalah expert Front-End Developer spesialis landing page modern.

TUGAS: Buat sebuah file HTML TUNGGAL yang merupakan landing page modern, premium, profesional.
Gunakan React (via CDN), Tailwind CSS (via CDN), dan desain yang eye-catching.

ATURAN KETAT:
1.  Output HANYA kode HTML lengkap <!DOCTYPE html> sampai </html>. JANGAN ada teks lain.
2.  React 18 CDN    : https://unpkg.com/react@18/umd/react.production.min.js
3.  ReactDOM 18 CDN : https://unpkg.com/react-dom@18/umd/react-dom.production.min.js
4.  Babel Standalone: https://unpkg.com/@babel/standalone/babel.min.js
5.  Tailwind CSS    : https://cdn.tailwindcss.com
6.  Font Awesome    : https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css
7.  Google Fonts sesuai tema.
8.  Animasi smooth: CSS keyframes + React useState untuk scroll effects.
9.  Desain: modern, bersih, responsif (mobile-first), premium feel.
10. Path gambar adalah RELATIF di folder yang SAMA dengan file HTML.
    Contoh hero background yang BENAR di JSX:
    <div style={{backgroundImage: `url('hero.jpg')`, backgroundSize:'cover', backgroundPosition:'center'}}>
11. <title> browser HARUS sama persis dengan page_title di prompt.
12. Section WAJIB: Navbar sticky, Hero fullscreen, Features (3 card+gambar),
    How It Works, Gallery/About (gambar about), Testimonial (3 item), CTA, Footer.
13. Navbar: transparent -> glass morphism saat scroll (scroll event + useState).
14. Tombol CTA: gradient sesuai warna tema, shadow, hover lift animation.
15. Footer: gradient gelap, info kontak fiktif relevan, social media icons.
16. JANGAN pernah mengganti path gambar lokal dengan URL https://.
"""

def build_html_prompt(raw_name, description, page_title, color_theme, color_name,
                      photo_filenames, language="id"):
    lang_note = ("Semua teks konten dalam Bahasa Indonesia."
                 if language == "id" else "All content text in English.")
    hero = photo_filenames.get("hero", "hero.jpg")
    f1   = photo_filenames.get("feature_1", hero)
    f2   = photo_filenames.get("feature_2", hero)
    f3   = photo_filenames.get("feature_3", hero)
    ab   = photo_filenames.get("about", hero)
    return f"""Buat landing page untuk proyek berikut:

NAMA PROYEK : {raw_name}
DESKRIPSI   : {description}

METADATA WAJIB:
- <title> browser : {page_title}
- Warna utama     : {color_theme} ({color_name})

PATH GAMBAR LOKAL (file ada di folder yang SAMA dengan index.html, gunakan PERSIS seperti ini):
- hero_path  : {hero}
- feature_1  : {f1}
- feature_2  : {f2}
- feature_3  : {f3}
- about_path : {ab}

PENTING:
- Hero section: background-image url('{hero}') full-screen + dark overlay 50%.
- Feature cards: masing-masing pakai {f1}, {f2}, {f3} di <img src=...>.
- About/Gallery: <img src="{ab}">.
- JANGAN ganti path ini dengan URL https:// apapun.

INSTRUKSI TAMBAHAN:
- {lang_note}
- Desain premium dan modern. Micro-animations halus.
- 3 testimonial palsu yang relevan. Navbar glass morphism saat scroll.
- Footer gradient gelap dengan info kontak fiktif relevan.
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
    max_tokens = int(env.get("MAX_TOKENS", "6000"))
    if not api_key:
        print("[ERROR] OPENROUTER_API_KEY tidak ditemukan di .env"); sys.exit(1)
    print()
    print("=" * 60)
    print(f"  {app_name}")
    print(f"  Model : {model}")
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
    raw_out   = call_ai(api_key, model, SYSTEM_PROMPT, prompt, max_tokens=max_tokens)
    html_code = extract_code(raw_out)
    filepath  = save_project_html(html_code, project_dir)

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
