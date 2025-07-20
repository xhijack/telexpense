import requests
import frappe


@frappe.whitelist(allow_guest=True) 
def telegram_webhook():
    # Server Script (Type: API, Method: telegram_webhook)
# 1) Baca payload Telegram
    data = frappe.request.get_json()

    # 2) Ambil chat_id dan teks
    chat_id = data.get("message", {}).get("chat", {}).get("id")
    text    = data.get("message", {}).get("text", "")


    bot_account = frappe.get_all("Telegram Bot")

    bot = frappe.get_doc("Telegram Bot", bot_account[0])
    token = bot.get_password('api_token')
    url   = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

    # 4) Beri tahu Telegram bahwa request sudah sukses
    frappe.response["message"] = "ok"
