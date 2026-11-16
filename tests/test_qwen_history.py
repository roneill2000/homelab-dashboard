"""Per-tab chat history in the Ask box (docs/SPEC-ask-history.md).

Everything is offline: the broker, the Unraid service, the dashboard proxy and the model are mocked. The live Ask
service (192.168.0.2 or Unraid :11438) is never called, because its run lock is shared with the nightly review.
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'nightly'))
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'backend'))
import qwen_service as q
import qwen_proxy as proxy


def storage(now=1000):
    return {'disks':[dict(name='cache',online=True,sampled_at=now,free_bytes=2e9,
                         read_bps=3e6,write_bps=4e6,used_pct=50,busy_pct=5,latency_ms=2)],
            'activity':{'recovery':{'backup':dict(running=False,observed_at=now)}}}


def turns(count=4):
    return [dict(q=f'Question {i}?', a=f'Answer {i}.') for i in range(1,count+1)]


class FakeResponse:
    """Stands in for an urlopen response in the proxy tests."""
    def __init__(self, raw):
        self.raw=raw;self.status=202
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,_limit):return self.raw


class QwenHistoryTests(unittest.TestCase):
    # ---- item 6: submit() validates the history ----------------------------------
    def submit(self, value):
        with tempfile.TemporaryDirectory() as folder, patch.object(q.r,'ROOT',Path(folder)), patch.object(q.threading,'Thread') as thread:
            code,job=q.Broker().submit('Follow up?','a310',False,value)
            if code==202:thread.call_args.kwargs['args'][0].close()   # release run.lock the submit took
        return code,job

    def test_malformed_history_is_a_400(self):
        bad=[('a string','a string'),('a dict',dict(q='One?',a='One.')),('a non-dict item',[1]),('a nested list',[[dict(q='One?',a='One.')]]),
             ('q not a string',[dict(q=1,a='One.')]),('a missing answer',[dict(q='One?',a=None)]),
             ('answer over 6,000',[dict(q='One?',a='x'*6001)]),('question over 3,000',[dict(q='x'*3001,a='One.')]),
             ('21 turns',[dict(q=f'Q{i}?',a=f'A{i}.') for i in range(21)])]
        for reason,value in bad:
            code,job=self.submit(value)
            self.assertEqual(code,400,reason)
            self.assertIn('Chat history',job['error'],reason)

    def test_history_is_accepted_and_reported_on_the_job(self):
        code,job=self.submit(turns(3))
        self.assertEqual(code,202)
        self.assertEqual(job['history'],turns(3))
        self.assertEqual((job['history_sent'],job['history_dropped']),(3,0))
        self.assertEqual(job['context_note'],'Your question and 3 earlier turns were sent; no system prompt or homelab data.')

    def test_history_is_scrubbed_with_the_same_scrub_as_the_prompt(self):
        code,job=self.submit([dict(q='Where is token=hunter2 set?', a='It uses password: s3cret and Authorization: bearer xyz.')])
        self.assertEqual(code,202)
        turn=job['history'][0]
        self.assertNotIn('hunter2',turn['q']);self.assertNotIn('s3cret',turn['a'])
        self.assertIn('[redacted]',turn['q']);self.assertIn('[redacted]',turn['a'])

    def test_no_history_keeps_todays_request_exactly(self):
        code,job=self.submit(None)
        self.assertEqual(code,202)
        self.assertEqual(job['history'],[]);self.assertEqual((job['history_sent'],job['history_dropped']),(0,0))
        self.assertEqual(job['context_note'],q.PLAIN_NOTE)   # the nightly review, alert and digest path

    # ---- item 7: messages alternate, then the new question -----------------------
    def test_messages_alternate_and_end_with_the_new_question(self):
        messages=q.body_for('Third?',None,q.r.MODEL,None,turns(3))['messages']
        self.assertEqual([m['role'] for m in messages],['user','assistant','user','assistant','user','assistant','user'])
        self.assertEqual([m['content'] for m in messages],
                         ['Question 1?','Answer 1.','Question 2?','Answer 2.','Question 3?','Answer 3.','Third?'])

    def test_homelab_data_stays_on_the_new_question_only(self):
        evidence=dict(sources={'disks':{}},observed_at=1000,budget_omissions=[])
        messages=q.body_for('Third?',evidence,q.r.MODEL,None,turns(2))['messages']
        self.assertEqual(messages[0],dict(role='system',content=q.SYSTEM))
        self.assertEqual([m['role'] for m in messages[1:]],['user','assistant','user','assistant','user'])
        self.assertEqual(messages[-1]['content'],'Observations: '+json.dumps(evidence,separators=(',',':'))+'\nQuestion: Third?')
        self.assertNotIn('Observations:',json.dumps(messages[1:-1]))   # homelab data rides on the new question only

    def test_no_history_builds_exactly_todays_bytes(self):
        # Frozen shapes from the shipped service: nightly review, alert "Ask AI" and the 07:00 digest send no history.
        self.assertEqual(json.dumps(q.body_for('How is cache?')['messages']),
                         '[{"role": "user", "content": "How is cache?"}]')
        evidence=dict(sources={'disks':{}},observed_at=1000)
        today=q.body_for('How is cache?',evidence,q.r.MODEL)['messages']
        with_history=q.body_for('How is cache?',evidence,q.r.MODEL,None,[])['messages']
        self.assertEqual(today,with_history)
        self.assertEqual(len(today),2)
        self.assertEqual([m['role'] for m in today],['system','user'])
        self.assertEqual(today[1]['content'],'Observations: '+json.dumps(evidence,separators=(',',':'))+'\nQuestion: How is cache?')

    # ---- item 9: the context note names the turns -------------------------------
    def test_the_note_names_turns_sent_and_left_out(self):
        self.assertEqual(q.history_note(0,0,'the A310'),q.PLAIN_NOTE)
        self.assertEqual(q.history_note(0,0,'the gaming PC',True),q.STRATA_NOTE)
        self.assertEqual(q.history_note(3,0,'the A310'),'Your question and 3 earlier turns were sent; no system prompt or homelab data.')
        self.assertEqual(q.history_note(1,1,'the A310'),
                         'Your question and 1 earlier turn were sent; no system prompt or homelab data. 1 older turn was left out to fit the A310.')
        self.assertIn('Strata may search the web',q.history_note(2,0,'Strata',True))
        self.assertIn('3 older turns were left out to fit the gaming PC.',q.history_note(2,3,'the gaming PC'))

    def test_the_homelab_note_gains_the_turn_line_and_stays_untouched_without_them(self):
        evidence=dict(sources={'hosts':{},'disks':{}},observed_at=1000,budget_omissions=['logs detail'])
        old,omitted=q.context_note(evidence)
        self.assertEqual(omitted,['logs detail'])
        self.assertEqual(old,'Included: hosts, disks. Trimmed to fit: logs detail. Sources include timestamps; '
                              'unavailable readings stay unknown. No chat history or internet search.')
        note,_=q.context_note(evidence,3,2,'the A310')
        self.assertIn('Your question and 3 earlier turns from this tab were sent.',note)
        self.assertIn('2 older turns were left out to fit the A310.',note)
        self.assertTrue(note.endswith('Chat history from this tab was sent; no internet search.'))

    # ---- item 8: drop the oldest turns until the conversation fits ---------------
    def a310(self, overrides, tokens_of_messages, observations=None, reduce=False):
        """Runs the A310 worker with a fake /apply-template + /tokenize pair."""
        broker=q.Broker()
        broker.job=dict(status='queued',prompt='Follow up?',updated_at=1000,target='a310',**overrides)
        asked=[]
        def fake_fetch(url,body=None,timeout=None):
            if url.endswith('/api/storage'):return storage()
            if url.endswith('/v1/models'):return {'data':[{'id':q.r.MODEL}]}
            if url.endswith('/apply-template'):
                asked.append(body['messages']);return {'prompt':'template'}
            if url.endswith('/tokenize'):
                # The fake counter charges one block per earlier turn of this tab's chat.
                return {'tokens':[0]*tokens_of_messages(asked[-1])}
            return {'choices':[{'finish_reason':'stop','message':{'content':'Cache is fine.'}}]}
        lock=SimpleNamespace(close=unittest.mock.Mock())
        patches=[patch.object(q.time,'time',return_value=1000),patch.object(q.r,'fetch',side_effect=fake_fetch),patch.object(q.r.shutil,'disk_usage',return_value=SimpleNamespace(free=2**32)),
                 patch.object(q.r,'stop_model'),patch.object(q.subprocess,'run'),
                 patch.object(q.qwen_context,'reduce_evidence',return_value=reduce)]
        if observations is not None:
            patches.append(patch.object(q.Broker,'observations',return_value=(broker.state()['prompt'],observations)))
        for p in patches:
            p.start()
        try:
            broker.work(lock)
            state=broker.state()
        finally:
            for p in patches:
                p.stop()
        state['_asked']=asked
        return state

    def test_oldest_turns_are_dropped_until_the_a310_fits(self):
        def costs(messages):
            earlier=(len(messages)-1)//2
            return 1000+1200*earlier          # 4 turns 5,800; 3 turns 4,600; 2 turns 3,400 (INPUT_BUDGET 4,550)
        state=self.a310(dict(history=turns(4)),costs)
        self.assertEqual(state['status'],'complete')
        self.assertEqual((state['history_sent'],state['history_dropped']),(2,2))
        self.assertEqual(state['input_tokens'],3400)
        sent=state['_asked'][-1]
        self.assertEqual([m['content'] for m in sent],['Question 3?','Answer 3.','Question 4?','Answer 4.','Follow up?'])
        self.assertIn('Your question and 2 earlier turns were sent',state['context_note'])
        self.assertIn('2 older turns were left out to fit the A310.',state['context_note'])

    def test_the_request_sent_matches_the_turns_reported(self):
        def costs(messages):return 4000 if (len(messages)-1)//2<=1 else 9000
        state=self.a310(dict(history=turns(3)),costs)
        sent=state['_asked'][-1]
        self.assertEqual([m['role'] for m in sent],['user','assistant','user'])
        self.assertEqual((state['history_sent'],state['history_dropped'],state['input_tokens']),(1,2,4000))
        self.assertNotIn('Question 1?',json.dumps(sent))

    def test_a_question_that_alone_does_not_fit_keeps_todays_error(self):
        state=self.a310(dict(history=turns(2)),lambda messages:q.INPUT_BUDGET+1)
        self.assertEqual(state['status'],'failed')
        self.assertIn('too long for the A310',state['error'])
        self.assertEqual(state['history_dropped'],2)   # history was given up before the question was refused

    def test_homelab_data_gives_up_history_before_it_trims_observations(self):
        evidence=dict(sources={'disks':{}},observed_at=1000,budget_omissions=[])
        def costs(messages):return 1000+(len(messages)-2)//2*3000   # 2 or more earlier turns bust the budget
        state=self.a310(dict(history=turns(3),homelab=True),costs,observations=evidence)
        self.assertEqual(state['status'],'complete')
        self.assertEqual((state['history_sent'],state['history_dropped']),(1,2))
        self.assertIn('2 older turns were left out to fit the A310.',state['context_note'])
        self.assertIn('Your question and 1 earlier turn from this tab were sent.',state['context_note'])
        sent=state['_asked'][-1]
        self.assertEqual(sent[0]['role'],'system')
        self.assertEqual([m['role'] for m in sent[1:]],['user','assistant','user'])

    def test_the_pc_target_drops_oldest_turns_by_its_estimate(self):
        big=[dict(q='Question %d?'%i,a='x'*20000) for i in range(1,9)]
        broker=q.Broker()
        broker.job=dict(status='queued',prompt='Short follow-up?',updated_at=1000,target='pc',history=big)
        asked={}
        def fake_ask(token,body,model,api=None,timeout=None):
            asked['body']=body
            return {'choices':[{'finish_reason':'stop','message':{'content':'Done.'}}],'usage':{'prompt_tokens':30000}}
        lock=SimpleNamespace(close=unittest.mock.Mock())
        with patch.object(q,'pc_token',return_value='k'),patch.object(q,'ask_pc',side_effect=fake_ask),\
             patch.object(q,'PC_TOKEN',SimpleNamespace(read_text=lambda:'k')),\
             patch.object(q.r,'stop_model') as stop,patch.object(q.subprocess,'run') as run,patch.object(q.r,'fetch',return_value=storage()):
            broker.work(lock)
            state=broker.state()
        run.assert_not_called();stop.assert_not_called()
        self.assertEqual(state['status'],'complete')
        self.assertLess(state['history_sent'],8);self.assertGreater(state['history_dropped'],0)
        self.assertLessEqual(q.estimate_tokens(asked['body']['messages']),q.PC_LIMIT-q.PC_OUTPUT_RESERVE)
        self.assertEqual(asked['body']['messages'][-1],dict(role='user',content='Short follow-up?'))
        self.assertIn('left out to fit the gaming PC.',state['context_note'])

    # ---- items 11 and 10: the proxy forwards history inside a hard cap; the service handler passes it on
    def post_to_proxy(self, payload):
        raw=json.dumps(payload).encode()
        handler=SimpleNamespace(path='/api/qwen',
                                headers={'Host':'192.168.0.2','X-Qwen-Request':'1','Content-Length':str(len(raw))},
                                wfile=io.BytesIO(),rfile=io.BytesIO(raw),
                                connection=SimpleNamespace(settimeout=lambda s:None),
                                send_response=lambda code:codes.append(code),
                                send_header=lambda *a:None,end_headers=lambda:None)
        codes=[]
        with patch.object(proxy,'CONFIG',SimpleNamespace(read_text=lambda:'{"token":"t"}')),patch.object(proxy.urllib.request,'urlopen') as opened:
            opened.return_value=FakeResponse(b'{"status":"queued"}')
            self.assertTrue(proxy.handle(handler,post=True))
        return codes,opened

    def test_the_proxy_forwards_the_history_it_is_given(self):
        codes,opened=self.post_to_proxy({'prompt':'Follow up?','target':'a310','homelab':False,'history':turns(2)})
        self.assertEqual(codes,[202])
        self.assertEqual(json.loads(opened.call_args.args[0].data)['history'],turns(2))

    def test_the_proxy_refuses_a_malformed_history(self):
        for bad in ('a string',[1],[dict(q='One?',a='x'*6001)],[dict(q='One?',a='One.')]*21,[dict(q='One?')]):
            codes,opened=self.post_to_proxy({'prompt':'Follow up?','target':'a310','homelab':False,'history':bad})
            self.assertEqual(codes,[400],repr(bad)[:40])
            opened.assert_not_called()

    def test_the_proxy_keeps_a_hard_32_kib_cap(self):
        payload={'prompt':'p'*3000,'target':'a310','homelab':False,'history':[dict(q='q'*3000,a='a'*6000) for _ in range(20)]}
        codes,opened=self.post_to_proxy(payload)
        self.assertEqual(codes,[400]);opened.assert_not_called()   # the rebuilt body would exceed 32 KiB

    def service_handler(self, payload):
        raw=json.dumps(payload).encode()
        handler=q.Handler.__new__(q.Handler)
        handler.path='/job';handler.client_address=('127.0.0.1',1)
        handler.headers={'Content-Length':str(len(raw)),'Authorization':'Bearer t'}
        handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO()
        handler.connection=SimpleNamespace(settimeout=lambda s:None)
        codes=[]
        handler.send_response=codes.append;handler.send_header=lambda *a:None;handler.end_headers=lambda:None
        return handler,codes,raw

    def test_the_service_handler_passes_history_to_the_broker(self):
        handler,codes,_=self.service_handler({'prompt':'Follow up?','target':'a310','homelab':False,'history':[dict(q='One?',a='One.')]})
        captured={}
        def fake_submit(prompt,target,homelab,history):
            captured.update(prompt=prompt,target=target,homelab=homelab,history=history)
            return 202,{'status':'queued'}
        with patch.object(q,'CONFIG',SimpleNamespace(read_text=lambda:'t')),patch.object(q.BROKER,'submit',side_effect=fake_submit):
            handler.do_POST()
        self.assertEqual(codes,[202])
        self.assertEqual(captured['history'],[dict(q='One?',a='One.')])

    def test_the_service_handler_accepts_a_history_sized_body(self):
        payload={'prompt':'Follow up?','target':'a310','homelab':False,'history':[dict(q='q'*3000,a='a'*6000) for _ in range(3)]}
        raw=json.dumps(payload).encode()
        self.assertGreater(len(raw),16384);self.assertLess(len(raw),32768)   # today's 16 KiB cap would refuse it
        handler,codes,_=self.service_handler(payload)
        with patch.object(q,'CONFIG',SimpleNamespace(read_text=lambda:'t')),patch.object(q.BROKER,'submit',return_value=(202,{'status':'queued'})):
            handler.do_POST()
        self.assertEqual(codes,[202])


if __name__=='__main__':unittest.main()
