#!/usr/bin/env python3
"""Standalone Stage B: one request, one answer, mechanical checks and program assembly."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from _common import ROOT, atomic_write_text, load_json, load_yaml, stable_json
from research_element_contracts import check_answer, require_message
from research_element_checks import valid_url


def digest(text):
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError) as exc:
        raise ValueError('--now must be RFC3339 with timezone') from exc
    if result.tzinfo is None:
        raise ValueError('--now must include timezone')
    return result


class Engine:
    """A locked checkpoint owns all values; files are recoverable projections of it.

    The old task runtime is deliberately not completed by this standalone driver.
    Its leases and whole-role acceptance remain the legacy execution contract.
    """
    def __init__(self, project: Path):
        path = project.expanduser().resolve()
        if project.expanduser().is_symlink() or path == Path(path.anchor):
            raise ValueError(f'{project}: use a real external project directory without symlinks')
        if ROOT == path or ROOT in path.parents or any((p / '.git').exists() for p in (path, *path.parents)):
            raise ValueError(f'{project}: research elements and retrieved bodies must stay outside Git')
        if not (path / 'manifest.yaml').is_file():
            raise ValueError(f'{project}: manifest.yaml is required')
        self.project = path
        self.config = load_yaml(ROOT / 'config/research-elements.yaml')
        from research_element_validation import require_schema
        require_schema(ROOT, 'research-elements-config', self.config)
        self.path = self.safe(self.config['paths']['state'])

    def safe(self, relative):
        path = self.project / relative
        if path.resolve() != path or self.project not in path.parents:
            raise ValueError(f'{relative}: unsafe project target')
        return path

    @contextmanager
    def locked(self):
        lock = self.safe(self.config['paths']['lock'])
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def inputs(self):
        return {relative: self.safe(relative).read_text(encoding='utf-8') for relative in (
            'manifest.yaml', '00_intake/creative-intent.md', '00_intake/constraints.yaml',
            '01_planning/research-plan.yaml')}

    def initialize(self, now, run_id, history_root=None, rights_table=None):
        from completion_quality import load_completion_quality_policy
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id):
            raise ValueError('Unsafe run ID')
        inputs = self.inputs()
        intent = inputs['00_intake/creative-intent.md'].strip()
        section = re.search(r'^## (?:現時点の中心命題|中心の問い|Central question)\s*\n(.*?)(?=^## |\Z)', intent, re.M | re.S)
        proposition = section.group(1).strip() if section else intent
        if not proposition or len(proposition.encode('utf-8')) > 2000:
            raise ValueError('00_intake/creative-intent.md: provide a concise Stage A proposition (at most 2000 UTF-8 bytes)')
        if any(word in proposition for word in [*self.config['forbidden'], '未確認']):
            raise ValueError('00_intake/creative-intent.md: a filled Stage A proposition is required')
        plan = load_yaml(self.safe('01_planning/research-plan.yaml'))
        from search_harness import _validate
        _validate(ROOT, 'research-plan', plan)
        config = deepcopy(self.config)
        if rights_table is not None:
            from research_element_validation import require_schema
            table = load_json(rights_table)
            require_schema(ROOT, 'research-element-rights', table)
            config['rights_by_url'] = table
        from research_element_validation import require_schema
        require_schema(ROOT, 'research-elements-config', config)
        config.update(plan.get('element_research', {}))
        policy = load_yaml(ROOT / 'config/stopping-policy.yaml')['defaults']
        quality = load_completion_quality_policy(ROOT, plan)
        count = min(config['question_count'], plan['budget']['max_questions'])
        budget = {**plan['budget'], 'question_count': count,
                  'sufficient_answers': max(1, math.ceil(max(quality.minimums['evidence'], quality.minimums['claims']) / count))}
        # Freeze vocabularies and policy; resuming a run never quietly adopts new defaults.
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            marker = ROOT / '.archive-commit'
            commit = marker.read_text().strip() if marker.exists() else ''
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Protocol source commit unavailable')
        state = {'contract_version': 'research-elements/v1', 'run_id': run_id, 'project_id': plan['project_id'],
                 'source_commit': commit, 'started_at': now, 'last_now': now,
                 'inputs': inputs, 'proposition': proposition, 'config': config, 'stopping': policy, 'budget': budget,
                 'minimums': quality.minimums, 'required_records': list(quality.required_records),
                 'vocabulary': load_yaml(ROOT / 'config/vocabularies.yaml'),
                 'answers': {}, 'ledger': {}, 'history': [], 'attempt': 1, 'previous_failure': None,
                 'status': 'WAITING', 'pending': None, 'files': {}, 'previous_files': {},
                 'history_root': str(history_root.resolve()) if history_root else None,
                 'history_snapshot': self.history_snapshot(history_root) if history_root else {},
                 'security_patterns': load_yaml(ROOT / 'config/access-policy.yaml')['secret_patterns'],
                 'baseline_log': self.safe(config['paths']['log']).read_text(encoding='utf-8')}
        from research_element_driver import managed_empty_files
        for relative, empty in managed_empty_files(config).items():
            path = self.safe(relative)
            actual = path.read_text(encoding='utf-8') if path.exists() else ''
            # Refuse to overwrite legacy or manually authored research.
            if relative.endswith('.yaml'):
                if (load_yaml(path) if path.exists() else {}) != empty:
                    raise ValueError(f'{relative}: existing research cannot be overwritten; use a fresh project')
            elif actual.strip():
                raise ValueError(f'{relative}: existing research cannot be overwritten; use a fresh project')
            state['previous_files'][relative] = actual
        state['previous_files'][config['paths']['log']] = state['baseline_log']
        return state

    def read(self):
        state = load_json(self.path)
        from research_element_validation import require_schema
        require_schema(ROOT, 'research-elements', state)
        if state.get('contract_version') != 'research-elements/v1':
            raise ValueError(f'{self.path}: unsupported checkpoint')
        if self.inputs() != state['inputs']:
            raise ValueError('Project input changed since the run started')
        if state['history_root'] and self.history_snapshot(Path(state['history_root'])) != state['history_snapshot']:
            raise ValueError('History inputs changed since the run started')
        for url, source in state['ledger'].items():
            if source['url'] != url or source['content_hash'] != digest(source['body']):
                raise ValueError('Pinned source body/hash mismatch')
        return state

    def save(self, state):
        from research_element_validation import require_schema
        require_schema(ROOT, 'research-elements', state)
        atomic_write_text(self.path, stable_json(state))
        os.chmod(self.path, 0o600)

    def materialize(self, state):
        # Preflight every file before any write. During recovery a file may have
        # either checkpoint generation; arbitrary third-party changes fail closed.
        for relative, content in state['files'].items():
            path = self.safe(relative)
            actual = path.read_text(encoding='utf-8') if path.exists() else ''
            if actual not in (content, state['previous_files'].get(relative, '')):
                raise ValueError(f'{relative}: output changed outside the element engine')
        for relative, content in state['files'].items():
            path = self.safe(relative)
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                atomic_write_text(path, content)

    def advance(self, state):
        from research_element_driver import advance
        state['previous_files'] = state['files'] or state['previous_files']
        advance(state)
        if state['pending'] is None:
            self.finish(state)
        self.save(state)  # Authoritative answers first; next() repairs interrupted projections.
        self.materialize(state)

    def report(self, state):
        return {'run_id': state['run_id'], 'status': state['status'],
                'accepted_count': len(state['answers']), 'blocked': state.get('blocked'),
                'completion': state.get('completion'),
                'next_action': {'kind': 'element', 'request': deepcopy(state['pending'])} if state['pending'] else None}

    def check_time(self, state, now):
        current = timestamp(now)
        if current < timestamp(state['last_now']):
            raise ValueError('--now cannot move backwards')
        state['last_now'] = now
        if state['status'] == 'WAITING' and (current - timestamp(state['started_at'])).total_seconds() >= state['budget']['max_runtime_minutes'] * 60:
            state.update(status='BLOCKED', pending=None, blocked={
                'element_id': state['pending']['element_id'], 'last_failure': [
                    {'check': 'runtime_budget', 'reason': 'Project runtime budget exhausted.'}]})
            self.advance(state)

    def next(self, *, now, run_id='research', history_root=None, rights_table=None):
        timestamp(now)
        with self.locked():
            if self.path.exists():
                state = self.read()
                if history_root is not None and str(history_root.resolve()) != state['history_root']:
                    raise ValueError('History root differs from the saved run')
                if rights_table is not None and load_json(rights_table) != state['config']['rights_by_url']:
                    raise ValueError('Rights table differs from the saved run')
                self.materialize(state)
                self.check_time(state, now)
                self.save(state)
            else:
                state = self.initialize(now, run_id, history_root.resolve() if history_root else None, rights_table)
                self.advance(state)
            return self.report(state)

    def answer(self, answer, *, now):
        timestamp(now)
        with self.locked():
            state = self.read()
            self.materialize(state)
            was_waiting = state['status'] == 'WAITING'
            self.check_time(state, now)
            if was_waiting and state['status'] == 'BLOCKED':
                return self.report(state)
            answer_hash = digest(canonical(answer))
            # Replay is exact and has no second effect, but cannot grant a
            # fresh request after the absolute runtime deadline.
            if any(item['answer_hash'] == answer_hash for item in state['history']):
                self.save(state)
                return self.report(state)
            if state['status'] != 'WAITING':
                raise ValueError('Run is not waiting for an answer')
            request = state['pending']
            kind = request['contract_version'].split('-')[0]
            require_message(answer, f'{kind}-answer')
            if any(answer[key] != request[key] for key in ('run_id', 'element_id', 'attempt')):
                raise ValueError('Answer does not match pending identity/attempt')
            if kind == 'search':
                failures = self.check_search(state, request, answer)
                value = answer['results']
            else:
                failures = check_answer(request, answer, state['ledger'], state['answers'])
                if request['element_id'].startswith('excerpt.') and isinstance(value := answer['value'], str) and value not in request['inputs']['body']:
                    failures.append({'check': 'excerpt_window', 'reason': 'Copy a passage from the supplied body window.'})
                value = answer['value']
            state['history'].append({'element_id': request['element_id'], 'attempt': request['attempt'],
                                     'answer_hash': answer_hash, 'occurred_at': now, 'failures': failures})
            if failures:
                state['previous_failure'] = failures
                if state['attempt'] >= state['config']['max_attempts']:
                    state.update(status='BLOCKED', pending=None,
                                 blocked={'element_id': request['element_id'], 'attempt': state['attempt'],
                                          'last_failure': failures})
                else:
                    state['attempt'] += 1
            else:
                state['answers'][request['element_id']] = deepcopy(value)
                if kind == 'search':
                    for source in value:
                        if source['url'] not in state['ledger']:
                            state['ledger'][source['url']] = {**source, 'id': f"SRC{len(state['ledger'])+1:03}", 'acquired_at': now, 'content_hash': digest(source['body'])}
                state.update(attempt=1, previous_failure=None)
            self.advance(state)
            return self.report(state)

    def history_snapshot(self, root):
        from self_repetition import ARTIFACT_NAMES
        if not root.is_dir() or root.is_symlink():
            raise ValueError('--history-root must be an available real directory')
        names = {Path(name).name for name in ARTIFACT_NAMES} | {'manifest.yaml'}
        files = {}
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.name in names:
                if path.resolve() != path or root.resolve() not in path.resolve().parents:
                    raise ValueError('History input contains a symlink')
                files[path.relative_to(root).as_posix()] = digest(path.read_text(encoding='utf-8'))
        return files

    def finish(self, state):
        from completion_quality import CompletionQualityPolicy, quality_failures
        config = state['config']
        # Reuse the native scanner, including its creator scope and risk thresholds.
        if 'repetition_scan' not in state and state['record_counts']['claims']:
            if state['history_root']:
                from self_repetition import scan_projects, CONTRACT_VERSION_V2
                with tempfile.TemporaryDirectory() as directory:
                    candidate = Path(directory)
                    atomic_write_text(candidate / 'manifest.yaml', state['inputs']['manifest.yaml'])
                    atomic_write_text(candidate / config['paths']['claims'], state['files'][config['paths']['claims']])
                    state['repetition_scan'] = scan_projects(candidate, Path(state['history_root']),
                        repository='masa-san-jp/agentic-art-research', source_commit=state['source_commit'],
                        now=state['last_now'], contract_version=CONTRACT_VERSION_V2)
                    # An empty corpus cannot measure the creator's prior practice.
                    if not state['repetition_scan']['scanned_project_count']:
                        state['repetition_scan'].update(risk_level='UNKNOWN',
                            assessment='No comparable creator history is available.', mitigation='Provide comparable history before handoff.')
            else:
                state['repetition_scan'] = {'risk_level': 'UNKNOWN', 'history_access': {
                    'status': 'UNAVAILABLE', 'reason_code': 'HISTORY_ROOT_UNAVAILABLE'},
                    'assessment': 'Creator history was not supplied; repetition is unmeasured.',
                    'mitigation': 'Supply explicit creator history and rescan before handoff.', 'matches': []}
        reviews = []
        scan = state.get('repetition_scan')
        if scan:
            state['files'][config['paths']['repetition_report']] = stable_json(scan)
            if scan['risk_level'] != 'UNKNOWN':
                # Existing report refs or the explicit pinned corpus identify the comparison basis.
                refs = sorted({match['prior_signal_ref'] for match in scan['matches']}) or [
                    'history-sha256:' + digest(canonical(state['history_snapshot'])).split(':', 1)[1]]
                reviews = [{'id': 'SR001', 'scope': 'Stage B claim-level creator history (Stage C mechanism scan remains required)',
                    'prior_work_refs': refs, 'risk_level': scan['risk_level'], 'assessment': scan['assessment'],
                    'mitigation': scan['mitigation'], 'reviewed_at': state['last_now']}]
        state['files'][config['paths']['reviews']] = __import__('yaml').safe_dump({'reviews': reviews}, allow_unicode=True, sort_keys=False)
        counts = {key: state['record_counts'].get(key, 0) for key in state['minimums']}
        counts.update(rejected_options=state['record_counts']['rejected'], uncertainty=state['record_counts']['uncertainties'],
                      prior_art=state['record_counts']['prior_art'], self_repetition_review=len(reviews))
        failures = quality_failures(CompletionQualityPolicy(state['minimums'], tuple(state['required_records'])), counts)
        state['completion'] = {'stage': 'B', 'status': 'INCOMPLETE' if failures or state['status'] == 'BLOCKED' else (
            'COMPLETE_WITH_GAPS' if state['question_gaps'] or state['record_counts']['contradictions'] or state['record_counts']['uncertainties'] else 'COMPLETE'),
            'counts': counts, 'quality_failures': failures, 'unresolved_questions': state['question_gaps'],
            'next_start': 'Stage C and the existing production-brief / native project acceptance gates.',
            'project_completed': False}

    def check_search(self, state, request, answer):
        failures = []
        def fail(check, reason):
            failures.append({'check': check, 'reason': reason})
        results = answer['results']
        # Empty searches are valid evidence rounds and participate in saturation.
        if len(results) > request['limit']:
            fail('search_limit', 'Return at most the requested number of results.')
        if len(canonical(answer).encode()) > state['config']['max_search_answer_bytes']:
            fail('search_bytes', 'Retrieved answer exceeds the configured byte budget.')
        urls = [r['url'] for r in results]
        if len(set(urls)) != len(urls):
            fail('unique_urls', 'Return each URL once.')
        for source in results:
            if any(re.search(spec['pattern'], source['title']+'\n'+source['body']) for spec in state['security_patterns']):
                fail('secret_output', 'Retrieved content matches a prohibited credential pattern.')
            if not valid_url(source['url']):
                fail('url_shape', 'Use HTTP(S) without embedded credentials.')
            if not source['title'].strip() or not source['body'].strip():
                fail('non_empty', 'Return a non-empty title and retrieved body.')
            question = state['answers']['question.' + request['element_id'].split('.')[1]]
            base = len(json.dumps({'question': question, 'body': '', 'source_url': source['url']}, ensure_ascii=False).encode())
            if base + 256 > state['config']['input_bytes']:
                fail('source_metadata_bytes', 'Source metadata leaves insufficient room for a bounded body window.')
            if len(source['body'].encode()) > state['config']['max_search_body_bytes']:
                fail('body_bytes', 'Retrieved body exceeds the configured byte budget.')
            old = state['ledger'].get(source['url'])
            if old and (old['body'] != source['body'] or old['title'] != source['title']):
                fail('source_conflict', 'This URL already has different pinned content.')
        return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['next', 'answer'])
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-id', default='research', help='identity for a new run')
    parser.add_argument('--history-root', type=Path, help='explicit creator-scoped historical projects (new run only)')
    parser.add_argument('--rights-table', type=Path, help='explicit URL-keyed verified rights JSON table (new run only)')
    parser.add_argument('--now', help='RFC3339; defaults to current UTC time')
    args = parser.parse_args()
    try:
        now = args.now or datetime.now(timezone.utc).isoformat(timespec='seconds')
        engine = Engine(args.project)
        if args.command == 'next':
            result = engine.next(now=now, run_id=args.run_id, history_root=args.history_root, rights_table=args.rights_table)
        else:
            # Bound stdin before JSON decoding; never invoke a provider or network.
            raw = sys.stdin.buffer.read(engine.config['max_search_answer_bytes'] + 1)
            if len(raw) > engine.config['max_search_answer_bytes']:
                raise ValueError('Answer envelope exceeds input byte budget')
            result = engine.answer(json.loads(raw), now=now)
    except (ValueError, OSError) as exc:
        print(f'research-elements: {exc}', file=sys.stderr)
        return 2
    print(stable_json(result), end='')
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
