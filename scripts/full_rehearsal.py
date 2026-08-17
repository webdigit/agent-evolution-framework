from copy import deepcopy
import json
from pathlib import Path

from aef.operations import init_project, audit_project, consolidate_knowledge
from aef.career_cycle import career_cycle_step
from aef.competency_learning import ensure_competency
from aef.learning_lifecycle import observe, derive_hypothesis, confirm_hypothesis, derive_rule
from aef.release import apply_framework_release

OUT = Path('reports/full-rehearsal.json')
MD = Path('reports/full-rehearsal.md')
steps=[]

def log(phase, status, details):
    steps.append({"phase": phase, "status": status, "details": details})

project = {"files":{"notes/project-owned.md":"Synthetic project content — preserve across AEF lifecycle."}}

status, blocked, meta = init_project(project, instance_id='synthetic-agent-001', required_decisions=['decision.role.primary.v1'])
assert status == 'BLOCKED' and blocked == project
log('01-preflight-init', status, meta)

status, project, meta = init_project(
    project, instance_id='synthetic-agent-001',
    answers={'decision.role.primary.v1':'generalist-agent'},
    required_decisions=['decision.role.primary.v1'], created_at='2026-08-13T00:00:00Z'
)
assert status == 'CHANGE'
log('02-init', status, {"instance_id": project['files']['.agent/manifest.json']['instance_id'], "decision": 'generalist-agent'})

status2, replayed, _ = init_project(project, instance_id='synthetic-agent-001', required_decisions=['decision.role.primary.v1'])
assert status2 == 'NO_CHANGE' and replayed == project
log('03-init-replay', status2, {"idempotent": True})

agent={
    'career': {'level':'L1','xp':0,'cases':0,'trust':None,'complex_cases':0,'recent_significant_errors':0,'probation':False},
    'competencies': {}
}
_, agent = ensure_competency(agent, 'record-classification', title='Record classification')
log('04-competency-birth', 'CHANGE', deepcopy(agent['competencies']['record-classification']))

supervised=0
for i in range(10):
    r = career_cycle_step(agent, {'competency':'record-classification','risk':'R0','difficulty':'D3'}, reward=1)
    assert r['status']=='COMPLETED'
    supervised += int(r['supervision_required'])
    agent = r['agent']
assert agent['career']['level']=='L2'
assert agent['competencies']['record-classification']['level']=='L2'
log('05-onboarding-10-safe-tasks', 'COMPLETED', {
    'career_level':agent['career']['level'], 'competency_level':agent['competencies']['record-classification']['level'],
    'trust':agent['competencies']['record-classification']['trust'], 'xp':agent['competencies']['record-classification']['xp'],
    'supervised_tasks':supervised
})

r = career_cycle_step(agent, {'competency':'record-classification','risk':'R1','difficulty':'D2'}, reward=1)
assert r['status']=='COMPLETED'
agent=r['agent']
log('06-first-local-r1', r['status'], {'permission':r['permission'], 'trust':agent['competencies']['record-classification']['trust']})

for _ in range(2):
    r=career_cycle_step(agent, {'competency':'record-classification','risk':'R0','difficulty':'D2'}, reward=-2, successful=False)
    agent=r['agent']
assert agent['competencies']['record-classification']['probation'] is True
log('07-incident-probation', 'PROBATION', {
    'trust':agent['competencies']['record-classification']['trust'],
    'recent_significant_errors':agent['competencies']['record-classification']['recent_significant_errors']
})

blocked_r1=career_cycle_step(agent, {'competency':'record-classification','risk':'R1','difficulty':'D2'}, reward=1)
assert blocked_r1['status']=='REQUIRE_APPROVAL'
log('08-probation-reduces-autonomy', blocked_r1['status'], {'state_mutated': blocked_r1['agent'] != agent})

for _ in range(2):
    r=career_cycle_step(agent, {'competency':'record-classification','risk':'R0','difficulty':'D2'}, reward=2, successful=True, successful_recovery_cases=5)
    agent=r['agent']
