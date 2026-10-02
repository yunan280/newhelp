
const log = document.getElementById('log');
const input = document.getElementById('q');
const sendBtn = document.getElementById('send');
const chipsEl = document.getElementById('chips');
const humanBtn = document.getElementById('human');

// session_id 存 sessionStorage:刷新页面还能接着上一轮聊。
// 用 sessionStorage 而不是 localStorage —— 关掉标签页就换一个新会话,符合"客服"的直觉;
// 服务端 checkpoint 持久化后，刷新或重启仍以服务端等待状态为准。
//
// 但 sessionStorage **不是一定可用**:隐私模式、站点数据被阻止时,访问它是**抛异常**
// 而不是返回 null。两处访问的代价各不相同,都得接住:
//   读 —— 在脚本顶层,抛出去整个页面的 JS 就不再有下文(连输入框都按不动);
//   写 —— 在流处理里,抛出去会被外层 catch 误判成"连接中断"。
// 读不到就当"没有会话 id"(服务端会发一个新的),写不进去只丢掉"刷新续聊"这个便利。
const SESSION_KEY = 'mewhelp_session_id';

function readStoredSessionId() {
  try { return sessionStorage.getItem(SESSION_KEY); } catch (e) { return null; }
}

function storeSessionId(id) {
  try { sessionStorage.setItem(SESSION_KEY, id); } catch (e) { /* 存不下不影响这一轮对话 */ }
}

let sessionId = readStoredSessionId();

// user_id 存 localStorage，和 session_id 的存储分开 —— 两者生命周期不同:
// 会话是"关掉标签页就换一个"，而用户身份要跨会话稳定(工单的归属人就是它)。
// 同样接住异常(隐私模式、站点数据被阻止时访问是**抛异常**)，
// 拿不到就退回一个固定的匿名标识 —— 服务端还有兜底默认值。
const USER_KEY = 'mewhelp_user_id';

function readOrCreateUserId() {
  try {
    const kept = localStorage.getItem(USER_KEY);
    if (kept) return kept;
    const fresh = 'web-' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(USER_KEY, fresh);
    return fresh;
  } catch (e) {
    return 'web-anon';
  }
}

const userId = readOrCreateUserId();

const HISTORY_KEY = 'mewhelp_ch04_history:' + userId;
const FEEDBACK_KEY = 'mewhelp_ch04_feedback';
let history = [];

function freshAnswerId() {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID()
    : 'answer-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
}

function saveHistory() {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify({session_id: sessionId, messages: history.slice(-200)})); }
  catch (e) { /* 存储不可用时，当前页面仍可聊天和锁定反馈。 */ }
}

function normalizeSources(sources) {
  if (!Array.isArray(sources)) return [];
  const seen = new Set();
  return sources.filter(source => {
    if (!source || !Number.isSafeInteger(source.number) || source.number < 1 || seen.has(source.number)
      || typeof source.chunk_id !== 'string' || !/^[1-9][0-9]{0,18}$/.test(source.chunk_id)
      || typeof source.answer !== 'string' || typeof source.questions !== 'string'
      || typeof source.section_path !== 'string') return false;
    seen.add(source.number);
    return true;
  });
}

const sourceDialog = document.getElementById('source-dialog');
let sourceRequest = 0;
document.getElementById('source-close').addEventListener('click', () => sourceDialog.close());
sourceDialog.addEventListener('click', e => { if (e.target === sourceDialog) sourceDialog.close(); });

