import importlib.util,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('runner',ROOT/'evaluation'/'runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class EvaluationTests(unittest.TestCase):
 def test_model_input_excludes_future_and_labels(self):
  case={'mode':'direct','context':[{'author':'A','text':'hello','private_note':'do not send'}],'review_focus':'secret','future_reply':'secret','expected':'secret'}
  self.assertEqual(runner.model_input(case),{'mode':'DIRECT','recent_messages':[{'author':'A','text':'hello'}]})
 def test_duplicate_scenarios_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'cases.json';path.write_text(json.dumps([{'id':'x','mode':'direct','context':[{'author':'a','text':'x'}]}]*2))
   with self.assertRaises(ValueError):runner.load_scenarios(path)
 def test_fixture_runs_and_blind_comparison(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);script=str(ROOT/'evaluation'/'runner.py');a=d/'a.json';b=d/'b.json';blind=d/'blind.json';key=d/'key.json'
   for f in (a,b):subprocess.run([sys.executable,script,'run','--backend','fixture','--output',str(f)],check=True,capture_output=True)
   subprocess.run([sys.executable,script,'compare','--run-a',str(a),'--run-b',str(b),'--output',str(blind),'--key',str(key)],check=True,capture_output=True)
   rows=json.loads(blind.read_text());mapping=json.loads(key.read_text())['mapping'];self.assertEqual(len(rows),16);self.assertEqual(len(mapping),16)
   self.assertEqual(sum(x['A']=='run_a' for x in mapping.values()),8)
   self.assertNotIn('scenario_id',rows[0]);self.assertNotIn('model',rows[0]);self.assertIn('review_focus',rows[0])
   result=subprocess.run([sys.executable,script,'run','--backend','fixture','--output',str(a)],capture_output=True)
   self.assertNotEqual(result.returncode,0)
 def test_mismatched_scenario_file_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);script=str(ROOT/'evaluation'/'runner.py');a=d/'a.json';sc=d/'scenarios.json'
   subprocess.run([sys.executable,script,'run','--backend','fixture','--output',str(a)],check=True,capture_output=True)
   cases=runner.load_scenarios(ROOT/'evaluation'/'scenarios.json');cases[0]['context'][0]['text']='changed';sc.write_text(json.dumps(cases))
   result=subprocess.run([sys.executable,script,'compare','--scenarios',str(sc),'--run-a',str(a),'--run-b',str(a),'--output',str(d/'out'),'--key',str(d/'key')],capture_output=True)
   self.assertNotEqual(result.returncode,0)
if __name__=='__main__':unittest.main()
