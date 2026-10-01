// 无浏览器冒烟:用最小 DOM 替身跑页面的脚本，把 SSE 帧喂进 send()，
// 断言徽章真的挂上了、正文没被清理动作用抹掉、等待动画的取舍符合预期。
// 语法的 `node --check` 只能证明它是个合法程序，证明不了这段交互是对的。
//
//   node tests/page-smoke.js src/mewhelp/static/index.html
//
// 它不属于 pytest 套件(要 Node，且它是页面的、不是后端的)——**故意**不接进 CI，
// 免得给后端套件添一个 Node 依赖。改完 index.html 手动跑一次。
//
// 为什么它值得留在仓库里:第 2 章收尾时,它是一个**真**缺陷唯一的拦路测试 ——
// 一轮里同一个工具被调两次时,页面把第一个调用的耗时标到了第二个徽章上,
// 徽章照样收尾、照样有 ms,是一份"看起来正常"的错数据。后端测试看不见这件事,
// 而 `node --check` 只证明它是个合法程序。
const fs = require('fs');

const SRC = process.argv[2];
const html = fs.readFileSync(SRC, 'utf8');
const m = /<script>\r?\n([\s\S]*?)\r?\n<\/script>/.exec(html);
if (!m) { console.error('找不到 <script> 块:', SRC); process.exit(2); }
const script = m[1];

// ── 最小 DOM ────────────────────────────────────────────────────────────
// textContent 的 setter 必须**清掉子节点** —— 页面那个坑就是这么来的，
// 替身不还原这一点，这个冒烟就白跑了。
class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.className = '';
    this._text = '';
    this.dataset = {};
    this.value = '';
    this.disabled = false;
    this.listeners = {};
    this.style = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    const self = this;
    this.classList = {
      add: (c) => { if (!self._classes().includes(c)) self.className = (self.className + ' ' + c).trim(); },
      remove: (c) => { self.className = self._classes().filter((x) => x !== c).join(' '); },
      contains: (c) => self._classes().includes(c),
      toggle: (c, force) => { const add = force === undefined ? !self._classes().includes(c) : force;
        if (add) self.classList.add(c); else self.classList.remove(c); },
    };
  }
  _classes() { return this.className.split(/\s+/).filter(Boolean); }
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this._text + this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) { this.children = []; this._text = String(v); }
  append(...nodes) { for (const n of nodes) { n._parent = this; this.children.push(n); } }
  appendChild(n) { n._parent = this; this.children.push(n); return n; }
  // 必须真的从父节点摘掉 —— 页面靠 remove() 撤掉徽章上的加载小圆点，
  // 空实现的替身会让那件事看起来没发生(第一版就是这么误报的)。
  remove() {
    if (!this._parent) return;
    const i = this._parent.children.indexOf(this);
    if (i >= 0) this._parent.children.splice(i, 1);
    this._parent = null;
  }
  querySelector(sel) {
    const cls = sel.replace(/^\./, '');
    for (const c of this.children) {
      if (c._classes && c._classes().includes(cls)) return c;
      if (c.querySelector) { const got = c.querySelector(sel); if (got) return got; }
    }
    return null;
  }
  querySelectorAll(sel) {
    const found = [];
    for (const c of this.children) {
      if (sel.startsWith('.') ? c._classes().includes(sel.slice(1)) : c.tagName === sel) found.push(c);
      found.push(...c.querySelectorAll(sel));
    }
    return found;
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  async click() {
    if (this.disabled) return;
    await Promise.all((this.listeners.click || []).map(h => h({target: this, preventDefault() {}})));
    if (this.type === 'submit') {
      let parent = this._parent;
      while (parent && parent.tagName !== 'form') parent = parent._parent;
      if (parent) await Promise.all((parent.listeners.submit || []).map(h =>
        h({target: parent, preventDefault() {}})));
    }
  }
  setAttribute(name, value) { this[name] = String(value); }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  // 递归找所有带某 class 的节点 —— 断言要用
  find(cls, acc = []) {
    for (const c of this.children) {
      if (c._classes && c._classes().includes(cls)) acc.push(c);
      if (c.find) c.find(cls, acc);
    }
    return acc;
  }
}

