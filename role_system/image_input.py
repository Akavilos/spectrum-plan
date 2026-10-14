"""Read received image bytes before a model request; never interpret file IDs as local paths."""
import asyncio
import base64
import binascii
import urllib.request
from .ports import logger,TextPart

MAX_BYTES=10*1024*1024


def read_image(source):
    if source.startswith(('data:','base64://')) and len(source)>MAX_BYTES*4//3+128:
        raise ValueError('图片超过 10 MB')
    if source.startswith('data:'):
        header,payload=source.split(',',1)
        if not header.startswith('data:image/') or not header.endswith(';base64'):
            raise ValueError('图片数据格式不受支持')
        data=base64.b64decode(payload,validate=True)
    elif source.startswith('base64://'):
        data=base64.b64decode(source[9:],validate=True)
    elif source.startswith(('https://','http://')):
        request=urllib.request.Request(source,headers={'User-Agent':'Spectrum-ImageReader/1.0'})
        with urllib.request.urlopen(request,timeout=20) as response:
            data=response.read(MAX_BYTES+1)
    else:
        raise ValueError('图片没有可读取的数据或下载地址')
    if len(data)>MAX_BYTES:raise ValueError('图片超过 10 MB')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif data.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif data.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':mime='image/webp'
    else:raise ValueError('下载结果不是支持的图片文件')
    return mime,base64.b64encode(data).decode('ascii')


async def receive_images(event,req):
    images=[part for part in event.parts if getattr(part,'type',None)=='image']
    req.media=[]
    failures=[]
    for index,part in enumerate(images,1):
        try:
            source=part.data.get('url') or part.data.get('file') or ''
            if not source.startswith(('http://','https://','data:','base64://')):
                platform=event.host.platform_for(event.unified_msg_origin)
                if platform is None:raise ValueError('图片所在的聊天平台未连接')
                info=await platform.image_info(source)
                source=info.get('url') or ''
            mime,data=await asyncio.to_thread(read_image,source)
            req.media.append({'type':'image_url','image_url':{'url':f'data:{mime};base64,{data}'}})
            logger.info('[图片读取成功] %s | 第 %s 张 | %s | %s 字节',
                        event.unified_msg_origin,index,mime,len(data)*3//4)
        except (OSError,ValueError,binascii.Error) as error:
            # Reading an attachment is not a model failure and does not freeze the system.
            reason=f'HTTP {error.code}' if hasattr(error,'code') else str(error).split('http',1)[0][:160]
            failures.append(str(index))
            logger.warning('[图片读取失败] %s | 第 %s 张 | %s',event.unified_msg_origin,index,reason)
    if failures:
        req.extra_user_content_parts.append(TextPart(text='【我本轮收到的图片】\n第 '+
            '、'.join(failures)+' 张图片文件读取失败，我只收到图片占位标记，没有看到这些图片的实际内容。').mark_as_temp())
    if images:
        logger.info('[图片传递] %s | 收到 %s 张 | 本轮模型请求携带 %s 张图片数据',
                    event.unified_msg_origin,len(images),len(req.media))
