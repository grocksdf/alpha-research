from pathlib import Path

from test_system import Base,FixtureSimulator
from alpharesearch.attempt_history import inventory
from alpharesearch.common import ResearchError,digest
from alpharesearch.engine import Engine


class AttemptHistoryTests(Base):
    def test_failed_invalid_reused_and_unfinished_attempts_all_remain_in_inventory(self):
        engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        engine.propose('rank(close)','TEST first','TEST');engine.propose('rank(close)','TEST repeated','TEST')
        engine.propose('ts_delay(close,-1)','TEST invalid','TEST')
        for expression,state in [('rank(volume)','failed'),('rank(vwap)','uncertain')]:
            valid=engine.validator.validate(expression)
            tid,_=engine.memory.add_trial(engine.run_id,{'expression':expression,'hypothesis':'TEST','source':'TEST'},valid,engine.context)
            engine.memory.set_state(tid,state,error='TEST retained')
        before=list(engine.memory.db.iterdump());value=inventory(self.config['database'])
        self.assertEqual(value['total_attempts'],5);self.assertEqual(value['registered_trial_count'],4)
        self.assertEqual(value['cached_duplicate_proposals'],1)
        self.assertEqual({a['status'] for a in value['attempts']},{'completed','rejected','failed','uncertain'})
        self.assertEqual(value['attempts_sha256'],digest(value['attempts']))
        self.assertFalse(value['complete']);self.assertFalse(value['reviewed']);self.assertIsNone(value['dsr_calibration'])
        self.assertEqual(before,list(engine.memory.db.iterdump()))
        self.assertEqual(value,inventory(self.config['database']))

    def test_corrupt_result_container_is_retained_without_aborting_inventory(self):
        engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        valid=engine.validator.validate('rank(close)')
        tid,_=engine.memory.add_trial(engine.run_id,{'expression':valid.expression,'hypothesis':'TEST','source':'TEST'},valid,engine.context)
        engine.memory.result(tid,{'metrics':None});value=inventory(self.config['database'])
        self.assertEqual(value['total_attempts'],1);self.assertEqual(value['attempts'][0]['candidate_id'],'trial:'+tid)
        self.assertFalse(value['complete'])

    def test_missing_database_is_not_created(self):
        with self.assertRaises(ResearchError):inventory(self.config['database'])
        self.assertFalse(Path(self.config['database']).exists())
