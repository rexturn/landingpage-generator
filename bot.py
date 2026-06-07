#!/usr/bin/env python3
"""
Telegram Bot — Landing Page Generator
======================================
Jalankan: python3 bot.py

Alur di Telegram:
  /start  →  STEP 1: kirim nama proyek
          →  STEP 2: upload foto (auto-klasifikasi hero/fitur/about berdasarkan ukuran)
                     ketik /skip untuk lanjut setelah foto hero terupload
          →  STEP 3: ketik deskripsi landing page
          →  [AI generate...]
          →  Notifikasi: "Hallo landingpage kamu telah berhasil dibuat akses dengan {nama}.qtl.web.id"
"""

import os
import sys
import re
import tempfile
import threading
import requests as req

try:
    import telebot
    from telebot import types as tg_types
except ImportError:
    print("[ERROR] Library telebot belum terinstall.")
    print("        Jalankan: pip install pyTelegramBotAPI")
    sys.exit(1)

# Import core dari generator.py (harus ada di folder yang sama)
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)

from generator import (
    load_env, parse_filename, is_duplicate,
    generate_domain_slug,
    call_ai, analyze_prompt, extract_code,
    setup_project_dir, copy_photos_to_project,
    save_project_html, build_html_prompt,
    copy_assets_to_project, STATIC_DIR,
    get_system_prompt, SYSTEM_PROMPT,
)
from cloudflare import setup_subdomain, find_existing_record, get_zone_id
from caddy import configure_caddy_site

# ════════════════════════════════════════════════════════════
#  STATES & SESSION
# ════════════════════════════════════════════════════════════

S_NAME     = "waiting_name"
S_HERO     = "waiting_hero"
S_FEATURES = "waiting_features"
S_DESC     = "waiting_description"
S_BUSY     = "generating"

sessions: dict = {}   # {chat_id: session_dict}


def new_session() -> dict:
    return {
        "state":         S_NAME,
        "raw_name":      None,
        "filename_base": None,
        "photos":        {},     # {"hero": "/tmp/...", "feature_1": ..., ...}
        "tmp_files":     [],     # file temp yang perlu dihapus setelah selesai
        "feature_count": 0,
    }


def cleanup_tmp(session: dict):
    for f in session.get("tmp_files", []):
        try:
            os.remove(f)
        except Exception:
            pass


def fill_missing_photos(session: dict) -> dict:
    """Isi slot yang kosong dengan foto hero sebagai fallback."""
    hero = session["photos"].get("hero", "")
    return {
        "hero":      hero,
        "feature_1": session["photos"].get("feature_1", hero),
        "feature_2": session["photos"].get("feature_2", hero),
        "feature_3": session["photos"].get("feature_3", hero),
        "about":     session["photos"].get("about", hero),
    }


# ════════════════════════════════════════════════════════════
#  AUTO-KLASIFIKASI FOTO BERDASARKAN UKURAN
# ════════════════════════════════════════════════════════════

def classify_and_assign(width: int, height: int, session: dict) -> str:
    """
    Tentukan role foto berdasarkan aspect ratio dan slot yang tersedia.
    - Landscape (ratio >= 1.5) → hero, lalu feature
    - Square (0.75 - 1.5)      → feature, lalu about
    - Portrait (< 0.75)        → about, lalu feature
    """
    if not session["photos"].get("hero"):
        return "hero"   # selalu hero kalau belum ada

    ratio = (width / height) if (width > 0 and height > 0) else 1.0

    if ratio >= 1.5:                              # landscape
        role = "hero"                             # hero sudah ada? → feature
    elif ratio < 0.75:                            # portrait
        role = "about"                            # about kosong? → about
    else:                                         # square
        role = "feature"                          # feature kosong? → feature

    # Resolve ke slot yang benar-benar kosong
    if role in ("hero", "feature"):
        for slot in ["feature_1", "feature_2", "feature_3"]:
            if not session["photos"].get(slot):
                return slot
        if not session["photos"].get("about"):
            return "about"

    if role == "about":
        if not session["photos"].get("about"):
            return "about"
        for slot in ["feature_1", "feature_2", "feature_3"]:
            if not session["photos"].get(slot):
                return slot

    return None   # semua slot penuh


