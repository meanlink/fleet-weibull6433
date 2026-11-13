import {datasets,jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {sampleStats,conflictList,decisionsPanel} from '../components/common/sample.js';
import {esc,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

function field(p){
 if(p.type==='select')return '<select name="param-'+esc(p.key)+'">'+p.options.map(o=>'<option value="'+esc(o.value)+'" '+(o.value==p.default?'selected':'')+'>'+esc(o.label)+'</option>').join('')+'</select>';
 if(p.type==='month')return '<input type="month" name="param-'+esc(p.key)+'" value="'+esc(p.default)+'" pattern="'+esc(p.pattern||'')+'" required>';
 return '<input type="number" step="any" name="param-'+esc(p.key)+'" value="'+esc(p.default)+'" min="'+esc(p.min??'')+'" max="'+esc(p.max??'')+'" required>';
}

export async function workbench(){
 workspace.datasets=await datasets.list();const m=workspace.meta;
 return heading('03 / ANALYSIS LAB',m.workbench_title,'按评估截止月归并跨月补报：晚到记录按观测月归位，截止月后的记录不参与；失效复活或里程倒退将列出冲突并阻止估计。')
 +'<div class="workbench-grid"><section class="panel"><form id="job-form"><label>分析任务名称<input name="name" required minlength="2" value="'+esc(m.name)+' · 复核试算"></label>'
 +'<label>数据批次<select name="dataset_id" required>'+workspace.datasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label>'
 +'<div class="parameter-grid">'+m.parameters.map(p=>'<label>'+esc(p.label)+field(p)+'</label>').join('')+'</div>'
 +'<div class="form-actions"><button type="button" class="button" id="preview-sample" '+(session.user.role==='viewer'||!workspace.datasets.length?'disabled':'')+'>按截止月预览样本 ↻</button>'
 +'<button type="submit" class="primary" '+(session.user.role==='viewer'||!workspace.datasets.length?'disabled':'')+'>'+esc(m.action_label||'执行分析')+' ↗</button></div>'
 +'<p class="hint">提交后由后台任务计算。观察员可查看参数、预览与结果，无法提交。样本口径、冲突与逐部件取舍依据随任务快照留存。</p></form></section>'
 +'<section class="method-sheet"><p class="eyebrow">METHOD / BUSINESS RULE</p><h2>'+esc(m.method)+'</h2><p>'+esc(m.tagline)+'</p><h3>业务判读边界</h3><ul>'+m.limitations.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul><div class="method-trace"><span>输入</span><strong>'+esc(m.dataset_name)+'</strong><span>输出</span><strong>可复核结果 / 参数版本 / 样本快照 / 分发归档</strong></div></section></div>'
 +'<section class="panel" id="sample-panel"><div class="section-title"><h2>截止月样本预览</h2><span>选择批次与截止月后点击预览</span></div><div class="empty">尚未预览。预览会展示每个资产的纳入/替代/剔除/冲突结论与依据，不产生分析任务。</div></section>';
}

function readParams(form){
 const params={};
 for(const p of workspace.meta.parameters){
  const value=form.get('param-'+p.key);
  if(p.type==='number')params[p.key]=Number(value);
  else params[p.key]=value;
 }
 return params;
}

function renderPreview(s){
 const blocked=!!s.conflicts?.length||!!s.malformed?.length;
 document.querySelector('#sample-panel').innerHTML='<div class="section-title"><h2>截止月样本预览 · '+esc(s.dataset?.name||'')+'</h2><span>'+esc(s.cutoff_month)+' 口径</span></div>'
  +sampleStats(s)+(blocked?'<p class="warning">该口径下估计将被阻止，请先处理下列冲突。</p>':'<p class="hint">口径通过校验，可提交拟合。</p>')
  +conflictList(s)+decisionsPanel(s);
}

export function bindWorkbench(){
 const form=document.querySelector('#job-form');
 document.querySelector('#preview-sample')?.addEventListener('click',async()=>{
  try{
   const s=await jobs.preview({dataset_id:Number(form.dataset_id.value),params:readParams(form)});
   workspace.lastPreview=s;renderPreview(s);
   if(s.conflicts?.length)notify('预览发现 '+s.conflicts.length+' 条冲突，估计将被阻止',true);
   else notify('样本预览已生成：'+s.sample_size+' 件进入当前型号拟合');
  }catch(e){notify(e.message,true);}
 });
 submit(form,async f=>{
  const payload={name:f.get('name'),dataset_id:Number(f.get('dataset_id')),params:readParams(f)};
  const j=await jobs.create(payload);
  workspace.selectedJob=j.id;notify('分析任务已提交，样本口径已随任务留存');
  location.hash='#/results/'+j.id;
 });
}