const nodes = {
  log: new El('div'), q: new El('input'), send: new El('button'),
  chips: new El('div'), human: new El('button'), f: new El('form'),
};
for (const id of ['source-dialog','source-close','source-title','source-path','source-meta',
                  'source-question','source-answer','source-status','source-link']) nodes[id] = new El('div');
const document = { createElement: (t) => new El(t), getElementById: (id) => nodes[id] || null,
  createTextNode: t => { const n = new El('#text'); n.textContent = t; return n; } };

const makeStore = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
};
const localStorage = makeStore();
const sessionStorage = makeStore();

// 先塞一个"上一轮留下的"会话 id —— **必须在脚本执行之前**：脚本一跑就把
// sessionId 读进闭包了，之后再塞是塞给下一个页面实例的。
// 塞它是为了让 resumed=false 走"服务端把这段会话弄丢了"那条提示分支。
sessionStorage.setItem('mewhelp_session_id', 'stale-1');

// ── 假 fetch:把帧**切块**吐 ───────────────────────────────────────────
// 块与块之间会再进一次 read() —— 那是"流进行中"唯一能被观测到的时刻
// (流一结束，streaming 类就被 finally 摘掉了，事后再看只剩终止态)。
const encode = (frames) =>
  new TextEncoder().encode(frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(''));

const TOOL_START = ['tool', { name: 'query_logistics', args: { order_id: '1001' }, phase: 'start' }];
const TOOL_END = ['tool', { name: 'query_logistics', args: { order_id: '1001' }, phase: 'end', ok: true, elapsed_ms: 3.4, attempts: 1 }];

// 场景 A:turn1 有前言 —— 工具**还没跑完**，首字已经到了。
const SCENE_WITH_PREAMBLE = [
  [['session', { session_id: 'srv-1', resumed: false }], TOOL_START, ['token', { text: '订单 1001 ' }]],
  [TOOL_END, ['token', { text: '已发出。' }], ['done', { finish_reason: 'stop' }]],
];
// 场景 B:turn1 没有前言 —— 工具跑的那一两秒里，正文区应该还挂着等待动画。
const SCENE_NO_PREAMBLE = [
  [['session', { session_id: 'srv-2', resumed: true }], TOOL_START],
  [TOOL_END, ['token', { text: '已经发出了。' }], ['done', { finish_reason: 'stop' }]],
];
// 场景 C:一轮里**同一个工具被调两次** —— 后端明确支持(registry.run_all 的用例就拿
// 两个同名调用当范例),现实问法是「订单 1001 和 1002 的物流到哪了」。
// 服务端先发完全部 start、再按同一顺序发完 end;前端若按名字只存一个节点,
// 第二个 start 会覆盖第一个,第一个徽章就永远转圈,第二个 end 还会直接落空。
const TOOL_START_1002 = ['tool', { name: 'query_logistics', args: { order_id: '1002' }, phase: 'start' }];
const TOOL_END_1001 = ['tool', { name: 'query_logistics', args: { order_id: '1001' }, phase: 'end', ok: true, elapsed_ms: 3.4, attempts: 1 }];
const TOOL_END_1002 = ['tool', { name: 'query_logistics', args: { order_id: '1002' }, phase: 'end', ok: true, elapsed_ms: 5.1, attempts: 1 }];
const SCENE_TWO_SAME_TOOLS = [
  [['session', { session_id: 'srv-3', resumed: true }], TOOL_START, TOOL_START_1002],
  [TOOL_END_1001, TOOL_END_1002, ['token', { text: '两个订单都在路上。' }], ['done', { finish_reason: 'stop' }]],
];

let sentBody = null;
let snapshot = {};
function makeFetch(scene) {
  const chunks = scene.map(encode);
  let n = 0;
  return async (url, opts) => {
    sentBody = JSON.parse(opts.body);
    snapshot = {};
    return {
      ok: true, status: 200, statusText: 'OK',
      body: {
        getReader: () => ({
          read: async () => {
            // 走到这里，说明上一块已经被 send() 的处理循环吃干净了。
            // **只在 n===1 取一次**:read() 会被调到 done 为止，每次都覆盖的话
            // 留下的就是终止态，而终止态本来就看不到"进行中"。
            if (n === 1) {
              const badge = nodes.log.find('badge').pop();
              const text = nodes.log.find('text').pop();
              snapshot = {
                badgeClass: badge ? badge.className : null,
                badgeHasSpin: badge ? badge.find('spin').length > 0 : false,
                text: text ? text.textContent : null,
                textClass: text ? text.className : null,
                dotsAlive: text ? text.find('dots').length : -1,
              };
            }
            if (n >= chunks.length) return { value: undefined, done: true };
            return { value: chunks[n++], done: false };
          },
        }),
      },
    };
  };
}

