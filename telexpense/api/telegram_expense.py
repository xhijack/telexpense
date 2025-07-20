# your_app/api/telegram.py

import os
import json
import requests
from io import BytesIO
from PIL import Image
import google.generativeai as genai
import frappe

# Inisialisasi Gemini sekali di module
# API_KEY = frappe.conf.get("google_api_key") or os.getenv("GOOGLE_API_KEY")
# if not API_KEY:
#     frappe.throw("GOOGLE_API_KEY belum di-set di site_config.json atau environment.")

# genai.configure(api_key=API_KEY)
# MODEL = genai.GenerativeModel("gemini-1.5-flash")


def _extract_json(raw_text: str) -> str:
    """Ambil blok JSON dari markdown ```json ...``` atau kembalikan raw."""
    import re
    m = re.search(r'```json\n(.*)\n```', raw_text, re.DOTALL)
    return m.group(1).strip() if m else raw_text.strip()


@frappe.whitelist(allow_guest=True)
def telegram_webhook():
    tele_setting = frappe.get_doc("Telegram Expense Setting","Telegram Expense Setting")
    if tele_setting.ai_enabled == 1:
        """Webhook endpoint untuk Telegram + Gemini receipt OCR."""
        # 1) Terima payload
        data = frappe.request.get_json() or {}
        msg  = data.get("message", {})

        chat_id = msg.get("chat", {}).get("id")
        if not chat_id:
            frappe.response["message"] = "no chat_id"
            return

        # 2) Ambil Bot token terenkripsi
        bots = frappe.get_all("Telegram Bot", pluck="name")
        if not bots:
            frappe.throw("Tidak ada Telegram Bot yang aktif")
        bot = frappe.get_doc("Telegram Bot", bots[0])
        token = bot.get_password("api_token")

        reply_text = None

        # 3) Jika user kirim foto
        if msg.get("photo"):
            # ambil file_id foto terbesar
            photo = max(msg["photo"], key=lambda p: p.get("file_size", 0))
            file_id = photo["file_id"]

            # get file_path dari Telegram
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": file_id}
            ).json()
            file_path = r["result"]["file_path"]
            file_url  = f"https://api.telegram.org/file/bot{token}/{file_path}"

            # download dan load image
            resp = requests.get(file_url)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content))

            # 4) Susun prompt untuk Gemini
            expense_categories = frappe.get_all("Expense Category", pluck="name")
            prompt = (
                "Dari struk ini, identifikasi jenis transaksi "
                "(misalnya: BBM di Cimahi, Makan di Restauran Pak Unang, Tol Jagorawi, dll) sebagai 'description' "
                "kemudian tambahkan 'expense_category' sesuai dengan yang disini  {0}".format(expense_categories) +
                "dan total jumlah pembayaran sebagai 'amount'. Perhatikan angkanya terkadang bentuknya 800.000,00 atau 800,000.00. itu artinya tetap 800000\n"
                "Sajikan hasilnya dalam format JSON berikut:\n"
                '{"description": "string","expense_category": "string", "amount": float}\n'
                "Output harus HANYA objek JSON tanpa teks tambahan."
            )

            # 5) Kirim ke Gemini
            API_KEY = tele_setting.get_password("api_key")
            genai.configure(api_key=API_KEY)
            MODEL = genai.GenerativeModel("gemini-1.5-flash")

            gem_resp = MODEL.generate_content([prompt, img])

            # 6) Ekstrak JSON dan parse
            js = _extract_json(gem_resp.text)
            try:
                result = json.loads(js)
                
                expense_log = frappe.get_doc({
                    "doctype": "Telegram Expense Log",
                    "telegram_user": chat_id,
                    "description": result.get("description"),
                    "expense_category": result.get("expense_category"),
                    "amount": result.get("amount"),
                    "user": frappe.db.get_value("Telegram User", {"telegram_user_id": chat_id}, "user") or frappe.session.user
                })

                expense_log.insert(ignore_permissions=True)

                # setelah kamu insert Telegram Expense Log (expense_log)
                # dan sudah punya `file_url`
                file_doc = frappe.get_doc({
                    "doctype": "File",
                    "file_name": file_url.split("/")[-1],
                    "file_url": file_url,
                    "attached_to_doctype": "Telegram Expense Log",
                    "attached_to_name": expense_log.name
                }).insert(ignore_permissions=True)


                # format ulang sebagai string untuk dikirim
                # reply_text = json.dumps(result, ensure_ascii=False)
                reply_text = (
                    f"Transaksi berhasil disimpan\n"
                    f"Deskripsi: {result.get('description')}\n"
                    f"Kategori: {result.get('expense_category')}\n"
                    f"Jumlah: {result.get('amount')}"
                )
            except ValueError:
                # kalau parsing gagal, kirim mentah saja
                reply_text = f"Error parsing JSON:\n{js}"

        else:
            # 7) Kalau text biasa → echo
            text = msg.get("text", "")
            reply_text = f"Kamu bilang: {text}"

        # 8) Kirim balasan ke Telegram
        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(send_url, json={
            "chat_id": chat_id,
            "text": reply_text
        })

        # 9) Respon 200 OK
        frappe.response["message"] = "ok"