async function openCitation(source) {
  const request = ++sourceRequest;
  document.getElementById('source-title').textContent = '回答依据 [' + source.number + ']';
  document.getElementById('source-path').textContent = source.section_path;
  document.getElementById('source-meta').textContent = [source.category, source.product_category,
    '来源 ID：' + source.chunk_id].filter(Boolean).join(' · ');
  document.getElementById('source-question').textContent = source.questions;
  document.getElementById('source-answer').textContent = source.answer;
  const status = document.getElementById('source-status');
  status.textContent = '这里展示本次回答使用的原文快照。正在核对当前来源…';
  document.getElementById('source-link').href = '/kb/source/' + encodeURIComponent(source.chunk_id)
    + '?snapshot=' + encodeURIComponent(source.content_hash || '');
  if (!sourceDialog.open) sourceDialog.showModal();
  try {
    const response = await fetch('/api/kb/chunks/' + encodeURIComponent(source.chunk_id));
    if (request !== sourceRequest || !sourceDialog.open) return;
    if (response.status === 404) { status.textContent = '当前来源已删除或尚未发布；本次回答的原文快照仍保留。'; return; }
    if (!response.ok) throw new Error('读取失败');
    const current = await response.json();
    if (request !== sourceRequest || !sourceDialog.open) return;
    status.textContent = current.content_hash !== source.content_hash
      ? '当前内容已更新；这里仍展示本次回答的原文快照。可打开当前原文查看变化。'
      : '本次回答的原文快照与当前来源一致。';
  } catch (e) {
    if (request === sourceRequest && sourceDialog.open) status.textContent = '暂时无法核对当前来源；这里展示本次回答的原文快照。';
  }
}

function renderAnswer(body, text, sources) {
  const map = new Map(normalizeSources(sources).map(source => [String(source.number), source]));
  body.replaceChildren();
  let offset = 0;
  for (const match of text.matchAll(/\[([1-9][0-9]*)\]/g)) {
    body.append(document.createTextNode(text.slice(offset, match.index)));
    const source = map.get(match[1]);
    if (source) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'citation'; button.textContent = match[0];
      button.setAttribute('aria-label', '查看引用 ' + match[1]);
      button.addEventListener('click', () => openCitation(source));
      body.append(button);
    } else body.append(document.createTextNode(match[0]));
    offset = match.index + match[0].length;
  }
  body.append(document.createTextNode(text.slice(offset)));
}

