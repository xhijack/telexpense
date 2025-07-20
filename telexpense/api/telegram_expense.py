import os
import json
import re
import requests
from io import BytesIO
from PIL import Image

import frappe
import google.generativeai as genai

# Constants
MODEL_NAME = "gemini-1.5-flash"
FILE_DOWNLOAD_URL = "https://api.telegram.org/file/bot{token}/{file_path}"
SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"
GET_FILE_URL = "https://api.telegram.org/bot{token}/getFile"
JSON_BLOCK_REGEX = r'```json\n(.*)\n```'


def _init_gemini(api_key: str):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL_NAME)


def _extract_json(raw_text: str) -> str:
    match = re.search(JSON_BLOCK_REGEX, raw_text, re.DOTALL)
    return match.group(1).strip() if match else raw_text.strip()


def _download_telegram_file(token: str, file_id: str) -> Image.Image:
    # Get file path
    resp = requests.get(GET_FILE_URL.format(token=token), params={"file_id": file_id})
    resp.raise_for_status()
    file_path = resp.json()["result"]["file_path"]

    # Download image
    download_url = FILE_DOWNLOAD_URL.format(token=token, file_path=file_path)
    img_resp = requests.get(download_url)
    img_resp.raise_for_status()
    return Image.open(BytesIO(img_resp.content)), download_url


def _log_expense(result: dict, chat_id: int, file_url: str) -> frappe.Document:
    user = frappe.db.get_value("Telegram User", {"telegram_user_id": chat_id}, "user") or frappe.session.user
    log = frappe.get_doc({
        "doctype": "Telegram Expense Log",
        "telegram_user": chat_id,
        "description": result.get("description"),
        "expense_category": result.get("expense_category"),
        "amount": result.get("amount"),
        "user": user
    }).insert(ignore_permissions=True)

    # Attach original image
    frappe.get_doc({
        "doctype": "File",
        "file_name": os.path.basename(file_url),
        "file_url": file_url,
        "attached_to_doctype": "Telegram Expense Log",
        "attached_to_name": log.name
    }).insert(ignore_permissions=True)

    return log


def _compose_reply(result: dict) -> str:
    # Format amount
    amount = result.get("amount", 0)
    formatted_amount = f"Rp{amount:,.0f}".replace(",", ".")

    return (
        "✅ Transaksi berhasil disimpan:\n"
        f"Deskripsi: {result.get('description', '-')}\n"
        f"Kategori: {result.get('expense_category', '-')}\n"
        f"Jumlah: {formatted_amount}"
    )


def _send_telegram_message(token: str, chat_id: int, text: str):
    requests.post(
        SEND_MESSAGE_URL.format(token=token),
        json={"chat_id": chat_id, "text": text}
    )


@frappe.whitelist(allow_guest=True)
def telegram_webhook():
    """
    Webhook endpoint for Telegram -> Gemini OCR -> Expense Logging
    """
    try:
        setting = frappe.get_doc("Telegram Expense Setting", "Telegram Expense Setting")
        if not setting.ai_enabled:
            frappe.response["message"] = "AI processing disabled"
            return

        payload = frappe.request.get_json(force=True) or {}
        msg = payload.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        if not chat_id:
            frappe.response["message"] = "no chat_id"
            return

        # Get bot token
        bot_names = frappe.get_all("Telegram Bot", filters={"enabled": 1}, pluck="name")
        if not bot_names:
            frappe.throw("No active Telegram Bot found")
        bot = frappe.get_doc("Telegram Bot", bot_names[0])
        token = bot.get_password("api_token")

        # Handle photo
        if msg.get("photo"):
            photo = max(msg["photo"], key=lambda p: p.get("file_size", 0))
            img, file_url = _download_telegram_file(token, photo["file_id"])

            # Prepare Gemini
            ai_key = setting.get_password("api_key")
            model = _init_gemini(ai_key)

            categories = frappe.get_all("Expense Category", pluck="name")
            prompt = (
                f"Dari struk ini, identifikasi jenis transaksi sebagai 'description', "
                f"pilih kategori dari {categories} sebagai 'expense_category', dan "
                "total pembayaran sebagai 'amount'. Format JSON:"
                "{'description':'string','expense_category':'string','amount':float}" 
            )
            gem_resp = model.generate_content([prompt, img])

            js = _extract_json(gem_resp.text)
            result = json.loads(js)

            # Log expense and attach file
            _log_expense(result, chat_id, file_url)
            reply_text = _compose_reply(result)
        else:
            # Echo text
            text = msg.get("text", "")
            reply_text = f"Kamu bilang: {text}"

        _send_telegram_message(token, chat_id, reply_text)
        frappe.response["message"] = "ok"

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Telegram Webhook Error")
        frappe.response["message"] = str(e)
