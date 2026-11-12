"""Read-only rule HTTP service. No model calls, queues, uploads or result storage."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__:
    from .checklist_rules import build_rule_inventory
    from .docx_reader import read_docx_layout
    from .material_tree import build_category_catalog
    from .phrase_rules import snapshot_phrase_library
else:
    from checklist_rules import build_rule_inventory
    from docx_reader import read_docx_layout
    from material_tree import build_category_catalog
    from phrase_rules import snapshot_phrase_library


class InvalidFileName(ValueError):
    pass


class RuleStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _files(self):
        found = {}
        for path in sorted(self.root.iterdir()):
            if not path.is_file() or path.name.startswith('~$') or not path.name.endswith('材料清单.docx'):
                continue
            name = path.name.removesuffix('材料清单.docx').removesuffix('_')
            if name in found:
                raise ValueError('产品存在重复清单：'+name)
            if not path.resolve().is_relative_to(self.root):
                raise ValueError('规则路径不合法')
            found[name] = path
        if not found:
            raise ValueError('规则目录没有有效清单')
        return found

    def products(self):
        return list(self._files())

    def files(self):
        return [dict(productName=name, fileName=path.name) for name, path in self._files().items()]

    def _snapshot(self, name, path):
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.root):
            raise ValueError('规则路径不合法')
        before = path.stat()
        raw = path.read_bytes()
        content, sections = read_docx_layout(io.BytesIO(raw))
        after = path.stat()
        identity = lambda s: (s.st_mtime_ns, s.st_ctime_ns, s.st_size, s.st_ino)
        if identity(before) != identity(after) or len(raw) != before.st_size:
            raise ValueError('清单正在更新，请重新运行')
        return dict(name=name, filename=path.name, content=content, raw_bytes=raw,
                    content_hash=hashlib.sha256(raw).hexdigest()), sections

    def file_content(self, filename):
        if (not isinstance(filename, str) or not filename.strip() or filename in ('.', '..')
                or any(c in filename for c in '/\\:') or any(ord(c) < 32 for c in filename)):
            raise InvalidFileName('文件名无效')
        for name, path in self._files().items():
            if path.name == filename:
                try:
                    snapshot, sections = self._snapshot(name, path)
                except OSError as error:
                    raise ValueError('清单读取期间不可用') from error
                rules = build_rule_inventory([snapshot])
                for rule in rules:
                    rule.pop('documentContext', None)
                categories = []
                by_path = {(): categories}
                for path in build_category_catalog([snapshot])[name]:
                    key = tuple((part['categoryId'], part['categoryName']) for part in path)
                    children = []
                    by_path[key[:-1]].append(dict(path[-1], children=children))
                    by_path[key] = children
                return dict(fileName=filename, content=snapshot['content'], categories=categories,
                            rules=rules, sections=sections)
        raise FileNotFoundError(filename)

    def bundle(self, names):
        files = self._files()
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            raise ValueError('products 必须是非空产品名称数组')
        names = [n.strip() for n in names]
        if len(names) != len(set(names)) or any(n not in files for n in names):
            raise ValueError('产品名称不存在、重复或不是完整名称')
        snapshots = [self._snapshot(name, files[name])[0] for name in names]
        rules = build_rule_inventory(snapshots)
        categories = build_category_catalog(snapshots)
        phrases = snapshot_phrase_library(self.root)
        # The document context occurs once in content, not once for every rule.
        for rule in rules:
            rule.pop('documentContext', None)
        return dict(documents=[{k:v for k,v in s.items() if k!='raw_bytes'} for s in snapshots],
                    rules=rules, categories=categories,
                    phraseLibrary={k:v for k,v in phrases.items() if k!='raw_bytes'})


def make_server(root, host='127.0.0.1', port=8008):
    store = RuleStore(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log business contents or provider credentials.
            pass

        def send_json(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            try:
                if self.path == '/health':
                    self.send_json(200, dict(status='ok', document_count=len(store.products())))
                elif self.path == '/v1/products':
                    self.send_json(200, dict(products=store.products()))
                else:
                    self.send_json(404, dict(error='接口不存在'))
            except Exception:
                self.send_json(503, dict(error='规则目录不可用或产品清单重复'))

        def do_POST(self):
            if self.path != '/v1/rules':
                self.send_json(404, dict(error='接口不存在'))
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > 65536:
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or set(body) != {'products'}:
                    raise ValueError()
                result = store.bundle(body['products'])
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self.send_json(422, dict(error=str(error) or '请求格式不合法'))
            except Exception:
                self.send_json(503, dict(error='清单或话术库缺失、损坏、格式不完整或正在更新'))
            else:
                self.send_json(200, result)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8008)
    parser.add_argument('--rules', default=os.environ.get('RULES_DIR', str(Path(__file__).resolve().parents[1]/'rules')))
    args=parser.parse_args()
    server=make_server(args.rules, args.host, args.port)
    print(f'Rule service listening on {args.host}:{server.server_port}; rules={Path(args.rules).resolve()}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
