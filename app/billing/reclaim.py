"""把补扫重算后变成游离的回执认回原停靠。

认回只依据原清算批次留下的归属证据，不放宽到达分钟：

1. 回执必须曾挂在原批次的一笔逐单上（原批次自己认定的归属）；
2. 新停靠必须覆盖该回执的到达分钟，且仍是同一笔停靠——原停靠的检测编号
   全部还在新停靠里，起点不晚于到达；
3. 同车同泊位只能有一笔新停靠满足以上条件。

任一条不成立就保留为仍有争议的款，避免同车同泊位两次相邻停靠被挂错。
回执本身还要与原批次一致：泊位、车辆、金额都不能变。
"""

from collections import defaultdict
from datetime import datetime

from app.curb.timeaxis import minute


def _at(value):
    if isinstance(value,datetime):
        return value
    try:
        return minute(value)
    except (TypeError,ValueError):
        return None


def _iso(value):
    return value.isoformat() if isinstance(value,datetime) else value


def _receipt(row):
    fields=row.get('fields') if isinstance(row,dict) else None
    return fields.get('receipt') if isinstance(fields,dict) else None


def _events(stay):
    events=set()
    for row in stay.get('detections') or []:
        fields=row.get('fields') if isinstance(row,dict) else None
        if isinstance(fields,dict) and isinstance(fields.get('event'),str):
            events.add(fields['event'])
    return events


def _occupied(stay,when):
    start,end=_at(stay.get('start')),_at(stay.get('end'))
    return start is not None and end is not None and start<=when<end


def _lineage(parent_stay,parent_arrival,stay):
    """新停靠是否是原停靠向前延伸后的同一笔。

    连续性凭据是原停靠的检测编号仍在新停靠里；缺少原停靠明细时不认回。
    """
    if parent_stay is None or parent_arrival is None:
        return False
    start=_at(stay.get('start'))
    if start is None or start>parent_arrival or not _occupied(stay,parent_arrival):
        return False
    original=_events(parent_stay)
    return bool(original) and original<=_events(stay)


def _parent_stays(parent):
    evidence=parent.get('evidence') or {}
    table={}
    for stay in (evidence.get('occupancy') or {}).get('stays') or []:
        if isinstance(stay,dict) and stay.get('stay'):
            table[stay['stay']]=stay
    return table


def _owners(parent):
    account=(parent.get('evidence') or {}).get('settlement') or {}
    owners=defaultdict(list)
    for line in account.get('lines') or []:
        for row in line.get('receipts') or []:
            receipt=_receipt(row)
            if receipt is not None:
                owners[receipt].append((line,row))
    return owners


def reconcile(lines,stays,parent,orphans):
    """返回 (认回明细, 仍有争议明细)。

    ``lines`` 会被就地更新：认回的回执并入对应逐单的实收，只计一次。
    """
    owners=_owners(parent)
    parent_stays=_parent_stays(parent)
    line_by_stay={line.get('stay'):line for line in lines}
    by_slot=defaultdict(list)
    for stay in stays:
        by_slot[stay.get('berth'),stay.get('vehicle')].append(stay)

    reclaimed,kept=[],[]
    claimed={_receipt(row) for line in lines for row in line.get('receipts') or []}
    for row in orphans:
        receipt=_receipt(row)
        arrival=_at(row.get('arrival'))
        candidates=owners.get(receipt) or []
        parent_line=parent_receipt=None
        reason=None
        if not candidates:
            reason='NO_PARENT_ATTRIBUTION'
        elif len(candidates)>1:
            reason='AMBIGUOUS'
        else:
            parent_line,parent_receipt=candidates[0]
            parent_arrival=_at(parent_receipt.get('arrival'))
            if receipt in claimed:
                reason='AMBIGUOUS'
            elif parent_arrival is None or arrival is None:
                reason='OUTSIDE_STAY'
            elif arrival!=parent_arrival:
                reason='ARRIVAL_CHANGED'
            elif (row.get('berth'),row.get('vehicle'))!=(parent_line.get('berth'),parent_line.get('vehicle')):
                reason='SOURCE_CHANGED'
            elif row.get('cents')!=parent_receipt.get('cents'):
                reason='AMOUNT_CHANGED'
            else:
                slot=by_slot.get((parent_line.get('berth'),parent_line.get('vehicle')),[])
                touching=[stay for stay in slot if _occupied(stay,arrival)]
                usable=[stay for stay in touching
                        if _lineage(parent_stays.get(parent_line.get('stay')),parent_arrival,stay)]
                if len(touching)>1 or len(usable)>1:
                    reason='AMBIGUOUS'
                elif not usable:
                    reason='LINEAGE_BROKEN' if touching else 'OUTSIDE_STAY'
                else:
                    stay=usable[0]
                    line=line_by_stay.get(stay.get('stay'))
                    if line is None:
                        reason='LINEAGE_BROKEN'
                    else:
                        claimed.add(receipt)
                        line.setdefault('receipts',[]).append(row | {'code':'RECLAIMED'})
                        line['paid_cents']=line.get('paid_cents',0)+row.get('cents',0)
                        line['difference_cents']=line['paid_cents']-line['due_cents']
                        line['code']='MATCH' if line['difference_cents']==0 else 'GAP'
                        reclaimed.append({
                            'receipt':receipt,'line':row.get('line'),'cents':row.get('cents'),
                            'berth':line.get('berth'),'vehicle':line.get('vehicle'),
                            'receipt_arrival':_iso(row.get('arrival')),'status':'RECLAIMED',
                            'basis':'PARENT_ATTACHMENT_AND_INTERVAL',
                            'parent_stay':parent_line.get('stay'),
                            'parent_arrival':_iso(parent_line.get('arrival')),
                            'new_stay':line.get('stay'),'new_arrival':_iso(line.get('arrival')),
                            'new_end':_iso(stay.get('end')),
                            'detections':sorted(_events(stay)),
                        })
                        continue
        entry=row | {'code':'ORPHAN','reason':reason,'status':'DISPUTED'}
        if parent_line is not None:
            entry=entry | {'parent_stay':parent_line.get('stay'),
                           'parent_arrival':_iso(parent_line.get('arrival'))}
        kept.append(entry)
    return reclaimed,kept
