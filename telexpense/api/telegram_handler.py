import requests
import frappe
from frappe.utils import fmt_money


@frappe.whitelist(allow_guest=True)
def telegram_webhook(telegram_bot, updater):
    print("Telegram webhook called")

@frappe.whitelist(allow_guest=True)
def incoming_chat(update, context):
    """
    Pre-processor untuk Telegram Bot (polling).
    Cek apakah chat_id sudah terdaftar sebagai Telegram User di ERPNext.
    Jika belum, kirim instruksi /login dan hentikan pemrosesan selanjutnya.
    """
    # Ambil chat_id dari update
    chat_id = None
    if update.effective_chat:
        chat_id = update.effective_chat.id
    elif update.message and update.message.chat:
        chat_id = update.message.chat.id

    msg = update.message
    # Jika chat_id tidak ditemukan, kita hentikan
    if not chat_id:
        frappe.response["message"] = "no chat_id"
        return

    bot_name = frappe.get_all("Telegram Bot", pluck="name")[0]
    bot = frappe.get_doc("Telegram Bot", bot_name)
    token = bot.get_password("api_token")

    reply_text = ""

    # ========== 1. Perintah /cek_tagihan ==========
    customer = get_customer_by_telegram_user_id(chat_id)
    user_id = frappe.db.get_value("Telegram User", {"telegram_user_id": chat_id}, "user")
    if msg.text == "/cek_tagihan":

        if not customer:
            reply_text = "Data Anda belum terdaftar. Mohon hubungi admin RT."
        else:
            invoices = frappe.get_all("Sales Invoice", filters={
                "customer": customer.customer_name,
                "docstatus": 1,
                "outstanding_amount": [">", 0]
            }, fields=["name", "posting_date", "due_date", "outstanding_amount"])

            if not invoices:
                reply_text = "✅ Anda tidak memiliki tagihan aktif."
            else:
                reply_text = "Berikut daftar tagihan Anda:\n\n"
                for inv in invoices:
                    reply_text += (
                        f"📄 *{inv.name}*\n"
                        f"Tgl: {inv.posting_date}, Jatuh Tempo: {inv.due_date}\n"
                        f"Jumlah: {fmt_money(inv.outstanding_amount, currency='IDR')}\n\n"
                    )

    # ========== 2. Perintah /bukti_transfer ==========
    elif msg.text == "/bukti_transfer":
        frappe.cache().set(f"tg_state_{chat_id}", "waiting_image")
        reply_text = "Silakan kirim foto bukti transfer."

    # ========== 3. Gambar masuk ==========
    elif msg.text == None:
        state = frappe.cache().get(f"tg_state_{chat_id}")
        if state != b"waiting_image":
            reply_text = "Silakan ketik /bukti_transfer dulu sebelum kirim foto."
        else:
            frappe.cache().delete(f"tg_state_{chat_id}")

            # Ambil foto terbesar
            photo = max(msg.photo, key=lambda p: p.file_size)
            file_id = photo.file_id

            # Ambil file_path dari Telegram
            file_info = requests.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": file_id}
            ).json()
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"

            # Simpan dokumen Telegram Payment Submission
            if not customer:
                reply_text = "Data Anda belum terdaftar. Hubungi admin RT."
            else:
                invoices = frappe.get_all("Sales Invoice", filters={
                    "customer": customer.customer_name,
                    "docstatus": 1,
                    "outstanding_amount": [">", 0]
                }, fields=["name", "posting_date", "outstanding_amount"], order_by="posting_date asc")

                matched_invoice = invoices[0]["name"] if invoices else None

                doc = frappe.get_doc({
                    "doctype": "Telegram Income Log",
                    "telegram_user_id": chat_id,
                    "user": user_id,
                    "customer": customer.customer_name,
                    "invoice": matched_invoice,
                    "image": file_url,
                })
                doc.insert(ignore_permissions=True)

                frappe.get_doc({
                    "doctype": "File",
                    "file_name": file_url.split("/")[-1],
                    "file_url": file_url,
                    "attached_to_doctype": "Telegram Income Log",
                    "attached_to_name": doc.name
                }).insert(ignore_permissions=True)

                reply_text = (
                    f"📥 Bukti transfer berhasil disimpan.\n"
                    f"Invoice: {matched_invoice or 'Belum terdeteksi'}\n"
                    f"Akan segera diverifikasi oleh admin."
                )

    # ========== 4. Pesan selain perintah ==========
    else:
        reply_text = "Perintah tidak dikenali. Gunakan Sesuai yang ada pada menu"

    # ========== 5. Kirim balasan ke Telegram ==========
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": reply_text, "parse_mode": "HTML"}
    )
    frappe.response["message"] = "ok"

def get_customer_by_telegram_user_id(telegram_user_id: str):
    result = frappe.db.sql("""
        SELECT
            tu.name AS telegram_user_doc,
            tu.telegram_user_id,
            tu.user AS system_user,
            c.name AS customer_id,
            c.customer_name
        FROM
            `tabTelegram User` tu
        LEFT JOIN
            `tabUser` u ON u.name = tu.user
        LEFT JOIN
            `tabPortal User` pu ON pu.user = u.name
        LEFT JOIN
            `tabCustomer` c ON c.name = pu.parent
        WHERE
            tu.telegram_user_id = %s
        LIMIT 1
    """, (telegram_user_id,), as_dict=True)

    return result[0] if result else {}