"""Pure DP-10/14 computation. No ledger mutations or implicit normalization."""
from bisect import bisect_right
from collections import Counter, defaultdict
from decimal import Decimal, Inexact, Rounded, localcontext

MATCH_VERSION = 'matching-d38-v1'
RULES = {'version': MATCH_VERSION, 'candidate': 'same-normalized-customer-stock; positive-sale; sale-time<=cancel-time',
         'price_tolerance': '0', 'demand': 'all-unique-claims-including-price-failures',
         'quality': 'd38-conflicts-visible-candidate-quality-propagated', 'combination': 'hint-only'}
NO_KEY = {'MISSING_CUSTOMER', 'MISSING_STOCK', 'KEY_INVALID'}


def exact(operation, *values):
    """Choose precision from operand exponents, rejecting rounded arithmetic."""
    nums = [Decimal(str(v)) for v in values]
    if not all(n.is_finite() for n in nums):
        raise ValueError('Non-finite decimal operand')
    with localcontext() as ctx:
        ctx.prec = max(28, sum(len(n.as_tuple().digits) + abs(n.as_tuple().exponent) for n in nums) + 20)
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        if operation == 'multiply':
            return nums[0] * nums[1]
        if operation == 'subtract':
            return nums[0] - nums[1]
        return sum(nums, Decimal(0))


def issue(code, category, line_id, field=None):
    return {'code': code, 'category': category, 'origin_line_id': line_id, 'field': field}


def inherited_quality(original):
    tags = [issue(x['code'], 'DATA_QUALITY', original['line_id'], x['field']) for x in original['quality']]
    codes = {t['code'] for t in tags}
    primary = next((c for c in ('INVOICE_DEDUP_TYPE_CONFLICT', 'INVOICE_VERSION_CONFLICT') if c in codes), None)
    if primary is None and any(t['field'] == 'Price' for t in tags):
        primary = 'PRICE_INVALID_OR_NONPOSITIVE'
    if primary is None and tags:
        primary = 'TEXT_FIELD_FORMULA_OR_ERROR'
    return tags, primary


