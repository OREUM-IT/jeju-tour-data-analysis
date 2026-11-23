import importlib.util
import io
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('set_openai', Path(__file__).parents[1] / 'langflow/set-openai.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
KEY = 'sk-proj-' + 'Synthetic_key-' * 3
ENVIRONMENT = {'LANGFLOW_SUPERUSER': 'admin', 'LANGFLOW_SUPERUSER_PASSWORD': 'password-not-printed'}


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

    def __call__(self, **_kwargs):
        return self

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


class SetOpenAITest(unittest.TestCase):
    def test_new_key_is_saved_once_as_credential_without_provider_call(self):
        client = Client()
        result = setup.run(io.BytesIO((KEY + '\n').encode()), ENVIRONMENT, client)
        self.assertEqual(result, {'status': 'configured_without_inference', 'modelExecuted': False,
                                  'providerAccessVerified': False})
        saved = [kwargs['json'] for method, path, kwargs in client.calls if path == '/api/v1/variables/' and method == 'POST']
        self.assertEqual(saved, [{'name': 'JEJU_OPENAI_API_KEY', 'type': 'Credential', 'default_fields': [], 'value': KEY}])
        self.assertNotIn(KEY, repr(result))

    def test_existing_credential_is_preserved_and_never_overwritten(self):
        client = Client([{'name': 'JEJU_OPENAI_API_KEY', 'type': 'Credential'}])
        result = setup.run(io.BytesIO(KEY.encode()), ENVIRONMENT, client)
        self.assertEqual(result['status'], 'existing_preserved')
        self.assertFalse(any(path == '/api/v1/variables/' and method == 'POST' for method, path, _ in client.calls))
        conflict = Client([{'name': 'JEJU_OPENAI_API_KEY', 'type': 'Generic'}])
        with self.assertRaisesRegex(setup.OpenAISetupError, '^OPENAI_CREDENTIAL_CONFLICT$'):
            setup.run(io.BytesIO(KEY.encode()), ENVIRONMENT, conflict)

    def test_malformed_input_fails_before_any_langflow_call(self):
        for raw in (b'', b'x' * 1025, b'sk-short', (KEY + ' ').encode(), (' ' + KEY).encode(),
                    (KEY + '\n\n').encode(), ('sk-' + 'ㄱ' * 30).encode(), b'Bearer ' + KEY.encode()):
            client = Client()
            with self.subTest(raw=raw[:12]), self.assertRaises(setup.OpenAISetupError):
                setup.run(io.BytesIO(raw), ENVIRONMENT, client)
            self.assertEqual(client.calls, [])

    def test_failed_save_and_missing_admin_report_fixed_codes(self):
        with self.assertRaisesRegex(setup.OpenAISetupError, '^LANGFLOW_CREDENTIAL_SAVE_FAILED$'):
            setup.run(io.BytesIO(KEY.encode()), ENVIRONMENT, Client(status=500))
        with self.assertRaisesRegex(setup.OpenAISetupError, '^LANGFLOW_ADMIN_CREDENTIALS_MISSING$'):
            setup.run(io.BytesIO(KEY.encode()), {}, Client())


if __name__ == '__main__':
    unittest.main()
