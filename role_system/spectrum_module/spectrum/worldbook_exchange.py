"""解析整本世界书文件，保留未执行的扩展字段，并显式报告位置转换。"""
import json
from . import worldbook as wb

TEXT_FIELDS = {'title','text','keys','secondary','group_name','selective_logic','position','extensions','generation_triggers'}
BOOLEAN_FIELDS = {'enabled','constant','selective','case_sensitive','match_whole_words','use_regex','use_group_scoring',
                  'prioritize','vectorized','ignore_budget','exclude_recursion','prevent_recursion','match_persona'}


def validate_entry(row, columns):
    if not isinstance(row,dict) or set(row)-set(columns):
        raise ValueError('世界书条目包含未知字段')
    if not isinstance(row.get('text',''),str) or not row.get('text','').strip():
        raise ValueError('世界书条目正文不能为空')
    for key,value in row.items():
        if key in TEXT_FIELDS:
            if not isinstance(value,str): raise ValueError('字段必须为文字：'+key)
        elif key=='group_weight':
            import math
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError('组内权重必须为非负数')
        elif not isinstance(value,(int,bool)):
            raise ValueError('字段必须为整数：'+key)
        if key in BOOLEAN_FIELDS and value not in (0,1): raise ValueError('开关值必须为0或1：'+key)
        if key in ('sticky','cooldown','delay','delay_until_recursion','book_id') and value<0:
            raise ValueError('字段不能为负数：'+key)
        if key=='probability' and not 0<=value<=100: raise ValueError('触发概率范围为0–100')
        if key=='scan_depth' and not -1<=value<=120: raise ValueError('扫描消息数范围为-1–120')
        if key=='delay_until_recursion' and not 0<=value<=9: raise ValueError('最早递归层级范围为0–9')
    if row.get('position','l3') not in wb.POSITIONS: raise ValueError('未知的注入位置')
    if row.get('selective_logic','and_any') not in wb.SELECTIVE_LOGICS: raise ValueError('未知的次关键词逻辑')
    if 'extensions' in row and not isinstance(json.loads(row['extensions']),dict): raise ValueError('扩展字段必须为JSON对象')
    if set(wb.split_terms(row.get('generation_triggers','')))-{'normal','background'}: raise ValueError('未知的调用场景')
    return row


def keys(value):
    return json.dumps(value,ensure_ascii=False) if isinstance(value,list) else str(value or '')


def read_book(document, columns):
    if not isinstance(document,dict): raise ValueError('世界书文件必须为JSON对象')
    warnings=[]
    if document.get('format')=='spectrum-worldbook-v1':
        rows=document.get('entries')
        if not isinstance(rows,list): raise ValueError('世界书缺少条目数组')
        rows=[validate_entry(row,columns) for row in rows]
    elif isinstance(document.get('entries'),(dict,list)):
        entries=document['entries']
        rows=[]
        for source in (entries.values() if isinstance(entries,dict) else entries):
            if not isinstance(source,dict): raise ValueError('条目必须为对象')
            extension=source.get('extensions',{})
            if not isinstance(extension,dict): raise ValueError('通用条目扩展字段必须为对象')
            source={**extension,**source}
            if not isinstance(source.get('content',''),str): raise ValueError('条目正文必须为文字')
            if source.get('triggers') is not None and not isinstance(source['triggers'],list): raise ValueError('调用场景必须为数组')
            position=source.get('spectrumPosition')
            if position not in wb.POSITIONS:
                external=source.get('position',1)
                position='core' if external in (0,'before_char') else 'turn' if external in (4,2,3,5,6,7,'in_chat') else 'l3'
                if external not in (0,1,'before_char','after_char'):
                    warnings.append('条目“'+str(source.get('comment',''))+'”的深度、示例、作者注或出口位置转换为本轮补充；历史和系统前缀不被插入或改写。')
            row={'title':str(source.get('comment',source.get('name',''))), 'text':str(source.get('content','')),
                 'keys':keys(source.get('key',source.get('keys',[]))), 'secondary':keys(source.get('keysecondary',source.get('secondary_keys',[]))),
                 'constant':int(bool(source.get('constant',False))), 'selective':int(bool(source.get('selective',False))),
                 'use_regex':int(bool(source.get('spectrumUseRegex',False))),
                 'selective_logic':wb.normalize_logic(source.get('selectiveLogic',0)),
                 'order_value':int(source.get('order',source.get('insertion_order',100))),
                 'probability':int(source.get('probability',100)) if source.get('useProbability',True) else 100,
                 'enabled':int(not source.get('disable',not source.get('enabled',True))), 'position':position,
                 'group_name':str(source.get('group','')), 'group_weight':float(source.get('groupWeight',100)),
                 'generation_triggers':','.join('background' if t=='quiet' else 'normal' for t in (source.get('triggers') or []) if t in ('normal','quiet')),
                 'extensions':json.dumps(source,ensure_ascii=False)}
            for ours,theirs in [('case_sensitive','caseSensitive'),('match_whole_words','matchWholeWords'),
                                ('use_group_scoring','useGroupScoring'),('prioritize','groupOverride'),('vectorized','vectorized'),
                                ('ignore_budget','ignoreBudget'),('exclude_recursion','excludeRecursion'),
                                ('prevent_recursion','preventRecursion'),('match_persona','matchPersonaDescription')]:
                if source.get(theirs) is not None: row[ours]=int(bool(source[theirs]))
            for ours,theirs in [('sticky','sticky'),('cooldown','cooldown'),('delay','delay'),('delay_until_recursion','delayUntilRecursion')]:
                row[ours]=int(source.get(theirs,0))
            if source.get('scanDepth') is not None:
                row['scan_depth']=int(source['scanDepth']) or -1
            if source.get('automationId') or source.get('characterFilter') or source.get('outletName'):
                warnings.append('条目“'+row['title']+'”的外部脚本、角色过滤或出口字段已保留，但不会在当前系统自动执行。')
            rows.append(validate_entry(row,columns))
    else:
        raise ValueError('无法识别世界书文件；请使用整本世界书JSON')
    return {'name':str(document.get('name') or '导入的世界书'), 'description':str(document.get('description') or ''), 'entries':rows},list(dict.fromkeys(warnings))


