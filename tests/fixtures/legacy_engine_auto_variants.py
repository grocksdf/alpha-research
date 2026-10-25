"""TEST-ONLY historical protocol replay, never imported by alpharesearch/.

Old v0.6.1 safety tests expected implicit parameter perturbations. The v0.8
production engine NEVER generates those. This isolated fixture exercises the
historical safety and submission gates against legacy frozen records. Modern
v0.8 tests separately assert zero hardcoded candidate generation.
"""
from __future__ import annotations
from unittest.mock import patch

from alpharesearch.common import digest
from alpharesearch.engine import Engine

PRODUCTION_PROPOSE = Engine.propose


def legacy_propose(self, *args, **kwargs):
    outcome = PRODUCTION_PROPOSE(self, *args, **kwargs)
    if outcome.get('state') in ('completed','duplicate'):
        legacy_ensure_sensitivity(self, outcome['trial_id'])
        # Old implementation evaluated *after* synthesizing the test neighbors.
        if outcome.get('state')=='completed' and self.memory.latest(outcome['trial_id']):
            outcome['evaluation'] = self.evaluate_trial(outcome['trial_id'])
    return outcome


def legacy_ensure_sensitivity(self, trial_id):
    if self.memory.db.execute('SELECT 1 FROM directed_studies WHERE run_id=?',(self.run_id,)).fetchone():
        return
    trial = self.memory.trial(trial_id)
    intents=[i for i in self.memory.intents(self.run_id,trial_id) if i['state']=='active']
    if not intents and trial['run_id']==self.run_id and trial['kind']=='candidate':
        self.memory.migrate_intents()
        intents=[i for i in self.memory.intents(self.run_id,trial_id) if i['state']=='active']
    if trial['state'] != 'completed' or not intents:
        return
    design_hash=digest({'expressions':self.validator.neighbors(trial['expression']),'scope':self.config['scope']})
    for expression in self.validator.neighbors(trial['expression']):
        val=self.validator.validate(expression)
        existing=self.memory.existing_trial(self.context,val.fingerprint)
        if not existing and self.memory.run(self.run_id)['simulations']>=self.simulation_limit:
            continue
        neighbor_id,fresh=self.memory.add_trial(self.run_id,{
            'expression':expression,'hypothesis':trial['hypothesis'],
            'source':'historical_test_only_parameter_sensitivity'}, val, self.context,
            kind='sensitivity',parent_id=trial_id)
        self.memory.transaction(lambda:self.memory.event(self.run_id,'sensitivity_link',{
            'parent_id':trial_id,'neighbor_id':neighbor_id}))
        for intent in intents:
            self.memory.link_sensitivity(intent['id'],neighbor_id,design_hash)
        if fresh:
            self.execute(neighbor_id)


def enable_legacy_historical_fixture(test_case):
    for name,method in (('propose',legacy_propose),('ensure_sensitivity',legacy_ensure_sensitivity)):
        patcher=patch.object(Engine,name,method)
        patcher.start()
        test_case.addCleanup(patcher.stop)
