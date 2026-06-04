# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE
import os
import os.path

import boto3
import frappe
from botocore.exceptions import ClientError
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, split_emails
from frappe.utils.background_jobs import enqueue
from rq.timeouts import JobTimeoutException


class S3BackupSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_key_id: DF.Data
		backup_files: DF.Check
		backup_path: DF.Data | None
		bucket: DF.Data
		enabled: DF.Check
		endpoint_url: DF.Data | None
		frequency: DF.Literal["Daily", "Weekly", "Monthly", "None"]
		notify_email: DF.Data
		secret_access_key: DF.Password
		send_email_for_successful_backup: DF.Check
	# end: auto-generated types

	def validate(self):
		if not self.enabled:
			return

		if not self.endpoint_url:
			self.endpoint_url = "https://s3.amazonaws.com"

		if self.backup_path and self.backup_path[-1] != "/":
			self.backup_path += "/"

		conn = boto3.client(
			"s3",
			aws_access_key_id=self.access_key_id,
			aws_secret_access_key=self.get_password("secret_access_key"),
			endpoint_url=self.endpoint_url,
		)

		try:
			# Head_bucket returns a 200 OK if the bucket exists and have access to it.
			# Requires ListBucket permission
			conn.head_bucket(Bucket=self.bucket)
		except ClientError as e:
			error_code = e.response["Error"]["Code"]
			bucket_name = frappe.bold(self.bucket)
			if error_code == "403":
				msg = _("Do not have permission to access bucket {0}.").format(bucket_name)
			elif error_code == "404":
				msg = _("Bucket {0} not found.").format(bucket_name)
			else:
				msg = e.args[0]

			frappe.throw(msg)


@frappe.whitelist()
def take_backup():
	"""Enqueue longjob for taking backup to s3"""
	enqueue(
		"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_s3",
		queue="long",
		timeout=1500,
	)
	frappe.msgprint(_("Queued for backup. It may take a few minutes to an hour."))


def take_backups_daily():
	take_backups_if("Daily")


def take_backups_weekly():
	take_backups_if("Weekly")


def take_backups_monthly():
	take_backups_if("Monthly")


def take_backups_if(freq):
	if cint(frappe.db.get_single_value("S3 Backup Settings", "enabled")):
		if frappe.db.get_single_value("S3 Backup Settings", "frequency") == freq:
			take_backups_s3()


@frappe.whitelist()
def take_backups_s3(retry_count=0):
	try:
		validate_file_size()
		backup_to_s3()
		send_email(True, "Amazon S3", "S3 Backup Settings", "notify_email")
	except JobTimeoutException:
		if retry_count < 2:
			args = {"retry_count": retry_count + 1}
			enqueue(
				"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_s3",
				queue="long",
				timeout=1500,
				**args,
			)
		else:
			notify()
	except Exception:
		notify()


def notify():
	error_message = frappe.get_traceback()
	send_email(False, "Amazon S3", "S3 Backup Settings", "notify_email", error_message)


def backup_to_s3():
	from frappe.utils import get_backups_path
	from frappe.utils.backups import new_backup

	doc = frappe.get_single("S3 Backup Settings")
	bucket = doc.bucket
	path = doc.backup_path or ""
	backup_files = cint(doc.backup_files)

	conn = boto3.client(
		"s3",
		aws_access_key_id=doc.access_key_id,
		aws_secret_access_key=doc.get_password("secret_access_key"),
		endpoint_url=doc.endpoint_url or "https://s3.amazonaws.com",
	)

	if frappe.flags.create_new_backup:
		backup = new_backup(
			ignore_files=False,
			backup_path_db=None,
			backup_path_files=None,
			backup_path_private_files=None,
			force=True,
		)
		db_filename = os.path.join(get_backups_path(), os.path.basename(backup.backup_path_db))
		site_config = os.path.join(get_backups_path(), os.path.basename(backup.backup_path_conf))
		if backup_files:
			files_filename = os.path.join(get_backups_path(), os.path.basename(backup.backup_path_files))
			private_files = os.path.join(
				get_backups_path(), os.path.basename(backup.backup_path_private_files)
			)
	else:
		if backup_files:
			db_filename, site_config, files_filename, private_files = get_latest_backup_file(
				with_files=backup_files
			)

			if not files_filename or not private_files:
				generate_files_backup()
				db_filename, site_config, files_filename, private_files = get_latest_backup_file(
					with_files=backup_files
				)

		else:
			db_filename, site_config = get_latest_backup_file()

	folder = path + os.path.basename(db_filename)[:15] + "/"
	# for adding datetime to folder name

	upload_file_to_s3(db_filename, folder, conn, bucket)
	upload_file_to_s3(site_config, folder, conn, bucket)

	if backup_files:
		if private_files:
			upload_file_to_s3(private_files, folder, conn, bucket)

		if files_filename:
			upload_file_to_s3(files_filename, folder, conn, bucket)

	# Delete old backups after uploading new one
	delete_old_backups(conn, bucket, path)

	# Delete old local backups too
	delete_old_local_backups()


