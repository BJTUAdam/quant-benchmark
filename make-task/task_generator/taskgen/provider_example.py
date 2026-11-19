"""Illustrative native-adapter contract, not a production vendor integration.

Configure adapter=provider_plugin and
provider_plugin=taskgen.provider_example:ExampleNativeAdapter. A trusted operator
must replace the endpoint/payload/extraction with their provider's actual API.
The shared APIClient owns networking, attempt budgets and safe logging.
"""
import json
import urllib.parse
import urllib.request


class ExampleNativeAdapter:
    def __init__(self, config):
        self.url = config.get('base_url', '')
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Configure an HTTP(S) native endpoint without URL credentials.')

    def build_request(self, messages, *, model, api_key, json_mode, schema):
        # This deliberately demonstrates a non-Chat-Completions envelope.
        payload = {'model_id':model, 'input_messages':messages,
                   'output_format':'json' if json_mode != 'off' else 'text'}
        if schema is not None:
            payload['output_schema'] = schema
        return urllib.request.Request(self.url,
            data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8'),
            headers={'X-API-Key':api_key,'Content-Type':'application/json'},method='POST')

    def extract_content(self, response_object):
        return response_object['output']['text']
