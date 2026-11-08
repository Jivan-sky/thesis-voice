#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文风体检：拿 41 篇文献蒸出来的指纹，给一段/一章论文正文打分。

用法:
    python3 voice_check.py 草稿.md
    python3 voice_check.py 草稿.md --json out.json
    python3 voice_check.py 草稿.md --target abstract   # 摘要用另一套阈值

只报"离人写的论文有多远"，不判定是否 AI 生成。
每条给：判定、你的值、参考区间、命中的原句、改法。
"""
import re
import sys
import json
import argparse
from pathlib import Path

CJK = r'\u4e00-\u9fff'

# 41 篇实测区间（见 out/01_量化指纹.md）
TARGET = {
    # 学位论文正文（6 篇整本 OCR 实测）：均句 48.7–56.1，长句 34.8–43.4%，短句 2.0–6.3%，
    # CV 0.56–0.86，相邻句长差 25–37，三项串联 0.6–2.7/千字，硬空词 0.2–0.7，数字 30–68
    'body': {
        '平均句长': (45, 85),
        '长句占比': (30, 70),
        '短句占比': (2, 20),
        '句长CV': (0.55, 1.20),
        '相邻句长差': (24, 80),
        '三项串联密度': (0, 4.5),
        '空词密度': (0, 2.5),
        '数字密度': (8, 90),
        '本文密度': (0.2, 4.5),
        '分号密度': (0, 8),
        '软程度词密度': (0, 4.0),
    },
    # 摘要（19 篇实测）：均句 73，≥80 字句占 33%，单句上限 150
    'abstract': {
        '平均句长': (55, 120),
        '长句占比': (40, 95),
        '短句占比': (0, 12),
        '句长CV': (0.55, 1.90),
        '相邻句长差': (35, 150),
        '三项串联密度': (0, 2.0),
        '空词密度': (0, 4.0),
        '数字密度': (10, 130),
        '本文密度': (1.0, 7.0),
        '分号密度': (0, 7),
        '软程度词密度': (0, 3.0),
    },
    # 期刊论文（13 篇实测）：均句 64，长句 52%，相邻句长差 49
    'journal': {
        '平均句长': (50, 85),
        '长句占比': (35, 70),
        '短句占比': (4, 20),
        '句长CV': (0.60, 1.20),
        '相邻句长差': (35, 80),
        '三项串联密度': (0, 4.5),
        '空词密度': (0, 2.5),
        '数字密度': (8, 80),
        '本文密度': (0.2, 3.0),
        '分号密度': (0, 8),
        '软程度词密度': (0, 4.0),
    },
}

# 硬空词：出现即扣分（换掉它们不会丢信息）
HARD_EMPTY = ['赋能', '助力', '极大', '大幅', '蓬勃', '飞速', '日益', '愈发',
              '至关重要', '不可或缺', '不可忽视', '具有重要意义', '发挥重要作用',
              '提供有力支撑', '全方位', '创新性', '先进性', '效果良好', '表现出色',
              '具有一定的', '提供了新思路', '具有参考价值', '得到了广泛', '新范式',
              '新动能', '注入新', '提供新思路', '取得较好', '效果显著']
# 软程度词：本身可以是实词（"有效率""全面测试"），但所在句若无数字，就是空话
SOFT_DEGREE = ['显著', '有效', '全面', '充分', '迅速', '较好', '较强', '明显', '优异', '丰富']

# 工科论文里实测≈0 的套句（41 篇：综上/由此可见 0.03/千字，值得注意的是 0.00，笔者 0）
BANNED_PHRASES = ['综上所述', '由此可见', '不难发现', '值得注意的是', '需要指出的是',
                  '笔者', '我们认为', '众所周知', '不言而喻']

# 只在"结果句"里查：有没有数字/基线/数据集
RESULT_MARK = r'(实验(结果)?表明|结果(表明|显示)|测试(结果|表明)|对比实验|经测试)'
BASELINE_MARK = r'(相比|较|优于|对比|基线|baseline|高出|低于|提升(了)?\d|下降(了)?\d)'

# 段落首句的"模板化"开头
FIRST_SENT_TEMPLATE = [
    '本章', '本节', '本文', '该系统', '本系统', '该模块', '系统采用',
    '首先', '其次', '最后', '综上',
]

SPLIT = re.compile(r'[。！？]')


def han(s):
    return len(re.findall(f'[{CJK}]', s))


def is_struct_line(s):
    s = s.strip()
    return (s.startswith('|') or s.startswith('#') or re.match(r'^[-*+]\s', s)
            or re.match(r'^\d+[.、)]\s', s) or re.match(r'^(图|表)\s*\d', s)
            or s.startswith('>'))


def clean_ocr(text):
    """去掉 OCR 页码标记、页眉、中国知网水印、纯数字行。"""
    text = re.sub(r'===== PAGE \d+ =====', '\n', text)
    text = text.replace('中国知网', '').replace('https://www.cnki.net', '')
    keep = []
    for ln in text.split('\n'):
        s = ln.strip()
        if not s or re.fullmatch(r'\d{1,3}', s):
            continue
        if re.search(r'(大学|学院)(硕士|博士)?学位论文', s):
            continue
        keep.append(s)
    return '\n'.join(keep)


def split_doc(text):
    """返回 (正文段落列表, 结构行列表)"""
    prose, struct = [], []
    for ln in text.split('\n'):
        if not ln.strip():
            continue
        (struct if is_struct_line(ln) else prose).append(ln.strip())
    # 合并被硬换行打断的句子
    merged = []
    for p in prose:
        if merged and not re.search(r'[。！？：；”"）)]$', merged[-1]) and len(merged[-1]) > 10:
            merged[-1] += p
        else:
            merged.append(p)
    return merged, struct


def sentences(paras):
    out = []
    for p in paras:
        for s in SPLIT.split(p):
            s = s.strip()
            if han(s) >= 6:
                out.append(s)
    return out


def cv(xs):
    if not xs:
        return 0.0
    m = sum(xs) / len(xs)
    return 0.0 if m == 0 else (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 / m


def check(text, mode='body'):
    paras, struct = split_doc(text)
    sents = sentences(paras)
    prose = '\n'.join(paras)          # 密度只按正文算，表格/列表不算
    n_han = sum(han(p) for p in paras)
    per_k = 1000.0 / max(n_han, 1)
    L = [han(s) for s in sents]
    tgt = TARGET[mode]

    def dens(pat, flags=0):
        return len(re.findall(pat, prose, flags)) * per_k

    m = {
        '平均句长': round(sum(L) / max(len(L), 1), 1),
        '长句占比': round(sum(1 for x in L if x > 50) * 100.0 / max(len(L), 1), 1),
        '短句占比': round(sum(1 for x in L if x <= 15) * 100.0 / max(len(L), 1), 1),
        '句长CV': round(cv(L), 2),
        '相邻句长差': round(sum(abs(L[i] - L[i - 1]) for i in range(1, len(L))) / max(len(L) - 1, 1), 1),
        '三项串联密度': round(dens(f'[{CJK}]{{2,12}}、[{CJK}]{{2,12}}、[{CJK}]{{2,12}}'), 1),
        '数字密度': round(dens(r'\d+(?:\.\d+)?%?'), 1),
        '本文密度': round(prose.count('本文') * per_k, 1),
        '分号密度': round(prose.count('；') * per_k, 1),
    }
    # 硬空词直接计数；软程度词只在"所在句没数字"时算空话
    empty_hits = []
    for w in HARD_EMPTY:
        for mm in re.finditer(re.escape(w), prose):
            empty_hits.append((w, mm.start()))
    soft_hits = []
    for s_i, s_txt in enumerate(sents):
        if re.search(r'\d', s_txt):
            continue
        for w in SOFT_DEGREE:
            if w in s_txt:
                soft_hits.append((w, s_txt[:60]))
    m['空词密度'] = round(len(empty_hits) * per_k, 1)
    m['软程度词密度'] = round(len(soft_hits) * per_k, 2)

    issues = []
    for k, (lo, hi) in tgt.items():
        v = m.get(k)
        if v is None:
            continue
        if v < lo:
            issues.append(('偏低', k, v, (lo, hi)))
        elif v > hi:
            issues.append(('偏高', k, v, (lo, hi)))

    # 结果句体检
    bad_results = []
    for s in sents:
        if re.search(RESULT_MARK, s) and not re.search(r'\d', s):
            bad_results.append(s)

    # 连续同构句（相邻两句开头 4 字相同）
    same_head = []
    for i in range(1, len(sents)):
        if sents[i][:4] == sents[i - 1][:4] and len(sents[i]) > 10:
            same_head.append((sents[i - 1], sents[i]))

    # 段落首句模板化
    tmpl = []
    for p in paras:
        first = SPLIT.split(p)[0]
        for t in FIRST_SENT_TEMPLATE:
            if first.strip().startswith(t):
                tmpl.append((t, first.strip()[:40]))
                break

    # 段长是否均匀
    plen = [han(p) for p in paras if han(p) >= 30]
    para_cv = round(cv(plen), 2) if len(plen) > 2 else None

    # 重复短语（4-gram 出现 3 次以上）
    grams = {}
    clean = re.sub(f'[^{CJK}]', ' ', ' '.join(paras))
    for seg in clean.split():
        for i in range(len(seg) - 3):
            g = seg[i:i + 4]
            grams[g] = grams.get(g, 0) + 1
    dup = sorted([(c, g) for g, c in grams.items() if c >= 3], reverse=True)[:8]

    # 段级剂量：AI 高频词每段 >2、三元并列每段 >1 都要报
    para_dose = []
    for i, p in enumerate(paras, 1):
        if han(p) < 40 or not re.search(r'[。！？；]', p):   # 跳过标题/页眉等无句读行
            continue
        w = [wd for wd in HARD_EMPTY if wd in p]
        tri_n = len(re.findall(f'[{CJK}]{{2,12}}、[{CJK}]{{2,12}}、[{CJK}]{{2,12}}', p))
        if len(w) > 2 or tri_n > 2:
            para_dose.append((i, len(w), w[:4], tri_n, p[:30]))

    banned = []
    for ph in BANNED_PHRASES:
        for mm in re.finditer(re.escape(ph), prose):
            banned.append((ph, prose[max(0, mm.start() - 10):mm.start() + 16].replace('\n', '')))

    dash = len(re.findall('——', prose))
    bold = len(re.findall(r'\*\*[^*]+\*\*', text))

    # 段首同构：连续 3 段以相同 3 字开头
    heads = [(p[:3], i) for i, p in enumerate(paras, 1) if han(p) >= 40]
    same_start = []
    for i in range(2, len(heads)):
        if heads[i][0] == heads[i - 1][0] == heads[i - 2][0]:
            same_start.append((heads[i][0], heads[i - 2][1], heads[i][1]))

    return {
        'mode': mode,
        '汉字数': n_han,
        '段级超限': para_dose,
        '禁用套句': banned,
        '破折号': dash,
        '加粗': bold,
        '段首同构': same_start,
        '句数': len(sents),
        '段数': len(paras),
        '指标': m,
        '偏离': issues,
        '结果句无数字': bad_results[:8],
        '结果句总数': sum(1 for s in sents if re.search(RESULT_MARK, s)),
        '空词命中': [(w, prose[max(0, i - 12):i + 16].replace('\n', '')) for w, i in empty_hits[:12]],
        '空词合计': len(empty_hits),
        '软程度词': soft_hits[:10],
        '相邻同头句': same_head[:5],
        '模板化段首': tmpl[:10],
        '段长CV': para_cv,
        '高频4字串': dup,
        '结构行数': len(struct),
        '长句示例': [s for s in sents if han(s) > 50][:2],
        '短句示例': [s for s in sents if han(s) <= 15][:3],
    }


def render(r):
    L = []
    A = L.append
    A(f"== 文风体检（{r['mode']}）  正文 {r['汉字数']} 字 / {r['句数']} 句 / {r['段数']} 段 ==")
    if r['句数'] < 30:
        A('   (样本不足 30 句，比例类指标波动大，只看方向和示例句)')
    A('')
    A('【1】指标对照')
    tgt = TARGET[r['mode']]
    for k, v in r['指标'].items():
        lo, hi = tgt.get(k, ('', ''))
        flag = '  '
        if isinstance(lo, (int, float)):
            flag = 'ok' if lo <= v <= hi else '!!'
        A(f"  {flag} {k:<8}{v:>9}   参考 {lo}–{hi}")
    A('')
    A('【2】偏离项与改法')
    if not r['偏离']:
        A('  无。指标全部落在 41 篇区间内。')
    fix = {
        '平均句长': '句子太短/太长。中文学位论文靠 60–90 字长句承载因果与条件，再跟 10–20 字短句收束。',
        '长句占比': '长句不足 → 论证被切成零散短句，读起来像清单。把"条件+机制+结果"合成一句。',
        '短句占比': '几乎没有短句 → 缺顿挫。每段末尾用一句 15 字以内的判断收尾。',
        '句长CV': '句长过于均匀 → 这是最像机器的一条。刻意制造长句—短句交替。',
        '相邻句长差': '局部无起伏。人写论文的相邻句长差平均 49 字。',
        '三项串联密度': '"A、B、C"式枚举过多 → 用枚举代替了论证。把枚举拆成有主次的句子。',
        '空词密度': '显著/有效/深度/赋能这类词过多 → 换成主语+动作+对象的实词句。',
        '数字密度': '全文几乎没有具体数字 → 人写的实证论文每千字 26 个数字。',
        '本文密度': '"本文"过少则责任主体不清，过多则句句自我指涉。',
        '分号密度': '分号过多说明长句是靠分号硬拼的，不是靠逻辑组织。',
    }
    for kind, k, v, (lo, hi) in r['偏离']:
        A(f'  - {k} {kind}：{v}（参考 {lo}–{hi}）')
        if k in fix:
            A(f'      改法：{fix[k]}')
    A('')
    A('【3】结果句证据')
    A(f"  结果句 {r['结果句总数']} 句，其中无数字 {len(r['结果句无数字'])} 句。")
    for s in r['结果句无数字']:
        A(f'    ! {s[:80]}')
    A('')
    A('【4】空词与空话')
    A(f"  硬空词 {r['空词合计']} 处")
    for w, ctx in r['空词命中']:
        A(f'    {w}：…{ctx}…')
    A(f"  无数字的程度句 {len(r['软程度词'])} 句（句里有显著/有效/全面，但没给出任何数字）")
    for w, s_txt in r['软程度词']:
        A(f'    {w}：{s_txt}')
    A('')
    A('【5】句法与段法重复')
    A(f"  相邻同头句 {len(r['相邻同头句'])} 组")
    for a, b in r['相邻同头句']:
        A(f'    {a[:36]} / {b[:36]}')
    A(f"  模板化段首 {len(r['模板化段首'])} 处")
    for t, s in r['模板化段首']:
        A(f'    「{t}」 {s}')
    if r['段长CV'] is not None:
        A(f"  段长CV {r['段长CV']}（<0.35 表示每段差不多长，观感机械）")
    if r['高频4字串']:
        A('  高频重复 4 字串：' + '，'.join(f'{g}×{c}' for c, g in r['高频4字串']))
    A('')
    A('【6】段级剂量与禁用项（工科版硬上限）')
    if r['段级超限']:
        A(f"  段级超限 {len(r['段级超限'])} 段（上限：AI 高频词每段 ≤2，三元并列每段 ≤2）")
        for i, nw, ws, tn, head in r['段级超限']:
            A(f"    第{i}段：高频词 {nw} 个{ws}，三元并列 {tn} 处 ｜ {head}…")
    else:
        A('  段级剂量正常。')
    if r['禁用套句']:
        A(f"  禁用套句 {len(r['禁用套句'])} 处（工科论文实测应≈0）")
        for ph, ctx in r['禁用套句'][:8]:
            A(f"    {ph}：…{ctx}…")
    else:
        A('  无"综上所述/由此可见/不难发现/值得注意的是/笔者"。')
    A(f"  破折号 {r['破折号']} 处（工科实测 0.02/千字，全文 ≤2 处）")
    A(f"  正文加粗 {r['加粗']} 处（应为 0）")
    if r['段首同构']:
        A(f"  段首同构 {len(r['段首同构'])} 组（连续 3 段同起始词）")
        for h, a, b in r['段首同构'][:5]:
            A(f"    第{a}–{b}段均以「{h}」开头")

    A('')
    A('【7】判读')
    bad = len([1 for k, (lo, hi) in tgt.items()
               if isinstance(lo, (int, float)) and not lo <= r['指标'].get(k, 0) <= hi])
    A(f'  {bad} 项偏离参考区间。' + ('基本落在人写论文的范围。' if bad <= 2 else
                                '先按【2】的改法重写，再看数字。'))
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--json', default='')
    ap.add_argument('--target', default='body', choices=['body', 'abstract', 'journal'])
    a = ap.parse_args()
    text = Path(a.path).read_text(encoding='utf-8', errors='ignore')
    if '===== PAGE' in text or '中国知网' in text:
        text = clean_ocr(text)
    r = check(text, a.target)
    print(render(r))
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
