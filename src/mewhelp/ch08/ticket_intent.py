"""以真实 user 消息保守建立建单依据，模型文本不能加入依据。"""
import re
from copy import deepcopy

REQUEST = re.compile(r'(?:帮我|给我|替我|帮忙|麻烦|请|我要|我想).{0,8}(?:创建|建|开|提交|登记).{0,5}工单|^(?:创建|建|开|提交).{0,5}工单')
COMMAND = re.compile(r'(?:可以)?(?:帮我|给我|替我|帮忙|麻烦|请|我要|我想)?(?:创建|建|开|提交|登记).{0,5}工单(?:吗)?')
NEGATIVE = re.compile(r'(?:不要|不用|不想|不必|不需要|别).{0,10}(?:创建|建|开|提交).{0,5}工单|不建了|算了|取消.{0,6}(?:工单|建单)')
NON_REQUEST = re.compile(r'如果|假如|假设|例如|比如|客服说|他说|她说|是什么意思|这句话|要不要|是不是|是否')
PROBLEM = re.compile(r'坏|损|破|裂|漏|少了|缺|错|异常|噪|卡|黑屏|发热|没(?:有|法|反应|收到)|无法|不能|不(?:工作|通电|启动|响应|亮|制冷)|投诉|态度|欺骗|辱骂|咨询|询问|问题')
UNRELATED = re.compile(r'^(?:查|查询|看看|请查|帮我查|谢谢|你好|再见|天气|物流|订单)')


def ticket_request_patch(question: str, message_id: str, previous: dict | None) -> dict:
    text = question.strip()
    # Quoted requests are data. Keep raw user wording separately for problem grounding.
    unquoted = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"|‘[^’]*’', '', text)
    blank = {'explicit_request': False, 'utterances': [], 'message_ids': [], 'cancelled': False}
    if NEGATIVE.search(unquoted):
        return {**blank, 'cancelled': True}
    explicit = bool(REQUEST.search(unquoted)) and not NON_REQUEST.search(unquoted)
    if explicit:
        base = blank
    elif (previous or {}).get('explicit_request') and not UNRELATED.search(text) and PROBLEM.search(text):
        base = deepcopy(previous)
    else:
        return blank
    utterances = [*base.get('utterances', []), {'message_id': message_id, 'text': text}]
    return {'explicit_request': True, 'utterances': utterances,
            'message_ids': [u['message_id'] for u in utterances], 'cancelled': False}


def validate_ticket_draft(args: dict, evidence: dict) -> list[str]:
    errors = []
    if not evidence.get('explicit_request') or evidence.get('cancelled'):
        errors.append('用户尚未明确要求建工单，请先询问用户。')
    description = args.get('description')
    utterances = [u.get('text', '') for u in evidence.get('utterances', [])]
    if not isinstance(description, str) or not description.strip():
        errors.append('请追问实际问题描述。')
    elif not any(description.strip() in text for text in utterances):
        errors.append('问题描述必须取自相关用户原话，不得编造，请追问补齐。')
    elif not PROBLEM.search(COMMAND.sub('', description)):
        errors.append('创建工单的要求不能充当实际问题描述，请追问具体问题。')
    kind = args.get('ticket_type')
    source = ' '.join(utterances)
    supported = {'售后': r'售后|坏|损|破|裂|漏|缺|错|异常|无法|不能|不通电|没反应|没收到|卡|黑屏|发热',
                 '投诉': r'投诉|态度|欺骗|辱骂', '咨询': r'咨询|询问'}
    if kind not in supported or not re.search(supported.get(kind, r'(?!)'), source):
        errors.append('工单类型缺少用户问题依据，请追问工单类型。')
    return errors
