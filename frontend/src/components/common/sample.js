import {esc,fmt} from '../../utils/dom.js';
import {Inclusion} from '../../constants/enums.js';

const inclusionClass={included:'ok',superseded:'muted',excluded:'warn',conflict:'bad'};

export function sampleStats(s){
 if(!s)return '';
 const st=s.stats||{};
 const cards=[
  ['评估截止月',s.cutoff_month,''],
  ['当前型号样本',`${s.sample_size} 件`,''],
  ['其中失效 / 在役',`${s.failures} / ${s.sample_size-s.failures}`,''],
  ['归并资产 / 纳入',`${st.assets} / ${st.included}`,''],
  ['晚到归位',`${st.late_arrivals||0} 件`,''],
  ['截止月后剔除',`${st.after_cutoff||0} 条`,''],
  ['冲突资产',`${st.conflict_assets||0} 件`,''],
 ];
 return '<div class="sample-stats">'+cards.map(([k,v])=>'<div><span>'+esc(k)+'</span><strong>'+esc(v)+'</strong></div>').join('')+'</div>';
}

export function conflictList(s){
 if(!s?.conflicts?.length)return '';
 return '<div class="conflict-box"><div class="section-title"><h2>数据冲突 · 已阻止估计</h2><span>'+s.conflicts.length+' 条</span></div>'
  +s.conflicts.map(c=>'<p class="warning"><strong>'+esc(c.label)+'</strong> · '+esc(c.detail)+'</p>').join('')
  +(s.malformed?.length?'<p class="warning">另有 '+s.malformed.length+' 行记录无法识别，请核对原始台账。</p>':'')
  +'<p class="hint">冲突资产的全部观测已在下方取舍依据中标红；修正源数据并重新接入批次后才能提交估计。</p></div>';
}

function observationRows(decision){
 return decision.observations.map(o=>'<tr class="inc-'+esc(inclusionClass[o.inclusion]||'')+'">'
  +'<td>'+esc(o.month)+(o.explicit_month?'':' <small>(按批次月归位)</small>')+'</td>'
  +'<td>'+esc(fmt(o.km))+'</td><td>'+(o.failed?'失效':'在役')+'</td>'
  +'<td><span class="tag tag-'+esc(inclusionClass[o.inclusion]||'')+'">'+esc(Inclusion[o.inclusion]||o.inclusion)+'</span></td>'
  +'<td class="reason">'+esc(o.reason)+'</td></tr>').join('');
}

export function decisionsPanel(s,{limit=12,title='逐部件取舍依据'}={}){
 if(!s?.decisions?.length)return '';
 const decisions=[...s.decisions].sort((a,b)=>{
  const rank={conflict:0,excluded:1,included:2,superseded:3};
  return (rank[a.decision]??9)-(rank[b.decision]??9)||a.asset.localeCompare(b.asset);
 });
 const shown=decisions.slice(0,limit);
 return '<div class="panel decisions-panel"><div class="section-title"><h2>'+esc(title)+'</h2><span>'+decisions.length+' 个资产 · 仅显示前 '+shown.length+' 个（冲突优先）</span></div>'
  +shown.map(d=>'<details class="decision-item '+(d.decision==='conflict'?'is-conflict':'')+'"'+(d.decision==='conflict'?' open':'')+'><summary><strong>'+esc(d.asset)+'</strong><span>'+esc(d.model)+'</span>'
   +'<span class="tag tag-'+esc(inclusionClass[d.decision]||'')+'">'+esc(Inclusion[d.decision]||d.decision)+'</span>'
   +(d.selected?'<small>取 '+esc(d.selected.month)+' · '+esc(fmt(d.selected.km))+' 公里 · '+(d.selected.failed?'失效':'在役')+'</small>':'<small>无截止月内有效观测</small>')+'</summary>'
   +(d.conflicts.length?'<div class="conflict-lines">'+d.conflicts.map(c=>'<p class="warning">'+esc(c)+'</p>').join('')+'</div>':'')
   +'<div class="table-wrap"><table><thead><tr><th>观测月</th><th>里程</th><th>状态</th><th>取舍</th><th>依据</th></tr></thead><tbody>'+observationRows(d)+'</tbody></table></div></details>').join('')
  +(decisions.length>shown.length?'<p class="hint">其余资产可在结果详情的样本快照中通过接口复核，口径与本页完全一致。</p>':'')+'</div>';
}
