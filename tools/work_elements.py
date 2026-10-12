#!/usr/bin/env python3
"""Stage C: the work's content from completed Stage B research, one request and one answer at a time.

The same wire contracts, checks and retries as Stage B (`research_elements.py`). Stage C reads
the completed Stage B checkpoint, pins its hash, and owns only the files it writes
(production hypotheses, comparison, requirements, acceptance tests, prototype plan,
creative direction and production brief). It never calls a model or the network.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys

import yaml

from _common import ROOT, atomic_write_text, load_json, load_yaml, stable_json
from research_element_contracts import check_answer, require_message
from research_elements import Engine as ResearchEngine, canonical, digest, timestamp

CONTRACT = 'work-elements/v1'


def stage_b_digest(state):
    """Hash of the completed Stage B content. `last_now` is excluded: every Stage B `next` advances
    it, and a driver that asks Stage B first must not invalidate a running Stage C."""
    return digest(canonical({key: value for key, value in state.items() if key != 'last_now'}))


def next_element(project, *, now, run_id='research', dry_run=False):
    """One entry for drivers: Stage B until it is COMPLETED with a decision, then Stage C."""
    work = WorkEngine(project)
    if not work.path.exists():
        research = work.research
        report = research.preview(now=now, run_id=run_id) if dry_run else research.next(now=now, run_id=run_id)
        if report['status'] != 'COMPLETED' or not report['completion']['counts'].get('decisions'):
            return report
    return work.preview(now=now, run_id=run_id) if dry_run else work.next(now=now, run_id=run_id)


def jsonl(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def research_snapshot(stage_b, manifest):
    """Everything Stage C needs from Stage B, read from its authoritative checkpoint files."""
    paths, files = stage_b['config']['paths'], stage_b['files']

    def collection(key, name):
        return (yaml.safe_load(files[paths[key]]) or {}).get(name, [])
    sources = jsonl(files[paths['sources']])
    evidence = jsonl(files[paths['evidence']])
    by_url = {s['url']: s['id'] for s in sources}
    evidence_source = {e['id']: by_url.get(e['source_location']) for e in evidence}
    decisions = collection('decisions', 'decisions')
    for decision in decisions:
        decision['source_ids'] = [evidence_source[e] for e in decision['evidence_ids'] if evidence_source.get(e)]
    uncertainties = collection('uncertainties', 'uncertainties')
    completion = stage_b.get('completion') or {}
    counts = completion.get('counts', {})
    failures = [f"{key} {counts.get(key, 0)}/{minimum}" for key, minimum in stage_b['minimums'].items()
                if key != 'requirements' and counts.get(key, 0) < minimum]
    failures += [key for key in stage_b['required_records'] if counts.get(key, 0) < 1]
    acquired = sorted(s['acquired_at'] for s in stage_b['ledger'].values())
    return {
        'title': str((manifest.get('project') or {}).get('title') or stage_b['project_id']),
        'proposition': stage_b['proposition'],
        'questions': [{'id': q['id'], 'question': q['question'], 'status': q['status']} for q in collection('questions', 'questions')],
        'claims': [{k: c[k] for k in ('id', 'statement', 'evidence_ids')} for c in jsonl(files[paths['claims']])],
        'insights': [{k: i[k] for k in ('id', 'statement', 'claim_ids', 'opposing_claim_ids')} for i in collection('insights', 'insights')],
        'decisions': [{k: d[k] for k in ('id', 'question', 'selected_option', 'insight_ids', 'evidence_ids', 'source_ids')} for d in decisions],
        'prior_art': [{k: p[k] for k in ('id', 'work_title', 'source_url', 'difference', 'relation_to_proposal')}
                      for p in jsonl(files[paths['prior_art']])],
        'sources': [{'id': s['id'], 'title': s['title'], 'url': s['url'], 'duplicate_of': s.get('duplicate_of')} for s in sources],
        'counts': {**stage_b['record_counts'], **{k: v for k, v in counts.items() if isinstance(v, int)}},
        'question_gaps': list(stage_b['question_gaps']),
        'quality_failures': failures,
        'repetition_scan': {k: v for k, v in (stage_b.get('repetition_scan') or {}).items()
                            if k in ('risk_level', 'assessment', 'mitigation', 'matches')},
        'max_uncertainty': max([int(u['id'][1:]) for u in uncertainties] or [0]),
        'acquired_through': acquired[-1][:10] if acquired else stage_b['last_now'][:10],
        'minimums': stage_b['minimums'], 'required_records': stage_b['required_records'],
        'stage_b_counts': counts,
    }


class WorkEngine:
    """A locked checkpoint owns all Stage C values; files are projections written when complete."""

    def __init__(self, project: Path):
        self.research = ResearchEngine(project)  # Same project safety rules as Stage B.
        self.project = self.research.project
        self.config = load_yaml(ROOT / 'config/work-elements.yaml')
        from research_element_validation import require_schema
        require_schema(ROOT, 'work-elements-config', self.config)
        owned = set(self.research.config['paths'].values())
        overlap = owned & set(self.config['paths'].values())
        if overlap:
            raise ValueError('Stage C must not write Stage B files: ' + ', '.join(sorted(overlap)))
        self.path = self.research.safe(self.config['paths']['state'])

    def safe(self, relative):
        return self.research.safe(relative)

    @contextmanager
    def locked(self):
        lock = self.safe(self.config['paths']['lock'])
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def stage_b(self):
        if not self.research.path.exists():
            raise ValueError('Stage C needs a Stage B run; run research_elements.py first')
        state = self.research.read()  # Verifies Stage B inputs and pinned source hashes.
        if state['status'] != 'COMPLETED':
            raise ValueError(f"Stage C needs a COMPLETED Stage B run (status {state['status']})")
        return state, stage_b_digest(state)

    def fresh_outputs(self):
        """Current output files; refuse anything but the untouched project templates."""
        current = {}
        templates = ROOT / 'templates/project'
        for key, relative in self.config['paths'].items():
            if key in ('state', 'lock'):
                continue
            path = self.safe(relative)
            text = path.read_text(encoding='utf-8') if path.exists() else ''
            current[relative] = text
            if not text.strip():
                continue
            if relative.endswith('.md'):
                sections = re.findall(r'^## [^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
                fresh = all(body.strip() in ('', '未確定。') for body in sections)
            else:
                value = load_yaml(path)
                template = load_yaml(templates / relative) if (templates / relative).exists() else None
                fresh = value == template or (isinstance(value, dict) and len(value) == 1
                                              and list(value.values()) == [[]])
            if not fresh:
                raise ValueError(f'{relative}: existing work content cannot be overwritten; use a fresh project')
        return current

    def initialize(self, now, run_id):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id):
            raise ValueError('Unsafe run ID')
        stage_b, stage_b_hash = self.stage_b()
        research = research_snapshot(stage_b, load_yaml(self.safe('manifest.yaml')) or {})
        if not research['decisions']:
            raise ValueError('Stage B produced no adopted decision; Stage C has nothing to build a work plan on')
        from work_element_driver import select_plans
        state = {'contract_version': CONTRACT, 'run_id': run_id, 'project_id': stage_b['project_id'],
                 'stage_b_run_id': stage_b['run_id'], 'stage_b_hash': stage_b_hash,
                 'source_commit': stage_b['source_commit'], 'started_at': now, 'last_now': now,
                 'config': deepcopy(self.config), 'research': research,
                 'plans': select_plans(research, self.config['plan_count']),
                 'answers': {}, 'abandoned': {}, 'history': [], 'attempt': 1, 'previous_failure': None,
                 'status': 'WAITING', 'pending': None, 'files': {}, 'previous_files': self.fresh_outputs(),
                 'record_counts': {}}
        return state

    def read(self):
        state = load_json(self.path)
        from research_element_validation import require_schema
        require_schema(ROOT, 'work-elements', state)
        _, stage_b_hash = self.stage_b()
        if stage_b_hash != state['stage_b_hash']:
            raise ValueError('Stage B checkpoint changed since Stage C started; start Stage C in a fresh project')
        return state

    def save(self, state):
        from research_element_validation import require_schema
        require_schema(ROOT, 'work-elements', state)
        atomic_write_text(self.path, stable_json(state))
        os.chmod(self.path, 0o600)

    def materialize(self, state):
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
        from work_element_driver import advance
        advance(state)
        if state['pending'] is None:
            self.finish(state)
        self.save(state)  # Authoritative answers first; next() repairs an interrupted projection.
        self.materialize(state)

    def finish(self, state):
        from completion_quality import CompletionQualityPolicy, quality_failures
        research = state['research']
        counts = dict(research['stage_b_counts'])
        counts['requirements'] = state['record_counts'].get('requirements', 0)
        failures = quality_failures(CompletionQualityPolicy(research['minimums'], tuple(research['required_records'])), counts)
        judgement = (state.get('mechanism_rounds') or [{}])[-1].get('judgement')
        if judgement == 'MERELY_PLAINER':
            # The plan is kept, but production treats this as a blocking gap; do not report COMPLETE.
            failures.append({'id': 'WORK-MECHANISM-MERELY-PLAINER',
                             'reason': 'After every mechanism round the work still only gets plainer without its technique.',
                             'impact': 'Production records a blocking gap; revise the mechanism before handoff.'})
        state['completion'] = {
            'stage': 'C', 'status': 'INCOMPLETE' if failures or state['status'] == 'BLOCKED' else 'COMPLETE',
            'counts': counts, 'quality_failures': failures,
            'recommended_hypothesis_id': state.get('recommended_hypothesis_id'),
            'without_the_technique': (state.get('mechanism_rounds') or [{}])[-1].get('judgement'),
            'dropped_plans': state['record_counts'].get('dropped_plans', []),
            'next_start': 'Stage D (agentic-art-production build_plan) and the native rights/privacy reviews.',
            'project_completed': False}

    def report(self, state):
        return {'run_id': state['run_id'], 'stage': 'C', 'answer_with': 'tools/work_elements.py answer', 'status': state['status'],
                'accepted_count': len(state['answers']), 'abandoned': sorted(state['abandoned']),
                'blocked': state.get('blocked'), 'completion': state.get('completion'),
                'next_action': {'kind': 'element', 'request': deepcopy(state['pending'])} if state['pending'] else None}

    def check_time(self, state, now):
        if timestamp(now) < timestamp(state['last_now']):
            raise ValueError('--now cannot move backwards')
        state['last_now'] = now

    def preview(self, *, now, run_id='work'):
        from work_element_driver import advance
        timestamp(now)
        state = self.read() if self.path.exists() else self.initialize(now, run_id)
        advance(state)
        if state['pending'] is None:
            self.finish(state)
        return self.report(state)

    def next(self, *, now, run_id='work'):
        timestamp(now)
        with self.locked():
            if self.path.exists():
                state = self.read()
                self.materialize(state)
                self.check_time(state, now)
                self.save(state)
            else:
                state = self.initialize(now, run_id)
                self.advance(state)
            return self.report(state)

    def answer(self, answer, *, now):
        from work_element_driver import abandonable
        timestamp(now)
        with self.locked():
            state = self.read()
            self.materialize(state)
            self.check_time(state, now)
            answer_hash = digest(canonical(answer))
            if any(item['answer_hash'] == answer_hash for item in state['history']):
                self.save(state)
                return self.report(state)
            if state['status'] != 'WAITING':
                raise ValueError('Run is not waiting for an answer')
            request = state['pending']
            require_message(answer, 'element-answer')
            if any(answer[key] != request[key] for key in ('run_id', 'element_id', 'attempt')):
                raise ValueError('Answer does not match pending identity/attempt')
            failures = check_answer(request, answer, {}, state['answers'])
            state['history'].append({'element_id': request['element_id'], 'attempt': request['attempt'],
                                     'answer_hash': answer_hash, 'occurred_at': now, 'failures': failures})
            if failures:
                state['previous_failure'] = failures
                if state['attempt'] >= state['config']['max_attempts'] and abandonable(state, request['element_id']):
                    # An optional item or a second plan that cannot be stated is skipped, not fatal.
                    state['abandoned'][request['element_id']] = failures
                    state.update(attempt=1, previous_failure=None)
                elif state['attempt'] >= state['config']['max_attempts']:
                    state.update(status='BLOCKED', pending=None,
                                 blocked={'element_id': request['element_id'], 'attempt': state['attempt'],
                                          'last_failure': failures})
                else:
                    state['attempt'] += 1
            else:
                state['answers'][request['element_id']] = deepcopy(answer['value'])
                state.update(attempt=1, previous_failure=None)
            self.advance(state)
            return self.report(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['next', 'answer'])
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-id', default='work', help='identity for a new Stage C run')
    parser.add_argument('--now', help='RFC3339; defaults to current UTC time')
    args = parser.parse_args()
    try:
        now = args.now or datetime.now(timezone.utc).isoformat(timespec='seconds')
        engine = WorkEngine(args.project)
        if args.command == 'next':
            result = engine.next(now=now, run_id=args.run_id)
        else:
            raw = sys.stdin.buffer.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError('Answer envelope exceeds input byte budget')
            result = engine.answer(json.loads(raw), now=now)
    except (ValueError, OSError) as exc:
        print(f'work-elements: {exc}', file=sys.stderr)
        return 2
    print(stable_json(result), end='')
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
