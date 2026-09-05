# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt

import frappe
from cryptography.fernet import Fernet
from frappe.tests import IntegrationTestCase
from frappe.utils.password import remove_encrypted_password, set_encrypted_password


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class TestS3BackupSettings(IntegrationTestCase):
	"""
	Integration tests for S3BackupSettings.
	Use this class for testing interactions between multiple components.
	"""

	def setUp(self):
		# Save the encryption key that the site is using and make sure one is
		# present in frappe.local.conf so the tests don't write a random one to
		# site_config.json.
		self._original_key = frappe.local.conf.get("encryption_key")
		if not self._original_key:
			self._original_key = Fernet.generate_key().decode()
			frappe.local.conf.encryption_key = self._original_key

	def tearDown(self):
		frappe.local.conf.encryption_key = self._original_key
		remove_encrypted_password("S3 Backup Settings", "S3 Backup Settings", "secret_access_key")

	def test_get_secret_access_key_prefers_new_value(self):
		"""A freshly typed secret key must be used without trying to decrypt the old one."""
		doc = frappe.get_single("S3 Backup Settings")
		doc.secret_access_key = "newly-typed-secret"
		self.assertEqual(doc._get_secret_access_key(), "newly-typed-secret")

	def test_get_secret_access_key_decrypts_stored_value(self):
		"""When no new value is provided, the stored secret should be decrypted."""
		set_encrypted_password("S3 Backup Settings", "S3 Backup Settings", "my-stored-secret", "secret_access_key")

		doc = frappe.get_single("S3 Backup Settings")
		doc.secret_access_key = None  # simulate untouched form field
		self.assertEqual(doc._get_secret_access_key(), "my-stored-secret")

	def test_get_secret_access_key_raises_on_decryption_failure(self):
		"""A wrong encryption key should raise a clear, actionable error."""
		old_key = Fernet.generate_key().decode()
		new_key = Fernet.generate_key().decode()

		# Store a secret with a different key.
		frappe.local.conf.encryption_key = old_key
		set_encrypted_password("S3 Backup Settings", "S3 Backup Settings", "old-secret", "secret_access_key")

		# Try to read it with the current (new) key.
		frappe.local.conf.encryption_key = new_key
		doc = frappe.get_single("S3 Backup Settings")
		doc.secret_access_key = None

		with self.assertRaises(frappe.ValidationError) as cm:
			doc._get_secret_access_key()

		self.assertIn("re-enter the Secret Access Key", str(cm.exception))

	def test_validate_resets_undecryptable_secret(self):
		"""validate() must clear the old, undecryptable secret and let the user enter a new one."""
		from frappe.utils.password import get_decrypted_password

		old_key = Fernet.generate_key().decode()
		new_key = Fernet.generate_key().decode()

		# Store a secret with an old key.
		frappe.local.conf.encryption_key = old_key
		set_encrypted_password("S3 Backup Settings", "S3 Backup Settings", "old-secret", "secret_access_key")
		frappe.db.commit()

		# Switch to a new key and validate without providing a new secret.
		frappe.local.conf.encryption_key = new_key
		doc = frappe.get_single("S3 Backup Settings")
		doc.enabled = 1
		doc.access_key_id = "AKIAIOSFODNN7EXAMPLE"
		doc.bucket = "my-test-bucket"
		doc.endpoint_url = "https://s3.amazonaws.com"
		doc.secret_access_key = "*****"  # dummy value, as sent by an untouched form

		doc.validate()

		# After validate, the field is reset and the encrypted auth record is gone.
		self.assertIsNone(doc.get("secret_access_key"))
		self.assertIsNone(get_decrypted_password("S3 Backup Settings", "S3 Backup Settings", "secret_access_key", raise_exception=False))
