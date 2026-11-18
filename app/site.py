"""供采集程序读取的本地演练来源。

目录(/catalog)、适用范围(/scope)、规则正文(/detail)是三个可以独立改变的观察来源，
各自使用目录中的稳定标识，不使用显示名称做业务键。edition 为 1 或 2，表示两个发布期。
blank=catalog|scope|rule 模拟临时页面空白，fault=1 模拟来源故障；两者都不代表"无优惠"。
"""
import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DATA = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'source.json').read_text(encoding='utf-8'))
EDITIONS = ('1', '2')
BLANK_MARKER = {'blank': True}


def find_item(item_id):
    return next((x for x in DATA['items'] if x['id'] == item_id), None)


def catalog_view(edition):
    return {'edition': edition, 'items': [
        {'id': x['id'], 'name': x['name'], 'group': x['group'],
         'level': x['catalog_ed' + edition]['level'],
         'benefit': x['catalog_ed' + edition]['benefit'],
         'summary': x['catalog_ed' + edition]['summary']}
        for x in DATA['items']]}


def scope_view(item, edition):
    scope = item['scope_ed' + edition]
    return {'id': item['id'], 'name': item['name'], 'group': item['group'],
            'edition': edition, 'stores': list(scope['stores']), 'levels': list(scope['levels'])}


def detail_view(item, edition):
    return {'id': item['id'], 'name': item['name'], 'group': item['group'],
            'edition': edition, 'revision': edition,
            'level': item['catalog_ed' + edition]['level'],
            'benefit': item['catalog_ed' + edition]['benefit'],
            'detail': item['rule_ed' + edition]}


def is_blank(item, edition, part):
    return part in item.get('blank_ed' + edition, [])


def catalog_page(edition):
    cards = ''.join(
        '<article data-id="' + escape(x['id']) + '"><h2>' + escape(x['name']) + '</h2>'
        '<p class="level">' + escape(x['catalog_ed' + edition]['level']) + '</p>'
        '<p class="benefit">' + escape(x['catalog_ed' + edition]['benefit']) + '</p>'
        '<a href="/detail.html?id=' + escape(x['id']) + '&edition=' + edition + '">查看详情</a></article>'
        for x in DATA['items'])
    return ('<html lang="zh"><meta charset="utf-8"><h1>' + escape(DATA['title']) + '</h1>'
            '<main data-edition="' + edition + '">' + cards + '</main></html>')


def scope_page(item, edition):
    scope = item['scope_ed' + edition]
    stores = ''.join('<li class="store">' + escape(s) + '</li>' for s in scope['stores'])
    levels = ''.join('<li class="level">' + escape(lv) + '</li>' for lv in scope['levels'])
    return ('<html lang="zh"><meta charset="utf-8"><h1>' + escape(item['name']) + '</h1>'
            '<main data-id="' + escape(item['id']) + '" data-edition="' + edition + '" data-part="scope">'
            '<h2>' + escape(DATA['fact']) + '</h2><ul class="stores">' + stores + '</ul>'
            '<h2>' + escape(DATA['choice']) + '</h2><ul class="levels">' + levels + '</ul></main></html>')


def detail_page(item, edition):
    info = detail_view(item, edition)
    return ('<html lang="zh"><meta charset="utf-8"><h1>' + escape(item['name']) + '</h1>'
            '<main data-id="' + escape(item['id']) + '" data-edition="' + edition + '" data-part="rule">'
            '<label>' + escape(DATA['choice']) + '<select id="choice"><option>甲</option><option>乙</option></select></label>'
            '<button id="show">查看' + escape(DATA['detail_label']) + '</button><section id="result"></section>'
            '<p>' + escape(DATA['revision']) + ' ' + edition + '</p></main>'
            '<script>const c=document.querySelector("#choice"),r=document.querySelector("#result");'
            'c.onchange=()=>r.textContent="";document.querySelector("#show").onclick=()=>{'
            'r.textContent="读取中";let selected=c.value;setTimeout(()=>r.textContent=selected+":"'
            '+' + json.dumps(info['detail'], ensure_ascii=False) + ',50)}</script></html>')


def blank_page():
    return '<html lang="zh"><meta charset="utf-8"><main data-blank="true"></main></html>'


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, html):
        raw = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ('text/html' if html else 'application/json') + '; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlsplit(self.path)
        args = parse_qs(url.query)
        edition = args.get('edition', ['1'])[0]
        if edition not in EDITIONS:
            self.send_error(400)
            return
        if 'fault' in args:
            self.send_error(500)
            return
        html = url.path.endswith('.html')
        blank = args.get('blank', [None])[0]
        if blank not in (None, 'catalog', 'scope', 'rule'):
            self.send_error(400)
            return
        if url.path in ('/catalog', '/catalog.html'):
            if blank == 'catalog':
                self._send(blank_page() if html else json.dumps(BLANK_MARKER, ensure_ascii=False), html)
                return
            self._send(catalog_page(edition) if html else json.dumps(catalog_view(edition), ensure_ascii=False), html)
            return
        if url.path in ('/scope', '/scope.html', '/detail', '/detail.html'):
            item = find_item(args.get('id', [''])[0])
            if item is None:
                self.send_error(404)
                return
            part = 'scope' if url.path.startswith('/scope') else 'rule'
            if blank == part:
                self._send(blank_page() if html else json.dumps(BLANK_MARKER, ensure_ascii=False), html)
                return
            if html:
                self._send(scope_page(item, edition) if part == 'scope' else detail_page(item, edition), True)
            else:
                view = scope_view(item, edition) if part == 'scope' else detail_view(item, edition)
                self._send(json.dumps(view, ensure_ascii=False), False)
            return
        self.send_error(404)

    def log_message(self, *args):
        pass


def start_server():
    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    print('本地来源：http://127.0.0.1:8765/catalog', flush=True)
    server.serve_forever()