function attachFeedback(reply, record) {
  if (!record.complete || record.error || record.finish_reason === 'output_limit'
    || reply.bubble.querySelector('.feedback')) return;
  const group = document.createElement('div');
  group.className = 'feedback'; group.setAttribute('role', 'group'); group.setAttribute('aria-label', '回答满意度');
  const status = document.createElement('span'); status.className = 'status'; status.setAttribute('role', 'status');
  const buttons = ['up', 'down'].map(choice => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = choice === 'up' ? '👍' : '👎';
    button.setAttribute('aria-label', choice === 'up' ? '满意' : '不满意');
    button.addEventListener('click', () => {
      if (record.feedback) return;
      record.feedback = {answer_id: record.answer_id, session_id: record.session_id || sessionId,
        choice, created_at: new Date().toISOString()};
      refresh(); // 先锁定，再尝试存储；存储失败也不能改选。
      try {
        const stored = JSON.parse(localStorage.getItem(FEEDBACK_KEY) || '[]');
        const signals = Array.isArray(stored) ? stored : [];
        signals.push(record.feedback);
        localStorage.setItem(FEEDBACK_KEY, JSON.stringify(signals));
      } catch (e) { /* 纯前端信号，当前页面锁定仍保留。 */ }
      saveHistory();
    });
    group.append(button);
    return button;
  });
  function refresh() {
    buttons.forEach((button, i) => {
      const selected = record.feedback && record.feedback.choice === (i === 0 ? 'up' : 'down');
      button.disabled = Boolean(record.feedback);
      button.classList.toggle('selected', Boolean(selected));
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    status.textContent = record.feedback ? '已反馈' : '';
  }
  group.append(status); reply.bubble.append(group); refresh();
}

function normalizeOffer(offer) {
  if (!offer || typeof offer.offer_id !== 'string' || !offer.offer_id
    || typeof offer.description !== 'string' || !Array.isArray(offer.actions)) return null;
  return {...offer, actions: [...new Set(offer.actions.filter(a => ['handoff', 'create_ticket'].includes(a)))],
    ticket_type: ['售后', '投诉', '咨询'].includes(offer.ticket_type) ? offer.ticket_type : '咨询'};
}

function simulateHandoff(record, button) {
  if (record?.handoff_done || !window.confirm('确认转接人工客服？')) return;
  if (record) record.handoff_done = true;
  if (button && record) { button.disabled = true; button.textContent = '已转接人工客服'; }
  note('已转接人工客服');
  const text = '您好，我是客服小猫，请问有什么可以帮您的';
  bubble('ai', text);
  history.push({who:'ai',text,session_id:sessionId,sources:[],complete:false,
    error:false,local_simulation:true,answer_id:freshAnswerId()});
  saveHistory();
}

function attachActions(reply, record) {
  const offer = normalizeOffer(record.offer);
  if (!record.complete || record.error || !offer || !offer.actions.length
    || reply.bubble.querySelector('.actions')) return;
  const group = document.createElement('div'); group.className = 'actions';
  group.setAttribute('role','group'); group.setAttribute('aria-label','可选服务');
  const status = document.createElement('div'); status.className = 'action-status';
  status.setAttribute('role','status');
  let pending = false, panel = null;
  for (const action of offer.actions) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = action === 'handoff' ? 'action-handoff' : 'action-ticket';
    button.textContent = action === 'handoff' ? '转人工' : '建工单';
    if (action === 'handoff') {
      button.disabled = Boolean(record.handoff_done);
      if (record.handoff_done) button.textContent = '已转接人工客服';
      button.addEventListener('click', () => simulateHandoff(record, button));
    } else {
      button.disabled = Boolean(record.ticket_receipt);
      if (record.ticket_receipt) status.textContent = '工单已创建：' + record.ticket_receipt.ticket_no;
      button.addEventListener('click', () => {
        if (pending || record.ticket_receipt || panel) return;
        panel = document.createElement('form'); panel.className = 'ticket-confirmation';
        const label = document.createElement('label'); label.textContent = '问题描述';
        const description = document.createElement('textarea'); description.className = 'ticket-description';
        description.required = true; description.maxLength = 2000;
        // 已确认的请求必须保持原参数，回执丢失后才能取回同一张单。
        const confirmed = record.ticket_request;
        description.value = (confirmed?.description || offer.description).slice(0,2000);
        description.disabled = Boolean(confirmed); label.append(description);
        const typeLabel = document.createElement('label'); typeLabel.textContent = '工单类型';
        const type = document.createElement('select'); type.className = 'ticket-type';
        for (const value of ['售后','投诉','咨询']) {
          const option = document.createElement('option'); option.value = value; option.textContent = value;
          type.append(option);
        }
        type.value = confirmed?.ticket_type || offer.ticket_type;
        type.disabled = Boolean(confirmed); typeLabel.append(type);
        const controls = document.createElement('div'); controls.className = 'controls';
        const cancel = document.createElement('button'); cancel.type = 'button';
        cancel.className = 'ticket-cancel'; cancel.textContent = '取消';
        const confirm = document.createElement('button'); confirm.type = 'submit';
        confirm.className = 'ticket-confirm'; confirm.textContent = '确认建工单';
        controls.append(cancel,confirm); panel.append(label,typeLabel,controls); group.append(panel);
        cancel.addEventListener('click', () => { if (!pending) { panel.remove(); panel = null; } });
        panel.addEventListener('submit', async e => {
          e.preventDefault();
          if (pending || record.ticket_receipt) return;
          if (!description.value.trim()) { status.textContent = '请补充问题描述。'; return; }
          if (!record.ticket_request) {
            record.ticket_request = {session_id:record.session_id,user_id:userId,
              offer_id:offer.offer_id,confirmed:true,
              description:description.value,ticket_type:type.value};
            saveHistory(); // 请求前保存；刷新仅恢复，仍需用户主动确认重试。
          }
          description.disabled = type.disabled = true;
          pending = true; button.disabled = confirm.disabled = cancel.disabled = true;
          status.textContent = '正在创建工单…';
          try {
            const response = await fetch('/ch05/tickets', {method:'POST',
              headers:{'Content-Type':'application/json'}, body:JSON.stringify(record.ticket_request)});
            const receipt = await response.json();
            if (!response.ok) throw new Error(typeof receipt.detail === 'string' ? receipt.detail : '创建失败');
            if (typeof receipt.ticket_no !== 'string' || !receipt.ticket_no) throw new Error('未收到工单号');
            record.ticket_receipt = receipt; status.textContent = '工单已创建：' + receipt.ticket_no;
            panel.remove(); panel = null; saveHistory();
          } catch (error) {
            status.textContent = error.message + '。回执尚未确认，稍后确认重试会沿用已提交内容。';
          } finally {
            pending = false; button.disabled = Boolean(record.ticket_receipt);
            confirm.disabled = cancel.disabled = false;
          }
        });
      });
    }
    group.append(button);
  }
  group.append(status); reply.bubble.append(group);
}

