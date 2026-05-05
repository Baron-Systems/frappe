frappe.provide("frappe.ui.misc");
frappe.ui.misc.about = function () {
	if (frappe.ui.misc.about_dialog) {
		frappe.ui.misc.about_dialog.show();
		return;
	}

	const dialog = new frappe.ui.Dialog({ title: __("AL Baron E-R-P") });

	$(dialog.body).html(
		`<div>
				<p>${__("ALBaron Systems – Smart Business Solutions for the Digital Era")}</p>

				<p>
					<i class='fa fa-globe fa-fw'></i>
					${__("Website")}:
					<a href='https://albaronsystems.com/' target='_blank'>https://albaronsystems.com/</a>
				</p>
				
				<p>
					<i class='fa fa-facebook fa-fw'></i>
					${__("Facebook")}:
					<a href='https://www.facebook.com/share/1XLW9nBbQ6/?mibextid=wwXIfr' target='_blank'>https://www.facebook.com/share/1XLW9nBbQ6/?mibextid=wwXIfr</a>
				</p>

				<hr>

				<p class='text-muted'>${__("&copy; AL Baron Systems ")} </p>
			</div>`
	);

	frappe.ui.misc.about_dialog = dialog;

	frappe.ui.misc.about_dialog.on_page_show = function () {
		if (!frappe.versions) {
			frappe.call({
				method: "frappe.utils.change_log.get_versions",
				callback: function (r) {
					show_versions(r.message);
				},
			});
		} else {
			show_versions(frappe.versions);
		}
	};

	const show_versions = function (versions) {
		const $wrap = $("#about-app-versions").empty();
		let app = {};

		function get_version_text(app) {
			if (app.branch) {
				return `v${app.branch_version || app.version} (${app.branch})`;
			} else {
				return `v${app.version}`;
			}
		}

		for (const app_name in versions) {
			app = versions[app_name];
			const title = `${app_name}: ${app.branch_version || app.version}`;
			const text = `<p class='app-version' role='button' title='${title}'>
							<b>${app.title}:</b> ${get_version_text(app)}
						</p>`;
			$(text).appendTo($wrap);
		}

		frappe.versions = versions;

		if (frappe.versions) {
			$(dialog.body).find("#copy-apps-info").removeClass("hidden");
		}
	};

	const code_block = (snippet, lang = "") => "```" + lang + "\n" + snippet + "\n```";

	// Listener for copying installed apps info
	$(dialog.body).on("click", "#copy-apps-info", function () {
		if (!frappe.versions) return;

		const versions = Object.entries(frappe.versions).reduce((acc, [key, app]) => {
			acc[key] = app.branch_version || app.version;
			return acc;
		}, {});

		frappe.utils.copy_to_clipboard(code_block(JSON.stringify(versions, null, "\t"), "json"));
	});

	// Listener for copy app version
	$(dialog.body).on("click", ".app-version", function () {
		const title = $(this).attr("title");
		if (title) {
			frappe.utils.copy_to_clipboard(title);
		}
	});

	frappe.ui.misc.about_dialog.show();
};
