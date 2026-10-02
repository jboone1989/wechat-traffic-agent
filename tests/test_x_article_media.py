import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from wechat_traffic_agent.x_article_media import ArticleImageSpec, CommonsImageSearch, prepare_article_media
from wechat_traffic_agent.x_image_generation import GENERATE


def png_bytes():
    image = Image.new('RGB', (1024, 600), '#145d80')
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


class MediaTests(unittest.TestCase):
    def test_request_rejects_empty_generation_and_search(self):
        for mode in ['GENERATE', 'SEARCH']:
            with self.assertRaises(ValueError):
                ArticleImageSpec(mode=mode, alt_text='A useful image')

    def test_search_retains_attribution_and_downloads_exact_selected_file(self):
        def respond(request):
            if request.url.host == 'upload.wikimedia.org':
                return httpx.Response(200, content=png_bytes())
            return httpx.Response(200, json={'query': {'pages': {'12': {
                'pageid': 12, 'title': 'File:An image.png', 'imageinfo': [{
                    'url': 'https://upload.wikimedia.org/wikipedia/commons/a/image.png',
                    'descriptionurl': 'https://commons.wikimedia.org/wiki/File:An_image.png',
                    'width': 1024, 'height': 600, 'extmetadata': {
                        'Artist': {'value': '<a href="/">Photographer</a>'},
                        'LicenseShortName': {'value': 'CC BY 4.0'},
                        'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by/4.0/'}}}]}}}})
        search = CommonsImageSearch(httpx.Client(transport=httpx.MockTransport(respond)))
        candidates = search.search('external checkpoint')
        self.assertEqual(candidates[0]['creator'], 'Photographer')
        with tempfile.TemporaryDirectory() as folder:
            receipt = search.download(candidates[0], Path(folder))
            self.assertEqual(receipt['license'], 'CC BY 4.0')
            self.assertIn(candidates[0]['source_url'], receipt['attribution'])
            self.assertEqual(hashlib.sha256(Path(receipt['path']).read_bytes()).hexdigest(), receipt['sha256'])

    def test_search_failure_is_not_replaced_by_a_graphic(self):
        search = CommonsImageSearch(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
        with tempfile.TemporaryDirectory() as folder:
            draft = {'title': 'Recoverable state', 'images': [{
                'mode': 'SEARCH', 'query': 'checkpoint', 'alt_text': 'External state storage'}]}
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(prepare_article_media(draft=draft, folder=Path(folder), search=search))
            self.assertFalse((Path(folder) / 'x_article_cover.png').exists())

    def test_generated_image_is_reused_and_bound_to_exact_request(self):
        class Generator:
            calls = 0
            async def generate(self, *, prompt, folder):
                self.calls += 1
                path = folder / 'generated.png'
                path.write_bytes(png_bytes())
                return {'status': 'GENERATED', 'path': str(path), 'kind': 'AI_GENERATED',
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'ai_image_model': True}
        generator = Generator()
        with tempfile.TemporaryDirectory() as folder:
            draft = {'title': 'Recoverable state', 'images': [{'mode': 'GENERATE',
                'prompt': 'Original illustration of recoverable state', 'alt_text': 'Recoverable agent state'}]}
            first = asyncio.run(prepare_article_media(draft=draft, folder=Path(folder), generator=generator))
            second = asyncio.run(prepare_article_media(draft=draft, folder=Path(folder), generator=generator))
            self.assertEqual(first, second)
            self.assertEqual(generator.calls, 1)
            draft['images'][0]['prompt'] = 'Different image'
            with self.assertRaisesRegex(ValueError, 'plan changed'):
                asyncio.run(prepare_article_media(draft=draft, folder=Path(folder), generator=generator))

    def test_download_rejects_non_commons_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'not a Commons'):
                CommonsImageSearch(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))).download(
                    {'url': 'http://127.0.0.1/private'}, Path(folder))


