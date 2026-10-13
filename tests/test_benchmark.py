import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmark import parse_result, summarize, endpoint_url, extract_probs, validate_dataset
class TestBenchmark(unittest.TestCase):
    def test_choices(self):
        row={'question':{'type':'choice','criteria':{'a':'one','b':'two'}},'gold':'b'}
        response={'answers':{'decision':{'choice':'b','probabilities':{'a':.02,'b':.98}}}}
        r=parse_result(row,response)
        self.assertEqual(r['prediction'],'b');self.assertEqual(r['correct'],1);self.assertAlmostEqual(r['confidence'],.98)
    def test_noul(self):
        row={'question':{'type':'noul'},'gold':0}
        r=parse_result(row,{'answers':{'decision':{'noul':.1}}})
        self.assertEqual(r['prediction'],0);self.assertAlmostEqual(r['probabilities']['0'],.9)
    def test_score(self):
        row={'question':{'type':'score','criteria':['bad','ok','great']},'gold':2}
        r=parse_result(row,{'answers':{'decision':{'score':1.7,'probabilities':{'0':.1,'1':.1,'2':.8}}}})
        self.assertEqual(r['prediction'],2);self.assertEqual(r['correct'],1)
    def test_invalid_logits(self):
        self.assertIsNone(extract_probs({'a':2.,'b':3.},['a','b']))
    def test_failed_not_hidden(self):
        rs=[{'error':None,'correct':1,'gold':1,'prediction':1,'question_type':'noul','latency_ms':10,'confidence':.8,'probabilities':{'0':.2,'1':.8}},
            {'error':'HTTP 500','correct':0,'gold':0,'prediction':None,'question_type':'noul','latency_ms':99,'confidence':None,'probabilities':None}]
        m=summarize(rs)
        self.assertEqual(m['accuracy_all'],.5);self.assertEqual(m['accuracy_success_only'],1)
        self.assertEqual(m['errors'],1)
    def test_endpoint(self):
        self.assertEqual(endpoint_url('http://127.0.0.1:8700/v1'), 'http://127.0.0.1:8700/v1/systemone')
    def test_data(self):
        p=Path(__file__).resolve().parents[1]/'data/clm_jev_1300.jsonl'
        data,c=validate_dataset(p)
        self.assertEqual(len(data),1300);self.assertEqual(c['choice'],860)
if __name__=='__main__':unittest.main()
