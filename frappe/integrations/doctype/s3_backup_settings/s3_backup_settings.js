// Copyright (c) 2026, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on("S3 Backup Settings", {
	refresh: function (frm) {
		frm.clear_custom_buttons();
		frm.events.take_backup(frm);
		frm.events.update_next_backup(frm);
	},

	onload: function (frm) {
		frm.events.update_next_backup(frm);
	},

	on_unload: function (frm) {
		if (frm._backup_timer) {
			clearInterval(frm._backup_timer);
			frm._backup_timer = null;
		}
	},

	update_next_backup: function (frm) {
		if (frm._backup_timer) {
			clearInterval(frm._backup_timer);
			frm._backup_timer = null;
		}

		if (!frm.doc.enabled || frm.doc.frequency === "None") {
			frm.dashboard.clear_headline();
			return;
		}

		frappe.call({
			method: "frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.get_next_backup_time",
			callback: function (r) {
				if (!r.message || !r.message.server_time) {
					frm.dashboard.clear_headline();
					return;
				}

				let total_seconds = frm.events.time_to_seconds(r.message.server_time);

				frm.dashboard.clear_headline();
				frm.dashboard.set_headline(
					__("Server time: {0}", [r.message.server_time]),
					"blue"
				);

				frm._backup_timer = setInterval(function () {
					total_seconds = (total_seconds + 1) % 86400;
					let server_time = frm.events.seconds_to_time(total_seconds);
					frm.dashboard.clear_headline();
					frm.dashboard.set_headline(
						__("Server time: {0}", [server_time]),
						"blue"
					);
				}, 1000);
			},
		});
	},

	time_to_seconds: function (time_str) {
		let [h, m, s] = time_str.split(":").map(Number);
		return h * 3600 + m * 60 + s;
	},

	seconds_to_time: function (total_seconds) {
		let h = Math.floor(total_seconds / 3600) % 24;
		let m = Math.floor((total_seconds % 3600) / 60);
		let s = total_seconds % 60;
		return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
	},

	take_backup: function (frm) {
		if (frm.doc.access_key_id && frm.doc.secret_access_key) {
			frm.add_custom_button(__("Take Backup Now"), function () {
				frm.dashboard.set_headline_alert("S3 Backup Started!");
				frappe.call({
					method: "frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backup",
					callback: function (r) {
						if (!r.exc) {
							frappe.msgprint(__("S3 Backup queued for processing!"));
							frm.dashboard.clear_headline();
						}
					},
				});
			}).addClass("btn-primary");
		}
	},
});