def role_label(role: str) -> str:
    labels = {
        "hero":      "Foto Utama (Hero)",
        "feature_1": "Foto Fitur 1",
        "feature_2": "Foto Fitur 2",
        "feature_3": "Foto Fitur 3",
        "about":     "Foto About/Gallery",
    }
    return labels.get(role, role)


def shape_note(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    ratio = width / height
    if ratio >= 1.5:   return "Landscape (cocok untuk banner/hero)"
    elif ratio < 0.75: return "Portrait (cocok untuk about/team)"
    else:              return "Square (cocok untuk card fitur)"


def slots_summary(session: dict) -> str:
    emojis = {"hero": "🖼", "feature_1": "1️⃣", "feature_2": "2️⃣",
               "feature_3": "3️⃣", "about": "🏢"}
    parts = []
    for key in ["hero", "feature_1", "feature_2", "feature_3", "about"]:
        mark = "✅" if session["photos"].get(key) else "⬜"
        parts.append(f"{mark} {emojis[key]} {role_label(key)}")
    return "\n".join(parts)


def all_slots_full(session: dict) -> bool:
    return all(session["photos"].get(k) for k in
               ["hero", "feature_1", "feature_2", "feature_3", "about"])


# ════════════════════════════════════════════════════════════
#  DOWNLOAD FOTO DARI TELEGRAM
# ════════════════════════════════════════════════════════════

def download_telegram_file(bot_instance, file_id: str) -> str:
    """Download file dari Telegram ke temp file, kembalikan path-nya."""
    file_info = bot_instance.get_file(file_id)
    file_url  = (f"https://api.telegram.org/file/"
                 f"bot{bot_instance.token}/{file_info.file_path}")
    resp = req.get(file_url, timeout=60)
    resp.raise_for_status()
    ext = os.path.splitext(file_info.file_path)[1] or ".jpg"
    tmp = tempfile.mktemp(suffix=ext)
    with open(tmp, "wb") as f:
        f.write(resp.content)
    return tmp


# ════════════════════════════════════════════════════════════
#  BOT FACTORY
# ════════════════════════════════════════════════════════════

def make_bot(env: dict):
    token      = env.get("API_TELEGRAM", "")
    output_dir = env.get("OUTPUT_DIR", "output")
    domain     = env.get("DOMAIN_SUFFIX", "qtl.web.id")
    api_key    = env.get("OPENROUTER_API_KEY", "")
    model      = env.get("AI_MODEL", "google/gemini-2.5-flash")
    language   = env.get("LANGUAGE", "id")
    # ── Cloudflare config ────────────────────────────────────────────────────
    cf_api_token  = env.get("CF_API_TOKEN", "")
    cf_account_id = env.get("CF_ACCOUNT_ID", "")
    cf_zone_id    = env.get("CF_ZONE_ID", "")
    cf_dns_target = env.get("CF_DNS_TARGET", "")
    cf_enabled    = bool(cf_api_token)
    max_tokens    = int(env.get("MAX_TOKENS", "6000"))
    template_type = env.get("TEMPLATE_TYPE", "vanilla").strip().lower()
    # ── Caddy config ─────────────────────────────────────────────────────────
    caddy_json    = env.get("CADDY_JSON", "/etc/caddy/caddy.json")
    caddy_enabled = bool(caddy_json)

    if not token:
        print("[ERROR] API_TELEGRAM tidak ditemukan di .env"); sys.exit(1)
    if not api_key:
        print("[ERROR] OPENROUTER_API_KEY tidak ditemukan di .env"); sys.exit(1)

    bot = telebot.TeleBot(token, parse_mode="Markdown")

    # ── /start ────────────────────────────────────────────────────────────────
    @bot.message_handler(commands=["start", "baru", "reset"])
    def cmd_start(msg):
        chat_id = msg.chat.id
        sessions[chat_id] = new_session()
        bot.send_message(chat_id,
            "👋 *Halo! Selamat datang di Landing Page Generator*\n\n"
            "Saya akan membantu kamu membuat landing page keren dalam 3 langkah.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "*STEP 1 — Nama Proyek*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Ketik *nama proyek* kamu:\n"
            "_Contoh: Toko Baju Online_\n\n"
            "💡 Nama ini akan menjadi nama folder dan URL landing page kamu."
        )

    # ── /skip ─────────────────────────────────────────────────────────────────
    @bot.message_handler(commands=["skip", "lanjut", "done"])
    def cmd_skip(msg):
        chat_id = msg.chat.id
        session = sessions.get(chat_id)
        if not session:
            bot.send_message(chat_id, "Ketik /start untuk memulai."); return

        if session["state"] == S_FEATURES:
            if not session["photos"].get("hero"):
                bot.send_message(chat_id,
                    "⚠️ Foto *hero/utama* belum diupload.\n"
                    "Kirim setidaknya 1 foto untuk melanjutkan."
                ); return
            session["state"] = S_DESC
            bot.send_message(chat_id,
                f"✅ Foto terkumpul:\n{slots_summary(session)}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "*STEP 3 — Deskripsi*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Deskripsikan landing page yang kamu inginkan:\n\n"
                "_Contoh: Toko baju muslimah, warna pink pastel, "
                "target ibu-ibu muda kota, tagline: Cantik itu Mudah_"
            )
        else:
            bot.send_message(chat_id, "Tidak ada langkah yang bisa di-skip sekarang.")

    # ── /status ───────────────────────────────────────────────────────────────
    @bot.message_handler(commands=["status"])
    def cmd_status(msg):
        chat_id = msg.chat.id
        session = sessions.get(chat_id)
        if not session:
            bot.send_message(chat_id, "Ketik /start untuk memulai."); return
        state_names = {
            S_NAME:     "Menunggu nama proyek",
            S_HERO:     "Menunggu foto utama",
            S_FEATURES: "Menunggu foto tambahan",
            S_DESC:     "Menunggu deskripsi",
            S_BUSY:     "Sedang generate...",
        }
        bot.send_message(chat_id,
            f"*Status sesi:* {state_names.get(session['state'], '?')}\n"
            f"*Proyek:* {session.get('raw_name') or '-'}\n\n"
            f"*Foto:*\n{slots_summary(session)}"
        )

    # ── Text handler ──────────────────────────────────────────────────────────
    @bot.message_handler(content_types=["text"])
    def text_handler(msg):
        chat_id = msg.chat.id
        text    = msg.text.strip()

        if chat_id not in sessions:
            bot.send_message(chat_id, "Ketik /start untuk memulai."); return

        session = sessions[chat_id]
        state   = session["state"]

        # ── STEP 1: nama proyek ───────────────────────────────────────────────
        if state == S_NAME:
            raw_text = text.strip()
            if not re.search(r"[a-zA-Z0-9]", raw_text):
                bot.send_message(chat_id,
                    "⚠️ Nama tidak valid. Gunakan huruf dan angka.\n"
                    "Coba lagi:"
                ); return

            # Generate slug bersih via AI (tanpa underscore/spasi)
            bot.send_message(chat_id, "⏳ _Membuat nama domain yang cocok..._")
            slug = generate_domain_slug(api_key, model, raw_text)

            # ── Validasi tabrakan: folder lokal + DNS ─────────────────────────
            base_slug = slug
            counter   = 1
            _zid_cache: list = []          # cache zone_id agar tidak lookup 2x
            while True:
                # 1. Cek folder lokal
                if is_duplicate(output_dir, slug):
                    slug = f"{base_slug}{counter}"; counter += 1; continue
                # 2. Cek DNS Cloudflare
                if cf_enabled:
                    try:
                        if not _zid_cache:
                            _zid_cache.append(
                                cf_zone_id or get_zone_id(
                                    cf_api_token, domain, cf_account_id or None
                                )
                            )
                        fqdn = f"{slug}.{domain}"
                        if find_existing_record(cf_api_token, _zid_cache[0], fqdn):
                            slug = f"{base_slug}{counter}"; counter += 1; continue
                    except Exception:
                        pass   # jika CF gagal, lanjut tanpa DNS check
                break
            # ─────────────────────────────────────────────────────────────────

            session["raw_name"]      = raw_text
            session["filename_base"] = slug
            session["state"]         = S_HERO

            suffix_note = f" _(auto: `{slug}` sudah dipakai, diganti)_" if slug != base_slug else ""
            bot.send_message(chat_id,
                f"✅ Nama proyek: *{raw_text}*\n"
                f"🌐 Domain    : `{slug}.{domain}`{suffix_note}\n"
                f"📁 Folder    : `{output_dir}/{slug}/`\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "*STEP 2 — Upload Foto*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Upload *foto utama (hero)* kamu.\n\n"
                "💡 *Tips berdasarkan ukuran foto:*\n"
                "• 📐 Landscape (lebar)  → otomatis jadi *hero banner*\n"
                "• 📐 Square (kotak)     → otomatis jadi *card fitur*\n"
                "• 📐 Portrait (tinggi)  → otomatis jadi *about/team*\n\n"
                "Kirim foto pertama sekarang:"
            )

        # ── STEP 3: deskripsi + generate ─────────────────────────────────────
        elif state == S_DESC:
            if not session["photos"].get("hero"):
                session["state"] = S_HERO
                bot.send_message(chat_id, "⚠️ Foto hero belum ada. Upload foto dulu."); return

            session["state"] = S_BUSY
            description      = text

            bot.send_message(chat_id,
                "⏳ *Sedang generate landing page...*\n"
                "_Proses ini membutuhkan 30–90 detik, mohon tunggu._\n\n"
                "☕ Sambil nunggu, cek kopi dulu ya!"
            )

            def run_generation():
                try:
                    fn_base  = session["filename_base"]
                    raw_name = session["raw_name"]
                    selected = fill_missing_photos(session)

                    # Analisis tema
                    meta        = analyze_prompt(api_key, model, description, fn_base)
                    page_title  = meta.get("page_title", raw_name)
                    color_theme = meta.get("color_theme", "#3B82F6")
                    color_name  = meta.get("color_name", "blue")

                    # Setup folder & salin foto
                    project_dir     = setup_project_dir(output_dir, fn_base)
                    photo_filenames = copy_photos_to_project(selected, project_dir)

                    # Generate HTML
                    prompt   = build_html_prompt(raw_name, description, page_title,
                                                 color_theme, color_name,
                                                 photo_filenames, language)
                    raw_html = call_ai(api_key, model, get_system_prompt(template_type),
                                       prompt, max_tokens=max_tokens)
                    html     = extract_code(raw_html)
                    save_project_html(html, project_dir, page_title, color_theme,
                                      language, template_type)

                    # Bersihkan temp files
                    cleanup_tmp(session)

                    url       = f"https://{fn_base}.{domain}"
                    caddy_msg = ""
                    dns_msg   = ""

                    # ── Caddy web server config ───────────────────────────
                    if caddy_enabled:
                        bot.send_message(chat_id,
                            "🔧 *Mengatur Caddy web server...*"
                        )
                        try:
                            web_root     = os.path.abspath(project_dir)
                            caddy_result = configure_caddy_site(
                                fqdn            = f"{fn_base}.{domain}",
                                web_root        = web_root,
                                caddy_json_path = caddy_json,
                            )
                            caddy_msg = (
                                f"\n\n🔧 *Caddy berhasil dikonfigurasi!*\n"
                                f"  Site  : `{caddy_result['fqdn']}`\n"
                                f"  Root  : `{caddy_result['web_root']}`\n"
                                f"  Status: {caddy_result['action']}"
                            )
                        except Exception as caddy_err:
                            caddy_msg = (
                                f"\n\n⚠️ *Caddy config gagal:*\n"
                                f"`{str(caddy_err)[:800]}`"
                            )
                    # ─────────────────────────────────────────────────────

                    # ── Cloudflare DNS record ─────────────────────────────
                    if cf_enabled:
                        bot.send_message(chat_id,
                            "🌐 *Mendaftarkan DNS record di Cloudflare...*"
                        )
                        try:
                            dns = setup_subdomain(
                                api_token   = cf_api_token,
                                account_id  = cf_account_id,
                                zone_id     = cf_zone_id,
                                subdomain   = fn_base,
                                base_domain = domain,
                                target      = cf_dns_target,
                                proxied     = True,
                            )
                            dns_msg = (
                                f"\n\n🌐 *DNS Record berhasil {dns['action']}!*\n"
                                f"  Type   : `{dns['type']}`\n"
                                f"  Name   : `{dns['name']}`\n"
                                f"  Target : `{dns['content']}`\n"
                                f"  Proxy  : {'✅ aktif (Cloudflare CDN)' if dns['proxied'] else '⬜ bypass'}"
                            )
                        except Exception as cf_err:
                            dns_msg = (
                                f"\n\n⚠️ *DNS gagal didaftarkan:*\n"
                                f"`{str(cf_err)[:200]}`\n"
                                f"_Daftarkan manual di Cloudflare: `{fn_base}.{domain}`_"
                            )
                    # ─────────────────────────────────────────────────────

                    # Pesan sukses detail
                    bot.send_message(chat_id,
                        f"✅ *Landing page berhasil dibuat!*\n\n"
                        f"📁 Folder: `{output_dir}/{fn_base}/`\n"
                        f"📄 File  : `{output_dir}/{fn_base}/index.html`\n"
                        f"🎨 Judul : {page_title}\n"
                        f"🎨 Warna : {color_theme} ({color_name})"
                        + caddy_msg
                        + dns_msg
                        + f"\n\nKetik /start untuk membuat landing page baru."
                    )

                    # Notifikasi sesuai permintaan
                    bot.send_message(chat_id,
                        f"Hallo landingpage kamu telah berhasil dibuat "
                        f"akses dengan *{fn_base}.{domain}*"
                    )

                    if chat_id in sessions:
                        del sessions[chat_id]

                except Exception as e:
                    bot.send_message(chat_id,
                        f"❌ *Error saat generate:*\n`{str(e)}`\n\n"
                        "Ketik /start untuk coba lagi."
                    )
                    cleanup_tmp(session)
                    if chat_id in sessions:
                        del sessions[chat_id]

            thread = threading.Thread(target=run_generation, daemon=True)
            thread.start()

        elif state == S_BUSY:
            bot.send_message(chat_id, "⏳ Masih sedang generate, harap tunggu...")

        else:
            bot.send_message(chat_id,
                "📸 Saya sedang menunggu foto.\n"
                "Kirim foto atau ketik /skip untuk lanjut ke deskripsi."
            )

    # ── Photo / Document handler ──────────────────────────────────────────────
    @bot.message_handler(content_types=["photo", "document"])
    def photo_handler(msg):
        chat_id = msg.chat.id

        if chat_id not in sessions:
            bot.send_message(chat_id, "Ketik /start untuk memulai."); return

        session = sessions[chat_id]
        state   = session["state"]

        if state == S_BUSY:
            bot.send_message(chat_id, "⏳ Masih sedang generate..."); return

        if state == S_NAME:
            bot.send_message(chat_id,
                "⚠️ Masukkan *nama proyek* dulu sebelum upload foto."
            ); return

        if state == S_DESC:
            bot.send_message(chat_id,
                "⚠️ Saya sedang menunggu *deskripsi* (teks), bukan foto.\n"
                "Ketik deskripsi landing page kamu."
            ); return

        if state not in (S_HERO, S_FEATURES):
            bot.send_message(chat_id, "Ketik /start untuk memulai."); return

        # Download foto
        try:
            if msg.content_type == "photo":
                best    = msg.photo[-1]         # ambil resolusi terbesar
                file_id = best.file_id
                width   = best.width
                height  = best.height
            else:                               # document
                doc = msg.document
                if not doc.mime_type or not doc.mime_type.startswith("image/"):
                    bot.send_message(chat_id,
                        "⚠️ File bukan gambar. Kirim file gambar (jpg/png/webp)."
                    ); return
                file_id = doc.file_id
                width   = getattr(doc, "width",  0) or 0
                height  = getattr(doc, "height", 0) or 0

            bot.send_message(chat_id, "📥 Mengunduh foto...")
            tmp_path = download_telegram_file(bot, file_id)
            session["tmp_files"].append(tmp_path)

        except Exception as e:
            bot.send_message(chat_id, f"❌ Gagal mengunduh foto: {e}"); return

        # Tentukan role
        if state == S_HERO:
            # Foto pertama selalu jadi hero
            role = "hero"
            session["photos"]["hero"] = tmp_path
            session["state"] = S_FEATURES
            s_note = shape_note(width, height)
            size_str = f"{width}×{height}px" if width and height else "ukuran tidak diketahui"
            bot.send_message(chat_id,
                f"✅ *Foto hero tersimpan!*\n"
                f"📐 {s_note} ({size_str})\n\n"
                f"*Slot foto saat ini:*\n{slots_summary(session)}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "*STEP 2b — Foto Tambahan (Opsional)*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Upload foto tambahan untuk section *Fitur* dan *About*.\n"
                "Bot akan otomatis memilih slot berdasarkan ukuran foto.\n\n"
                "Kirim foto atau ketik /skip untuk lanjut ke deskripsi."
            )

        else:   # S_FEATURES
            role = classify_and_assign(width, height, session)

            if role is None:
                bot.send_message(chat_id,
                    "✅ Semua slot foto sudah terisi!\n"
                    "Ketik /skip untuk lanjut ke deskripsi."
                ); return

            if role.startswith("feature_"):
                session["feature_count"] += 1
            session["photos"][role] = tmp_path

            s_note   = shape_note(width, height)
            size_str = f"{width}×{height}px" if width and height else ""

            msg_text = (
                f"✅ *Tersimpan sebagai: {role_label(role)}*\n"
                f"📐 {s_note} {size_str}\n\n"
                f"*Slot foto saat ini:*\n{slots_summary(session)}\n\n"
            )

            if all_slots_full(session):
                msg_text += "✅ Semua slot terisi! Ketik /skip untuk lanjut."
            else:
                msg_text += "Kirim foto lagi atau ketik /skip untuk lanjut."

            bot.send_message(chat_id, msg_text)

    return bot


# ════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════

def main():
    # Cari .env di direktori yang sama dengan bot.py
    env_path = os.path.join(_dir, ".env")
    env      = load_env(env_path)

    webhook_url  = env.get("WEBHOOK_URL", "").rstrip("/")
    webhook_port = int(env.get("WEBHOOK_PORT", "5001"))
    token        = env.get("API_TELEGRAM", "")

    print()
    print("=" * 55)
    print("  Telegram Landing Page Bot")
    print(f"  Model : {env.get('AI_MODEL', 'google/gemini-2.5-flash')}")
    print(f"  Domain: *.{env.get('DOMAIN_SUFFIX', 'qtl.web.id')}")
    print(f"  Output: {env.get('OUTPUT_DIR', 'output')}/")
    print(f"  Mode  : {'Webhook :' + str(webhook_port) if webhook_url else 'Polling'}")
    print("=" * 55)
    print()

    bot = make_bot(env)

    if webhook_url:
        # ── Webhook mode (production / VPS) ──────────────────────────────
        # Telegram kirim update ke https://<webhook_url>/<token>
        # Bot listen di 0.0.0.0:5001 (di belakang nginx/reverse proxy)
        full_hook = f"{webhook_url}/{token}"
        print(f"[BOT] Webhook mode")
        print(f"      URL   : {full_hook[:60]}...")
        print(f"      Listen: 0.0.0.0:{webhook_port}")
        print()
        bot.remove_webhook()
        bot.run_webhooks(
            listen       = "0.0.0.0",
            port         = webhook_port,
            url_path     = f"/{token}",
            webhook_url  = full_hook,
            debug        = False,
        )
    else:
        # ── Polling mode (development / lokal) ───────────────────────────
        print("[BOT] Polling mode. Tekan Ctrl+C untuk stop.\n")
        bot.remove_webhook()
        bot.infinity_polling(timeout=30, long_polling_timeout=20)


if __name__ == "__main__":
    main()
