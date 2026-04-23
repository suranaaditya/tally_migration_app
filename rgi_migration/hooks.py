app_name = "rgi_migration"
app_title = "Rgi Migration"
app_publisher = "Dux Digitech"
app_description = "Tally to ERPNext opening balance migration for RGI entities"
app_email = "aditya.surana@thesvsgroup.org"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "rgi_migration",
# 		"logo": "/assets/rgi_migration/logo.png",
# 		"title": "Rgi Migration",
# 		"route": "/rgi_migration",
# 		"has_permission": "rgi_migration.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/rgi_migration/css/rgi_migration.css"
# app_include_js = "/assets/rgi_migration/js/rgi_migration.js"

# include js, css files in header of web template
# web_include_css = "/assets/rgi_migration/css/rgi_migration.css"
# web_include_js = "/assets/rgi_migration/js/rgi_migration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "rgi_migration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "rgi_migration/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "rgi_migration.utils.jinja_methods",
# 	"filters": "rgi_migration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "rgi_migration.install.before_install"
# after_install = "rgi_migration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "rgi_migration.uninstall.before_uninstall"
# after_uninstall = "rgi_migration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "rgi_migration.utils.before_app_install"
# after_app_install = "rgi_migration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "rgi_migration.utils.before_app_uninstall"
# after_app_uninstall = "rgi_migration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "rgi_migration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events
#
# Item 4 Commit 3: Account + Supplier on_trash hooks nullify our app's
# Link references (Mapping Decision.final_account / proposed_account /
# final_supplier / proposed_supplier, ACR.created_account,
# SCR.created_supplier) BEFORE Frappe's check_if_doc_is_linked runs.
# Without this, Frappe refuses to delete an Account / Supplier that
# any migration-era decision or request row ever referenced, blocking
# legitimate post-migration COA / vendor-master cleanup.
#
# Execution order (verified from frappe/model/delete_doc.py):
#   doc.run_method("on_trash")     # ← our hook nullifies our Links
#   doc.flags.in_delete = True
#   doc.run_method("on_change")
#   check_if_doc_is_linked(doc)    # ← now finds zero references, allows delete
#
# If delete fails after our nullification (e.g., links from OTHER apps
# still block), Frappe rolls back the transaction and our nullification
# is reverted too — so we never end up with nullified Links on a
# still-alive Account/Supplier. Safe.

doc_events = {
	"Account": {
		"on_trash": "rgi_migration.hooks_impl.clear_account_links",
	},
	"Supplier": {
		"on_trash": "rgi_migration.hooks_impl.clear_supplier_links",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"rgi_migration.tasks.all"
# 	],
# 	"daily": [
# 		"rgi_migration.tasks.daily"
# 	],
# 	"hourly": [
# 		"rgi_migration.tasks.hourly"
# 	],
# 	"weekly": [
# 		"rgi_migration.tasks.weekly"
# 	],
# 	"monthly": [
# 		"rgi_migration.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "rgi_migration.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "rgi_migration.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "rgi_migration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "rgi_migration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["rgi_migration.utils.before_request"]
# after_request = ["rgi_migration.utils.after_request"]

# Job Events
# ----------
# before_job = ["rgi_migration.utils.before_job"]
# after_job = ["rgi_migration.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"rgi_migration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