class ImageBrowser:
    def __init__(self, submitted=False, drops_fill=False):
        self.submitted = submitted
        self.drops_fill = drops_fill
        self.native_inputs = 0
        self.outbound = ''
        self.sends = 0
    def evaluate(self, expression):
        if 'const users=' in expression:
            return {'url': 'https://chatgpt.com/c/owned' if self.submitted else 'https://chatgpt.com/',
                    'user_count': int(self.submitted), 'assistant_count': int(self.submitted),
                    'last_user': self.outbound, 'generating': False, 'response': '',
                    'images': [{'src': 'https://chatgpt.com/backend-api/estuary/content?id=real',
                                'assistant_index': 0, 'width': 1024, 'height': 600,
                                'message_id': 'assistant-image'}] if self.submitted else []}
        if 'data-testid' in expression:
            return 'send-button'
        if 'document.activeElement===e' in expression:
            return True
        if 'nodes.get' in expression:
            return self.outbound
        raise AssertionError(expression)
    def observe(self, screenshot=False):
        return {'actions': [{'kind': 'fill', 'role': 'textbox', 'label': 'Prompt', 'node': 1},
                            {'kind': 'click', 'role': 'button', 'label': 'Send', 'node': 2}]}
    def act(self, action, page, text=None):
        if action['kind'] == 'fill': self.outbound = '' if self.drops_fill else text
        else: self.submitted = True; self.sends += 1
    def call(self, method, **params):
        if method == 'Input.insertText':
            self.native_inputs += 1
            self.outbound = params['text']
            return {}
        self.assert_download = params
        assert method == 'Runtime.evaluate'
        return {'result': {'value': base64.b64encode(png_bytes()).decode()}}


class GenerationTransactionTests(unittest.TestCase):
    def setUp(self):
        import time
        clock = [0.]
        self.enterContext(patch.object(time, 'monotonic', lambda: clock[0]))
        self.enterContext(patch.object(time, 'sleep', lambda n: clock.__setitem__(0, clock[0]+n)))
        module = types.ModuleType('jev_ultrafast.browser')
        module.StalePage = type('StalePage', (ValueError,), {})
        self.enterContext(patch.dict(sys.modules, {'jev_ultrafast.browser': module}))

    def execute(self, browser, folder):
        scope = {'browser': browser, 'prompt': 'A clear original illustration',
                 'transaction_path': str(folder / 'tx.json'), 'image_path': str(folder / 'image.png'),
                 'prompt_sha256': 'bound-prompt', 'timeout': 10}
        exec(compile(GENERATE, 'image-generation-test', 'exec'), scope)
        return scope['result']

    def test_saves_real_image_bytes_and_chatgpt_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            browser = ImageBrowser()
            result = self.execute(browser, folder)
            self.assertEqual(result['status'], 'GENERATED')
            self.assertEqual(result['conversation_url'], 'https://chatgpt.com/c/owned')
            self.assertEqual(result['message_id'], 'assistant-image')
            self.assertEqual(browser.sends, 1)
            self.assertEqual(result['sha256'], hashlib.sha256((folder / 'image.png').read_bytes()).hexdigest())
            recovered = self.execute(browser, folder)
            self.assertEqual(recovered['status'], 'GENERATED')
            self.assertEqual(browser.sends, 1)

    def test_ambiguous_submission_never_sends_again(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / 'tx.json').write_text(json.dumps({'attempted': True, 'status': 'SUBMIT_PENDING',
                'baseline_user_count': 0, 'baseline_assistant_count': 0}))
            browser = ImageBrowser()
            result = self.execute(browser, folder)
            self.assertEqual(result['status'], 'GENERATION_UNVERIFIED')
            self.assertEqual(browser.sends, 0)
            self.assertFalse((folder / 'image.png').exists())

    def test_discarded_fill_is_repaired_with_native_input_before_send(self):
        with tempfile.TemporaryDirectory() as root:
            browser = ImageBrowser(drops_fill=True)
            result = self.execute(browser, Path(root))
            self.assertEqual(result['status'], 'GENERATED')
            self.assertEqual(browser.native_inputs, 1)
            self.assertEqual(browser.sends, 1)
