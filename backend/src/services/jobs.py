import time
from fastapi import HTTPException
from redis import Redis
from src.config.settings import REDIS_URL
from src.models import Dataset, Job
from src.services.business import metadata
from src.services.samples import build_sample, parse_month
def create(db, data, user):
    d=db.get(Dataset,data.dataset_id)
    if not d: raise HTTPException(404,'数据批次不存在')
    allowed={p['key']:p for p in metadata().get('parameters',[])}
    if set(data.params)-set(allowed): raise HTTPException(422,'包含未知分析参数')
    params={k:p['default'] for k,p in allowed.items()}
    params.update(data.params)
    for key,p in allowed.items():
        v=params[key]
        if p['type']=='number':
            try:
                v=float(v)
                if not (p.get('min',-1e9)<=v<=p.get('max',1e9)): raise ValueError()
                params[key]=v
            except (ValueError,TypeError): raise HTTPException(422, f'{p["label"]} 超出范围或非数值')
        elif p['type']=='month':
            try: params[key]=parse_month(v)
            except ValueError as exc: raise HTTPException(422, f'{p["label"]} 格式非法：{exc}') from None
        elif str(v) not in [str(o['value']) for o in p.get('options',[])]:
            raise HTTPException(422,f'{p["label"]} 不是有效选项')
    # 提交前先按截止月试算样本口径：冲突直接在提交环节拒绝，不生成无效任务。
    preview=build_sample(d.records, params, d.source)
    if preview['conflicts'] or preview['malformed']:
        labels='；'.join(dict.fromkeys(c['label'] for c in preview['conflicts']))
        extra=f'；{len(preview["malformed"])} 行记录无法识别' if preview['malformed'] else ''
        raise HTTPException(422, f'样本口径存在冲突，已阻止估计：{labels or "数据格式问题"}{extra}。请在工作台查看取舍依据并核对台账')
    j=Job(name=data.name, dataset_id=d.id, params=params, created_by=user.id,
          created_at=time.time(), status='queued', dataset_checksum=d.checksum)
    db.add(j); db.flush()
    return j
def preview(db, dataset_id, params):
    d=db.get(Dataset,dataset_id)
    if not d: raise HTTPException(404,'数据批次不存在')
    allowed={p['key']:p for p in metadata().get('parameters',[])}
    merged={k:p['default'] for k,p in allowed.items()}
    merged.update({k:v for k,v in (params or {}).items() if k in allowed})
    try:
        merged['cutoff_month']=parse_month(merged.get('cutoff_month'))
        merged['reliability']=float(merged.get('reliability',0.9))
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    if str(merged.get('model')) not in [str(o['value']) for o in allowed['model']['options']]:
        raise HTTPException(422,'部件型号不是有效选项')
    snapshot=build_sample(d.records, merged, d.source)
    snapshot['reliability']=merged['reliability']
    snapshot['dataset']={'id':d.id,'name':d.name,'checksum':d.checksum,'source':d.source}
    return snapshot
def enqueue(job_id):
    try:
        r=Redis.from_url(REDIS_URL); r.rpush('analysis:queue',job_id); r.close()
    except Exception:
        pass # SQL queued status is the durable fallback
