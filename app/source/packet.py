from hashlib import sha256
import json

from app.problems import DocketError
from app.setup import SAMPLES
from app.source.csvscan import scan
from app.source.structure import SCHEMAS,Survey

NAMES=('survey',*SCHEMAS)
PARENT_KEY='parent'


def bundle(name):
    if name not in {'dirty','clean'}:
        raise DocketError('SAMPLE_BAD','找不到选定的清算样例',404)
    return {key:(SAMPLES/name/(key+('.json' if key=='survey' else '.csv'))).read_bytes().decode()
            for key in NAMES}


def _parent(payload):
    if PARENT_KEY not in payload:
        return None
    value=payload[PARENT_KEY]
    if not isinstance(value,str) or not value.strip() or len(value)>128:
        raise DocketError('PARENT_BAD','补扫重算需引用一个有效的原清算批次',422)
    return value.strip()


def decode(payload):
    if not isinstance(payload,dict):
        raise DocketError('BUNDLE_BAD','需清算窗口和三份 UTF-8 单据')
    parent=_parent(payload)
    rest={key:value for key,value in payload.items() if key!=PARENT_KEY}
    if set(rest)=={'sample'}:
        name=str(rest['sample'])
        return bundle(name),name,parent
    files=rest.get('bundle') if set(rest)=={'bundle'} else None
    if not isinstance(files,dict) or set(files)!=set(NAMES) or any(
        not isinstance(text,str) or len(text)>200_000 for text in files.values()
    ):
        raise DocketError('BUNDLE_BAD','需清算窗口和三份 UTF-8 单据')
    try:
        for text in files.values():
            text.encode()
    except UnicodeEncodeError:
        raise DocketError('BUNDLE_BAD','原件含错误字符') from None
    return files,'upload',parent


def load(files):
    try:
        obj=json.loads(files['survey'].removeprefix('\ufeff'))
    except (json.JSONDecodeError,RecursionError):
        raise DocketError('SURVEY_BAD','清算窗口 JSON 无效') from None
    return Survey.build(obj),{name:scan(files[name],name) for name in SCHEMAS}


def fingerprint(files):
    return {name:sha256(text.encode()).hexdigest() for name,text in sorted(files.items())}
