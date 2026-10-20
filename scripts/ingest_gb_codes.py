# -*- coding: utf-8 -*-
"""建筑设计规范知识库入库：建库 → 按章上传条文 Markdown → 分块 → 建意图节点（含引用规则）。
只调用 Ragent 现有 HTTP 接口，不改源码。用法：python -I ingest_gb_codes.py [maxChars]"""
import io, json, os, sys, time, uuid, urllib.request, urllib.error

BASE = 'http://127.0.0.1:9090/api/ragent'
# 规范全文受版权保护，未随仓库发布；请自行整理后放入 data/gb-codes/，格式见 samples/
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'gb-codes')
MAX_CHARS = int(sys.argv[1]) if len(sys.argv) > 1 else 512
# Ragent 知识库为逻辑删除且 collection 名唯一，重建时用带时间戳的新名字
KB_NAME, EMBEDDING = '建筑设计规范', 'qwen-emb-8b'
COLLECTION = f'gb_codes_{MAX_CHARS}_{time.strftime("%m%d%H%M")}'
SPEC = json.dumps({'parseProfile': 'fast', 'maxChars': MAX_CHARS, 'overlapChars': 64})
CITE_RULE = ('回答中每一个数值要求都必须在句末注明来源条文，格式为（GB 50096-2011 第x.x.x条），条文号只能取自资料中【】内的标注，'
             '资料中没有的条文号一律不写；涉及“应/不应/宜/可/必须”时保留原文措辞，不得改变强制程度；标注“强制性条文”的要求需明确指出其为强制性条文。')
token = None


def call(method, path, body=None, raw=None, ctype='application/json;charset=UTF-8'):
    data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None)
    headers = {'Content-Type': ctype} if data is not None else {}
    if token:
        headers['Authorization'] = token
    try:
        raw_resp = urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers, method=method), timeout=120).read()
        if not raw_resp.strip():
            return None  # 部分写接口成功时不返回响应体
        resp = json.loads(raw_resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path} HTTP {e.code}: {e.read().decode("utf-8", "replace")[:300]}')
    if str(resp.get('code')) not in ('0', '200'):
        raise RuntimeError(f'{method} {path} 失败: {resp}')
    return resp.get('data')


def upload(kb_id, path):
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    parts = []
    for k, v in (('sourceType', 'file'), ('processMode', 'chunk'), ('scheduleEnabled', 'false'), ('ingestionSpec', SPEC)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode('utf-8'))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                  f'Content-Type: text/markdown\r\n\r\n').encode('utf-8') + open(path, 'rb').read() + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return call('POST', f'/knowledge-base/{kb_id}/docs/upload', raw=b''.join(parts), ctype=f'multipart/form-data; boundary={boundary}')


def main():
    global token
    token = call('POST', '/auth/login', {'username': 'admin', 'password': 'admin'})['token']
    # 已存在则删除重建，保证每次实验起点一致
    kbs = call('GET', '/knowledge-base?current=1&size=100') or {}
    for kb in (kbs.get('records') if isinstance(kbs, dict) else kbs) or []:
        if str(kb.get('collectionName', '')).startswith('gb_codes_'):
            docs = call('GET', f"/knowledge-base/{kb['id']}/docs?current=1&size=100") or {}
            for d in docs.get('records', []):
                call('DELETE', f"/knowledge-base/docs/{d['id']}")
            call('DELETE', f"/knowledge-base/{kb['id']}")
    kb_id = str(call('POST', '/knowledge-base', {'name': KB_NAME, 'embeddingModel': EMBEDDING, 'collectionName': COLLECTION}))
    print('知识库', kb_id, 'maxChars', MAX_CHARS)
    report = []
    for fn in sorted(os.listdir(DATA_DIR)):
        doc = upload(kb_id, os.path.join(DATA_DIR, fn))
        call('POST', f"/knowledge-base/docs/{doc['id']}/chunk")
        t0 = time.time()
        while True:
            d = call('GET', f"/knowledge-base/docs/{doc['id']}")
            if d.get('status', '').lower() in ('success', 'failed'):
                break
            time.sleep(2)
        report.append({'doc': fn, 'status': d.get('status'), 'chunks': d.get('chunkCount'), 'seconds': round(time.time() - t0, 1)})
        print(report[-1])

    # 意图树：根节点 + 两个知识库叶子，引用规则写进 promptSnippet
    tree = call('GET', '/intent-tree/trees') or []
    def walk(nodes):
        for n in nodes or []:
            yield n
            yield from walk(n.get('children'))
    old = [n['id'] for n in walk(tree) if str(n.get('intentCode', '')).startswith('gb-codes')]
    if old:
        call('POST', '/intent-tree/batch/delete', {'ids': old})
    common = {'kind': 0, 'enabled': 1, 'requireConfirm': 0, 'collectionNames': [COLLECTION]}
    call('POST', '/intent-tree', dict(common, intentCode='gb-codes', name='建筑设计规范', level=0, sortOrder=100,
                                      description='建筑设计规范条文查询，包括住宅设计规范 GB 50096 中的套型面积、层高净高、楼梯电梯、阳台栏杆、采光通风、隔声、给排水与电气等要求',
                                      examples=['住宅卧室最小面积是多少？', '几层住宅必须设电梯？', '阳台栏杆高度有什么要求？']))
    call('POST', '/intent-tree', dict(common, intentCode='gb-codes-residential-space', name='住宅套内与共用空间', level=1, parentCode='gb-codes', sortOrder=101,
                                      description='住宅套型、卧室起居室厨房卫生间面积、层高与净高、阳台、门窗、楼梯、电梯、走廊、安全出口、无障碍、地下室等设计要求',
                                      examples=['双人卧室最小使用面积', '楼梯踏步宽度和高度要求', '十二层住宅要设几部电梯', '住宅层高宜为多少'],
                                      promptSnippet=CITE_RULE))
    call('POST', '/intent-tree', dict(common, intentCode='gb-codes-residential-env', name='住宅室内环境与设备', level=1, parentCode='gb-codes', sortOrder=102,
                                      description='住宅日照、采光、通风、隔声、防潮、室内空气质量，以及给水排水、供暖、燃气、电气、排气道等建筑设备要求',
                                      examples=['分户墙隔声要求', '入户给水压力上限', '卧室采光系数要求', '住宅每套电源插座数量'],
                                      promptSnippet=CITE_RULE))
    print('意图节点已创建')
    json.dump({'kbId': kb_id, 'collection': COLLECTION, 'maxChars': MAX_CHARS, 'docs': report}, io.open(f'ingest_result_{MAX_CHARS}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
