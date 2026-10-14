"""DP-03/05/06/10 input contracts, shared by all entry points."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import unicodedata

HEADERS = ('Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country')
NORM_VERSION = 'nfkc-trim-uppercase-u15-v1'
READ_VERSION = 'openpyxl-3.1.5-decimal-str-v1'
INGEST_VERSION = 'module-a-v1'
TRIM = ''.join(chr(n) for a, b in [(9, 13), (28, 32), (133, 133), (160, 160), (5760, 5760),
                                 (8192, 8202), (8232, 8233), (8239, 8239), (8287, 8287), (12288, 12288)]
               for n in range(a, b + 1))
FORBIDDEN = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}


@dataclass(frozen=True)
class Cell:
    value: object
    kind: str


def packed(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not Decimal(str(value)).is_finite():
        return {'nonfinite': str(value)}
    return value


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()


def read_keys(cells):
    keys, narrows, issues = {}, {}, []
    for field, pos in [('Invoice', 0), ('StockCode', 1), ('Customer ID', 6)]:
        keys[field], narrows[field], error = key(cells[pos])
        if error:
            issue = {'field': field, 'code': error}
            if error == 'KEY_FORBIDDEN_FORMAT_CHAR':
                issue['characters'] = [{'index': i, 'codepoint': f'U+{ord(c):04X}'}
                                       for i, c in enumerate(cells[pos].value) if ord(c) in FORBIDDEN]
            issues.append(issue)
    return keys, narrows, issues


def unpack_cells(raw):
    cells = []
    for item in raw:
        value = item['value']
        if item['type'] == 'd' and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif isinstance(value, dict) and 'nonfinite' in value:
            value = float(value['nonfinite'])
        cells.append(Cell(value, item['type']))
    return tuple(cells)


def typed_content(cells, keys, suspicious, *, normalized_identifiers):
    """Raw seven-column comparison; numeric representations remain exact and type-aware."""
    items = []
    for pos in range(1, 8):
        cell = cells[pos]
        if cell.kind in {'f', 'e'}:
            return None
        field = {1: 'StockCode', 6: 'Customer ID'}.get(pos)
        if normalized_identifiers and field and keys[field] and (field, keys[field]) not in suspicious:
            items.append(('approved-key', keys[field]))
        elif cell.kind == 'n' and cell.value is not None and not isinstance(cell.value, bool):
            number = Decimal(str(cell.value))
            if number.is_finite():
                sign, digits, exponent = number.as_tuple()
                digits = list(digits)
                while len(digits) > 1 and digits[-1] == 0:
                    digits.pop()
                    exponent += 1
                if number.is_zero():
                    sign, digits, exponent = 0, [0], 0
                items.append(('number', sign, ''.join(map(str, digits)), exponent))
            else:
                items.append(('nonfinite-number', str(number)))
        else:
            items.append((cell.kind, packed(cell.value)))
    return compact(items)


def numeric(cell: Cell):
    if cell.kind == 'f':
        return None, 'NUMERIC_FORMULA'
    if cell.value is None:
        return None, 'NUMERIC_MISSING'
    if cell.kind != 'n' or isinstance(cell.value, bool) or not isinstance(cell.value, (int, float, Decimal)):
        return None, 'NUMERIC_TYPE_INVALID'
    try:
        value = Decimal(str(cell.value))
    except InvalidOperation:
        return None, 'NUMERIC_TYPE_INVALID'
    return (value, None) if value.is_finite() else (None, 'NUMERIC_NONFINITE')


def key(cell: Cell):
    value = cell.value
    if value is None:
        return None, None, 'KEY_MISSING'
    if cell.kind in {'s', 'inlineStr'} and isinstance(value, str):
        text = value
    elif cell.kind == 'n' and not isinstance(value, bool) and isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            return None, None, 'KEY_TYPE_INVALID'
        text = str(int(number))
    else:
        return None, None, 'KEY_TYPE_INVALID'
    if any(ord(c) in FORBIDDEN for c in text):
        return None, None, 'KEY_FORBIDDEN_FORMAT_CHAR'
    normalized = unicodedata.normalize('NFKC', text).strip(TRIM).upper()
    if not normalized:
        return None, None, 'KEY_MISSING'
    narrow = ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else ' ' if c == '\u3000' else c
                     for c in text).strip(TRIM)
    narrow = ''.join(chr(ord(c) - 32) if 'a' <= c <= 'z' else c for c in narrow)
    return normalized, narrow, None


def parse(cells: tuple[Cell, ...], dictionary) -> dict:
    keys, narrows, issues = read_keys(cells)
    for field, pos in [('Description', 2), ('Country', 7)]:
        if cells[pos].kind in {'f', 'e'}:
            issues.append({'field': field, 'code': 'TEXT_FIELD_FORMULA_OR_ERROR', 'cell_type': cells[pos].kind})
    quantity, error = numeric(cells[3])
    if error:
        issues.append({'field': 'Quantity', 'code': error})
    elif quantity != quantity.to_integral_value():
        issues.append({'field': 'Quantity', 'code': 'QUANTITY_NOT_INTEGER'})
        quantity = None
    price, error = numeric(cells[5])
    if error:
        issues.append({'field': 'Price', 'code': error})
    when = cells[4].value
    if cells[4].kind != 'd' or not isinstance(when, datetime) or when.tzinfo is not None:
        issues.append({'field': 'InvoiceDate', 'code': 'TIME_MISSING_OR_INVALID'})
        when = None
    row = {'keys': keys, 'narrows': narrows, 'issues': issues,
           'quantity': int(quantity) if quantity is not None else None,
           'price': str(price) if price is not None else None,
           'invoice_date': when.isoformat() if when is not None else None}
    return route(row, dictionary)


def route(row: dict, dictionary) -> dict:
    keys, issues = row['keys'], row['issues']
    by_field = {field: [i['code'] for i in issues if i['field'] == field] for field in HEADERS}
    identity_bad = bool(by_field['Invoice'])
    invoice = keys['Invoice']
    side = 'UNKNOWN' if identity_bad else 'CANCEL' if invoice.startswith('C') else 'SALE'
    nonproduct = False if identity_bad else dictionary.is_nonproduct(invoice, keys['StockCode'])
    if nonproduct:
        issues = [i for i in issues if not(i['field'] == 'Customer ID' and i['code'] == 'KEY_MISSING')]
    q = row['quantity']
    if not nonproduct and side != 'UNKNOWN' and q is not None and (
        (side == 'CANCEL' and q >= 0) or (side == 'SALE' and q == 0)
    ):
        issues.append({'field': 'Quantity', 'code': 'QUANTITY_INVALID_OR_SIGN'})
    if not nonproduct and row['price'] is not None and Decimal(row['price']) <= 0:
        issues.append({'field': 'Price', 'code': 'PRICE_INVALID_OR_NONPOSITIVE'})
    # Complete reasons remain available; only the primary reason is mutually exclusive.
    issues = list({(i['field'], i['code']): i for i in issues}.values())
    fields = {i['field'] for i in issues}
    if identity_bad:
        primary, category = 'IDENTITY_KEY_UNAVAILABLE', 'IDENTITY_DATA_QUALITY'
    elif nonproduct and issues:
        primary, category = 'NP_DATA_QUALITY', 'NONPRODUCT_DATA_QUALITY'
    elif issues:
        category = 'DATA_QUALITY'
        primary = ('MISSING_CUSTOMER' if 'KEY_MISSING' in by_field['Customer ID'] else
                   'MISSING_STOCK' if 'KEY_MISSING' in by_field['StockCode'] else
                   'KEY_INVALID' if fields & {'Customer ID', 'StockCode'} else
                   'TIME_MISSING_OR_INVALID' if 'InvoiceDate' in fields else
                   'QUANTITY_INVALID_OR_SIGN' if 'Quantity' in fields else
                   'PRICE_INVALID_OR_NONPOSITIVE' if 'Price' in fields else 'TEXT_FIELD_FORMULA_OR_ERROR')
    else:
        primary, category = None, None
    comparable = side == 'SALE' and not nonproduct and not identity_bad and q is not None and q > 0 and not (
        fields & {'Customer ID', 'StockCode', 'InvoiceDate', 'Quantity'}
    )
    row.update(issues=issues, side=side, line_kind='IDENTITY_PENDING' if identity_bad else
               'NONPRODUCT' if nonproduct else 'MERCHANDISE', primary_reason=primary,
               reason_category=category, candidate_comparable=int(comparable))
    return row
