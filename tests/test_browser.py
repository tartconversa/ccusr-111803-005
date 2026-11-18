"""真实浏览器验证本地可控页面：目录导航、适用范围与空白页。"""
import threading
import unittest

from app.browser import read_visible_entry, read_visible_scope
from app.site import DATA, start_server


class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_server()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_navigation(self):
        item = DATA['items'][0]
        entry = read_visible_entry(self.origin, item['id'])
        self.assertEqual(entry['name'], item['name'])
        self.assertIn('/detail.html?', entry['url'])
        self.assertEqual(entry['state'], 'observed')

    def test_visible_scope(self):
        item = DATA['items'][0]
        scope = read_visible_scope(self.origin, item['id'], '1')
        self.assertEqual(scope['state'], 'observed')
        self.assertIn('东城店', scope['stores'])
        self.assertIn('甲', scope['levels'])

    def test_blank_page_reported_not_guessed(self):
        """空白页面被如实识别为 blank，而不是被当成无优惠内容。"""
        item = DATA['items'][1]
        entry = read_visible_entry(self.origin, item['id'], '2', blank=True)
        self.assertEqual(entry['state'], 'blank')
        scope = read_visible_scope(self.origin, item['id'], '2', blank=True)
        self.assertEqual(scope['state'], 'blank')
