from copy import deepcopy

from app.billing.reclaim import reconcile
from app.source.packet import bundle

AT='2026-11-30T23:40+08:00'
EARLIER='2026-11-30T23:35+08:00'
LATE='2026-11-30T23:50+08:00'


def receipt_row(**over):
    row={'line':2,'berth':'B-01','vehicle':'V','arrival':AT,'cents':850,
         'fields':{'receipt':'0007'},'code':'ORPHAN'}
    return row | over


def parent_record(*,stays=None,line_receipts=None):
    return {'manifest':{'rule':'CURB-1.0'},
            'evidence':{'occupancy':{'stays':stays if stays is not None else [
                          {'stay':'P1','berth':'B-01','vehicle':'V','start':AT,'end':LATE,
                           'detections':[{'fields':{'event':'0001'}}]}]},
                        'settlement':{'lines':[{'stay':'P1','berth':'B-01','vehicle':'V','arrival':AT,
                                                'due_cents':700,'paid_cents':850,'difference_cents':150,
                                                'receipts':line_receipts if line_receipts is not None
                                                else [receipt_row()]}]}}}


def child_line():
    return {'stay':'C1','berth':'B-01','vehicle':'V','arrival':EARLIER,
            'due_cents':1200,'paid_cents':0,'difference_cents':-1200,'code':'NO_PAYMENT','receipts':[]}


def child_stay(**over):
    stay={'stay':'C1','berth':'B-01','vehicle':'V','start':EARLIER,'end':LATE,
          'detections':[{'fields':{'event':'0017'}},{'fields':{'event':'0001'}}]}
    return stay | over


def test_late_rescan_reclaims_receipt_to_extended_stay():
    lines=[child_line()]
    reclaimed,kept=reconcile(lines,[child_stay()],parent_record(),[receipt_row()])
    assert [r['receipt'] for r in reclaimed]==['0007'] and kept==[]
    assert (reclaimed[0]['basis'],reclaimed[0]['parent_stay'],reclaimed[0]['new_stay'])==(
        'PARENT_ATTACHMENT_AND_INTERVAL','P1','C1')
    assert (lines[0]['paid_cents'],lines[0]['difference_cents'],lines[0]['code'])==(850,-350,'GAP')
    assert lines[0]['receipts'][0]['code']=='RECLAIMED'


def test_reclaim_is_counted_once_when_processed_twice():
    lines=[child_line()]
    parent=parent_record()
    reconcile(lines,[child_stay()],parent,[receipt_row()])
    reconcile(lines,[child_stay()],parent,[])  # 第二次没有游离回执，不应再次计收
    assert lines[0]['paid_cents']==850


def test_nearby_stop_without_lineage_is_disputed_not_misattached():
    # 补扫把同一车同一泊位切成两截：前截含到达分钟但没有原检测编号，后截才有
    front=child_stay(stay='C1',end='2026-11-30T23:45+08:00',
                     detections=[{'fields':{'event':'0017'}}])
    back=child_stay(stay='C2',start='2026-11-30T23:45+08:00',
                    detections=[{'fields':{'event':'0001'}}])
    lines=[child_line(),{'stay':'C2','berth':'B-01','vehicle':'V','arrival':'2026-11-30T23:45+08:00',
                         'due_cents':0,'paid_cents':0,'difference_cents':0,'code':'MATCH','receipts':[]}]
    reclaimed,kept=reconcile(lines,[front,back],parent_record(),[receipt_row()])
    assert reclaimed==[] and [row['reason'] for row in kept]==['LINEAGE_BROKEN']
    assert all(line['paid_cents']==0 for line in lines)


def test_same_receipt_on_two_parent_lines_is_ambiguous():
    parent=parent_record()
    duplicate=deepcopy(parent['evidence']['settlement']['lines'][0])
    duplicate['stay']='P2'
    parent['evidence']['settlement']['lines'].append(duplicate)
    reclaimed,kept=reconcile([child_line()],[child_stay()],parent,[receipt_row()])
    assert reclaimed==[] and kept[0]['reason']=='AMBIGUOUS'


def test_changed_original_event_marks_disputed():
    lines=[child_line()]
    stay=child_stay(detections=[{'fields':{'event':'0017'}}])
    reclaimed,kept=reconcile(lines,[stay],parent_record(),[receipt_row()])
    assert reclaimed==[] and kept[0]['reason']=='LINEAGE_BROKEN'


def test_missing_parent_stay_detail_is_not_guessed():
    lines=[child_line()]
    reclaimed,kept=reconcile(lines,[child_stay()],parent_record(stays=[]),[receipt_row()])
    assert reclaimed==[] and kept[0]['reason']=='LINEAGE_BROKEN'


