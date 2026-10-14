"""Versioned, evidence-backed non-product dictionary; no online editing."""
import hashlib
import json
import re
from pathlib import Path

from vcr03 import ROOT


class Dictionary:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / 'config/nonproduct-v1.json'
        raw = self.path.read_bytes()
        self.hash = hashlib.sha256(raw).hexdigest()
        self.content = json.loads(raw)
        self.version = self.content['version']
        self.codes = {item['code']: item for item in self.content['entries']}
        self.patterns = [(re.compile(item['pattern']), item) for item in self.content['patterns']]

    def is_nonproduct(self, invoice, stock):
        return invoice.startswith('A') or stock in self.codes or bool(stock and any(p.fullmatch(stock) for p, _ in self.patterns))

    def description(self, invoice, stock):
        if invoice.startswith('A'):
            return {'name': '坏账调整单', 'category': '其他已识别非商品'}
        if stock in self.codes:
            return self.codes[stock]
        for pattern, item in self.patterns:
            if stock and pattern.fullmatch(stock):
                return item
        return {'name': stock or '编码缺失', 'category': '其他已识别非商品'}