// 脚本从作用域里抓走的是**这一个** fetch。要跑第二个场景就得让这个函数自己
// 转交 —— 换掉外面那个变量它看不见。
let currentFetch = makeFetch(SCENE_WITH_PREAMBLE);
const dispatchFetch = (...args) => currentFetch(...args);
const browserWindow = {confirmed: true, confirm() { return this.confirmed; }};

// 顶层是脚本作用域 —— 把函数声明暴露出来当返回值。
const factory = new Function(
  'document', 'localStorage', 'sessionStorage', 'fetch', 'TextDecoder', 'TextEncoder',
  'console', 'Math', 'JSON', 'Map', 'Set', 'Boolean', 'String', 'window',
  script + '\nreturn { send, submit, bubble, getUserId: () => userId };'
);
const api = factory(document, localStorage, sessionStorage, dispatchFetch,
  TextDecoder, TextEncoder, console, Math, JSON, Map, Set, Boolean, String, browserWindow);

const OFFER = {offer_id:'offer-1',turn_id:'turn-1',actions:['handoff','create_ticket'],
  description:'我要投诉',ticket_type:'投诉'};
const SCENE_COMPLAINT = [[['session',{session_id:'action-session',resumed:true}],
  ['token',{text:'很抱歉给您带来不好的体验。'}],['actions',{actions:OFFER.actions,offer:OFFER}],
  ['done',{finish_reason:'completed'}]]];

function actionPage(saved, scene = SCENE_COMPLAINT) {
  for (const key of Object.keys(nodes)) nodes[key] = new El(key === 'f' ? 'form' : 'div');
  const local = saved?.local || makeStore(), session = saved?.session || makeStore();
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({url,body:JSON.parse(options.body)});
    if (url === '/ch05/tickets') return {ok:true,status:200,
      json:async () => ({ticket_no:'T20261001002',ticket_type:'投诉',replayed:false})};
    return makeFetch(scene)(url, options);
  };
  const api = factory(document,local,session,fetch,TextDecoder,TextEncoder,
    console,Math,JSON,Map,Set,Boolean,String,browserWindow);
  return {api,local,session,calls};
}

async function runActionScenario({click,confirm}) {
  const page = actionPage();
  await page.api.submit('我要投诉');
  const handoff = nodes.log.find('action-handoff')[0], ticket = nodes.log.find('action-ticket')[0];
  if (!handoff || !ticket) return {ticketRequests:0,logText:nodes.log.textContent,
    ticketButtonDisabled:null,buttons:0,calls:page.calls};
  browserWindow.confirmed = confirm;
  if (click === 'handoff') await handoff.click();
  if (click === 'ticket') {
    await ticket.click();
    const button = nodes.log.find(confirm ? 'ticket-confirm' : 'ticket-cancel')[0];
    if (button) await button.click();
  }
  if (click === 'ignore') await page.api.submit('你好');
  return {ticketRequests:page.calls.filter(c=>c.url==='/ch05/tickets').length,
    logText:nodes.log.textContent,ticketButtonDisabled:ticket.disabled,buttons:2,
    calls:page.calls,handoffDisabled:handoff.disabled,page};
}

const fails = [];
const check = (name, cond, extra = '') => {
  console.log(`  [${cond ? '✓' : '✗'}] ${name}${extra ? '  ' + extra : ''}`);
  if (!cond) fails.push(name);
};

