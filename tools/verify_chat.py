"""Run in the Open WebUI container. One disposable native tool-calling chat."""
import asyncio,datetime,json,logging,time,urllib.request,uuid,os
from pathlib import Path
for entry in Path("/proc/1/environ").read_bytes().split(b"\0"):
 if entry.startswith((b"WEBUI_SECRET_KEY=", b"WEBUI_JWT_SECRET_KEY=")):
  key,value=entry.split(b"=",1);os.environ[key.decode()]=value.decode()
logging.disable(logging.CRITICAL)
from open_webui.models.models import Models
from open_webui.utils.auth import create_token
from open_webui.utils.mfa import get_auth,get_mfa_config,is_mfa_required
async def owner():
 model=await Models.get_model_by_id('qwen3.8-27b')
 auth=await get_auth(model.user_id)
 assert not is_mfa_required('password',await get_mfa_config()), 'Use an existing verified browser session when MFA is required'
 return {'id':model.user_id,'session_stamp':auth.session_stamp,'auth_method':'password'}
token=create_token(asyncio.run(owner()),expires_delta=datetime.timedelta(minutes=5))
base='http://127.0.0.1:8080'
def call(path,body=None,method=None):
 request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
 with urllib.request.urlopen(request,timeout=120) as response:
  data=response.read()
  return json.loads(data) if data else None
assert any(t['id']=='homelab_diagnostics' for t in call('/api/v1/tools/'))
user_id=str(uuid.uuid4());assistant_id=str(uuid.uuid4());stamp=int(time.time())
model='gemma-4-26b-a4b'
prompt='Use homelab_diagnostics to run exactly one summary check on unraid. Then report its RAM available and cache free space in two short sentences. Do not guess values or call any other tools.'
messages={user_id:{'id':user_id,'role':'user','content':prompt,'timestamp':stamp,'models':[model],'childrenIds':[assistant_id]},assistant_id:{'id':assistant_id,'role':'assistant','content':'','parentId':user_id,'childrenIds':[],'model':model,'modelName':model,'modelIdx':0,'done':False,'timestamp':stamp+1}}
chat=call('/api/v1/chats/new',{'chat':{'title':'Temporary homelab tool verification','models':[model],'history':{'currentId':assistant_id,'messages':messages}}})
chat_id=chat['id']
try:
 call('/api/chat/completions',{'model':model,'messages':[{'role':'user','content':prompt}],'stream':True,'chat_id':chat_id,'id':assistant_id,'tool_ids':['homelab_diagnostics'],'params':{'max_tokens':2048,'chat_template_kwargs':{'enable_thinking':False}},'background_tasks':{'title_generation':False,'tags_generation':False,'follow_up_generation':False}})
 result=call('/api/v1/chats/'+chat_id)['chat']['history']['messages'][assistant_id]
 output=result.get('output',[])
 serialized=json.dumps(output)
 print('ANSWER='+result.get('content','')[:1600])
 print('RESULT_JSON='+json.dumps({'done':result.get('done'),'content_present':bool(result.get('content')),'output_types':[item.get('type') for item in output],'tool_name_present':'homelab_diagnostics' in serialized,'observation_present':'observed_at' in serialized,'ok_present':'\\"ok\\": true' in serialized or '"ok": true' in serialized}))
 # Record only our own diagnostic chat temporarily for local assertion, never user conversations.
 assert result.get('content') and 'observed_at' in serialized, 'Chat did not finish a verified native tool call'
finally:
 call('/api/v1/chats/'+chat_id,method='DELETE')
