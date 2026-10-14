"""将自有消息与工具转换成公开模型协议，保留工具往返的原生内容。"""
import base64
import copy
import json
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode
from .model_http import ModelError


def endpoint(base, protocol, model='', *, listing=False, stream=False, api_version='2024-10-21'):
    parts = urlsplit(base.rstrip('/'))
    path = parts.path.rstrip('/')
    query = dict(parse_qsl(parts.query))
    suffixes = ('/chat/completions', '/responses', '/messages', '/embeddings', '/models', '/api/chat', '/api/embed', '/api/tags')
    for suffix in suffixes:
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if protocol == 'gemini':
        if '/models/' in path:
            path = path.split('/models/', 1)[0]
        if not path:
            path = '/v1beta'
        path += '/models' if listing else '/models/' + quote(model.removeprefix('models/'), safe='') + (':streamGenerateContent' if stream else ':generateContent')
        if stream:
            query['alt'] = 'sse'
    elif protocol == 'anthropic':
        if not path:
            path = '/v1'
        path += '/models' if listing else '/messages'
    elif protocol == 'ollama':
        path = path.removesuffix('/api')
        path += '/api/tags' if listing else '/api/chat'
    elif protocol == 'azure':
        query['api-version'] = api_version
        if listing:
            path = '/openai/models'
        else:
            if '/deployments/' in path:
                path = path.split('/deployments/', 1)[0]
            if not path.endswith('/openai'):
                path += '/openai'
            path += '/deployments/' + quote(model, safe='') + '/chat/completions'
    else:
        path += '/models' if listing else '/responses' if protocol == 'responses' else '/chat/completions'
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ''))


def image(url):
    from .image_input import read_image
    return read_image(url)


def content_parts(content, protocol):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if protocol == 'anthropic' else [{'text': content}]
    out = []
    for part in content or []:
        if part['type'] == 'text':
            out.append({'type': 'text', 'text': part['text']} if protocol == 'anthropic' else {'text': part['text']})
        elif part['type'] == 'image_url':
            mime, data = image(part['image_url']['url'])
            out.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}}
                       if protocol == 'anthropic' else {'inlineData': {'mimeType': mime, 'data': data}})
    return out


def encode(protocol, canonical, parameters, config):
    model = canonical['model']
    messages = canonical['messages']
    tools = canonical.get('tools', [])
    if protocol in ('openai', 'azure'):
        body = {**parameters, **canonical}
        if 'max_completion_tokens' in parameters and 'max_tokens' in body:
            body['max_completion_tokens'] = body.pop('max_tokens')
        return body
    if protocol == 'responses':
        inputs, system = [], []
        for message in messages:
            role = message['role']
            if role == 'system':
                system.append(message['content'])
            elif role == 'tool':
                inputs.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': str(message['content'])})
            elif message.get('_native_protocol') == 'responses':
                inputs.extend(message['_native_content'])
            else:
                content = message.get('content')
                if isinstance(content, list):
                    content = [{'type': 'input_text', 'text': part['text']} if part['type'] == 'text'
                               else {'type': 'input_image', 'image_url': part['image_url']['url']} for part in content]
                if content:
                    inputs.append({'role': role, 'content': content})
                for call in message.get('tool_calls') or []:
                    inputs.append({'type': 'function_call', 'call_id': call['id'], **call['function']})
        body = {**parameters, 'model': model, 'input': inputs}
        if system:
            body['instructions'] = '\n\n'.join(system)
        if 'temperature' in canonical:
            body['temperature'] = canonical['temperature']
        if 'max_tokens' in canonical:
            body['max_output_tokens'] = canonical['max_tokens']
        if tools:
            body['tools'] = [{'type': 'function', **item['function']} for item in tools]
        if canonical.get('tool_choice'):
            body['tool_choice'] = canonical['tool_choice']
        return body
    if protocol == 'ollama':
        converted, names = [], {}
        for message in messages:
            item = {'role': message['role'], 'content': message.get('content') or ''}
            if isinstance(item['content'], list):
                item['images'] = [image(p['image_url']['url'])[1] for p in item['content'] if p['type'] == 'image_url']
                item['content'] = '\n'.join(p['text'] for p in item['content'] if p['type'] == 'text')
            if message.get('tool_calls'):
                item['tool_calls'] = [{'function': {'name': c['function']['name'], 'arguments': json.loads(c['function']['arguments'])}} for c in message['tool_calls']]
                names.update({c['id']: c['function']['name'] for c in message['tool_calls']})
            if message.get('tool_call_id'):
                item['tool_name'] = names[message['tool_call_id']]
            converted.append(item)
        body = {**parameters, 'model': model, 'messages': converted, 'stream': bool(parameters.get('stream', False))}
        options = dict(body.get('options') or {})
        if 'temperature' in canonical: options['temperature'] = canonical['temperature']
        if 'max_tokens' in canonical: options['num_predict'] = canonical['max_tokens']
        body['options'] = options
        if tools: body['tools'] = tools
        return body
    system, turns, names = [], [], {}
    for message in messages:
        role = message['role']
        if role == 'system':
            system.append(message['content'])
            continue
        if role == 'tool':
            name = names[message['tool_call_id']]
            parts = ([{'type': 'tool_result', 'tool_use_id': message['tool_call_id'], 'content': str(message['content'])}]
                     if protocol == 'anthropic' else [{'functionResponse': {'name': name, 'response': {'result': str(message['content'])}}}])
            role = 'user'
        elif message.get('_native_protocol') == protocol:
            parts = copy.deepcopy(message['_native_content'])
        else:
            parts = content_parts(message.get('content') or '', protocol)
            for call in message.get('tool_calls') or []:
                fn = call['function']
                parts.append({'type': 'tool_use', 'id': call['id'], 'name': fn['name'], 'input': json.loads(fn['arguments'])}
                             if protocol == 'anthropic' else {'functionCall': {'name': fn['name'], 'args': json.loads(fn['arguments'])}})
        for call in message.get('tool_calls') or []:
            names[call['id']] = call['function']['name']
        native_role = role if protocol == 'anthropic' else 'model' if role == 'assistant' else 'user'
        field = 'content' if protocol == 'anthropic' else 'parts'
        if turns and turns[-1]['role'] == native_role:
            turns[-1][field].extend(parts)
        else:
            turns.append({'role': native_role, field: parts})
    if protocol == 'anthropic':
        body = {**parameters, 'model': model, 'messages': turns,
                'max_tokens': canonical.get('max_tokens') or parameters.get('max_tokens') or config.get('max_tokens') or 4096}
        if system:
            body['system'] = [{'type': 'text', 'text': '\n\n'.join(system)}]
            if config.get('prompt_cache', True):
                body['system'][-1]['cache_control'] = {'type': 'ephemeral'}
        if 'temperature' in canonical: body['temperature'] = canonical['temperature']
        if tools: body['tools'] = [{'name': t['function']['name'], 'description': t['function']['description'], 'input_schema': t['function']['parameters']} for t in tools]
        if canonical.get('tool_choice') == 'none': body['tool_choice'] = {'type': 'none'}
        return body
    generation = dict(parameters.get('generationConfig') or {})
    if 'temperature' in canonical: generation['temperature'] = canonical['temperature']
    if 'max_tokens' in canonical: generation['maxOutputTokens'] = canonical['max_tokens']
    body = {k: v for k, v in parameters.items() if k not in ('generationConfig', 'stream')}
    body.update(contents=turns, generationConfig=generation)
    if system: body['systemInstruction'] = {'parts': [{'text': '\n\n'.join(system)}]}
    if tools:
        body['tools'] = list(parameters.get('tools') or []) + [{'functionDeclarations': [{'name': t['function']['name'], 'description': t['function']['description'],
                         'parametersJsonSchema': t['function']['parameters']} for t in tools]}]
    if canonical.get('tool_choice') == 'none': body['toolConfig'] = {'functionCallingConfig': {'mode': 'NONE'}}
    return body