(async () => {
  // ══ 场景 A:有前言 ══════════════════════════════════════════════════
  console.log('场景 A · turn1 有前言(首字先到，工具后完成)');
  await api.send('订单 1001 的物流到哪了');
  const midA = snapshot;

  check('工具在跑时，徽章是加载态', midA.badgeHasSpin === true, `class=${midA.badgeClass}`);
  check('工具在跑时，徽章还没写结果', midA.badgeClass === 'badge');
  check('首字到了 → 正文挂上流式光标',
        typeof midA.textClass === 'string' && midA.textClass.includes('streaming'), `class=${midA.textClass}`);
  check('首字到了 → 等待动画已撤', midA.dotsAlive === 0, `dots=${midA.dotsAlive}`);
  check('途中正文只有前半句', midA.text === '订单 1001 ', JSON.stringify(midA.text));

  let badges = nodes.log.find('badge');
  let texts = nodes.log.find('text');
  check('徽章挂上了', badges.length === 1, `找到 ${badges.length} 个`);
  check('徽章显示中文工具名', badges.length === 1 && badges[0].textContent.includes('查物流'),
        badges.length ? JSON.stringify(badges[0].textContent) : '');
  check('徽章已收尾', badges.length === 1 && badges[0]._classes().includes('done'),
        badges.length ? `class=${badges[0].className}` : '');
  check('徽章带耗时', badges.length === 1 && badges[0].textContent.includes('ms'));
  check('加载态的小圆点已摘掉', badges.length === 1 && badges[0].find('spin').length === 0);
  check('正文没被徽章/清理动作抹掉', texts.length === 1 && texts[0].textContent === '订单 1001 已发出。',
        texts.length ? JSON.stringify(texts[0].textContent) : '(无正文节点)');
  check('流结束后收掉了流式光标', texts.length === 1 && !texts[0]._classes().includes('streaming'));
  check('请求体带上了 user_id 与 message',
        sentBody && sentBody.user_id && sentBody.message === '订单 1001 的物流到哪了', JSON.stringify(sentBody));
  check('请求体里的 session_id 是本地那个(不是空串)',
        sentBody && sentBody.session_id === 'stale-1', JSON.stringify(sentBody));
  check('服务端下发的新 session_id 已存回',
        sessionStorage.getItem('mewhelp_session_id') === 'srv-1');
  check('resumed=false 且本地有会话 → 提示这是一段新对话',
        nodes.log.find('hint').some((h) => h.textContent.includes('新对话')));
  check('user_id 落在 localStorage 且跨发送稳定',
        !!localStorage.getItem('mewhelp_user_id') && localStorage.getItem('mewhelp_user_id') === api.getUserId(),
        localStorage.getItem('mewhelp_user_id'));

  // ══ 场景 B:无前言 ══════════════════════════════════════════════════
  // 清空消息区再跑 —— 脚本把 log 抓进了闭包，清它这个对象本身即可。
  nodes.log.children = [];
  console.log('\n场景 B · turn1 无前言(工具在跑时正文区必须还看得见东西)');
  currentFetch = makeFetch(SCENE_NO_PREAMBLE);
  await api.send('降噪耳机多少钱');
  const midB = snapshot;

  check('工具在跑时，正文区仍挂着等待动画', midB.dotsAlive === 1, `dots=${midB.dotsAlive}`);
  check('工具在跑时还没挂流式光标',
        typeof midB.textClass === 'string' && !midB.textClass.includes('streaming'), `class=${midB.textClass}`);
  badges = nodes.log.find('badge');
  texts = nodes.log.find('text');
  check('徽章仍然只有一条轨迹', badges.length === 1, `找到 ${badges.length} 个`);
  check('正文最终是完整答复', texts.length === 1 && texts[0].textContent === '已经发出了。',
        texts.length ? JSON.stringify(texts[0].textContent) : '(无正文节点)');
  check('resumed=true → 不提示新对话',
        nodes.log.find('hint').every((h) => !h.textContent.includes('新对话')));

  // ══ 场景 C:同名工具调两次 ══════════════════════════════════════════
  nodes.log.children = [];
  console.log('\n场景 C · 一轮里同一个工具被调两次(「订单 1001 和 1002 的物流到哪了」)');
  currentFetch = makeFetch(SCENE_TWO_SAME_TOOLS);
  await api.send('订单 1001 和 1002 的物流到哪了');

  badges = nodes.log.find('badge');
  texts = nodes.log.find('text');
  check('两次调用 → 两个徽章', badges.length === 2, `找到 ${badges.length} 个`);
  check('两个徽章都收尾了',
        badges.length === 2 && badges.every((b) => b._classes().includes('done')),
        badges.map((b) => b.className).join(' | '));
  check('没有徽章卡在转圈',
        badges.every((b) => b.find('spin').length === 0),
        badges.map((b) => b.find('spin').length).join(' | '));
  check('两个徽章各自带耗时',
        badges.length === 2 && badges.every((b) => b.textContent.includes('ms')),
        badges.map((b) => JSON.stringify(b.textContent)).join(' | '));
  check('正文没被这两个收尾动作影响',
        texts.length === 1 && texts[0].textContent === '两个订单都在路上。',
        texts.length ? JSON.stringify(texts[0].textContent) : '(无正文节点)');

  console.log('\n场景 D · 投诉建议的两个独立动作');
  let action = await runActionScenario({click:'ignore',confirm:true});
  check('投诉提供两个按钮，不点也能继续聊天', action.buttons===2 && action.calls.length===2
    && action.calls.every(c=>c.url==='/ch05/chat/stream'));
  action = await runActionScenario({click:'handoff',confirm:false});
  check('取消人工，无建单或转接效果', action.ticketRequests===0
    && !action.logText.includes('已转接人工客服'));
  action = await runActionScenario({click:'handoff',confirm:true});
  check('人工确认只展示状态和客服小猫问候', action.ticketRequests===0
    && action.logText.includes('已转接人工客服')
    && action.logText.includes('您好，我是客服小猫，请问有什么可以帮您的'));
  check('转人工之后建单按钮仍可点', action.ticketButtonDisabled===false);
  if (action.buttons) {
    await nodes.log.find('action-ticket')[0].click();
    const submit = nodes.log.find('ticket-confirm')[0];
    if (submit) await Promise.all([submit.click(),submit.click()]);
    check('人工后确认建单、双击只一个请求', action.page.calls.filter(c=>c.url==='/ch05/tickets').length===1
      && action.page.calls.find(c=>c.url==='/ch05/tickets').body.confirmed===true);
  }
  action = await runActionScenario({click:'ticket',confirm:false});
  check('取消工单没有写请求', action.buttons===2 && action.ticketRequests===0);
  action = await runActionScenario({click:'ticket',confirm:true});
  check('单独确认工单只发一个写请求', action.ticketRequests===1
    && action.calls.find(c=>c.url==='/ch05/tickets').body.offer_id==='offer-1');
  check('建单后人工按钮仍可点', action.handoffDisabled===false);
  if (action.buttons) {
    await nodes.log.find('action-handoff')[0].click();
    check('工单后人工不再建单', action.page.calls.filter(c=>c.url==='/ch05/tickets').length===1);
    const restored = actionPage(action.page);
    check('恢复历史仅渲染，没有网络副作用', restored.calls.length===0
      && nodes.log.find('action-ticket').length===1);
  }
  const toolbar = actionPage();
  browserWindow.confirmed = true;
  await nodes.human.click();
  check('工具栏人工也只模拟，不发聊天或建单请求', toolbar.calls.length===0
    && nodes.log.textContent.includes('已转接人工客服'));

  console.log('\n场景 E · call_id 和 round 配对，结束帧允许乱序');
  nodes.log.children=[];
  const callPage=actionPage(null,[[[ 'session',{session_id:'calls',resumed:true}],
    ['tool',{...TOOL_START[1],call_id:'a',round:1}],
    ['tool',{...TOOL_START[1],call_id:'b',round:2}]],
    [['tool',{...TOOL_END[1],call_id:'b',round:2,elapsed_ms:52}],
     ['tool',{...TOOL_END[1],call_id:'a',round:1,elapsed_ms:31}],
     ['token',{text:'已查到'}],['done',{finish_reason:'completed'}]]]);
  await callPage.api.send('两个物流查询');
  badges=nodes.log.find('badge');
  check('同名工具乱序 end 对应正确耗时',badges.length===2
    && badges[0].textContent.includes('31 ms') && badges[1].textContent.includes('52 ms'));
  console.log(fails.length ? `\nFAILED: ${fails.join(' / ')}` : '\nALL OK');
  process.exit(fails.length ? 1 : 0);
})();