// 返回气泡的三个把手:整体、徽章容器、正文容器。
// AI 气泡的两者必须分开(理由见 CSS 里 `.bubble .tools` 那段);用户气泡没有徽章，
// 两个把手都指向气泡本身，调用方因此不必分支。
function bubble(who, text) {
  const row = document.createElement('div');
  row.className = 'wrap';
  const inner = document.createElement('div');
  inner.className = 'row ' + who;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = who === 'ai' ? '🐱' : '🙋';

  const b = document.createElement('div');
  b.className = 'bubble';

  let tools = b;
  let body = b;
  if (who === 'ai') {
    tools = document.createElement('div');
    tools.className = 'tools';
    body = document.createElement('div');
    body.className = 'text';
    b.append(tools, body);
  }
  body.textContent = text;

  inner.append(avatar, b);
  row.appendChild(inner);
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return { bubble: b, tools, text: body };
}

// 系统提示行(会话丢失之类)。刻意不做成消息气泡 —— 它不是对话的一方说的话。
function note(text) {
  const el = document.createElement('div');
  el.className = 'hint';
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

// SSE 一个事件一个块,块之间空行分隔;行尾可能是 \n 也可能是 \r\n。
// 按"找到完整分隔符"切,而不是 split('\n\n') —— 后者遇到 \r\n\r\n 会切不开。
const SEP = /\r?\n\r?\n/;

function parseBlock(block) {
  let event = 'message';
  const dataLines = [];
  for (const rawLine of block.split(/\r?\n/)) {
    if (rawLine.startsWith(':')) continue;              // 注释行(心跳 / 保活)
    if (rawLine.startsWith('event:')) event = rawLine.slice(6).trim();
    else if (rawLine.startsWith('data:')) dataLines.push(rawLine.slice(5).replace(/^ /, ''));
  }
  return { event: event, data: dataLines.join('\n') };
}

const ERROR_LABEL = { empty_completion: '模型没答上来', upstream_error: '上游出错' };

// 工具名 → 人话。没列到的直接显示原名:多一个新工具时页面不会瞎猜一个中文名。
// (服务端的工具名是英文契约，这个映射是纯粹的展示层。)
const TOOL_LABEL = {
  query_order: '查订单',
  query_product: '查商品',
  query_logistics: '查物流',
  query_faq: '查 FAQ',
  create_ticket: '建工单',
};

// 三个点必须是真 DOM 节点,不能靠 CSS 的 `content` 逐帧变 —— 理由见上面 .dots 的注释。
function makeDots() {
  const d = document.createElement('span');
  d.className = 'dots';
  d.append(document.createElement('i'), document.createElement('i'), document.createElement('i'));
  return d;
}

const pendingCards = new Map();
const recordReplies = new WeakMap();
const REFUND_REASONS = ['七天无理由', '商品质量问题', '与描述不符', '破损或缺件', '发错货', '其他'];
const price = value => '¥' + Number(value).toFixed(2);

function businessNode(parent, tag, text, className = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  parent.appendChild(node);
  return node;
}

function expireCards() {
  for (const {record, buttons, status} of pendingCards.values()) {
    record.selection_expired = true;
    buttons.forEach(button => { button.disabled = true; });
    status.textContent = '已失效：已开始新的问题。';
  }
  pendingCards.clear();
}

function attachOrderPicker(reply, record, selection) {
  reply.bubble.querySelector('.pending-check')?.remove();
  reply.bubble.querySelector('.order-picker')?.remove();
  const box = businessNode(reply.bubble, 'div', '', 'order-picker');
  businessNode(box, 'strong', '选择要处理的订单');
  const status = businessNode(box, 'div', '点选后继续处理这笔订单。', 'business-status');
  status.setAttribute('role', 'status');
  const buttons = selection.orders.map(order => {
    const button = businessNode(box, 'button', '', 'order-card');
    button.type = 'button';
    businessNode(button, 'strong', order.product_name + ' · ' + price(order.paid_amount));
    businessNode(button, 'small', '订单 ' + order.order_id + ' · ' + order.status
      + (order.received_at ? ' · ' + order.received_at + ' 签收' : ''));
    button.disabled = Boolean(record.selected_order_id && record.selected_order_id !== order.order_id);
    button.addEventListener('click', async () => {
      if (busy || record.selection_expired || record.complete) return;
      busy = true;
      setChatBusy(true);
      record.selected_order_id = order.order_id;
      buttons.forEach(item => { item.disabled = true; });
      status.textContent = '已选订单 ' + order.order_id + '，正在查询订单和政策…';
      saveHistory();
      try {
        await send('', {reply, record, selection, order});
        if (record.complete) {
          pendingCards.delete(selection.selection_id);
          box.replaceChildren();
          businessNode(box, 'div', '已选择：' + order.product_name + ' · 订单 ' + order.order_id);
        } else if (!record.selection_expired) {
          status.textContent = '流程尚未完成，点选同一订单重试。';
          button.disabled = false;
        } else status.textContent = '订单卡片已失效，请重新提问。';
      } finally { busy = false; setChatBusy(false); }
    });
    return button;
  });
  record.order_selection = selection;
  record.selection_expired = false;
  pendingCards.set(selection.selection_id, {reply, record, buttons, status});
  log.scrollTop = log.scrollHeight;
}

async function attachRefund(reply, record) {
  const offer = record.refund_offer;
  if (!offer || !record.complete || reply.bubble.querySelector('.refund-form')) return;
  const box = businessNode(reply.bubble, 'div', '', 'refund-form');
  businessNode(box, 'strong', '申请退款');
  businessNode(box, 'div', offer.order.product_name + ' · 订单 ' + offer.order.order_id
    + ' · 实付 ' + price(offer.order.paid_amount));
  const status = businessNode(box, 'div', '正在核对申请回执…', 'business-status');
  status.setAttribute('role', 'status');
  const showReceipt = receipt => {
    box.querySelector('label')?.remove();
    box.querySelector('.business-controls')?.remove();
    box.classList.add('refund-receipt');
    status.textContent = '申请已提交，待审核。申请号：' + receipt.application_no + '；原因：' + receipt.reason;
    record.refund_receipt = receipt;
    saveHistory();
  };
  try {
    const receiptResponse = await fetch('/ch06/refunds/' + encodeURIComponent(offer.offer_id)
      + '?session_id=' + encodeURIComponent(record.session_id) + '&user_id=' + encodeURIComponent(userId));
    const saved = await receiptResponse.json();
    if (receiptResponse.ok) { showReceipt(saved); return; }
    if (receiptResponse.status !== 404 || saved.detail !== '退款回执不存在') {
      status.textContent = '这份申请已无法操作，请重新咨询。'; return;
    }
  } catch (error) { status.textContent = '回执核对失败，请刷新后重试。'; return; }
  status.textContent = '提交后进入人工审核。';
  const label = businessNode(box, 'label', '退款原因');
  const select = document.createElement('select');
  select.setAttribute('aria-label', '退款原因');
  select.append(new Option('请选择退款原因', ''));
  REFUND_REASONS.forEach(reason => select.append(new Option(reason, reason)));
  select.value = REFUND_REASONS.includes(record.refund_reason) ? record.refund_reason : '';
  label.appendChild(select);
  const controls = businessNode(box, 'div', '', 'business-controls');
  const confirm = businessNode(controls, 'button', '确认提交退款申请');
  const cancel = businessNode(controls, 'button', '取消');
  confirm.type = cancel.type = 'button';
  confirm.disabled = !select.value;
  select.addEventListener('change', () => {
    record.refund_reason = select.value; confirm.disabled = !select.value; saveHistory();
  });
  cancel.addEventListener('click', () => {
    const hidden = !label.hidden;
    label.hidden = hidden; confirm.hidden = hidden;
    cancel.textContent = hidden ? '重新填写' : '取消';
    status.textContent = hidden ? '已取消填写，未提交申请。' : '提交后进入人工审核。';
  });
  confirm.addEventListener('click', async () => {
    if (!select.value || confirm.disabled || busy) return;
    confirm.disabled = cancel.disabled = select.disabled = true;
    busy = true; setChatBusy(true);
    status.textContent = '正在提交…';
    try {
      const response = await fetch('/ch06/refunds', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          session_id: record.session_id, user_id: userId, offer_id: offer.offer_id,
          order_id: offer.order.order_id, reason: select.value, confirmed: true})});
      const result = await response.json();
      if (!response.ok) {
        if ([403, 409].includes(response.status)) {
          status.textContent = String(result.detail) + '，请重新咨询。'; return;
        }
        throw new Error(typeof result.detail === 'string' ? result.detail : '提交未完成');
      }
      showReceipt(result);
    } catch (error) {
      status.textContent = error.message + '；保留同一原因后可重试。';
      confirm.disabled = cancel.disabled = select.disabled = false;
    } finally { busy = false; setChatBusy(false); }
  });
}

