# Copyright (c) 2025, PT Sopwer Teknologi Indonesia and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TelegramIncomeLog(Document):
	def create_payment_entry(self):

		# Assume self.sales_invoice is the Sales Invoice reference
		if not self.invoice:
			return

		si = frappe.get_doc("Sales Invoice", self.invoice)
		if si.outstanding_amount <= 0:
			return
		default_bank_account = frappe.get_cached_value("Company", si.company, "default_bank_account")
		payment_entry = frappe.get_doc({
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"party_type": "Customer",
			"party": si.customer,
			"company": si.company,
			"posting_date": frappe.utils.nowdate(),
			"paid_amount": si.outstanding_amount,
			"received_amount": si.outstanding_amount,
			"paid_from": si.debit_to,
			"paid_to": default_bank_account,
			"reference_no": self.name,
			"reference_date": frappe.utils.nowdate(),
			"target_exchange_rate": 1.0,
			"references": [{
				"reference_doctype": "Sales Invoice",
				"reference_name": si.name,
				"total_amount": si.grand_total,
				"outstanding_amount": si.outstanding_amount,
				"allocated_amount": si.outstanding_amount
			}]
		})
		payment_entry.insert()
		payment_entry.submit()
		return payment_entry.name

	def on_submit(self):
		pe_name = self.create_payment_entry()
		frappe.db.set_value('Telegram Income Log', self.name, 'payment_entry',pe_name)
		frappe.db.commit()