def test_receipt_changed_from_parent_is_disputed():
    lines=[child_line()]
    for field,value,reason in (
        ('cents',900,'AMOUNT_CHANGED'),
        ('arrival','2026-11-30T23:41+08:00','ARRIVAL_CHANGED'),
        ('berth','B-02','SOURCE_CHANGED'),
        ('vehicle','W','SOURCE_CHANGED'),
    ):
        reclaimed,kept=reconcile([deepcopy(child_line())],[child_stay()],parent_record(),
                                 [receipt_row(**{field:value})])
        assert reclaimed==[] and kept[0]['reason']==reason,field


def test_no_parent_attachment_stays_disputed():
    lines=[child_line()]
    reclaimed,kept=reconcile(lines,[child_stay()],parent_record(line_receipts=[]),[receipt_row()])
    assert reclaimed==[] and kept[0]['reason']=='NO_PARENT_ATTRIBUTION'


def rescan_payload(key):
    files=bundle('dirty')
    late='0017,B-01,粤B01234,north-loop,2026-11-30T23:35+08:00,2026-11-30T23:40+08:00\n'
    return {'bundle':files | {'sensors':files['sensors']+late},'parent':key}


def test_archived_rescan_links_parent_and_splits_reclaimed_from_disputed(client):
    base=client.post('/api/dockets',json={'sample':'dirty'}).json()
    child=client.post('/api/dockets',json=rescan_payload(base['key'])).json()
    assert child['reused'] is False and child['key']!=base['key']
    assert child['manifest']['parent']==base['key']
    [entry]=child['manifest']['reclaimed']
    assert (entry['receipt'],entry['cents'])==('0007',850)
    assert entry['parent_stay']!=entry['new_stay']
    assert child['manifest']['disputed']==[{'receipt':'0011','reason':'NO_PARENT_ATTRIBUTION'}]
    assert child['money']=={'due_cents':2740,'paid_cents':2310,'difference_cents':-430,
                            'orphan_cents':140,'reclaimed_cents':850}


def test_same_rescan_reuses_one_docket_and_never_double_counts(client):
    base=client.post('/api/dockets',json={'sample':'dirty'}).json()
    first=client.post('/api/dockets',json=rescan_payload(base['key'])).json()
    second=client.post('/api/dockets',json=rescan_payload(base['key'])).json()
    assert (first['reused'],second['reused'],first['key']==second['key'])==(False,True,True)
    assert first['money']==second['money']
    assert len(client.get('/api/dockets').json()['dockets'])==2


def test_child_and_signed_parent_evidence_both_verify(client):
    base=client.post('/api/dockets',json={'sample':'dirty'}).json()
    child=client.post('/api/dockets',json=rescan_payload(base['key'])).json()
    for key in (base['key'],child['key']):
        exported=client.get('/api/export/'+key)
        assert exported.status_code==200
        doc=exported.json()
        assert doc['minutes_balance']==0 and doc['sha256']
    lines=client.get('/api/dockets/'+child['key']).json()['evidence']['settlement']['lines']
    b01=next(line for line in lines if line['stay']==child['manifest']['reclaimed'][0]['new_stay'])
    assert [row['fields']['receipt'] for row in b01['receipts']]==['0007']
    assert b01['paid_cents']==850


def test_parent_must_exist_and_be_text(client):
    missing=client.post('/api/dockets',json={'sample':'dirty','parent':'nope'})
    assert (missing.status_code,missing.json()['code'])==(404,'PARENT_BAD')
    wrong=client.post('/api/dockets',json={'sample':'dirty','parent':7})
    assert (wrong.status_code,wrong.json()['code'])==(422,'PARENT_BAD')
    preview=client.get('/api/cases/dirty/settlement',params={'parent':'nope'})
    assert (preview.status_code,preview.json()['code'])==(404,'PARENT_BAD')


def test_pages_show_attribution_basis(client):
    base=client.post('/api/dockets',json={'sample':'dirty'}).json()
    child=client.post('/api/dockets',json=rescan_payload(base['key'])).json()
    settlement=client.get('/clearing/settlement',params={'key':child['key']})
    assert settlement.status_code==200 and '补扫认回' in settlement.text and '0007' in settlement.text
    assert '仍有争议的款' in settlement.text and '原批次归属且区间连续' in settlement.text
    appeal=client.get('/clearing/appeal',params={'key':child['key']})
    assert f'/clearing/appeal?key={base["key"]}' in appeal.text
    plain=client.get('/clearing/settlement',params={'sample':'dirty'})
    assert '游离实收' in plain.text and '仍有争议的款' not in plain.text
    preview=client.get('/api/cases/dirty/settlement',params={'parent':base['key']})
    assert preview.status_code==200
    # 未补扫的原样样例没有游离回执，因此没有可认回的款
    assert preview.json()['reclaimed']==[]
    assert preview.json()['parent']==base['key']