assert agent['competencies']['record-classification']['probation'] is False
log('09-recovery', 'COMPLETED', {
    'trust':agent['competencies']['record-classification']['trust'],
    'recent_significant_errors':agent['competencies']['record-classification']['recent_significant_errors']
})

obs=[]; hyp=[]; rules=[]
for i in (1,2):
    _,obs=observe(obs, observation_id=f'obs-{i}', summary='Ambiguous records need source verification', pattern_key='verify-ambiguous-source')
_,hyp,hid=derive_hypothesis(obs,hyp,pattern_key='verify-ambiguous-source')
for _ in range(3):
    _,hyp=confirm_hypothesis(hyp,hid)
_,rules,rid=derive_rule(hyp,rules,hypothesis_id=hid)
log('10-learning-consolidation-input', 'RULE_FORMED', {'observations':len(obs),'hypothesis':hid,'rule':rid})

knowledge={'rules':rules}
status,knowledge,decisions=consolidate_knowledge(knowledge, rule_reviews=[{
    'rule_id':rid,'contradictions':1,'contexts':[{'record_type':'ambiguous'}],
    'reason':'Evidence shows extra verification is needed only for ambiguous records',
    'evidence_ids':['obs-1','obs-2']
}])
assert status=='CHANGE' and knowledge['rules'][0]['status']=='specialized'
log('11-consolidate', status, {'decision':decisions[0]['decision'],'rule_status':knowledge['rules'][0]['status'],'context':knowledge['rules'][0]['context']})

snapshot=deepcopy(project)
audit1=audit_project(project); audit2=audit_project(project)
assert audit1==audit2 and project==snapshot
log('12-audit', audit1['status'], {'read_only':True,'findings':audit1['findings']})

def mig(mid,f,t,key):
    def transform(p):
        p=deepcopy(p); p.setdefault('files',{})[f'.agent/state/{key}.json']={'version':t}; return p
    return {'id':mid,'from_version':f,'to_version':t,'transform':transform,
            'postcondition':lambda p: p.get('files',{}).get(f'.agent/state/{key}.json',{}).get('version')==t}

migrations=[mig('schema-100-110','1.0.0','1.1.0','evaluation'),mig('schema-110-120','1.1.0','1.2.0','learning-signals')]
status, upgraded, meta = apply_framework_release(project,target_version='1.2.0',migrations=migrations,
    managed_updates={'.agent/core/learning.md':'# AEF Learning v1.2\n'})
assert status=='CHANGE'
assert upgraded['files']['notes/project-owned.md']==project['files']['notes/project-owned.md']
log('13-upgrade-1.2.0', status, {'schema_version':upgraded['files']['.agent/manifest.json']['schema_version'],'meta':meta,'project_owned_preserved':True})

status2, replay, meta2 = apply_framework_release(upgraded,target_version='1.2.0',migrations=migrations,
    managed_updates={'.agent/core/learning.md':'# AEF Learning v1.2\n'})
assert status2=='NO_CHANGE' and replay==upgraded
log('14-upgrade-replay', status2, {'idempotent':True,'meta':meta2})

report={
    'scenario':'AEF V1 full birth-to-evolution rehearsal',
    'result':'PASS',
    'steps':steps,
    'final':{
        'career':agent['career'],
        'competency':agent['competencies']['record-classification'],
        'knowledge':knowledge,
        'schema_version':upgraded['files']['.agent/manifest.json']['schema_version'],
        'project_owned_preserved': upgraded['files']['notes/project-owned.md']==project['files']['notes/project-owned.md']
    }
}
OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
lines=['# AEF V1 — Full Birth-to-Evolution Rehearsal','','**Result: PASS**','']
for s in steps:
    lines.append(f"## {s['phase']} — {s['status']}")
    lines.append('```json')
    lines.append(json.dumps(s['details'],indent=2,ensure_ascii=False))
    lines.append('```')
    lines.append('')
lines += ['## Final state','```json',json.dumps(report['final'],indent=2,ensure_ascii=False),'```','']
MD.write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'result':'PASS','steps':len(steps),'final_schema':report['final']['schema_version'], 'final_level':agent['career']['level'], 'competency_level':agent['competencies']['record-classification']['level']}, indent=2))
