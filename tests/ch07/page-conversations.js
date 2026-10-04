const fs = require('fs'), assert = require('assert');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = /<script>\r?\n([\s\S]*?)\r?\n<\/script>/.exec(html)[1];
const oldSmoke = fs.readFileSync('tests/page-smoke.js', 'utf8');
const El = new Function(oldSmoke.slice(oldSmoke.indexOf('class El {'), oldSmoke.indexOf('\nconst nodes =')) + '\nreturn El;')();
const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new El('div')]));
const document = {getElementById: id => nodes[id], createElement: tag => new El(tag),
  createTextNode: text => { const n = new El('#text'); n.textContent = text; return n; }};
const store = () => { const map = new Map(); return {getItem: k => map.get(k) || null,
  setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k)}; };
const local = store(), session = store();
local.setItem('mewhelp_user_id', 'alice');
let resolveA, resolveB, failList = false, chatCalls = 0;
const items = [{id: 2, session_id:'B', first_question:'第二个会话', has_summary:false},
               {id: 1, session_id:'A', first_question:'订单1001', has_summary:true}];
const response = value => ({ok:true, json:async () => value});
const fetch = async (url, options) => {
  if (url.startsWith('/api/conversations?')) {
    if (failList) throw new Error('offline');
    return response({conversations:items});
  }
  if (url.startsWith('/api/conversations/1/')) return new Promise(r => {resolveA=r;});
  if (url.startsWith('/api/conversations/2/')) return new Promise(r => {resolveB=r;});
  if (url.includes('/pending?')) return response(null);
  chatCalls++;
  const data = new TextEncoder().encode('event: session\ndata: {"session_id":"fresh","resumed":false}\n\nevent: token\ndata: {"text":"客服回复"}\n\nevent: done\ndata: {"stop_reason":"completed"}\n\n');
  let done = false;
  return {ok:true, body:{getReader:() => ({read:async() => done ? {done:true} : (done=true,{done:false,value:data})})}};
};
const api = new Function('document','localStorage','sessionStorage','fetch','window',
  script + '\nreturn {loadConversationList,switchConversation,startNewConversation,setChatBusy,submit,' +
  'state:()=>({sessionId,history,conversationItems}),busy:v=>{busy=v;setChatBusy(v);}};')(
    document, local, session, fetch, {confirm:()=>true});
(async () => {
  await api.loadConversationList();
  assert(nodes['conversation-list'].textContent.includes('已摘要'));
  const a = api.switchConversation(1), b = api.switchConversation(2);
  resolveB(response({id:2,session_id:'B',messages:[{id:20,role:'user',content:'全文'+'原'.repeat(3500),citations:[]}]}));
  await b;
  resolveA(response({id:1,session_id:'A',messages:[{id:10,role:'user',content:'迟到A',citations:[]}]}));
  await a;
  assert.equal(api.state().sessionId, 'B');
  assert(nodes.log.textContent.endsWith('原'.repeat(3500)) && !nodes.log.textContent.includes('迟到A'));
  api.busy(true);
  assert.equal(await api.switchConversation(1), false);
  assert.equal(api.startNewConversation(), false);
  assert(nodes['new-conversation'].disabled);
  api.busy(false);
  api.startNewConversation();
  assert.equal(api.state().sessionId, null);
  assert.equal(api.state().conversationItems.length, 2);
  assert.equal(JSON.parse(local.getItem('mewhelp_ch07_history:alice:B')).messages[0].text.length, 3502);
  failList = true;
  await api.loadConversationList();
  await api.submit('继续聊');
  assert.equal(chatCalls, 1);
  assert(nodes.log.textContent.includes('客服回复'));
  console.log('ALL OK: stale switch, full original, busy guard, retained sessions, silent list failure');
})().catch(e => {console.error(e);process.exitCode=1;});
