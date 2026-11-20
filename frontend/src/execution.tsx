import { useState } from 'react'
import { ArrowUpRight, Search } from 'lucide-react'
import type { ActualEntry, Batch, Plan } from './types'
import { Badge, Empty, dateTime, num, timeValue } from './ui'

export function BatchList({plan,onBatch}:{plan:Plan;onBatch:(batch:Batch)=>void}) {
  const [key,setKey]=useState('')
  const [line,setLine]=useState('')
  const [page,setPage]=useState(0)
  const text=key.trim().toLowerCase()
  const rows=plan.batches.filter(batch=>(!line||batch.line===line)&&[batch.job,batch.code,batch.model].some(value=>value.toLowerCase().includes(text)))
  const last=Math.max(0,Math.ceil(rows.length/16)-1)
  const index=Math.min(page,last)
  return <>
    <div className="table-tools execution-tools">
      <label>产线<select value={line} onChange={e=>{setLine(e.target.value);setPage(0)}}><option value="">全部产线</option>{plan.lines.map(line=><option key={line}>{line}</option>)}</select></label>
      <label className="execution-search">查找计划作业<span className="search-box"><Search size={15}/><input value={key} onChange={e=>{setKey(e.target.value);setPage(0)}} placeholder="作业号或产品"/></span></label>
    </div>
    {rows.length ? <div className="table-scroll"><table><thead><tr><th>作业</th><th>产线</th><th>开工</th><th>完工</th><th>数量</th><th>状态</th></tr></thead><tbody>
      {rows.slice(index*16,(index+1)*16).map(batch=><tr key={batch.key}><td><button className="table-link" type="button" onClick={()=>onBatch(batch)}>{batch.job}</button><small className="id-line">{batch.model}</small></td>
        <td>{batch.line}</td><td>{dateTime(batch.start)}</td><td>{dateTime(batch.end)}</td><td>{num(batch.qty)} 件</td><td><Badge tone={batch.state==='planned'?'draft':'approved'}>{batch.status}</Badge></td></tr>)}
    </tbody></table></div> : <Empty title="没有匹配的作业" detail="试试其他产线、作业号或产品编码。"/>}
    <div className="pagination"><span>共 {rows.length} 道 · {index+1} / {last+1}</span>
      <div><button className="button quiet compact-button" type="button" disabled={index===0} onClick={()=>setPage(index-1)}>上一页</button>
        <button className="button quiet compact-button" type="button" disabled={index===last} onClick={()=>setPage(index+1)}>下一页</button></div></div>
  </>
}

export function ExecutionTable({plan, records, editable, until, onUntil, onRecord}: {
  plan:Plan; records:ActualEntry[]; editable:boolean; until:string; onUntil:(value:string)=>void;
  onRecord:(batch:Batch, record?:ActualEntry)=>void
}) {
  const [query,setQuery]=useState('')
  const [line,setLine]=useState('')
  const [filter,setFilter]=useState('pending')
  const [index,setIndex]=useState(0)
  const known=new Map(records.map(record=>[record.batch_key,record]))
  const searchable=query.trim().toLowerCase()
  const isStale=(record?:ActualEntry)=>record?.kind==='not_started' && !!until && (!record.checked_at||record.checked_at<until)
  const rows=plan.batches.filter(batch=>{
    if (batch.state==='actual' && filter!=='all') return false
    if (line && batch.line!==line) return false
    if (![batch.job,batch.model,batch.code].some(key=>key.toLowerCase().includes(searchable))) return false
    if (filter==='reported') return known.has(batch.key)
    const record=known.get(batch.key)
    if (filter==='pending') return (!record||isStale(record)) && (!until || timeValue(batch.setup_start)<timeValue(until))
    return true
  }).sort((a,b)=>a.start.localeCompare(b.start)||a.line.localeCompare(b.line))
  const maxPage=Math.max(0,Math.ceil(rows.length/16)-1)
  const page=Math.min(index,maxPage)
  return <>
    <div className="table-tools execution-tools">
      <label>核对截至时点<input type="datetime-local" value={until} onChange={e=>{onUntil(e.target.value);setIndex(0)}}/></label>
      <label>产线<select value={line} onChange={e=>{setLine(e.target.value);setIndex(0)}}><option value="">全部产线</option>{plan.lines.map(item=><option key={item}>{item}</option>)}</select></label>
      <label>报工状态<select value={filter} onChange={e=>{setFilter(e.target.value);setIndex(0)}}>
        <option value="pending">截至时点待确认</option><option value="reported">已登记</option><option value="all">全部作业</option>
      </select></label>
      <label className="execution-search">查找作业<span className="search-box"><Search size={15}/><input value={query} onChange={e=>{setQuery(e.target.value);setIndex(0)}} placeholder="作业号或产品"/></span></label>
    </div>
    {rows.length ? <div className="table-scroll"><table><thead><tr><th>作业</th><th>产线</th><th>计划开工</th><th>计划量</th><th>实际完成</th><th>执行状态</th><th><span className="sr-only">操作</span></th></tr></thead>
      <tbody>{rows.slice(page*16,(page+1)*16).map(batch=>{
        const record=known.get(batch.key)
        const label=isStale(record)?'未开工确认已过时':record?.kind==='not_started'?'确认未开工':record||batch.state==='actual'?'实报完成':'待确认'
        return <tr key={batch.key}><td>{batch.job}<small className="id-line">{batch.model}</small></td><td>{batch.line}</td>
          <td>{dateTime(batch.start)}</td><td>{num(batch.qty)} 件</td><td>{record?num(record.qty)+' 件':batch.state==='actual'?num(batch.qty)+' 件':'—'}</td>
          <td><Badge tone={!isStale(record)&&(record||batch.state==='actual')?'approved':'review'}>{label}</Badge></td>
          <td>{editable&&batch.state!=='actual'&&<button className="button quiet compact-button" type="button" onClick={()=>onRecord(batch,record)}><ArrowUpRight size={14}/>{record?'更正报工':'登记报工'}</button>}</td></tr>
      })}</tbody></table></div> : <Empty title="没有符合条件的作业" detail="可切换时间、产线或报工状态。"/>}
    <div className="pagination"><span>共 {rows.length} 道 · {page+1} / {maxPage+1}</span>
      <div><button className="button quiet compact-button" type="button" disabled={page===0} onClick={()=>setIndex(page-1)}>上一页</button>
        <button className="button quiet compact-button" type="button" disabled={page===maxPage} onClick={()=>setIndex(page+1)}>下一页</button></div></div>
  </>
}