def upload_file_to_s3(filename, folder, conn, bucket):
	destpath = os.path.join(folder, os.path.basename(filename))
	print("Uploading file:", filename)
	conn.upload_file(filename, bucket, destpath)  # Requires PutObject permission


def delete_old_backups(conn, bucket, backup_path):
	"""Delete old backups from S3 based on backup_limit setting"""
	from botocore.exceptions import ClientError

	backup_limit = cint(frappe.db.get_single_value("System Settings", "backup_limit") or 3)

	# List all backup folders in S3
	prefix = backup_path if backup_path else ""
	try:
		response = conn.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/")
		if "CommonPrefixes" not in response:
			return

		backup_folders = [p["Prefix"] for p in response["CommonPrefixes"]]

		# Sort folders by name (datetime format makes them sort chronologically)
		backup_folders.sort(reverse=True)

		# Delete old backups beyond the limit
		if len(backup_folders) > backup_limit:
			folders_to_delete = backup_folders[backup_limit:]
			for folder in folders_to_delete:
				print(f"Deleting old backup folder: {folder}")
				delete_s3_folder(conn, bucket, folder)

	except ClientError as e:
		frappe.log_error(f"S3 Backup Cleanup Error: {e}")
		print(f"Error cleaning up old backups: {e}")


def delete_s3_folder(conn, bucket, folder_prefix):
	"""Delete all objects in a folder prefix"""
	from botocore.exceptions import ClientError

	try:
		# List objects with the prefix
		response = conn.list_objects_v2(Bucket=bucket, Prefix=folder_prefix)
		if "Contents" not in response:
			return

		# Prepare delete request
		objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]

		if objects_to_delete:
			conn.delete_objects(Bucket=bucket, Delete={"Objects": objects_to_delete})
			print(f"Deleted {len(objects_to_delete)} objects from {folder_prefix}")

	except ClientError as e:
		frappe.log_error(f"S3 Delete Error for {folder_prefix}: {e}")
		print(f"Error deleting folder {folder_prefix}: {e}")


def delete_old_local_backups():
	"""Delete old local backups from private/backups folder based on backup_limit"""
	from frappe.utils import get_backups_path

	backup_limit = cint(frappe.db.get_single_value("System Settings", "backup_limit") or 3)
	backups_path = get_backups_path()

	if not os.path.exists(backups_path):
		return

	# Get all backup files with timestamps (database files)
	backup_files = []
	for f in os.listdir(backups_path):
		if f.endswith("-database.sql.gz"):
			file_path = os.path.join(backups_path, f)
			# Get timestamp from filename (format: YYYYMMDD_HHMMSS-site-database.sql.gz)
			try:
				timestamp_str = f.split("-")[0]
				backup_files.append((timestamp_str, file_path))
			except IndexError:
				continue

	# Sort by timestamp (newest first)
	backup_files.sort(reverse=True)

	if len(backup_files) > backup_limit:
		files_to_delete = backup_files[backup_limit:]
		for timestamp_str, db_file in files_to_delete:
			# Delete related files with same timestamp
			delete_local_backup_set(backups_path, timestamp_str)


