"""Apply approved chat defaults using Open WebUI's history-preserving model API.

All database data stays on LXC112. No credentials or conversations are printed.
"""
import subprocess

REMOTE = r'''
import datetime,json,os,pathlib,sqlite3,subprocess
os.umask(0o077)
backup=pathlib.Path('/opt/open-webui/backups')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup.mkdir(parents=True,mode=0o700)
source=sqlite3.connect('file:/var/lib/docker/volumes/open-webui_open-webui/_data/webui.db?mode=ro',uri=True)
target=sqlite3.connect(str(backup/'webui.db'));source.backup(target);target.close();source.close()
script=r"""
import asyncio,json,logging
logging.disable(logging.CRITICAL)
from open_webui.models.models import Models,ModelForm
async def main():
    reference=await Models.get_model_by_id('qwen3.8-27b')
    assert reference is not None, 'Existing 27B configuration required'
    updates={
        'qwen3.8-27b':{'reasoning_effort':'medium'},
        'qwen3.6-35b-moe':{'temperature':1.0,'top_p':0.95,'top_k':20,'min_p':0.0,'presence_penalty':1.5,'repeat_penalty':1.0},
    }
    verified=[]
    for name,params in updates.items():
        existing=await Models.get_model_by_id(name)
        if existing:
            data=existing.model_dump(exclude={'user_id','created_at','updated_at','version_id','access_grants'})
            data['params'].update(params)
        else:
            data={'id':name,'base_model_id':None,'name':name,'meta':{},'params':params,'is_active':True}
        data['commit_message']='Ryan-approved medium reasoning and Qwen MoE thinking defaults'
        form=ModelForm.model_validate(data)
        if existing:result=await Models.update_model_by_id(name,form,user_id=existing.user_id)
        else:result=await Models.insert_new_model(form,reference.user_id)
        assert result is not None
        check=await Models.get_model_by_id(name)
        values=check.params.model_dump()
        assert all(values.get(k)==v for k,v in params.items())
        verified.append({'model':name,'params':{k:values[k] for k in params},'version_saved':bool(check.version_id)})
    print('VERIFIED_JSON='+json.dumps(verified))
asyncio.run(main())
"""
r=subprocess.run(['docker','exec','-i','-w','/app/backend','open-webui','python','-'],input=script,text=True,capture_output=True,timeout=60)
lines=[line for line in r.stdout.splitlines() if line.startswith('VERIFIED_JSON=')]
if r.returncode or not lines:
    print(json.dumps({'status':'failed','backup':str(backup),'returncode':r.returncode}))
    print('Application update did not verify; inspect private host logs before retrying.')
    raise SystemExit(1)
print(json.dumps({'status':'verified','backup':str(backup),'models':json.loads(lines[-1].split('=',1)[1])}))
'''

if __name__ == '__main__':
    subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','112','--','python3','-'],input=REMOTE,text=True,check=True)
