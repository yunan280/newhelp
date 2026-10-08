// Vibe Coding后的DOM/SSE验收；真实浏览器与数据库验收由Task10执行。
const fs = require('fs'), assert = require('assert');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = /<script>\r?\n([\s\S]*?)\r?\n<\/script>/.exec(html)[1];
const oldSmoke = fs.readFileSync('tests/page-smoke.js', 'utf8');
const El = new Function(oldSmoke.slice(oldSmoke.indexOf('class El {'), oldSmoke.indexOf('\nconst nodes =')) + '\nreturn El;')();
const preview = {confirmation_id:'nonce1',ticket_type:'售后',description:'键盘坏了 <img src=x onerror=alert(1)>'};
const store = () => { const map = new Map(); return {getItem:k=>map.get(k)||null,
  setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)}; };
function page(previous, action='confirm', lose=false) {
  const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new El('div')]));
  const document = {getElementById:id=>nodes[id],createElement:tag=>new El(tag),
    createTextNode:text=>{const n=new El('#text');n.textContent=text;return n;}};
  const local=previous?.local||store(), session=previous?.session||store(), calls=[];
  local.setItem('mewhelp_user_id','alice');
  let pending=previous?.pending||null;
  const stream=frames=>{let done=false;const bytes=new TextEncoder().encode(frames.map(([event,data])=>`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''));
    return {ok:true,body:{getReader:()=>({read:async()=>done?{done:true}:(done=true,{done:false,value:bytes})})}};};
  let fail=lose;
  const fetch=async(url,opts)=>{
    if(url.startsWith('/api/conversations?'))return{ok:true,json:async()=>({conversations:[]})};
    if(url.includes('/pending?'))return{ok:true,json:async()=>url.startsWith('/ch08')?pending:null};
    if(url.includes('/receipts/'))return{ok:true,json:async()=>previous?.readonlyReceipt||null};
    const body=JSON.parse(opts.body);calls.push({url,body});
    if(url==='/ch05/chat/stream'){
      pending={ticket_preview:preview,answer:'请核对工单预览。'};
      return stream([['session',{session_id:'chat',resumed:false}],['token',{text:pending.answer}],
        ['ticket_preview',preview],['waiting_for_ticket',pending]]);
    }
    assert.equal(url,'/ch08/tickets/resume/stream');
    assert.deepEqual(body,{session_id:'chat',user_id:'alice',confirmation_id:'nonce1',action});
    if(fail){fail=false;throw new Error('响应丢失');}
    pending=null;
    return stream(action==='confirm'?[['ticket_receipt',{ticket_no:'T-demo',ticket_type:'售后'}],
      ['token',{text:'已提交 T-demo'}],['done',{stop_reason:'ticket_created'}]]
      :[['token',{text:'用户取消了本次工单提交。'}],['done',{stop_reason:'cancelled'}]]);
  };
  const api=new Function('document','localStorage','sessionStorage','fetch','window',script+
    '\nreturn {submit,restoreTicketPending,state:()=>history};')(document,local,session,fetch,{confirm:()=>true});
  return{api,nodes,calls,local,session,get pending(){return pending;}};
}
(async()=>{
  const confirmed=page();await confirmed.api.submit('键盘坏了，帮我建售后工单');
  assert.equal(confirmed.nodes.log.find('ticket-submit')[0].textContent,'确认提交');
  assert.equal(confirmed.nodes.log.find('ticket-problem')[0].textContent,preview.description);
  assert.equal(confirmed.nodes.log.querySelectorAll('img').length,0);
  await Promise.all([confirmed.nodes.log.find('ticket-submit')[0].click(),confirmed.nodes.log.find('ticket-submit')[0].click()]);
  assert.equal(confirmed.calls.filter(c=>c.url.includes('/resume/')).length,1);
  assert(confirmed.nodes.log.textContent.includes('T-demo'));
  const restoredReceipt=page(confirmed);await restoredReceipt.api.restoreTicketPending();
  assert(restoredReceipt.nodes.log.textContent.includes('T-demo'));
  const cancelled=page(null,'cancel');await cancelled.api.submit('帮我建工单');
  await cancelled.nodes.log.find('ticket-preview-cancel')[0].click();
  assert(cancelled.nodes.log.textContent.includes('已取消'));
  const waiting=page();await waiting.api.submit('帮我建工单');
  const restored=page(waiting);await restored.api.restoreTicketPending();
  assert.equal(restored.calls.length,0);assert.equal(restored.nodes.log.find('ticket-submit').length,1);
  const lost=page(null,'confirm',true);await lost.api.submit('帮我建工单');
  await lost.nodes.log.find('ticket-submit')[0].click();
  assert.equal(lost.nodes.log.find('ticket-preview-cancel')[0].disabled,true);
  await lost.nodes.log.find('ticket-submit')[0].click();
  assert.deepEqual(lost.calls[1].body,lost.calls[2].body);
  const committedButLost=page(null,'confirm',true);await committedButLost.api.submit('帮我建工单');
  await committedButLost.nodes.log.find('ticket-submit')[0].click();
  const recovered=page({local:committedButLost.local,session:committedButLost.session,pending:null,
    readonlyReceipt:{answer:'已提交 T-recovered',ticket_receipt:{ticket_no:'T-recovered'},stop_reason:'ticket_created'}});
  await recovered.api.restoreTicketPending();
  assert(recovered.nodes.log.textContent.includes('T-recovered'));
  assert.equal(recovered.calls.length,0);
  assert.equal(recovered.nodes.log.find('ticket-submit').length,0);
  console.log('PASS: preview/text safety, confirm, cancel, double click, receipt restore, pending restore, same-action retry, lost-response refresh readonly receipt');
})().catch(error=>{console.error(error);process.exit(1);});
