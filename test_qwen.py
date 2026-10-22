"""Offline lifecycle and bounded-context regressions; no live inference."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent/'nightly'))
import qwen_service as q
import qwen_proxy as proxy


def storage(now=1000):
    return {'disks':[dict(name='cache',online=True,sampled_at=now,free_bytes=2e9,
                         read_bps=3e6,write_bps=4e6,used_pct=50,busy_pct=5,latency_ms=2)],
            'activity':{'recovery':{'backup':dict(running=False,observed_at=now)}}}


class QwenTests(unittest.TestCase):
    def test_performance_uses_measured_generation_timings(self):
        data=q.performance({'usage':{'prompt_tokens':100,'completion_tokens':800},'timings':{'predicted_per_second':16,'predicted_ms':50000}},55)
        self.assertEqual(data['tokens_per_second'],16);self.assertEqual(data['generation_seconds'],50)
        self.assertEqual(data['response_seconds'],55);self.assertEqual(data['output_tokens'],800)
        self.assertIsNone(q.performance({},50)['tokens_per_second'])
        self.assertIsNone(q.performance({'timings':{'predicted_per_second':float('nan')}},1)['tokens_per_second'])

    def test_context_stale_and_future_samples_withhold_values(self):
        fresh=q.context(storage(),1000)
        self.assertTrue(fresh['disks'][0]['fresh'])
        self.assertEqual(fresh['disks'][0]['free_GB'],2)
        self.assertEqual(fresh['disks'][0]['read_MB_s'],3)
        for observed in (900,1001):
            result=q.context(storage(observed),1000)
            self.assertFalse(result['disks'][0]['fresh'])
            self.assertNotIn('free_GB',result['disks'][0])
        self.assertEqual(q.context(storage(1),1000)['backup_running'],'unknown')
        self.assertEqual(q.context(storage(1001),1000)['backup_running'],'unknown')

    def test_overlap_excluded_by_shared_file_lock_and_broker_state(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(q.r,'ROOT',Path(folder)),patch.object(q.threading,'Thread') as thread:
            first=q.Broker();second=q.Broker()
            self.assertEqual(first.submit('How is cache?')[0],202)
            self.assertEqual(first.submit('Another question')[0],409)
            self.assertEqual(second.submit('Competing question')[0],409)
            held=thread.call_args.kwargs['args'][0]
            held.close()
            self.assertEqual(second.submit('After lock released')[0],202)
            thread.call_args.kwargs['args'][0].close()

    def exercise(self,tokens=100,cleanup_error=False):
        broker=q.Broker();broker.job=dict(status='queued',prompt='How is cache?',updated_at=1000)
        lock=SimpleNamespace(close=unittest.mock.Mock())
        response={'choices':[{'finish_reason':'stop','message':{'content':'Measured cache space is available.'}}]}
        values=[storage(),{'data':[{'id':q.r.MODEL}]},storage(),{}, {'prompt':'template'},
                {'tokens':list(range(tokens))}]
        if tokens<=q.INPUT_BUDGET:values.append(response)
        cleanup=[None,RuntimeError('cleanup')] if cleanup_error else [None,None]
        with patch.object(q.time,'time',return_value=1000),patch.object(q.r,'fetch',side_effect=values) as fetch,patch.object(q.r.shutil,'disk_usage',return_value=SimpleNamespace(free=2**32)),patch.object(q.r,'stop_model',side_effect=cleanup) as stop,patch.object(q.subprocess,'run'),patch.object(q.qwen_context,'reduce_evidence',return_value=False):
            broker.work(lock)
            state=broker.state()
        lock.close.assert_called_once()
        self.assertEqual(stop.call_count,2)
        return state,fetch

    def test_success_refetches_context_then_cleans_up(self):
        state,fetch=self.exercise()
        self.assertEqual(state['status'],'complete')
        self.assertEqual(state['context_at'],1000)
        self.assertEqual(state['evidence']['observed_at'],1000)
        self.assertIn('sources',state['evidence'])
        calls=[c.args[0] for c in fetch.call_args_list]
        self.assertEqual(calls.count('http://192.168.0.2/api/storage'),2)
        self.assertEqual(calls[-1],q.r.API+'/v1/chat/completions')

    def test_cleanup_failure_withholds_answer(self):
        state,_=self.exercise(cleanup_error=True)
        self.assertEqual(state['status'],'failed')
        self.assertNotIn('answer',state)
        self.assertIn('cleanup',state['error'])

    def test_token_limit_prevents_generation_and_still_cleans_up(self):
        state,fetch=self.exercise(tokens=q.INPUT_BUDGET+1)
        self.assertEqual(state['status'],'failed')
        self.assertIn('too long',state['error'])
        self.assertNotIn(q.r.API+'/v1/chat/completions',[c.args[0] for c in fetch.call_args_list])
        self.assertEqual(q.body_for('Q',{})['max_tokens'],1400)
        self.assertLessEqual(q.INPUT_BUDGET+q.body_for('Q',{})['max_tokens'],6144-128)  # fits the 6K window with margin

    def test_failed_resource_gate_never_touches_container(self):
        broker=q.Broker();broker.job=dict(status='queued',prompt='Q',updated_at=1000)
        lock=SimpleNamespace(close=unittest.mock.Mock())
        with patch.object(q.r,'fetch',return_value={}),patch.object(q.r,'stop_model') as stop:
            broker.work(lock)
        self.assertEqual(broker.state()['status'],'failed')
        stop.assert_not_called();lock.close.assert_called_once()

    def test_parity_capacity_is_not_applicable(self):
        data=storage();data['disks'][0]['id']='parity'
        item=q.context(data,1000)['disks'][0]
        self.assertNotIn('free_GB',item);self.assertNotIn('used_pct',item)
        self.assertIn('not applicable',item['capacity'])

    def test_backup_started_during_load_prevents_generation(self):
        broker=q.Broker();broker.job=dict(status='queued',prompt='Q',updated_at=1000)
        lock=SimpleNamespace(close=unittest.mock.Mock())
        busy=storage();busy['activity']['recovery']['backup']['running']=True
        values=[storage(),{'data':[{'id':q.r.MODEL}]},busy]
        with patch.object(q.time,'time',return_value=1000),patch.object(q.r,'fetch',side_effect=values) as fetch,patch.object(q.r.shutil,'disk_usage',return_value=SimpleNamespace(free=2**32)),patch.object(q.r,'stop_model') as stop,patch.object(q.subprocess,'run'),patch.object(q.qwen_context,'reduce_evidence',return_value=False):
            broker.work(lock)
            state=broker.state()
        self.assertEqual(state['status'],'failed')
        self.assertIn('backup started',state['error'])
        self.assertEqual(stop.call_count,2)
        self.assertNotIn(q.r.API+'/v1/chat/completions',[c.args[0] for c in fetch.call_args_list])

    def test_expiration_discards_completed_question_and_answer(self):
        broker=q.Broker();broker.job=dict(status='complete',updated_at=1,prompt='private',answer='private',evidence={'sources':{'private':'private'}})
        with patch.object(q.time,'time',return_value=1000):state=broker.state()
        self.assertEqual(state['status'],'idle');self.assertNotIn('prompt',state);self.assertNotIn('answer',state);self.assertNotIn('evidence',state)

    def test_proxy_rejects_cross_origin_or_missing_custom_header(self):
        for headers in ({'Host':'192.168.0.2'},
                        {'Host':'192.168.0.2','X-Qwen-Request':'1','Origin':'http://evil.example'},
                        {'Host':'evil.example','X-Qwen-Request':'1'}):
            codes=[]
            handler=SimpleNamespace(path='/api/qwen',headers=headers,wfile=io.BytesIO(),
                                    send_response=codes.append,send_header=lambda *a:None,end_headers=lambda:None)
            with patch.object(proxy.urllib.request,'urlopen') as fetch:
                self.assertTrue(proxy.handle(handler,post=True))
            self.assertEqual(codes,[403]);fetch.assert_not_called()


if __name__=='__main__':unittest.main()
