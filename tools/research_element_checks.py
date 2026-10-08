"""Mechanical checks for a single value; no model or network calls."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from ipaddress import IPv6Address
import re
import unicodedata
from urllib.parse import urlsplit


class CheckConfigurationError(ValueError):
    pass


def valid_url(value: object) -> bool:
    if not isinstance(value, str) or re.search(r'\s', value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if not host:
            return False
        if ':' in host:
            IPv6Address(host)
        else:
            ascii_host = host.encode('idna').decode('ascii').rstrip('.')
            if len(ascii_host) > 253 or any(
                re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) is None
                for label in ascii_host.split('.')
            ):
                return False
        return (parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port != 0)
    except (ValueError, UnicodeError):
        return False


def parse_check(spec: str) -> tuple[str, str]:
    name, _, arg = spec.partition(':')
    plain = {'non_empty', 'ends_with_question', 'url_shape', 'url_in_ledger', 'one_sentence'}
    lists = {'contains_terms', 'contains_source_terms', 'forbidden', 'one_of'}
    keyed = {'reference_exists', 'exact_excerpt', 'not_similar_to', 'source_terms_or_sentence'}
    if name in plain and not arg and ':' not in spec:
        return name, arg
    if name in lists and arg and all(item.strip() for item in arg.split(',')):
        return name, arg
    if name in keyed and re.fullmatch(r'[A-Za-z0-9._-]+', arg):
        return name, arg
    if name in {'min_chars', 'max_chars'} and arg.isdigit() and int(arg) > 0:
        return name, arg
    if name == 'not_similar':
        try:
            if Decimal(0) < Decimal(arg) <= Decimal(1):
                return name, arg
        except InvalidOperation:
            pass
    raise CheckConfigurationError(f'unknown or malformed check: {spec}')


def _grams(value: str) -> set[str]:
    text = ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


def source_terms(value: str) -> set[str]:
    """Literal lexical anchors: Latin words and Japanese 2/3-character shingles.

    No morphological model or language-dependent external service is needed.
    Generic connective terms never suffice as source evidence.
    """
    stop = {'a', 'an', 'of', 'to', 'in', 'as', 'is', 'it', 'on', 'at', 'by', 'or', 'i',
            'the', 'and', 'for', 'with', 'from', 'that', 'this', 'ある', 'いる',
            'する', 'した', 'して', 'こと', 'もの', 'ため', 'れる', 'たい', 'いう',
            'という', 'できる', 'による', 'として'}
    text = unicodedata.normalize('NFKC', value)
    terms = set(re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text.casefold()))
    for token in re.findall(r'[\u3040-\u30ff\u3400-\u9fff]+', text):
        if len(token) == 1:
            terms.add(token)
        for size in (2, 3):
            terms.update(token[i:i + size] for i in range(len(token) - size + 1))
    return {term for term in terms - stop if re.search(r'[A-Za-z\u30a0-\u30ff\u3400-\u9fff]', term)}


def too_similar(left: str, right: str, threshold: str) -> bool:
    """NFKC/casefold/space-normalized character-bigram Jaccard, exact decimal comparison."""
    a, b = _grams(left), _grams(right)
    numerator, denominator = Decimal(threshold).as_integer_ratio()
    return len(a & b) * denominator >= numerator * len(a | b)


def check_value(value: object, checks: list[str], *, inputs: dict,
                ledger: dict, previous_answers: dict) -> list[dict[str, str]]:
    text = value.get('reason', '') if isinstance(value, dict) else value
    failures = []
    # This invariant applies even when a definition forgets to list non_empty.
    if not isinstance(text, str) or not text.strip():
        failures.append({'check': 'non_empty', 'reason': 'Return a non-empty value or reason.'})
        return failures
    for spec in checks:
        name, arg = parse_check(spec)
        ok = True
        reason = 'Value does not satisfy this check.'
        if name == 'non_empty':
            ok = bool(text.strip())
        elif name == 'min_chars':
            ok = len(text) >= int(arg)
            reason = f'Use at least {arg} characters.'
        elif name == 'max_chars':
            ok = len(text) <= int(arg)
            reason = f'Use at most {arg} characters.'
        elif name == 'ends_with_question':
            ok = text.rstrip().endswith(('?', '？'))
            reason = 'End the question with ? or ？.'
        elif name == 'one_sentence':
            ok = '\n' not in text.strip() and '\r' not in text.strip() and len(re.findall(r'[。!?！？]|(?<!\d)\.(?!\d)', text)) <= 1
            reason = 'Return one sentence only.'
        elif name == 'contains_source_terms':
            groups = [inputs.get(key) for key in arg.split(',')]
            ok = all(isinstance(body, str) and any(term in source_terms(text) or (len(term) == 1 and re.search(r'[\u30a0-\u30ff\u3400-\u9fff]', term) and term in unicodedata.normalize('NFKC', text)) for term in source_terms(body)) for body in groups)
            reason = 'Include at least one literal content term from each named source.'
        elif name == 'source_terms_or_sentence':
            question = inputs.get(arg, '')
            question_words = {'how', 'what', 'which', 'when', 'where', 'why', 'who',
                              'does', 'do', 'did', 'can', 'could', 'should', 'would'}
            anchored = bool(source_terms(text) & (source_terms(question) - question_words))
            body = ledger.get(inputs.get('source_url'), {}).get('body', '')
            start = body.find(text)
            prefix = body[:start].rstrip() if start >= 0 else ''
            sentence = (start >= 0 and (not prefix or prefix[-1] in '.!?。！？')
                        and text.rstrip().endswith(('.', '!', '?', '。', '！', '？'))
                        and '\n' not in text and '\r' not in text
                        and len(re.findall(r'[。!?！？]|(?<!\d)\.(?!\d)', text)) == 1)
            ok = anchored or sentence
            reason = 'Include a question content term, or copy one complete source sentence.'
        elif name == 'contains_terms':
            terms = [inputs.get(item, item) for item in arg.split(',')]
            ok = all(isinstance(term, str) and term.strip() and term in text for term in terms)
            reason = 'Include every required term (input key or literal).'
        elif name == 'forbidden':
            ok = all(term not in text for term in arg.split(','))
            reason = 'Remove the forbidden terms.'
        elif name == 'one_of':
            ok = text in arg.split(',')
            reason = 'Choose one of the listed values exactly.'
        elif name == 'reference_exists':
            ids = inputs.get(arg)
            ok = isinstance(ids, (list, dict)) and text in ids
            reason = 'Use an ID present in the supplied registry.'
        elif name in {'url_shape', 'url_in_ledger'}:
            ok = valid_url(text) and (name == 'url_shape' or text in ledger)
            reason = 'Use an HTTP(S) URL' + (' from the retrieved-source ledger.' if name == 'url_in_ledger' else '.')
        elif name == 'exact_excerpt':
            url = inputs.get(arg)
            source = ledger.get(url, {}) if isinstance(url, str) else {}
            body = source.get('body', '')
            # A quotation is an exact contiguous substring; no normalization.
            ok = bool(body) and text in body
            reason = 'Copy an exact contiguous passage from the retrieved body.'
        elif name == 'not_similar_to':
            prior = inputs.get(arg)
            prior = prior if isinstance(prior, list) else [prior]
            ok = all(isinstance(v, str) and not too_similar(text, v, inputs.get('similarity_threshold', '0.8')) for v in prior)
            reason = 'Use an operation distinct from the supplied material.'
        elif name == 'not_similar':
            old = [v.get('reason', '') if isinstance(v, dict) else v for v in previous_answers.values()]
            ok = all(not too_similar(text, v, arg) for v in old if isinstance(v, str) and v.strip())
            reason = 'The value is too similar to an already accepted answer.'
        if not ok:
            failures.append({'check': spec, 'reason': reason})
    return failures
