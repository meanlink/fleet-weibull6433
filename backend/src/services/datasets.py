from fastapi import HTTPException
from sqlalchemy import select
from src.models import Dataset, Job
from src.services.business import metadata
from src.services.audit import record
from src.utils.serialization import checksum
import math, time
def create(db, data, actor='system'):
    name=data.name.strip()
    if len(name)<2: raise HTTPException(422, '批次名称去除空格后至少需要两个字符')
    if db.scalar(select(Dataset).where(Dataset.name==name)):
        raise HTTPException(409, '该数据批次名已存在，请修改名称')
    optional={c['key'] for c in metadata()['columns'] if c.get('optional')}
    for index,row in enumerate(data.records):
        for c in metadata()['columns']:
            value=row.get(c['key'])
            if (value is None or value=='') and c['key'] in optional: continue
            if value is None or value=='': raise HTTPException(422, f'第 {index+1} 行缺少字段 {c["label"]}')
            if c['type']=='number':
                try:
                    if isinstance(value,bool) or not math.isfinite(float(value)): raise ValueError()
                    row[c['key']]=float(value)
                except (TypeError, ValueError, OverflowError): raise HTTPException(422, f'第 {index+1} 行 {c["label"]} 必须为有限数值')
            elif c['type']=='string':
                row[c['key']]=str(value).strip()
    d=Dataset(name=name, source=data.source, records=data.records, checksum=checksum(data.records), created_at=time.time())
    db.add(d); db.flush()
    mark_stale_jobs(db, d, actor)
    return d

def mark_stale_jobs(db, dataset, actor='system'):
    """源数据更新后，引用同一业务批次名之外的旧批次估计全部标记失效。

    数据集本身不可变；“源数据更新”在本系统中体现为接入新批次。旧任务的结果、
    参数与样本快照保持原样（已批准简报仍可回看当时口径），仅置 stale=True。
    """
    previous = list(db.scalars(select(Dataset).where(Dataset.id != dataset.id)))
    stale_ids = []
    for old in previous:
        for j in db.scalars(select(Job).where(Job.dataset_id == old.id, Job.stale == False)):
            j.stale = True
            stale_ids.append(j.id)
    if stale_ids:
        record(db, actor, 'job.stale_marked', {'new_dataset': dataset.id, 'jobs': stale_ids})
    return stale_ids
