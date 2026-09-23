"""多 AI 服务接入层（通义千问 / DeepSeek 等，统一走 OpenAI 兼容接口）。

设计原则：
- 以「服务(provider)」为单位管理多个 AI 服务，配置存于 SystemConfig.ai_providers（JSON）。
- 通义千问(DashScope 兼容模式) 与 DeepSeek 均暴露 OpenAI 兼容的 /chat/completions，
  因此用同一套 requests 调用即可并行接入多家服务。
- 无可用 Key 时返回 None，由各业务模块做「本地模板降级」，保证功能可见、离线可用。
- ask_all() 通过线程池并行调用所有已启用服务，实现「并行接入」。
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from .models import SystemConfig

logger = logging.getLogger('kb')

# 支持的服务类型；后续可扩展 azure / gemini 等
PROVIDER_TYPES = {
    'openai': 'OpenAI 兼容（通义千问 / DeepSeek / 本地 Ollama 等）',
}

# 出厂默认服务（未配置 ai_providers 时使用）
DEFAULT_PROVIDERS = [
    {
        'id': 'qwen', 'name': '通义千问', 'type': 'openai', 'api_key': '',
        'model': 'qwen-plus',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'enabled': True, 'is_default': True,
    },
    {
        'id': 'deepseek', 'name': 'DeepSeek', 'type': 'openai', 'api_key': '',
        'model': 'deepseek-chat',
        'base_url': 'https://api.deepseek.com/v1',
        'enabled': True, 'is_default': False,
    },
]


# ----------------------------- 配置读写 -----------------------------
def _load_providers():
    raw = SystemConfig.get_value('ai_providers', '').strip()
    if not raw:
        return [dict(p) for p in DEFAULT_PROVIDERS]
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return data
    except Exception:
        logger.warning('ai_providers 解析失败，回退默认配置')
    return [dict(p) for p in DEFAULT_PROVIDERS]


def _save_providers(providers):
    SystemConfig.set_value('ai_providers', json.dumps(providers, ensure_ascii=False))


def get_providers():
    """返回全部服务配置列表。"""
    return _load_providers()


def get_provider(pid):
    for p in get_providers():
        if p.get('id') == pid:
            return p
    return None


def migrate_legacy_key():
    """旧版 dashscope_api_key 迁移进 qwen provider（仅首次、且 qwen 无 key 时）。"""
    legacy = SystemConfig.get_value('dashscope_api_key', '').strip()
    if not legacy:
        return
    providers = get_providers()
    qwen = next((p for p in providers if p.get('id') == 'qwen'), None)
    if qwen and not qwen.get('api_key', '').strip():
        qwen['api_key'] = legacy
        _save_providers(providers)
    # 迁移后清空旧键，避免两处来源混淆
    SystemConfig.set_value('dashscope_api_key', '')


def upsert_provider(data):
    """新增或更新一个服务；返回其 id。"""
    providers = get_providers()
    pid = (data.get('id') or '').strip()
    name = (data.get('name') or '').strip() or '未命名服务'
    ptype = data.get('type', 'openai')
    api_key = (data.get('api_key') or '').strip()
    model = (data.get('model') or '').strip()
    base_url = (data.get('base_url') or '').strip()
    enabled = bool(data.get('enabled'))
    is_default = bool(data.get('is_default'))

    if not pid:
        # 自动生成 id
        base = name.lower().replace(' ', '_')
        pid = base or 'svc'
        n = 1
        while any(p.get('id') == pid for p in providers):
            pid = f'{base}_{n}'
            n += 1

    found = None
    for p in providers:
        if p.get('id') == pid:
            found = p
            break

    if found:
        # 编辑时若未填写 Key，保留原值（前端出于安全不回显 Key）
        if not api_key and found.get('api_key'):
            api_key = found.get('api_key')
        found.update({
            'name': name, 'type': ptype, 'api_key': api_key,
            'model': model, 'base_url': base_url,
            'enabled': enabled, 'is_default': is_default,
        })
    else:
        providers.append({
            'id': pid, 'name': name, 'type': ptype, 'api_key': api_key,
            'model': model, 'base_url': base_url,
            'enabled': enabled, 'is_default': is_default,
        })

    if is_default:
        for p in providers:
            if p.get('id') != pid:
                p['is_default'] = False
    # 注：尊重用户“不要默认”的显式选择，不再强制把 providers[0] 设为默认
    # （ask_ai 在默认缺失时会自动退化到其它已启用服务，无需硬保底）

    _save_providers(providers)
    return pid


def delete_provider(pid):
    providers = [p for p in get_providers() if p.get('id') != pid]
    if not any(p.get('is_default') for p in providers) and providers:
        providers[0]['is_default'] = True
    _save_providers(providers)


def set_default(pid):
    providers = get_providers()
    hit = False
    for p in providers:
        if p.get('id') == pid:
            p['is_default'] = True
            hit = True
        else:
            p['is_default'] = False
    if hit:
        _save_providers(providers)
    return hit


# ----------------------------- 调用 -----------------------------
def _call_openai(provider, system_prompt, user_prompt, model=None, temperature=0.7):
    """OpenAI 兼容 /chat/completions 调用；失败/无 Key 返回 None。"""
    import requests
    key = (provider.get('api_key') or '').strip()
    if not key:
        return None
    base = (provider.get('base_url') or '').rstrip('/')
    if not base:
        return None
    url = base + '/chat/completions'
    model = model or provider.get('model') or 'gpt-3.5-turbo'
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': temperature,
    }
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=90)
        if r.status_code == 200:
            data = r.json()
            return data['choices'][0]['message']['content']
        logger.error('AI 调用失败 %s: %s', r.status_code, r.text[:300])
    except Exception as e:
        logger.error('AI 调用异常: %s', e)
    return None


def ask_provider(pid, system_prompt, user_prompt, model=None, temperature=0.7):
    p = get_provider(pid)
    if not p or not p.get('enabled'):
        return None
    return _call_openai(p, system_prompt, user_prompt, model, temperature)


def ask_ai(system_prompt, user_prompt, provider_id=None, model=None, temperature=0.7):
    """统一入口：指定 provider_id 则用它；否则优先用默认(已启用)服务，
    若该服务调用失败（返回 None）则退化为其它已启用的服务，保证可用性。"""
    providers = get_providers()
    if provider_id:
        return ask_provider(provider_id, system_prompt, user_prompt, model, temperature)
    # 默认且启用的服务排在最前；依次尝试，首个成功即返回（实现「失败退化」）
    candidates = [p for p in providers if p.get('is_default') and p.get('enabled')]
    candidates += [p for p in providers if p.get('enabled') and not p.get('is_default')]
    for p in candidates:
        ans = ask_provider(p['id'], system_prompt, user_prompt, model, temperature)
        if ans:
            return ans
    return None


def ask_all(system_prompt, user_prompt, temperature=0.7):
    """并行调用所有已启用服务，返回 {服务名: 回答或 None}。"""
    enabled = [p for p in get_providers() if p.get('enabled')]
    results = {}
    if not enabled:
        return results

    def worker(p):
        return (p.get('name') or p.get('id'), _call_openai(p, system_prompt, user_prompt, None, temperature))

    with ThreadPoolExecutor(max_workers=max(1, len(enabled))) as ex:
        futures = {ex.submit(worker, p): p for p in enabled}
        for fut in futures:
            name, ans = fut.result()
            results[name] = ans
    return results


def test_provider(pid):
    """连通性测试：用一句固定提示验证 Key 与接口是否可用。"""
    p = get_provider(pid)
    if not p:
        return False, '服务不存在'
    if not (p.get('api_key') or '').strip():
        return False, '未配置 API Key'
    ans = _call_openai(p, '你是测试助手。', '请只回复「ok」两个字。', temperature=0.1)
    if ans:
        return True, (ans[:60] + ('…' if len(ans) > 60 else ''))
    return False, '调用失败（检查 Key / base_url / 网络）'


def ai_available():
    return any(p.get('enabled') and (p.get('api_key') or '').strip() for p in get_providers())


def ai_status():
    """给前端用的状态概览。"""
    return [{
        'id': p.get('id'), 'name': p.get('name'), 'type': p.get('type'),
        'model': p.get('model'), 'base_url': p.get('base_url'),
        'enabled': p.get('enabled', False), 'is_default': p.get('is_default', False),
        'configured': bool((p.get('api_key') or '').strip()),
    } for p in get_providers()]


# ----------------------------- 向后兼容 -----------------------------
def ask_qwen(system_prompt, user_prompt, model='qwen-plus', temperature=0.7):
    """兼容旧调用：优先走 qwen provider，否则走默认服务。"""
    p = get_provider('qwen')
    if p and p.get('enabled'):
        ans = ask_provider('qwen', system_prompt, user_prompt, model, temperature)
        if ans:
            return ans
    return ask_ai(system_prompt, user_prompt, model=model, temperature=temperature)


def ask_qwen_safe(system_prompt, user_prompt, fallback, model='qwen-plus'):
    ans = ask_qwen(system_prompt, user_prompt, model=model)
    return ans if ans else fallback
