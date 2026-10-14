"""Read-only source-format inspection for review S1-S11; no app code changes."""
import hashlib
import json
import csv
import ast
import sys
from collections import Counter
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

BASE = Path(__file__).resolve().parents[3]
SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else BASE / 'VC-R-03-退货对账与差异处理系统/data/raw/online_retail_II.xlsx'
OUT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).with_name('numeric_source_inspection.json')
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
result = {'measured_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
          'scope': 'Both worksheets, every data cell in Quantity, InvoiceDate, Price and Customer ID; raw XLSX XML, not business acceptance',
          'source_sha256': before, 'sheets': {}}
variants = defaultdict(set)
requested = set()
pairs = []
with (SOURCE.parents[2] / 'evidence/data/review-2026-10-14/batch_line_results.csv').open(encoding='utf-8-sig', newline='') as stream:
    for item in csv.DictReader(stream):
        if item['candidate_count'] != '1':
            continue
        original = ast.literal_eval(item['original']) if item['original'].startswith("{'" ) else json.loads(item['original'])
        si = 1 if item['sheet'] == 'Year 2009-2010' else 2
        oi = 1 if original['sheet'] == 'Year 2009-2010' else 2
        left, right = (si, 'F' + item['excel_row']), (oi, 'F' + str(original['excel_row']))
        requested.update((left, right))
        pairs.append((left, right, Decimal(item['price_delta'])))
raw_prices = {}
with ZipFile(SOURCE) as archive:
    workbook = ET.fromstring(archive.read('xl/workbook.xml'))
    result['workbook_properties'] = [e.attrib for e in workbook.findall(NS + 'workbookPr')]
    for si in (1, 2):
        counts = Counter()
        price_values = set()
        examples = {}
        with archive.open(f'xl/worksheets/sheet{si}.xml') as stream:
            for event, row in ET.iterparse(stream, events=('end',)):
                if row.tag != NS + 'row':
                    continue
                if row.get('r') == '1':
                    row.clear()
                    continue
                counts['data_rows'] += 1
                found = set()
                for cell in row:
                    ref = cell.get('r', '')
                    col = ref.rstrip('0123456789')
                    if col not in {'D', 'E', 'F', 'G'}:
                        continue
                    found.add(col)
                    value = cell.findtext(NS + 'v')
                    kind = cell.get('t', 'n')
                    formula = cell.find(NS + 'f') is not None
                    counts[f'{col}:type:{kind}'] += 1
                    counts[f'{col}:formula'] += formula
                    if value is None:
                        counts[f'{col}:empty'] += 1
                        continue
                    if kind != 'n':
                        examples.setdefault(f'{col}:non_numeric', {'cell': ref, 'type': kind, 'raw': value})
                        continue
                    dec = Decimal(value)
                    counts[f'{col}:nonfinite'] += not dec.is_finite()
                    if not dec.is_finite():
                        continue
                    if col in {'D', 'G'}:
                        counts[f'{col}:noninteger'] += dec != dec.to_integral_value()
                    if col == 'F':
                        price_values.add(value)
                        cast = float(value) if any(c in value for c in '.eE') else int(value)
                        variants[str(Decimal(str(cast)).normalize())].add(value)
                        if (si, ref) in requested:
                            raw_prices[(si, ref)] = dec
                        unequal = Decimal(str(cast)) != dec
                        counts['Price:decimal_str_openpyxl_vs_xml_unequal'] += unequal
                        if unequal:
                            examples.setdefault('Price:unequal', {'cell': ref, 'xml': value, 'cast_str': str(cast)})
                for col in {'D', 'E', 'F', 'G'} - found:
                    counts[f'{col}:absent'] += 1
                row.clear()
        result['sheets'][f'sheet{si}'] = {'counts': dict(counts), 'examples': examples,
            'distinct_price_lexemes': len(price_values), 'max_price_significant_digits': max(len(Decimal(v).as_tuple().digits) for v in price_values)}
result['source_unchanged'] = before == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
collisions = {key: sorted(values) for key, values in variants.items() if len({Decimal(v) for v in values}) > 1}
result['different_xml_numeric_values_with_same_read_value'] = collisions
result['unique_candidate_price_comparison'] = {'pairs': len(pairs),
    'changed_equal_unequal_decisions': sum(((raw_prices[l] == raw_prices[r]) != (old == 0)) for l,r,old in pairs),
    'raw_xml_equal': sum(raw_prices[l] == raw_prices[r] for l,r,old in pairs),
    'raw_xml_unequal': sum(raw_prices[l] != raw_prices[r] for l,r,old in pairs)}
assert result['source_unchanged']
result['inspection_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
