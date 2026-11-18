"""使用真实 Chromium 浏览本地目录、适用范围与规则正文。

页面空白(data-blank)时如实返回 state=blank，不把空白页面解释成无优惠。
"""
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def _check_origin(origin):
    url = urlsplit(origin)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('只允许本地页面')


def _page_state(page):
    return 'blank' if page.locator('main[data-blank="true"]').count() == 1 else 'observed'


def read_visible_entry(origin, item_id, edition='1', blank=False):
    """从目录卡片进入规则正文，核对目录与详情的稳定身份一致。"""
    _check_origin(origin)
    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            catalog_url = origin.rstrip('/') + '/catalog.html?edition=' + edition
            if blank:
                catalog_url += '&blank=catalog'
            page.goto(catalog_url)
            if page.locator('main[data-blank="true"]').count() == 1:
                return {'id': item_id, 'url': page.url, 'edition': edition,
                        'kind': 'catalog', 'state': 'blank'}
            card = page.locator('article[data-id="' + item_id + '"]')
            if card.count() != 1:
                raise ValueError('目录身份不唯一')
            name = card.locator('h2').inner_text()
            detail_url = origin.rstrip('/') + '/detail.html?id=' + item_id + '&edition=' + edition
            if blank:
                detail_url += '&blank=rule'
            page.goto(detail_url)
            if page.locator('main[data-blank="true"]').count() == 1:
                return {'id': item_id, 'name': name, 'url': page.url, 'edition': edition,
                        'kind': 'rule', 'state': 'blank'}
            if page.locator('main').get_attribute('data-id') != item_id:
                raise ValueError('详情身份不符')
            return {'id': item_id, 'name': name, 'url': page.url,
                    'edition': page.locator('main').get_attribute('data-edition'),
                    'kind': 'rule', 'state': _page_state(page)}
        finally:
            browser.close()


def read_visible_scope(origin, item_id, edition='1', blank=False):
    """直接打开适用范围页面，读取门店与会员等级。"""
    _check_origin(origin)
    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            url = origin.rstrip('/') + '/scope.html?id=' + item_id + '&edition=' + edition
            if blank:
                url += '&blank=scope'
            page.goto(url)
            main = page.locator('main')
            state = _page_state(page)
            if state == 'blank':
                return {'id': item_id, 'url': page.url, 'edition': edition,
                        'kind': 'scope', 'state': 'blank'}
            if main.get_attribute('data-id') != item_id:
                raise ValueError('适用范围身份不符')
            stores = page.locator('li.store').all_inner_texts()
            levels = page.locator('li.level').all_inner_texts()
            return {'id': item_id, 'url': page.url,
                    'edition': main.get_attribute('data-edition'),
                    'kind': 'scope', 'state': 'observed', 'stores': stores, 'levels': levels}
        finally:
            browser.close()
