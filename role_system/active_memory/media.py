"""将已获发送回执的音乐媒体记为事实，不保存签名载荷。"""
import json


def descriptions(chain):
    out=[]
    for part in chain.chain:
        if getattr(part,'type',None)=='record' and getattr(part,'metadata',{}).get('speech_text'):
            out.append('我已发送语音，使用台词：'+part.metadata['speech_text'])
        elif getattr(part,'type',None)=='music':
            data=part.data
            label=data.get('title') or f"{data.get('type','')} 音乐编号 {data.get('id','')}"
            out.append('我已发送一张音乐卡片：'+str(label))
        elif getattr(part,'type',None)=='json':
            payload=part.data.get('data')
            if isinstance(payload,str):
                try:payload=json.loads(payload)
                except ValueError:continue
            if not isinstance(payload,dict) or payload.get('app')!='com.tencent.music.lua':continue
            music=payload.get('meta',{}).get('music',{})
            out.append('我已发送一张音乐卡片：'+str(music.get('title') or '歌曲')+
                       ('，'+str(music['desc']) if music.get('desc') else ''))
    return out
