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

    def team_only(self, **overrides):
        return self.valid(**dict({'issuer':'','client_id':'','client_secret':'',
                                  'team_codes':{'hardware-admin':'a'*24}},**overrides))

    def test_team_code_replaces_oidc(self):
        self.team_only().check()

    def test_short_team_code_rejected(self):
        with self.assertRaises(RuntimeError):
            self.team_only(team_codes={'hardware-admin':'short'}).check()

    def test_production_needs_a_ui_team(self):
        with self.assertRaises(RuntimeError):
            self.team_only(team_codes={'projects-team':'p'*24}).check()

    def test_unknown_team_rejected(self):
        with self.assertRaises(RuntimeError):
            self.team_only(team_codes={'hardware-admin':'a'*24,'mystery':'m'*24}).check()