async function restorePending() {
  if (!sessionId || busy) return;
  try {
    const response = await fetch('/ch06/sessions/' + encodeURIComponent(sessionId)
      + '/pending?user_id=' + encodeURIComponent(userId));
    if (!response.ok) throw new Error('无法核对订单选择');
    const pending = await response.json();
    expireCards();
    if (!pending?.order_selection) {
      for (const record of history.filter(item => item.waiting)) {
        const checking = recordReplies.get(record)?.bubble.querySelector('.pending-check');
        if (checking) checking.textContent = '这张订单卡片已失效，请重新提问。';
      }
      saveHistory(); return;
    }
    let record = history.find(item => item.order_selection?.selection_id === pending.order_selection.selection_id);
    if (!record) {
      record = {who: 'ai', text: pending.answer, session_id: sessionId, sources: [], complete: false,
        answer_id: freshAnswerId(), waiting: true};
      history.push(record); recordReplies.set(record, bubble('ai', record.text));
    }
    attachOrderPicker(recordReplies.get(record), record, pending.order_selection);
    saveHistory();
  } catch (error) { note('订单卡片核对失败，请刷新后重试。'); }
}

async function send(text, continuation = null) {
  if (!continuation) {
    expireCards();
    bubble('me', text);
    history.push({who: 'me', text, session_id: sessionId});
  }
  const record = continuation?.record || {who: 'ai', text: '', sources: [], answer_id: freshAnswerId(),
    session_id: sessionId, feedback: null, complete: false, error: false};
  if (!continuation) history.push(record);
  else { record.text = ''; record.sources = []; record.error = false; record.waiting = false; }
  const reply = continuation?.reply || bubble('ai', '');
  recordReplies.set(record, reply);
  reply.text.textContent = '';
  reply.bubble.classList.remove('err');
  reply.text.appendChild(makeDots());
  // 发出去之前本地有没有会话 id —— 用来判断 session 帧的 resumed=false 该不该提示。
  // 首次发言时本地本来就没有，服务端新建一个，那不是"丢了历史"。
  const hadLocalSession = Boolean(sessionId);
  // Ch05 按 round + call_id 配对，允许同名工具的结束帧乱序。
  // 缺少这两个字段的旧协议帧才回退到同名徽章队列。
  const badges = new Map();
  let started = false;
  let gotAnything = false;
  let completed = false;
  let failed = false;
  let waiting = false;

  // 第一个 token 到达:撤掉等待动画、换上流式光标。
  // 早于第一个 token 挂光标会出现"三个点 + 光标"并存的怪相,所以放在这里。
  const start = () => {
    if (started) return;
    started = true;
    reply.text.textContent = '';       // 设置 textContent 会把子节点(那三个点)一并移除
    reply.text.classList.add('streaming');
  };

  // 工具跑的这段时间里正文区**保留**那三个点:撤掉的话那一两秒就是个空气泡,
  // 看着像卡住 —— 而这段时间恰恰是本页最需要"看得见在动"的一段。
  const startBadge = (payload) => {
    const el = document.createElement('span');
    el.className = 'badge';
    const spin = document.createElement('span');
    spin.className = 'spin';
    const name = document.createElement('span');
    name.textContent = '🔧 ' + (TOOL_LABEL[payload.name] || payload.name);
    el.append(spin, name);
    reply.tools.appendChild(el);
    const key = payload.call_id ? payload.round + ':' + payload.call_id : payload.name;
    const queue = badges.get(key);
    if (queue) queue.push(el);
    else badges.set(key, [el]);
    log.scrollTop = log.scrollHeight;
  };

  const finishBadge = (payload) => {
    const key = payload.call_id ? payload.round + ':' + payload.call_id : payload.name;
    const queue = badges.get(key);
    // 先 start 的先收尾，与服务端发 end 的顺序一致
    const el = queue && queue.length ? queue.shift() : null;
    // 这个名字收完了就整个删掉（留个空数组在 Map 里没有意义）
    if (queue && queue.length === 0) badges.delete(key);
    if (!el) return;                 // 没收到 start(理论上不会):宁可不补，也不挂个孤儿徽章
    el.className = 'badge ' + (payload.ok ? 'done' : 'fail');
    const spin = el.querySelector('.spin');
    if (spin) spin.remove();
    const tail = document.createElement('span');
    tail.textContent = payload.ok
      ? '✓ ' + Math.round(payload.elapsed_ms) + ' ms'
      : '✗ ' + (payload.error || '失败');
    el.appendChild(tail);
    log.scrollTop = log.scrollHeight;
  };

  const handle = ({ event, data }) => {
    if (!data) return;
    let payload;
    try {
      payload = JSON.parse(data);
    } catch (e) {
      return;   // 解析不了的块(心跳之类)忽略掉,不能让它把整条流打断
    }

    if (event === 'session') {
      sessionId = payload.session_id;
      storeSessionId(sessionId);
      record.session_id = sessionId;
      if (hadLocalSession && payload.resumed === false) {
        note('服务端没有这段会话的记录，下面是一段新对话。');
      }
    } else if (event === 'tool') {
      // 不碰正文、也不撤等待动画 —— 见 startBadge 上面的注释。
      if (payload.phase === 'end') finishBadge(payload);
      else startBadge(payload);
    } else if (event === 'sources') {
      record.sources = normalizeSources(payload.sources);
      record.refused = payload.refused === true;
      if (record.text) renderAnswer(reply.text, record.text, record.sources);
    } else if (event === 'token') {
      if (!started) start();
      record.text += typeof payload.text === 'string' ? payload.text : '';
      renderAnswer(reply.text, record.text, record.sources);
      gotAnything = true;
      log.scrollTop = log.scrollHeight;
    } else if (event === 'error') {
      if (payload.code === 'selection_error' && [403, 409].includes(payload.status)) record.selection_expired = true;
      failed = true; record.error = true; record.complete = false;
      // 服务端契约:这一轮**没有**写进会话历史,重发同一条消息即可重试,不用换会话。
      if (!started) start();
      reply.text.classList.remove('streaming');
      reply.bubble.classList.add('err');
      reply.text.textContent += (gotAnything ? '\n\n' : '')
        + '[' + (ERROR_LABEL[payload.code] || '出错') + '] ' + payload.message;
    } else if (event === 'actions') {
      record.offer = normalizeOffer(payload.offer);
    } else if (event === 'order_selection') {
      record.order_selection = payload.order_selection || payload;
    } else if (event === 'waiting_for_order' && !failed) {
      waiting = true; record.waiting = true;
      record.order_selection = payload.order_selection || record.order_selection;
      record.text = payload.answer || record.text || '请选择要处理的订单。';
      renderAnswer(reply.text, record.text, []);
      attachOrderPicker(reply, record, record.order_selection);
      saveHistory();
    } else if (event === 'done' && !failed && gotAnything && record.text.trim()) {
      completed = true; record.complete = true;
      record.waiting = false;
      record.refund_offer = payload.refund_offer || null;
      record.finish_reason = payload.finish_reason || payload.stop_reason || 'completed';
      attachFeedback(reply, record);
      attachActions(reply, record);
      attachRefund(reply, record);
      saveHistory();
    }
  };

  try {
    const resp = await fetch(continuation ? '/ch06/orders/selection/stream' : '/ch05/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // session_id 可能是 null —— **不能**把它换成空串:服务端那条"空白一律 422"的
      // 规则对空串同样生效。null 表示"我还没有会话"，服务端会发一个新的。
      body: JSON.stringify(continuation ? {session_id: sessionId, user_id: userId,
        selection_id: continuation.selection.selection_id, order_id: continuation.order.order_id}
        : { session_id: sessionId, user_id: userId, message: text }),
    });

    if (!resp.ok) {
      failed = true; record.error = true;
      // 参数不合法走的是普通 JSON 错误(422 + detail),不是 SSE —— 这层得单独接住。
      let detail = resp.status + ' ' + resp.statusText;
      try {
        const body = await resp.json();
        if (body.detail) detail = JSON.stringify(body.detail);
      } catch (e) { /* 不是 JSON 就退回状态码 */ }
      reply.text.classList.remove('streaming');
      reply.bubble.classList.add('err');
      reply.text.textContent = '[请求被拒] ' + detail;
      return;
    }

    // 必须 fetch + getReader(),不能用 EventSource —— EventSource 只发 GET,这接口是 POST。
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let carry = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      carry += decoder.decode(value, { stream: true });

      let m;
      while ((m = SEP.exec(carry)) !== null) {
        const block = carry.slice(0, m.index);
        carry = carry.slice(m.index + m[0].length);
        if (block.trim()) handle(parseBlock(block));
      }
    }

    carry += decoder.decode();
    if (carry.trim()) handle(parseBlock(carry));   // 最后一块可能没有结尾空行
  } catch (err) {
    failed = true; record.error = true; record.complete = false;
    reply.bubble.querySelector('.feedback')?.remove();
    // 响应头都没拿到(服务没起、断网),或读流中途断开。这一轮没写进历史,重发即可。
    reply.text.classList.remove('streaming');
    reply.bubble.classList.add('err');
    reply.text.textContent += (gotAnything ? '\n\n' : '') + '[连接中断] ' + err.message;
  } finally {
    reply.text.classList.remove('streaming');
    // 判空只看正文 —— 徽章不算答复。工具调了但一个字没吐，仍然是"空回复"。
    if (!reply.text.textContent) reply.text.textContent = '[空回复]';
    if (!completed && !waiting && !failed && gotAnything) {
      record.error = true;
      reply.bubble.classList.add('err');
      reply.text.append(document.createTextNode('\n\n[连接中断] 回复未完成，请重试。'));
    }
    if (record.error || !completed) record.text = reply.text.textContent;
    saveHistory();
  }
}

