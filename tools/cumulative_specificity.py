"""Pinned cumulative research comparison; does not claim artistic superiority."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from _common import ROOT, load_yaml, read_jsonl, yaml_list
from research_memory import query_memory, FIELDS as ARTIFACT_FIELDS, timestamp
from self_repetition import _similarity
from validate import _load_schema_validators, validate_repository

CONTRACT='cumulative-specificity/v1'
REQUEST='cumulative-specificity-request/v1'
REQUEST_PATH='04_decisions/cumulative-specificity-request.json'
POLICY_PATH=ROOT/'config/cumulative-specificity.yaml'
SHA=re.compile(r'^[0-9a-f]{40}$')
HASH=re.compile(r'^[0-9a-f]{64}$')
REQUEST_FIELDS=set('contract_version rule_version creator_id origin_instance_id seed at project_snapshot inputs sources candidates memory_query'.split())
INPUT_FIELDS=set('id record code_commit knowledge_commit payload_path'.split())
CANDIDATE_FIELDS=set('hypothesis mode mechanism_ids input_bindings source_ids knowledge_refs continuation'.split())

class SpecificityError(ValueError):
    pass


def need(condition, reason):
    if not condition:raise SpecificityError(reason)


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def hashed(value):
    return hashlib.sha256(value).hexdigest()


def project_snapshot(project):
    result={}
    for folder in ('00_intake','02_evidence','03_knowledge','04_decisions','05_production'):
        for path in sorted((project/folder).rglob('*')):
            if path.name.startswith('cumulative-specificity-') or path.is_dir():continue
            need(not path.is_symlink(),'PROJECT_SYMLINK')
            result[str(path.relative_to(project))]=hashed(path.read_bytes())
    return result


def load_inputs(rows,creator,at):
    need(isinstance(rows,list) and rows,'INPUTS_REQUIRED')
    result={}
    for row in rows:
        need(isinstance(row,dict) and set(row)==INPUT_FIELDS,'INPUT_FIELDS')
        need(isinstance(row['id'],str) and row['id'] and row['id'] not in result,'INPUT_ID')
        record=row['record']
        need(isinstance(record,dict) and set(record)==ARTIFACT_FIELDS and record['contract_version']=='artifact-record/v1','INPUT_ARTIFACT_CONTRACT')
        need(SHA.fullmatch(row['code_commit']) and SHA.fullmatch(row['knowledge_commit']) and HASH.fullmatch(record['content_sha256']),'INPUT_PIN')
        need(type(record['revision']) is int and record['revision']>0,'INPUT_REVISION')
        path=Path(row['payload_path'])
        need(path.is_absolute() and not any(p.is_symlink() for p in (path,*path.parents)) and path.is_file(),'INPUT_UNAVAILABLE')
        need(hashed(path.read_bytes())==record['content_sha256'],'INPUT_HASH_MISMATCH')
        need(record['owner_repository']!='self-model-notes' or record['creator_id']==creator,'SELF_SIGNAL_SCOPE')
        need(record['lifecycle']=='accepted','INPUT_LIFECYCLE')
        need(record['valid_until'] is None or timestamp(record['valid_until'])>=timestamp(at),'INPUT_EXPIRED')
        need(all(isinstance(record[k],str) and record[k] for k in ('owner_repository','creator_id','origin_instance_id','record_id')),'INPUT_IDENTITY')
        need(record['epistemic_status'] in ('observed','externally-supported','inferred','proposed','simulated','unknown'),'INPUT_EPISTEMIC')
        if record['access_scope'] in ('private','creator-private'):
            need(record['creator_id']==creator and record['consent_ref'],'INPUT_PRIVATE_SCOPE')
        result[row['id']]=dict(row,content_sha256=record['content_sha256'],epistemic_status=record['epistemic_status'])
    return result


def source_roots(rows,inputs):
    """AI-derived ancestors never become independent primary evidence."""
    nodes={}
    for row in rows:
        need(set(row)=={'id','kind','locator','content_sha256','parents','input_id'},'SOURCE_FIELDS')
        need(row['id'] not in nodes and row['kind'] in ('primary','secondary','ai-derived','unknown'),'SOURCE_ID_KIND')
        need(row['input_id'] in inputs and HASH.fullmatch(row['content_sha256']),'SOURCE_PIN')
        need(isinstance(row['parents'],list) and all(isinstance(p,str) for p in row['parents']),'SOURCE_PARENTS')
        parsed=urlsplit(row['locator']);need(parsed.scheme in ('https','http') and parsed.hostname and not parsed.username and not parsed.password,'SOURCE_LOCATOR')
        declared=inputs[row['input_id']]['record']['sources']
        need(any(ref.get('locator',ref.get('source'))==row['locator'] and ref.get('content_sha256')==row['content_sha256'] for ref in declared),'SOURCE_NOT_IN_OWNER_PROVENANCE')
        nodes[row['id']]=row
    done={};visiting=set()
    def roots(key):
        need(key in nodes,'SOURCE_UNRESOLVED')
        if key in done:return done[key]
        need(key not in visiting,'SOURCE_CYCLE');visiting.add(key);row=nodes[key]
        parent_roots=set().union(*(roots(p) for p in row['parents'])) if row['parents'] else set()
        if row['kind']=='primary':
            need(not row['parents'],'PRIMARY_HAS_DERIVED_PARENTS')
            value={row['content_sha256']}
        elif row['kind']=='ai-derived':value=set()
        else:value=parent_roots
        visiting.remove(key);done[key]=value;return value
    for key in sorted(nodes):roots(key)
    return nodes,done


def memory_history(query,creator,at):
    if query is None:return dict(status='UNAVAILABLE',records=[],reason='EXPLICIT_MEMORY_QUERY_REQUIRED')
    try:
        need(query['creator']==creator and query['at']==at,'HISTORY_SCOPE')
        found=query_memory(query)
        # A valid, empty store is distinct from missing permissions or broken indices.
        return dict(found,reason=None)
    except (OSError,ValueError,KeyError,TypeError):
        return dict(status='UNAVAILABLE',records=[],reason='MEMORY_UNAVAILABLE')


def reference(hit):
    r=hit['reference']
    return f"{r['origin_instance_id']}:{r['owner_repository']}:{r['record_id']}:{r['revision']}:{hit['item_id']}"


def history_text(hit):
    data=hit['data']
    if hit['kind']=='mechanism':return data.get('mechanism','')
    if hit['kind']=='production-hypothesis':return data.get('proposition','')
    if hit['kind']=='prior-art':return ' '.join(v for k,v in data.items() if k not in ('title','id','source_url','url') and isinstance(v,str))
    return ''


def category(hit,creator,origin):
    if hit['kind']=='prior-art':return 'external-precedent'
    if hit['source_creator_id']==creator and hit['reference']['origin_instance_id']==origin:return 'own-work'
    return 'inherited-work'


def evaluate(project,request):
    project=Path(project).resolve();policy=load_yaml(POLICY_PATH)
    need(isinstance(request,dict) and set(request)==REQUEST_FIELDS,'REQUEST_FIELDS')
    need(request['contract_version']==REQUEST and request['rule_version']==policy['contract_version'],'RULE_VERSION')
    need(type(request['seed']) is int,'SEED_INVALID')
    need(project_snapshot(project)==request['project_snapshot'],'PROJECT_SNAPSHOT_CHANGED')
    manifest=load_yaml(project/'manifest.yaml');need(manifest['project'].get('creator_id')==request['creator_id'],'PROJECT_CREATOR_MISMATCH')
    from research_memory import timestamp
    timestamp(request['at'])
    inputs=load_inputs(request['inputs'],request['creator_id'],request['at'])
    nodes,roots=source_roots(request['sources'],inputs)
    findings=[];validators=_load_schema_validators(ROOT,findings);need(not findings,'NATIVE_SCHEMAS_UNAVAILABLE')
    visual=load_yaml(project/'05_production/visual-language.yaml')
    need(not list(validators['visual-language'].iter_errors(visual)),'NATIVE_VISUAL_SCHEMA')
    need(visual['schema_version']=='1.1.0','GROUNDED_VISUAL_VERSION_REQUIRED')
    native=validate_repository(project.parent.parent,'project/'+project.name,protocol_root=ROOT,work_root=project.parent.parent)
    grounding=[f.rule for f in native if f.rule.startswith('VISUAL-LANGUAGE')]
    techniques={t['id']:t for t in visual['techniques']}
    history=memory_history(request['memory_query'],request['creator_id'],request['at'])
    hits={reference(h):h for h in history['records']}
    decisions={r['id'] for r in yaml_list(project/'04_decisions/decision-log.yaml','decisions')}
    insights={r['id'] for r in yaml_list(project/'04_decisions/insight-register.yaml','insights')}
    results=[];seen=set()
    for candidate in request['candidates']:
        need(set(candidate)==CANDIDATE_FIELDS,'CANDIDATE_FIELDS')
        hypothesis=candidate['hypothesis'];identifier=hypothesis['id']
        need(identifier not in seen,'DUPLICATE_CANDIDATE');seen.add(identifier)
        need(not list(validators['production-hypothesis'].iter_errors(hypothesis)),'NATIVE_HYPOTHESIS_SCHEMA')
        need(candidate['mode'] in policy['exploration_slots'],'EXPLORATION_MODE')
        reasons=list(grounding)
        if not set(hypothesis['source_decision_ids'])<=decisions or not set(hypothesis['source_insight_ids'])<=insights:reasons.append('HYPOTHESIS_GROUNDING_UNRESOLVED')
        if history['status']=='UNAVAILABLE':reasons.append('HISTORY_UNAVAILABLE')
        bindings=candidate['input_bindings']
        if not isinstance(bindings,dict) or not bindings or any(key not in inputs or value!=inputs[key]['content_sha256'] for key,value in bindings.items()):reasons.append('INPUT_BINDING_CHANGED')
        selected=[]
        for identifier_ in candidate['mechanism_ids']:
            if identifier_ not in techniques:reasons.append('MECHANISM_UNRESOLVED')
            else:selected.append(techniques[identifier_])
        if not selected:reasons.append('MECHANISM_UNGROUNDED')
        components=set(hypothesis['source_decision_ids']+hypothesis['source_insight_ids']+[hypothesis['id']])
        if any(not components.intersection(t.get('proposition_component_ids',[])) for t in selected):reasons.append('MECHANISM_PROPOSITION_UNBOUND')
        independent=set()
        for source in candidate['source_ids']:
            if source not in nodes:reasons.append('SOURCE_UNRESOLVED');continue
            if nodes[source]['input_id'] not in bindings:reasons.append('SOURCE_INPUT_UNBOUND')
            independent.update(roots[source])
        if len(independent)<policy['minimum_independent_roots']:reasons.append('NO_INDEPENDENT_PRIMARY_ROOT')
        relevant=[]
        for key in candidate['knowledge_refs']:
            hit=hits.get(key)
            if hit is None or hit['disposition'] not in ('CANDIDATE','RECONSIDER'):reasons.append('KNOWLEDGE_REFERENCE_UNAVAILABLE')
            else:relevant.append(hit)
        if candidate['mode']=='reuse' and not relevant:reasons.append('REUSE_REFERENCE_REQUIRED')
        if candidate['mode']=='unresolved-reexplore' and not any(h['kind']=='question' and h['data'].get('status')=='UNRESOLVED' for h in relevant):reasons.append('UNRESOLVED_QUESTION_REQUIRED')
        matches=[]
        for technique in selected:
            for key,hit in sorted(hits.items()):
                if hit['kind'] not in policy['history_kinds'] or not history_text(hit):continue
                score,_=_similarity(technique['mechanism'],history_text(hit))
                if score>=policy['match_threshold']:
                    matches.append(dict(mechanism_id=technique['id'],reference=key,knowledge_commit=hit['knowledge_commit'],classification=category(hit,request['creator_id'],request['origin_instance_id']),score=score,disposition=hit['disposition']))
        duplicate=[m for m in matches if m['score']>=policy['structural_duplicate_threshold']]
        continuation=candidate['continuation'];continuation_ok=False
        if continuation is not None:
            need(set(continuation)=={'reference','reason','before','after','observable_test'},'CONTINUATION_FIELDS')
            prior=hits.get(continuation['reference'])
            continuation_ok=bool(prior and prior['disposition'] in ('CANDIDATE','RECONSIDER') and continuation['reason'].strip() and continuation['observable_test'].strip() and continuation['before']==history_text(prior) and continuation['after'] in [t['mechanism'] for t in selected] and continuation['before']!=continuation['after'])
            if not continuation_ok:reasons.append('CONTINUATION_DELTA_UNPROVEN')
        if duplicate and not continuation_ok:reasons.append('STRUCTURAL_REPETITION_REQUIRES_DELTA')
        results.append(dict(id=identifier,mode=candidate['mode'],hypothesis=hypothesis,mechanisms=selected,input_bindings=bindings,knowledge_refs=candidate['knowledge_refs'],independent_primary_roots=sorted(independent),matches=matches,continuation_accepted=continuation_ok,reasons=sorted(set(reasons)),eligible=not reasons))
    need(bool(results),'CANDIDATES_REQUIRED')
    results.sort(key=lambda row:row['id'])
    selected=[]
    for mode,slots in sorted(policy['exploration_slots'].items()):
        eligible=[row for row in results if row['eligible'] and row['mode']==mode]
        eligible.sort(key=lambda row:(-len(row['independent_primary_roots']),len(row['matches']),hashed(encoded([request['seed'],row['id']]))))
        selected.extend(row['id'] for row in eligible[:slots])
    metrics=dict(grounded_candidates=sum(not any('GROUND' in reason or reason.startswith('VISUAL-') for reason in row['reasons']) for row in results),independent_roots=len(set().union(*(set(row['independent_primary_roots']) for row in results))),unresolved_candidates=sum(bool(row['reasons']) for row in results),structural_duplicate_matches=sum(m['score']>=policy['structural_duplicate_threshold'] for row in results for m in row['matches']))
    report=dict(contract_version=CONTRACT,rule_version=policy['contract_version'],rule_sha256=hashed(POLICY_PATH.read_bytes()),seed=request['seed'],input_snapshot=hashed(encoded(request['project_snapshot'])),request_sha256=hashed(encoded(request)),history_status=history['status'],history_reason=history['reason'],knowledge_commit=request['memory_query']['knowledge_commit'] if request['memory_query'] else None,code_commit=request['memory_query']['code_commit'] if request['memory_query'] else None,candidates=results,selected_ids=selected,status='QUALIFIED' if selected else 'INCOMPLETE',metrics=metrics,comparison=dict(with_reuse=metrics,without_reuse=dict(grounded_candidates=metrics['grounded_candidates'],independent_roots=metrics['independent_roots'],unresolved_candidates=sum(any(r not in ('HISTORY_UNAVAILABLE','STRUCTURAL_REPETITION_REQUIRES_DELTA','REUSE_REFERENCE_REQUIRED','UNRESOLVED_QUESTION_REQUIRED','KNOWLEDGE_REFERENCE_UNAVAILABLE') for r in row['reasons']) for row in results),structural_duplicate_matches=None,history_status='NOT_SCANNED')),artistic_quality_guarantee=False,input_provenance=[{k:v for k,v in row.items() if k!='payload_path'} for row in inputs.values()],source_verification='Declared lineage and immutable payload hashes checked; independent source reading is performed by the external worker.')
    return report


def evaluate_file(project):
    path=project/REQUEST_PATH
    need(not path.is_symlink(),'REQUEST_SYMLINK')
    return evaluate(project,json.loads(path.read_text()))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',type=Path,required=True);parser.add_argument('--request',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        request=json.loads((args.request or args.project/REQUEST_PATH).read_text());report=evaluate(args.project,request)
        text=json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
        if args.output:
            need(not args.output.exists(),'OUTPUT_EXISTS');args.output.write_text(text)
        else:print(text,end='')
        return 0 if report['status']=='QUALIFIED' else 2
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'INCOMPLETE','reason':str(exc) if isinstance(exc,SpecificityError) else 'INPUT_UNAVAILABLE'}));return 2

if __name__=='__main__':raise SystemExit(main())
