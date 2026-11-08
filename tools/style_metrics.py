#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文风指纹量化：把"像不像人写的论文"拆成可测的数字。

用法：
    python3 style_metrics.py <目录或文件> [<目录或文件> ...] --json out.json

只做统计，不做"是否 AI 生成"的判定。所有指标都是对照用的相对量。
"""
import re
import sys
import json
import argparse
from pathlib import Path
from collections import Counter

CJK = r'\u4e00-\u9fff'

# 连接词/逻辑词表：分四类，便于看出"逻辑是靠词堆出来的，还是靠句子推进的"
CONNECTIVES = {
    '因果': ['因此', '所以', '从而', '进而', '导致', '使得', '由于', '可见'],
    '转折': ['然而', '但是', '但', '不过', '反之', '相反', '尽管', '虽然', '却'],
    '并列递进': ['此外', '同时', '另外', '并且', '而且', '进一步', '更重要的是'],
    '枚举': ['首先', '其次', '再次', '最后', '第一', '第二', '第三', '一方面', '另一方面'],
    '总结': ['综上', '总之', '总的来说', '由此可见', '这表明', '这说明'],
    '问题引入': ['针对', '面向', '为了解决', '亟待', '尚缺乏', '不足', '难以'],
}

# 硬空词：换掉不丢信息
HARD_EMPTY = ['赋能', '助力', '极大', '大幅', '蓬勃', '飞速', '日益', '愈发',
              '至关重要', '不可或缺', '不可忽视', '具有重要意义', '发挥重要作用',
              '提供有力支撑', '全方位', '创新性', '先进性', '效果良好', '表现出色',
              '具有一定的', '提供了新思路', '具有参考价值', '得到了广泛', '新范式',
              '新动能', '注入新', '提供新思路', '取得较好', '效果显著']
# 软程度词：句中有数字就不算空话
SOFT_DEGREE = ['显著', '有效', '全面', '充分', '迅速', '较好', '较强', '明显', '优异', '丰富']

FIRST_PERSON = ['本文', '本研究', '笔者', '我们']

SENT_END = '。！？；'
SPLIT_SENT = re.compile(r'[。！？]')
CN_NUM = '一二三四五六七八九十'


def strip_md(t: str) -> str:
    """去掉 Markdown 的结构噪音（表格行、列表项、标题），只留成段正文。"""
    keep = []
    for ln in t.split('\n'):
        s = ln.strip()
        if not s:
            continue
        if s.startswith('|') or s.startswith('#'):
            continue
        if re.match(r'^[-*+]\s', s) or re.match(r'^\d+[.、)]\s', s):
            continue
        if re.match(r'^图\s*\d', s) or re.match(r'^表\s*\d', s):
            continue
        keep.append(s)
    return '\n'.join(keep)


def load_text(path: Path) -> str:
    raw = path.read_text(encoding='utf-8', errors='ignore')
    # 去掉页码标记、页眉、知网水印、纯数字行
    raw = re.sub(r'(大学|学院)(硕士|博士)?学位论文[^\n]*', '', raw)
    raw = '\n'.join(l for l in raw.split('\n') if not re.fullmatch(r'\s*\d{1,3}\s*', l))
    # 去掉页码标记与知网水印
    raw = re.sub(r'===== PDF PAGE \d+ =====', '\n', raw)
    raw = raw.replace('中国知网', '').replace('https://www.cnki.net', '')
    raw = re.sub(r'\n{2,}', '\n', raw)
    # 中文换行拼接：行尾不是标点则视为断行
    lines = [ln.strip() for ln in raw.split('\n')]
    buf = []
    for ln in lines:
        if not ln:
            continue
        if buf and not re.search(r'[。！？：；”"）)\]]$', buf[-1]) and len(buf[-1]) > 8:
            buf[-1] += ln
        else:
            buf.append(ln)
    out = '\n'.join(buf)
    if path.suffix == '.md':
        out = strip_md(out)
    return out


def han_count(s: str) -> int:
    return len(re.findall(f'[{CJK}]', s))


def cv(xs) -> float:
    if not xs:
        return 0.0
    m = sum(xs) / len(xs)
    if m == 0:
        return 0.0
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    return (var ** 0.5) / m


def analyze(text: str) -> dict:
    sents = [s.strip() for s in SPLIT_SENT.split(text) if han_count(s) >= 8]
    s_len = [han_count(s) for s in sents]
    n_han = han_count(text)
    paras = [p for p in text.split('\n') if han_count(p) >= 20]
    p_len = [han_count(p) for p in paras]
    per_k = 1000.0 / max(n_han, 1)

    conn = {}
    for cat, words in CONNECTIVES.items():
        c = Counter()
        for w in words:
            c[w] = text.count(w)
        conn[cat] = {'总数': sum(c.values()), '每千字': round(sum(c.values()) * per_k, 1),
                     '明细': {k: v for k, v in c.items() if v}}

    empty = {w: text.count(w) for w in HARD_EMPTY}
    empty = {k: v for k, v in sorted(empty.items(), key=lambda x: -x[1]) if v}
    soft = 0
    for st in sents:
        if re.search(r'\d', st):
            continue
        if any(w in st for w in SOFT_DEGREE):
            soft += 1

    # 句长分布
    def bucket(n):
        if n <= 15:
            return '≤15'
        if n <= 30:
            return '16-30'
        if n <= 50:
            return '31-50'
        return '>50'
    dist = Counter(bucket(n) for n in s_len)
    dist_pct = {k: round(v * 100.0 / max(len(s_len), 1), 1) for k, v in dist.items()}

    # 句首模式
    heads = Counter()
    for s in sents:
        h = s[:6]
        for pat in ['针对', '随着', '近年来', '当前', '本文', '在', '为', '该', '这', '现有', '传统', '然而']:
            if h.startswith(pat):
                heads[pat] += 1
                break
    head_pct = {k: round(v * 100.0 / max(len(sents), 1), 1) for k, v in heads.most_common()}

    # 列举式"三项串联"：X、Y、Z 出现在同一分句
    tri = len(re.findall(f'[{CJK}（）A-Za-z0-9]{{2,12}}、[{CJK}（）A-Za-z0-9]{{2,12}}、[{CJK}（）A-Za-z0-9]{{2,12}}', text))

    metrics = {
        '汉字数': n_han,
        '句数': len(sents),
        '平均句长': round(sum(s_len) / max(len(s_len), 1), 1),
        '句长标准差': round((sum((x - sum(s_len) / max(len(s_len), 1)) ** 2 for x in s_len) / max(len(s_len), 1)) ** 0.5, 1),
        '句长分布%': dist_pct,
        '长句占比%': round(sum(1 for n in s_len if n > 50) * 100.0 / max(len(sents), 1), 1),
        '短句占比%': round(sum(1 for n in s_len if n <= 15) * 100.0 / max(len(sents), 1), 1),
        # 节奏：句长变异系数越大，长短起伏越明显；AI 味稿子 CV 通常在 0.3 以下
        '句长CV': round(cv(s_len), 2),
        # 相邻句长差：局部起伏，比 CV 更能反映"读起来有没有顿挫"
        '相邻句长差': round(sum(abs(s_len[i] - s_len[i - 1]) for i in range(1, len(s_len))) / max(len(s_len) - 1, 1), 1),
        '长短短句比': round((sum(1 for n in s_len if n > 50) + 1) / (sum(1 for n in s_len if n <= 15) + 1), 1),
        '段数': len(paras),
        '平均段长': round(sum(p_len) / max(len(p_len), 1), 1),
        '分号每千字': round(text.count('；') * per_k, 1),
        '顿号每千字': round(text.count('、') * per_k, 1),
        '破折号每千字': round(text.count('——') * per_k * 1.0, 1),
        '括号每千字': round((text.count('（') + text.count('(')) * per_k, 1),
        '数字每千字': round(len(re.findall(r'\d+(?:\.\d+)?%?', text)) * per_k, 1),
        '百分号数': text.count('%'),
        '英文学名括号数': len(re.findall(r'[（(][A-Za-z][A-Za-z \-]{3,}[）)]', text)),
        '本文每千字': round(text.count('本文') * per_k, 1),
        '连接词': conn,
        '句首模式%': head_pct,
        '三项串联数': tri,
        '三项串联每千字': round(tri * per_k, 1),
        '空词总计': sum(empty.values()),
        '空词每千字': round(sum(empty.values()) * per_k, 1),
        '空词明细': empty,
        '软程度词句数': soft,
        '最长句': [s for s in sents if han_count(s) == max(s_len)][0][:200] if sents else '',
    }
    return metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--json', default='')
    args = ap.parse_args()

    files = []
    for p in args.paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(p.glob('*.txt')) + sorted(p.glob('*.md'))
        else:
            files.append(p)

    out = {}
    for f in files:
        try:
            m = analyze(load_text(f))
        except Exception as e:
            print(f'[skip] {f.name}: {e}', file=sys.stderr)
            continue
        out[f.stem] = {'file': str(f), **m}

    for name, m in out.items():
        print(f'{name}\t字数{m["汉字数"]}\t均句{m["平均句长"]}\t长句{m["长句占比%"]}%\t'
              f'短句{m["短句占比%"]}%\t本文{m["本文每千字"]}\t数字{m["数字每千字"]}\t'
              f'三项{m["三项串联每千字"]}\t空词{m["空词每千字"]}\t'
              f'CV{m["句长CV"]}\t相邻差{m["相邻句长差"]}\t长短比{m["长短短句比"]}')

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'\n-> {args.json}')


if __name__ == '__main__':
    main()
