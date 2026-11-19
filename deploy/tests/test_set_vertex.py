import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('set_vertex', Path(__file__).parents[1] / 'langflow/set-vertex.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
except ImportError:
    serialization = None


def account():
    return {'type': 'service_account', 'project_id': 'jeju-test-project',
            'client_email': 'agent@jeju-test-project.iam.gserviceaccount.com',
            'token_uri': 'https://oauth2.googleapis.com/token', 'private_key': 'private-input-never-print'}


def encoded(value):
    return io.BytesIO(json.dumps(value).encode())


class Response:
    def __init__(self, status, value=None):
        self.status_code = status
        self.value = value

    def json(self):
        return self.value


class Client:
    def __init__(self, variables=None, status=201):
        self.variables = variables or []
        self.status = status
        self.headers = {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def post(self, path, **kwargs):
        self.calls.append(('POST', path, kwargs))
        if path == '/api/v1/login':
            return Response(200, {'access_token': 'test-session-not-printed'})
        return Response(self.status)

    def get(self, path):
        self.calls.append(('GET', path, {}))
        return Response(200, self.variables)


class VertexSetupTest(unittest.TestCase):
    def test_empty_large_duplicate_and_nonfinite_input_refused(self):
        for raw in [b'', b'x' * (setup.MAX_BYTES + 1), b'{"type":"a","type":"b"}',
                    b'{"type":"service_account","nested":{"x":1,"x":2}}', b'{"x":NaN}']:
            with self.subTest(raw_size=len(raw)), self.assertRaises(setup.VertexSetupError):
                setup.credential_from_stdin(io.BytesIO(raw))

    def test_raw_and_documented_wrapper_normalize_to_global(self):
        with patch.object(setup, 'validate_rsa') as validate:
            raw = setup.credential_from_stdin(encoded(account()))
            wrapped = setup.credential_from_stdin(encoded({'serviceAccount': account(), 'location': 'global'}))
        self.assertEqual(raw, wrapped)
        self.assertEqual(raw['location'], 'global')
        self.assertEqual(validate.call_count, 2)

    def test_wrong_account_redirect_and_location_inputs_rejected_before_key_parse(self):
        for key, value in [('type', 'authorized_user'), ('client_email', 'agent@attacker.example'),
                           ('project_id', '../redirect'), ('token_uri', 'https://attacker.example/token'),
                           ('universe_domain', 'attacker.example'), ('client_secret', 'hidden')]:
            bad = account()
            bad[key] = value
            with self.subTest(key=key), patch.object(setup, 'validate_rsa') as validate:
                with self.assertRaises(setup.VertexSetupError):
                    setup.credential_from_stdin(encoded(bad))
                validate.assert_not_called()
        for location in ['us-central1', None, 'global/../other']:
            with self.subTest(location=location), self.assertRaises(setup.VertexSetupError):
                setup.credential_from_stdin(encoded({'serviceAccount': account(), 'location': location}))

    @unittest.skipIf(serialization is None, 'cryptography required for real RSA parsing tests')
    def test_actual_rsa_accepts_2048_and_rejects_small_ec_encrypted_or_malformed(self):
        def pem(key, encryption=serialization.NoEncryption()):
            return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption).decode()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        setup.validate_rsa(pem(key))
        bad = [pem(rsa.generate_private_key(public_exponent=65537, key_size=1024)),
               pem(ec.generate_private_key(ec.SECP256R1())),
               pem(key, serialization.BestAvailableEncryption(b'not-a-live-password')), 'malformed-private-key']
        for value in bad:
            with self.assertRaisesRegex(setup.VertexSetupError, '^INVALID_RSA_PRIVATE_KEY$'):
                setup.validate_rsa(value)

    def test_only_loopback_api_is_contacted_and_creation_never_runs_model(self):
        client = Client()
        options = {}
        def factory(**kwargs):
            options.update(kwargs)
            return client
        with patch.object(setup, 'validate_rsa'):
            result = setup.run(encoded(account()), {'LANGFLOW_SUPERUSER': 'admin', 'LANGFLOW_SUPERUSER_PASSWORD': 'password'}, factory)
        self.assertEqual(options, {'base_url': 'http://127.0.0.1:7860', 'timeout': 30.0,
                                   'follow_redirects': False, 'trust_env': False})
        self.assertEqual([(method, path) for method, path, _ in client.calls],
                         [('POST', '/api/v1/login'), ('GET', '/api/v1/variables/'), ('POST', '/api/v1/variables/')])
        saved = client.calls[-1][2]['json']
        self.assertEqual(saved['name'], setup.VARIABLE)
        self.assertEqual(saved['type'], 'Credential')
        self.assertEqual(json.loads(saved['value'])['serviceAccount'], account())
        self.assertEqual(result, {'status': 'configured_without_inference', 'modelExecuted': False, 'providerAccessVerified': False})

    def test_existing_credential_is_preserved_without_reading_its_value(self):
        class ProtectedValue(dict):
            def get(self, key, default=None):
                if key == 'value':
                    raise AssertionError('existing secret must not be accessed')
                return super().get(key, default)
        client = Client([ProtectedValue(name=setup.VARIABLE)])
        with patch.object(setup, 'validate_rsa'):
            result = setup.run(encoded(account()), {'LANGFLOW_SUPERUSER': 'admin', 'LANGFLOW_SUPERUSER_PASSWORD': 'password'}, lambda **_: client)
        self.assertEqual(result['status'], 'existing_preserved')
        self.assertEqual(len(client.calls), 2)

    def test_redirect_save_response_and_exception_details_never_reach_output(self):
        client = Client(status=307)
        with patch.object(setup, 'validate_rsa'), self.assertRaisesRegex(setup.VertexSetupError, '^LANGFLOW_CREDENTIAL_SAVE_FAILED$'):
            setup.run(encoded(account()), {'LANGFLOW_SUPERUSER': 'admin', 'LANGFLOW_SUPERUSER_PASSWORD': 'password'}, lambda **_: client)
        output = io.StringIO()
        with patch.object(setup, 'run', side_effect=RuntimeError('secret-provider-body')), contextlib.redirect_stdout(output):
            self.assertEqual(setup.main(), 1)
        self.assertNotIn('secret-provider-body', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['code'], 'LANGFLOW_CONFIGURATION_FAILED')


if __name__ == '__main__':
    unittest.main()