def external_export(book):
    entries={}
    for uid,row in enumerate(book['entries']):
        entry=json.loads(row.get('extensions') or '{}')
        entry.update(uid=uid,comment=row.get('title',''),content=row['text'],key=wb.split_terms(row.get('keys','')),
                     keysecondary=wb.split_terms(row.get('secondary','')),constant=bool(row.get('constant')),
                     selective=bool(row.get('selective')),selectiveLogic=wb.SELECTIVE_LOGICS.index(row.get('selective_logic','and_any')),
                     order=row.get('order_value',100),disable=not bool(row.get('enabled',1)),probability=row.get('probability',100),
                     useProbability=True,spectrumPosition=row.get('position','l3'),group=row.get('group_name',''),groupWeight=row.get('group_weight',100))
        if row.get('use_regex'):
            entry['key']=[term if term.startswith('/') else '/'+term.replace('/','\\/')+'/'+('' if row.get('case_sensitive') else 'i') for term in entry['key']]
            entry['keysecondary']=[term if term.startswith('/') else '/'+term.replace('/','\\/')+'/'+('' if row.get('case_sensitive') else 'i') for term in entry['keysecondary']]
        entry['spectrumUseRegex']=bool(row.get('use_regex'))
        entry.setdefault('position',0 if row.get('position')=='core' else 1)
        for ours,theirs in [('case_sensitive','caseSensitive'),('match_whole_words','matchWholeWords'),('use_group_scoring','useGroupScoring'),
                            ('prioritize','groupOverride'),('vectorized','vectorized'),('ignore_budget','ignoreBudget'),
                            ('exclude_recursion','excludeRecursion'),('prevent_recursion','preventRecursion'),('match_persona','matchPersonaDescription'),
                            ('sticky','sticky'),('cooldown','cooldown'),('delay','delay'),('delay_until_recursion','delayUntilRecursion')]:
            entry[theirs]=row.get(ours,0)
        depth=row.get('scan_depth',0)
        entry['scanDepth']=None if depth==0 else max(0,depth)
        entry['triggers']=['quiet' if t=='background' else t for t in wb.split_terms(row.get('generation_triggers',''))]
        entries[str(uid)]=entry
    return {'name':book['name'],'description':book['description'],'entries':entries}


def validate_library(document, columns):
    if isinstance(document,list):
        read_book({'format':'spectrum-worldbook-v1','entries':document},columns)
        return document
    if not isinstance(document,dict) or document.get('format')!='spectrum-worldbook-library-v1' or not isinstance(document.get('books'),list):
        raise ValueError('书库文件格式不正确')
    ids=[]
    for book in document['books']:
        if not isinstance(book,dict): raise ValueError('书库中的每本书必须为对象')
        if not isinstance(book.get('source_id'),int) or book['source_id'] in ids: raise ValueError('书库标识重复或格式错误')
        ids.append(book['source_id']);read_book(book,columns)
    choice=document.get('selection',{})
    if not isinstance(choice,dict) or choice.get('strategy','order') not in ('order','global_first','current_first'):
        raise ValueError('书库选择格式不正确')
    if any(choice.get(key) is not None and choice[key] not in ids for key in ('global_book','current_book')):
        raise ValueError('所选世界书不在导入书库中')
    return document
