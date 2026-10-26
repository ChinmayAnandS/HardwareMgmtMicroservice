from unittest import TestCase
from app.config import Settings


class ConfigurationTests(TestCase):
    def valid(self,**overrides):
        return Settings(**dict({'environment':'production','mongo_uri':'mongodb+srv://inventory.invalid',
            'origin':'https://admin.inventory.invalid','issuer':'https://login.microsoftonline.com/tenant/v2.0',
            'client_id':'test-client','client_secret':'unit-test-only-value','dev_code':''},**overrides))

    def test_https_required(self):
        with self.assertRaises(RuntimeError):
            self.valid(origin='http://admin.inventory.invalid').check()

    def test_dev_auth_forbidden_in_production(self):
        with self.assertRaises(RuntimeError):
            self.valid(dev_code='enabled').check()

    def test_verified_tls_required(self):
        with self.assertRaises(RuntimeError):
            self.valid(mongo_uri='mongodb+srv://inventory.invalid/?tls=false').check()

    def test_missing_oidc_fails_closed(self):
        with self.assertRaises(RuntimeError):
            self.valid(client_id='').check()

    def test_non_loopback_development_forbidden(self):
        with self.assertRaises(RuntimeError):
            Settings(environment='local',mongo_uri='mongodb://local',origin='http://public.invalid').check()