def delete_local_backup_set(backups_path, timestamp_str):
	"""Delete a set of local backup files with the same timestamp"""
	import glob

	# Files to delete with this timestamp prefix
	patterns = [
		f"{timestamp_str}*-database.sql.gz",
		f"{timestamp_str}*-files.tar",
		f"{timestamp_str}*-private-files.tar",
		f"{timestamp_str}*-site_config_backup.json",
	]

	deleted_count = 0
	for pattern in patterns:
		for file_path in glob.glob(os.path.join(backups_path, pattern)):
			try:
				os.remove(file_path)
				print(f"Deleted local backup file: {file_path}")
				deleted_count += 1
			except OSError as e:
				frappe.log_error(f"Error deleting local backup {file_path}: {e}")
				print(f"Error deleting {file_path}: {e}")

	if deleted_count > 0:
		print(f"Deleted {deleted_count} local backup files with timestamp {timestamp_str}")


# Helper functions (from offsite_backup_utils)

def send_email(success, service_name, doctype, email_field, error_status=None):
	recipients = get_recipients(doctype, email_field)
	if not recipients:
		frappe.log_error(
			f"No Email Recipient found for {service_name}",
			f"{service_name}: Failed to send backup status email",
		)
		return

	if success:
		if not frappe.db.get_single_value(doctype, "send_email_for_successful_backup"):
			return

		subject = "Backup Upload Successful"
		message = """
<h3>Backup Uploaded Successfully!</h3>
<p>Hi there, this is just to inform you that your backup was successfully uploaded to your {} bucket. So relax!</p>""".format(
			service_name
		)
	else:
		subject = "[Warning] Backup Upload Failed"
		message = f"""
<h3>Backup Upload Failed!</h3>
<p>Oops, your automated backup to {service_name} failed.</p>
<p>Error message: {error_status}</p>
<p>Please contact your system manager for more information.</p>"""

	frappe.sendmail(recipients=recipients, subject=subject, message=message)


def get_recipients(doctype, email_field):
	return split_emails(frappe.db.get_value(doctype, None, email_field))


def get_latest_backup_file(with_files=False):
	from frappe.utils.backups import BackupGenerator

	odb = BackupGenerator(
		frappe.conf.db_name,
		frappe.conf.db_user,
		frappe.conf.db_password,
		db_socket=frappe.conf.db_socket,
		db_host=frappe.conf.db_host,
		db_port=frappe.conf.db_port,
		db_type=frappe.conf.db_type,
	)
	database, public, private, config = odb.get_recent_backup(older_than=24 * 30)

	if with_files:
		return database, config, public, private

	return database, config


def get_file_size(file_path, unit="MB"):
	file_size = os.path.getsize(file_path)

	memory_size_unit_mapper = {"KB": 1, "MB": 2, "GB": 3, "TB": 4}
	i = 0
	while i < memory_size_unit_mapper[unit]:
		file_size = file_size / 1000.0
		i += 1

	return file_size


def get_chunk_site(file_size):
	"""this function will return chunk size in megabytes based on file size"""

	file_size_in_gb = cint(file_size / 1024 / 1024)

	MB = 1024 * 1024
	if file_size_in_gb > 5000:
		return 200 * MB
	elif file_size_in_gb >= 3000:
		return 150 * MB
	elif file_size_in_gb >= 1000:
		return 100 * MB
	elif file_size_in_gb >= 500:
		return 50 * MB
	else:
		return 15 * MB


def validate_file_size():
	frappe.flags.create_new_backup = True
	latest_file, site_config = get_latest_backup_file()
	file_size = get_file_size(latest_file, unit="GB") if latest_file else 0

	if file_size > 1:
		frappe.flags.create_new_backup = False


def generate_files_backup():
	from frappe.utils.backups import BackupGenerator

	backup = BackupGenerator(
		frappe.conf.db_name,
		frappe.conf.db_user,
		frappe.conf.db_password,
		db_socket=frappe.conf.db_socket,
		db_host=frappe.conf.db_host,
		db_port=frappe.conf.db_port,
		db_type=frappe.conf.db_type,
	)

	backup.set_backup_file_name()
	backup.zip_files()
