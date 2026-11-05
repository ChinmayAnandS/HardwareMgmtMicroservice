from unittest import TestCase
from app.config import Settings


class ConfigurationTests(TestCase):
    def valid(self, **overrides):
        return Settings(**dict({'environment': 'production', 'mongo_uri': 'mongodb+srv://inventory.invalid',
                                'dev_code': '', 'team_codes': {'hardware-admin': 'a' * 24}}, **overrides))

    def test_valid_production_settings(self):
        self.valid().check()

    def test_dev_auth_forbidden_in_production(self):
        with self.assertRaises(RuntimeError):
            self.valid(dev_code='enabled').check()

    def test_verified_tls_required(self):
        with self.assertRaises(RuntimeError):
            self.valid(mongo_uri='mongodb+srv://inventory.invalid/?tls=false').check()

    def test_srv_uri_required_in_production(self):
        with self.assertRaises(RuntimeError):
            self.valid(mongo_uri='mongodb://inventory.invalid').check()

    def test_mongo_uri_required(self):
        with self.assertRaises(RuntimeError):
            self.valid(mongo_uri='').check()

    def test_unknown_environment_rejected(self):
        with self.assertRaises(RuntimeError):
            self.valid(environment='staging').check()

    def test_local_environment_allows_plain_mongo(self):
        Settings(environment='local', mongo_uri='mongodb://local', team_codes={}).check()

    def test_short_team_code_rejected(self):
        with self.assertRaises(RuntimeError):
            self.valid(team_codes={'hardware-admin': 'short'}).check()

    def test_production_needs_a_ui_team(self):
        with self.assertRaises(RuntimeError):
            self.valid(team_codes={'projects-team': 'p' * 24}).check()

    def test_unknown_team_rejected(self):
        with self.assertRaises(RuntimeError):
            self.valid(team_codes={'hardware-admin': 'a' * 24, 'mystery': 'm' * 24}).check()
