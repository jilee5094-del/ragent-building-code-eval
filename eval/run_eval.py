# -*- coding: utf-8 -*-
"""调用 Ragent 纯检索评测接口 /rag/eval，计算条文级 Recall@K / Hit@K / MRR、无答案题行为与延迟。
用法：python -I run_eval.py <组别名> [重复次数]   结果写 result_<组别>.json"""
import io, json, os, re, statistics, sys, time, urllib.parse, urllib.request

BASE = 'http://127.0.0.1:9090/api/ragent'
HERE = os.path.dirname(os.path.abspath(__file__))
CLAUSE = re.compile(r'【(GB ?\d+-\d+) 第([\d.]+)条】')
KS = (1, 3, 5, 10)


def login():
    req = urllib.request.Request(BASE + '/auth/login', json.dumps({'username': 'admin', 'password': 'admin'}).encode(),
                                 {'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req))['data']['token']


def eval_one(token, item):
    url = BASE + '/rag/eval?question=' + urllib.parse.quote(item['question'])
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': token}), timeout=180))['data']
    ctxs = d.get('retrievedContexts') or []
    ranked = [{f"{a.replace(' ', '')}:{b}" for a, b in CLAUSE.findall(c)} for c in ctxs]
    gold = set(item['gold_clauses'])
    res = {'id': item['id'], 'type': item['type'], 'latency': d.get('latencyMs'), 'contexts': len(ctxs),
           'hasKb': d.get('hasKb'), 'intent': d.get('intentLeafIds'), 'sub': d.get('subIntents'),
           'top3': [sorted(r) for r in ranked[:3]]}
    for k in KS:
        got = set().union(*ranked[:k]) if ranked[:k] else set()
        res[f'recall@{k}'] = len(gold & got) / len(gold) if gold else None
        res[f'hit@{k}'] = (1.0 if gold & got else 0.0) if gold else None
    rr = 0.0
    for i, r in enumerate(ranked, 1):
        if gold & r:
            rr = 1 / i
            break
    res['rr'] = rr if gold else None
    return res


def avg(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    return sum(v) / len(v) if v else None


def summarize(rows):
    s = {f'recall@{k}': avg(rows, f'recall@{k}') for k in KS}
    s.update({f'hit@{k}': avg(rows, f'hit@{k}') for k in KS})
    s['mrr_single'] = avg([r for r in rows if r['type'] != 'multi'], 'rr')
    neg = [r for r in rows if r['type'] == 'no_answer']
    s['no_answer_rejected'] = sum(1 for r in neg if r['contexts'] == 0 or r['hasKb'] is False) / len(neg) if neg else None
    lat = sorted(r['latency'] for r in rows if r['latency'] is not None)
    s['latency_p50'] = lat[len(lat) // 2]
    s['latency_p95'] = lat[max(0, int(len(lat) * 0.95) - 1)]
    s['by_type_recall@5'] = {t: avg([r for r in rows if r['type'] == t], 'recall@5')
                             for t in sorted({r['type'] for r in rows}) if t != 'no_answer'}
    return s


def main(group, repeat=1):
    token = login()
    items = [json.loads(l) for l in io.open(os.path.join(HERE, 'gb50096_eval.jsonl'), encoding='utf-8') if l.strip()]
    runs = []
    for n in range(int(repeat)):
        rows = []
        for it in items:
            rows.append(eval_one(token, it))
            time.sleep(0.3)
        runs.append(rows)
        print(f'[{group}] 第 {n + 1} 轮完成')
    sums = [summarize(r) for r in runs]
    agg = {k: (statistics.mean(s[k] for s in sums), min(s[k] for s in sums), max(s[k] for s in sums))
           for k in sums[0] if isinstance(sums[0][k], (int, float))}
    out = {'group': group, 'questions': len(items), 'repeat': int(repeat), 'mean_min_max': agg,
           'by_type_recall@5': sums[-1]['by_type_recall@5'], 'runs': runs}
    json.dump(out, io.open(os.path.join(HERE, f'result_{group}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({k: round(v[0], 3) for k, v in agg.items()}))


if __name__ == '__main__':
    main(*sys.argv[1:])