// 唯一的入口。表单、快捷问题、转人工都走这里 —— 所以"正在发送"的互斥也只需要在这里做。
// (之前的版本靠 `input.disabled` 顺带挡住重复提交,那是偶然的;快捷按钮一加就不成立了。)
let busy = false;
const allButtons = () => [sendBtn, humanBtn, ...chipsEl.querySelectorAll('button')];
function setChatBusy(value) {
  input.disabled = value;
  allButtons().forEach(button => { button.disabled = value; });
}

async function submit(text) {
  if (busy || !text) return;
  busy = true;
  setChatBusy(true);
  try {
    await send(text);
  } finally {
    busy = false;
    setChatBusy(false);
    input.focus();
  }
}

document.getElementById('f').addEventListener('submit', (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  submit(text);
});

chipsEl.addEventListener('click', (e) => {
  const q = e.target.dataset && e.target.dataset.q;
  if (q) submit(q);
});

humanBtn.addEventListener('click', () => simulateHandoff(null, humanBtn));

// 兼容旧历史缺少 sources / answer_id / feedback 字段。
try {
  const stored = JSON.parse(localStorage.getItem(HISTORY_KEY) || 'null');
  const messages = Array.isArray(stored) ? stored : stored?.messages;
  if (Array.isArray(messages) && (Array.isArray(stored) || stored.session_id === sessionId)) {
    history = messages.filter(record => record && typeof record.text === 'string'
      && ['me', 'ai'].includes(record.who)).map(record => ({...record,
        sources: normalizeSources(record.sources), answer_id: record.answer_id || freshAnswerId(),
        complete: record.complete === undefined ? !record.error : record.complete,
        feedback: ['up', 'down'].includes(record.feedback?.choice) ? record.feedback : null}));
    for (const record of history) {
      const reply = bubble(record.who, record.text);
      recordReplies.set(record, reply);
      if (record.who === 'ai') {
        renderAnswer(reply.text, record.text, record.sources);
        if (record.error) reply.bubble.classList.add('err');
        attachFeedback(reply, record);
        attachActions(reply, record);
        attachRefund(reply, record);
        if (record.waiting) businessNode(reply.bubble, 'div', '正在核对订单卡片…', 'business-status pending-check');
      }
    }
  }
} catch (e) { /* 损坏/不可用的存储不影响当前页面。 */ }
restorePending();
