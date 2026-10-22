"""Install owner-only diagnostic Tools and enable them on the three desktop presets."""
from pathlib import Path
import subprocess
import sys
# --update replaces an existing tool's code; without it a changed tool is refused for review.
UPDATE=('--update' in sys.argv[1:])
source=(Path(__file__).parent/'openwebui_tool.py').read_text(encoding='utf-8')
inner=r'''
import asyncio,json,logging,os
from pathlib import Path
for entry in Path("/proc/1/environ").read_bytes().split(b"\0"):
 if entry.startswith((b"WEBUI_SECRET_KEY=", b"WEBUI_JWT_SECRET_KEY=")):
  key,value=entry.split(b"=",1);os.environ[key.decode()]=value.decode()
logging.disable(logging.CRITICAL)
from open_webui.models.tools import Tools,ToolForm
from open_webui.models.models import Models,ModelForm
from open_webui.routers.tools import get_local_tool_specs,load_tool_module_by_id
async def main():
 reference=await Models.get_model_by_id('qwen3.8-27b')
 assert reference
 tool_id='homelab_diagnostics'
 module,frontmatter,_=await load_tool_module_by_id(tool_id,content=SOURCE)
 specs=get_local_tool_specs(module)
 assert len(specs)==1
 existing=await Tools.get_tool_by_id(tool_id)
 if existing is None:
  form=ToolForm(id=tool_id,name='Homelab diagnostics',content=SOURCE,meta={'description':'Read-only Proxmox and Unraid SSH checks','manifest':frontmatter},access_grants=[],commit_message='Install Ryan-approved read-only diagnostic tools')
  result=await Tools.insert_new_tool(reference.user_id,form,specs,module=module)
  assert result
 else:
  if existing.content!=SOURCE:
   assert __UPDATE__,'Existing tool differs; review, then rerun with --update'
   meta=existing.meta.model_dump() if hasattr(existing.meta,'model_dump') else dict(existing.meta or {})
   meta['manifest']=frontmatter
   assert await Tools.update_tool_by_id(tool_id,{'content':SOURCE,'specs':specs,'meta':meta})
   assert (await Tools.get_tool_by_id(tool_id)).content==SOURCE
 assert await Tools.update_tool_valves_by_id(tool_id,{'BROKER_TOKEN':__BROKER_SECRET__})
 valves=await Tools.get_tool_valves_by_id(tool_id)
 assert valves.get('BROKER_TOKEN')==__BROKER_SECRET__
 module.valves=module.Valves(**valves)
 verified=[]
 for host in ('proxmox','unraid'):
  result=await module.homelab_diagnostics(host,'summary')
  assert result.get('ok'),(host,result.get('error'))
  verified.append(host)
 for name in ('qwen3.8-27b','qwen3.6-35b-moe','gemma-4-26b-a4b'):
  existing=await Models.get_model_by_id(name)
  if existing:
   data=existing.model_dump(exclude={'user_id','created_at','updated_at','version_id'})
  else:
   data={'id':name,'base_model_id':None,'name':name,'meta':{},'params':{},'is_active':True,'access_grants':[]}
  ids=data['meta'].get('toolIds') or []
  if tool_id not in ids: ids.append(tool_id)
  data['meta']['toolIds']=ids
  data['commit_message']='Enable read-only homelab diagnostics by default'
  form=ModelForm.model_validate(data)
  result=await Models.update_model_by_id(name,form,user_id=existing.user_id) if existing else await Models.insert_new_model(form,reference.user_id)
  assert result
  check=await Models.get_model_by_id(name)
  assert tool_id in check.meta.model_dump()['toolIds']
 retired=await Models.get_model_by_id('qwen2.5-coder-14b')
 if retired and retired.is_active:
  data=retired.model_dump(exclude={'user_id','created_at','updated_at','version_id'})
  data['is_active']=False
  data['commit_message']='Retire Qwen Coder after Ryan replaced it with Gemma 4; retain history'
  assert await Models.update_model_by_id(retired.id,ModelForm.model_validate(data),user_id=retired.user_id)
 print('VERIFIED_JSON='+json.dumps({'tool':tool_id,'hosts_checked':verified,'default_models':3,'tools':len(specs),'owner_only':True}))
asyncio.run(main())
'''.replace('SOURCE',repr(source)).replace('__UPDATE__',repr(UPDATE))
outer=r'''
import datetime,json,os,pathlib,sqlite3,subprocess
os.umask(0o077)
backup=pathlib.Path('/opt/open-webui/backups')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup.mkdir(parents=True,mode=0o700)
a=sqlite3.connect('file:/var/lib/docker/volumes/open-webui_open-webui/_data/webui.db?mode=ro',uri=True);b=sqlite3.connect(str(backup/'webui.db'));a.backup(b);b.close();a.close()
script=INNER.replace('__BROKER_SECRET__',repr(pathlib.Path('/var/lib/homelab-tools/token').read_text(encoding='utf-8').strip()))
r=subprocess.run(['docker','exec','-i','-w','/app/backend','open-webui','python','-'],input=script,text=True,capture_output=True,timeout=90)
result=[l for l in r.stdout.splitlines() if l.startswith('VERIFIED_JSON=')]
if r.returncode or not result:
 pathlib.Path('/var/lib/homelab-tools/install-error.log').write_text(r.stdout+'\n'+r.stderr)
 print(json.dumps({'status':'failed','backup':str(backup),'returncode':r.returncode,'details':'Private /var/lib/homelab-tools/install-error.log'}));raise SystemExit(1)
print(json.dumps({'status':'verified','backup':str(backup),'result':json.loads(result[-1].split('=',1)[1])}))
'''.replace('INNER',repr(inner))
subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','112','--','python3','-'],input=outer,text=True,check=True)
