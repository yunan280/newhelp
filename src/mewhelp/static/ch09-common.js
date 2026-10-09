function el(tag, text, className) { const node=document.createElement(tag); if(text!=null)node.textContent=String(text);if(className)node.className=className;return node; }
async function api(path, body) {
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response=await fetch('/api/ch09'+path,options);
  let value;try{value=await response.json()}catch(e){throw new Error('服务返回无法读取，请刷新后重试')}
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'请求未完成，请检查输入或稍后重试');
  return value;
}
function notice(message,error=false){const node=document.getElementById('notice');node.textContent=message;node.className='notice '+(error?'error':'success');}
function localTime(value){if(!value)return '—';return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z').toLocaleString('zh-CN',{hour12:false});}
function publication(status){return ({pending:'已核准，待发布',indexed:'已完成索引',published:'已发布'})[status]||'';}