def analyze(sales, cancellations):
    """Every result/candidate and global demand, independent of input order."""
    index = defaultdict(list)
    for row in sales:
        index[(row['customer_key'], row['stock_key'])].append(row)
    for rows in index.values():
        rows.sort(key=lambda r: (r['invoice_date'], r['line_id']))
    dates = {key: [r['invoice_date'] for r in rows] for key, rows in index.items()}
    results, candidates, demands = [], [], defaultdict(list)
    originals = {r['line_id']: r for r in sales}
    for row in sorted(cancellations, key=lambda r: r['line_id']):
        lid, q = row['line_id'], row['quantity']
        tags = [issue(t['code'], 'DATA_QUALITY', lid, t['field']) for t in row['quality']]
        result = {'cancel_line_id': lid, 'invoice_key': row['invoice_key'], 'classification': 'SUSPECTED',
                  'candidate_computed': 0, 'candidate_count': None, 'original_line_id': None,
                  'requested_quantity': -q if q is not None and q < 0 else None, 'price_delta': None,
                  'primary_reason': row['primary_reason'], 'reason_tags': tags,
                  'own_quality': int(bool(tags)), 'candidate_quality': 0}
        if tags:
            if result['primary_reason'] in NO_KEY:
                result['classification'] = 'NO_ORIGINAL'
            results.append(result)
            continue
        key = (row['customer_key'], row['stock_key'])
        pool = index.get(key, [])
        selected = pool[:bisect_right(dates.get(key, []), row['invoice_date'])]
        result.update(candidate_computed=1, candidate_count=len(selected))
        for original in selected:
            inherited, _ = inherited_quality(original)
            delta = str(exact('subtract', row['price'], original['price'])) if original['price'] is not None else None
            candidates.append({'cancel_line_id': lid, 'original_line_id': original['line_id'], 'price_delta': delta,
                               'quantity_delta': -q-original['quantity'], 'quality': inherited})
            tags.extend(inherited)
            result['candidate_quality'] |= int(bool(inherited))
        if not selected:
            primary = 'ONLY_LATER_SALES' if pool else 'NO_SAME_CUSTOMER_STOCK_IN_DATASET'
            result.update(classification='NO_ORIGINAL', primary_reason=primary)
            tags.append(issue(primary, 'MATCHING_DISCREPANCY', lid))
        elif len(selected) > 1:
            result['primary_reason'] = 'MULTIPLE_CANDIDATES'
            tags.append(issue('MULTIPLE_CANDIDATES', 'MATCHING_DISCREPANCY', lid))
            quantities = [r['quantity'] for r in selected]
            if max(quantities) < -q <= sum(quantities):
                tags.append(issue('COMBINATION_POSSIBLE_NOT_PROVEN', 'MATCHING_DISCREPANCY', lid))
        else:
            original = selected[0]
            oid = original['line_id']
            result['original_line_id'] = oid
            demands[oid].append(result)
            _, primary = inherited_quality(original)
            result['primary_reason'] = primary
            if original['price'] is not None:
                delta = exact('subtract', row['price'], original['price'])
                result['price_delta'] = str(delta)
                if delta != 0:
                    tags.append(issue('PRICE_DIFFERENCE', 'MATCHING_DISCREPANCY', lid, 'Price'))
                    result['primary_reason'] = primary or 'PRICE_DIFFERENCE'
            if -q > original['quantity']:
                tags.append(issue('SINGLE_RETURN_EXCEEDS_ORIGINAL', 'MATCHING_DISCREPANCY', lid, 'Quantity'))
                result['primary_reason'] = result['primary_reason'] or 'SINGLE_RETURN_EXCEEDS_ORIGINAL'
        results.append(result)
    quota = []
    for oid, claims in sorted(demands.items()):
        total = sum(r['requested_quantity'] for r in claims)
        over = total > originals[oid]['quantity']
        quota.append({'original_line_id': oid, 'original_quantity': originals[oid]['quantity'],
                      'requested_quantity': total, 'claim_count': len(claims), 'over_quota': int(over)})
        if over:
            for r in claims:
                r['reason_tags'].append(issue('CUMULATIVE_OVER_QUOTA', 'MATCHING_DISCREPANCY', oid, 'Quantity'))
                r['primary_reason'] = r['primary_reason'] or 'CUMULATIVE_OVER_QUOTA'
    for r in results:
        if r['candidate_count'] == 1 and not r['reason_tags']:
            r['classification'] = 'MATCHED_AUTO'
        r['reason_categories'] = sorted({t['category'] for t in r['reason_tags']})
    return results, candidates, quota


def summarize_invoices(cancel_rows, results):
    groups = defaultdict(list)
    by_id = {r['cancel_line_id']: r for r in results}
    invoices = []
    for row in cancel_rows:
        groups[row['invoice_key']].append(row)
    for invoice, rows in sorted(groups.items()):
        goods = [r for r in rows if r['line_kind'] != 'NONPRODUCT']
        nonproduct = [r for r in rows if r['line_kind'] == 'NONPRODUCT']
        lines = [by_id[r['line_id']] for r in goods]
        counts = Counter(r['classification'] for r in lines)
        classification = ('NONPRODUCT_ONLY' if not lines else 'NO_ORIGINAL' if counts['NO_ORIGINAL'] else
                          'SUSPECTED' if counts['SUSPECTED'] else 'MATCHED_AUTO')
        reasons = [dict(t, cancel_line_id=r['cancel_line_id']) for r in lines for t in r['reason_tags']]
        categories = sorted({t['category'] for t in reasons})
        ticket_type = 'MIXED' if len(categories) > 1 else categories[0] if categories else None
        amounts = {}
        invalid = 0
        for name, subset in [('commodity_amount', goods), ('nonproduct_amount', nonproduct)]:
            valid = [r for r in subset if r['quantity'] is not None and r['price'] is not None]
            invalid += len(subset)-len(valid)
            amounts[name] = str(exact('sum', *(exact('multiply', r['quantity'], r['price']) for r in valid))) if len(valid) == len(subset) else None
        amounts['total_amount'] = str(exact('sum', *amounts.values())) if invalid == 0 else None
        invoices.append({'invoice_key': invoice, 'classification': classification, 'ticket_type': ticket_type,
                         'commodity_line_ids': [r['line_id'] for r in goods],
                         'nonproduct_line_ids': [r['line_id'] for r in nonproduct], 'line_counts': dict(counts),
                         'reason_categories': categories, 'reasons': reasons, 'amounts': amounts,
                         'invalid_amount_rows': invalid, 'amount_complete': invalid == 0})
    return invoices