def decode(protocol, result):
    if 'error' in result:
        error = result['error']
        raise ModelError(error.get('message', str(error)) if isinstance(error, dict) else str(error))
    if protocol in ('openai', 'azure'):
        choice = result['choices'][0]
        return choice['message'], choice.get('finish_reason', 'stop'), result.get('usage') or {}
    calls, text, reasoning = [], [], []
    if protocol == 'responses':
        for item in result.get('output', []):
            if item['type'] == 'message':
                text.extend(p['text'] for p in item.get('content', []) if p['type'] == 'output_text')
            elif item['type'] == 'function_call':
                calls.append({'id': item['call_id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}})
        message = {'role': 'assistant', 'content': ''.join(text), 'tool_calls': calls,
                   '_native_protocol': protocol, '_native_content': result.get('output', [])}
        usage = result.get('usage') or {}
        return message, 'tool_calls' if calls else result.get('status', 'completed'), {
            **usage, 'prompt_tokens': usage.get('input_tokens'), 'completion_tokens': usage.get('output_tokens'),
            'prompt_tokens_details': usage.get('input_tokens_details', {})}
    if protocol == 'ollama':
        native = result['message']
        for i, call in enumerate(native.get('tool_calls') or []):
            calls.append({'id': f'ollama-{i}', 'type': 'function', 'function': {'name': call['function']['name'], 'arguments': json.dumps(call['function']['arguments'], ensure_ascii=False)}})
        return {'role': 'assistant', 'content': native.get('content', ''), 'tool_calls': calls}, 'tool_calls' if calls else 'stop', {
            'prompt_tokens': result.get('prompt_eval_count'), 'completion_tokens': result.get('eval_count')}
    if protocol == 'anthropic':
        parts = result.get('content', [])
        for part in parts:
            if part['type'] == 'text': text.append(part['text'])
            elif part['type'] == 'thinking': reasoning.append(part.get('thinking', ''))
            elif part['type'] == 'tool_use':
                calls.append({'id': part['id'], 'type': 'function', 'function': {'name': part['name'], 'arguments': json.dumps(part['input'], ensure_ascii=False)}})
        usage = result.get('usage') or {}
        usage = {**usage, 'prompt_tokens': (usage.get('input_tokens', 0) + usage.get('cache_creation_input_tokens', 0) + usage.get('cache_read_input_tokens', 0)),
                 'completion_tokens': usage.get('output_tokens'), 'prompt_cache_hit_tokens': usage.get('cache_read_input_tokens')}
        finish = result.get('stop_reason', 'end_turn')
    else:
        if not result.get('candidates'):
            raise ModelError('Gemini 未返回候选回复：' + str(result.get('promptFeedback') or '请检查接口响应格式'))
        candidate = (result.get('candidates') or [{}])[0]
        parts = candidate.get('content', {}).get('parts', [])
        for i, part in enumerate(parts):
            if part.get('text'): (reasoning if part.get('thought') else text).append(part['text'])
            if 'functionCall' in part:
                fn = part['functionCall']
                calls.append({'id': fn.get('id', f'gemini-{i}'), 'type': 'function', 'function': {'name': fn['name'], 'arguments': json.dumps(fn.get('args', {}), ensure_ascii=False)}})
        native_usage = result.get('usageMetadata') or {}
        usage = {**native_usage, 'prompt_tokens': native_usage.get('promptTokenCount'),
                 'completion_tokens': native_usage.get('candidatesTokenCount'),
                 'prompt_cache_hit_tokens': native_usage.get('cachedContentTokenCount')}
        finish = candidate.get('finishReason', 'STOP')
    message = {'role': 'assistant', 'content': ''.join(text), 'reasoning_content': ''.join(reasoning),
               'tool_calls': calls, '_native_protocol': protocol, '_native_content': parts}
    return message, 'tool_calls' if calls else finish, usage


def read_stream(response, protocol):
    if protocol == 'responses':
        text = []
        for raw in response:
            line = raw.decode('utf-8').strip()
            if not line.startswith('data:'): continue
            event = json.loads(line[5:])
            if event.get('type') == 'response.completed': return event['response']
            if event.get('type') == 'response.output_text.delta': text.append(event['delta'])
            if event.get('type') in ('error', 'response.failed'): raise ModelError(event.get('message', '流式响应失败'))
        return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': ''.join(text)}]}]}
    text, reasoning, calls, parts, usage, finish = [], [], {}, {}, {}, 'stop'
    native, ollama_calls = {}, []
    for raw in response:
        line = raw.decode('utf-8').strip()
        if protocol == 'ollama':
            if not line: continue
            event = json.loads(line)
            text.append(event.get('message', {}).get('content', ''))
            reasoning.append(event.get('message', {}).get('thinking', ''))
            ollama_calls.extend(event.get('message', {}).get('tool_calls') or [])
            native = event
            continue
        if not line.startswith('data:'): continue
        if line[5:].strip() == '[DONE]': break
        event = json.loads(line[5:])
        if event.get('error'): raise ModelError(str(event['error']))
        if protocol == 'anthropic':
            kind = event.get('type')
            if kind == 'message_start': usage.update(event['message'].get('usage') or {})
            elif kind == 'content_block_start': parts[event['index']] = dict(event['content_block'])
            elif kind == 'content_block_delta':
                block, delta = parts[event['index']], event['delta']
                for key in ('text', 'thinking', 'signature'):
                    if key in delta: block[key] = block.get(key, '') + delta[key]
                if 'partial_json' in delta: block['_json'] = block.get('_json', '') + delta['partial_json']
            elif kind == 'message_delta':
                usage.update(event.get('usage') or {}); finish = event.get('delta', {}).get('stop_reason', finish)
        elif protocol == 'gemini':
            candidate = (event.get('candidates') or [{}])[0]
            for part in candidate.get('content', {}).get('parts', []): parts[len(parts)] = part
            usage.update(event.get('usageMetadata') or {})
            finish = candidate.get('finishReason', finish)
        else:
            usage.update(event.get('usage') or {})
            for choice in event.get('choices', []):
                delta = choice.get('delta') or {}
                text.append(delta.get('content') or ''); reasoning.append(delta.get('reasoning_content') or '')
                finish = choice.get('finish_reason') or finish
                for call in delta.get('tool_calls') or []:
                    target = calls.setdefault(call['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    if call.get('id'): target['id'] = call['id']
                    fn = call.get('function') or {}
                    target['function']['name'] += fn.get('name') or ''
                    target['function']['arguments'] += fn.get('arguments') or ''
    if protocol == 'anthropic':
        for part in parts.values():
            if '_json' in part: part['input'] = json.loads(part.pop('_json'))
        return {'content': list(parts.values()), 'usage': usage, 'stop_reason': finish}
    if protocol == 'gemini': return {'candidates': [{'content': {'parts': list(parts.values())}, 'finishReason': finish}], 'usageMetadata': usage}
    if protocol == 'ollama':
        native.setdefault('message', {})['content'] = ''.join(text)
        native['message']['thinking'] = ''.join(reasoning)
        if ollama_calls: native['message']['tool_calls'] = ollama_calls
        return native
    return {'choices': [{'message': {'role': 'assistant', 'content': ''.join(text), 'reasoning_content': ''.join(reasoning), 'tool_calls': list(calls.values())}, 'finish_reason': finish}], 'usage': usage}
