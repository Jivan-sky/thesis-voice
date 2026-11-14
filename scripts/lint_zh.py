#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文论文低级错误扫描（Word/Markdown 通吃）。

用法:
    python3 lint_zh.py 章节.md
    python3 lint_zh.py 论文全文.md --max-show 5
    python3 lint_zh.py 章节.md --json out.json

只报可机械判定的错误，不做文风评价。文风看 voice_check.py。
"""
import re
import sys
import json
import argparse
from pathlib import Path

CJK = re.compile(r'[\u4e00-\u9fff]')

# (类别, 说明, 正则, 处理建议)
RULES = [
    ('标点', '中文标点后有多余空格', re.compile(r'[。，；：！？、][ \t]+'), '删掉标点后的空格'),
    ('标点', '标点前有空格', re.compile(r'\s+[。，；：！？、]'), '删掉标点前的空格'),
    ('标点', '连续标点', re.compile(r'[。，；：！？]{2,}'), '删掉重复标点'),
    ('标点', '英文省略号', re.compile(r'\.\.\.'), '中文用「……」'),
    ('标点', '中文句号连写', re.compile(r'。。。'), '中文用「……」'),
    ('全半角', '全角数字', re.compile(r'[０-９]'), '改成半角数字'),
    ('全半角', '全角字母', re.compile(r'[Ａ-Ｚａ-ｚ]'), '改成半角字母'),
    ('全半角', '全角百分号', re.compile(r'％'), '改成 %'),
    ('全半角', '中文语境里的英文逗号', re.compile(r'[\u4e00-\u9fff],|,[\u4e00-\u9fff]'), '中文句子用「，」'),
    ('全半角', '中文语境里的英文句点', re.compile(r'[\u4e00-\u9fff]\.(?!\d)'), '中文句子用「。」'),
    ('数字单位', '数字与英文单位之间缺空格', re.compile(r'\d+(?:pt|cm|mm|kg|ms|GB|MB|KB|px)\b', re.I), '写成「12 pt」「3.5 cm」'),
    ('数字单位', '数字后紧跟中英混写单位', re.compile(r'\d+(?:个小时|分钟|秒钟)'), '统一为「小时/分钟/秒」或英文单位'),
    ('用词', '慎用程度词', re.compile(r'显著|大幅|极大|极大提升|有效提升|全面提升'), '句中有数字才留，否则删'),
    ('用词', '自我宣称', re.compile(r'(?<![不未非无别])(首次提出|填补空白|国际领先|国内领先|具有重要意义)'), '删，或给出检索证据'),
    ('用词', '第一人称', re.compile(r'我们|笔者'), '单人课题统一用「本文」'),
    ('用词', '元话语', re.compile(r'值得注意的是|需要指出的是|综上所述|由此可见|不难发现'), '删掉，直接给结论'),
]
RULES = [r for r in RULES if r[2] is not None]

# 需要行内 CJK 前提的规则
CJK_ONLY = {'英文直引号', '半角单引号'}


def scan_line(line, lineno):
    hits = []
    for cat, msg, pat, fix in RULES:
        for m in pat.finditer(line):
            # 否定语境不算自我宣称（"不主张首次提出"）
            if msg == '自我宣称' and re.search(r'[不未非无别避免]', line[max(0, m.start() - 8):m.start()]):
                continue
            hits.append({
                'line': lineno, 'cat': cat, 'msg': msg,
                'text': m.group(0),
                'snippet': line[max(0, m.start() - 12):m.start() + 16].strip(),
                'fix': fix,
            })
    # 中文行里的英文直引号
    if CJK.search(line):
        for m in re.finditer(r'["]', line):
            hits.append({
                'line': lineno, 'cat': '全半角', 'msg': '中文里出现英文直引号',
                'text': '"', 'snippet': line[max(0, m.start() - 12):m.start() + 16].strip(),
                'fix': '改成「“ ”」，字体按本地格式规格',
            })
    # 段首空格
    if re.match(r'^[ \u3000]{1,}', line) and CJK.search(line):
        hits.append({
            'line': lineno, 'cat': '排版', 'msg': '段首用空格缩进',
            'text': line[:6], 'snippet': line.strip()[:24],
            'fix': '删空格，用样式的首行缩进',
        })
    # 重复词
    m = re.search(r'([\u4e00-\u9fff]{2})\1', line)
    if m:
        hits.append({
            'line': lineno, 'cat': '用词', 'msg': '疑似重复词',
            'text': m.group(0), 'snippet': line[max(0, m.start() - 10):m.start() + 14].strip(),
            'fix': '删重复',
        })
    return hits


def check(text):
    hits = []
    for i, line in enumerate(text.split('\n'), 1):
        if not line.strip():
            continue
        hits += scan_line(line, i)

    # 全文级
    paren_cn = (text.count('（'), text.count('）'))
    paren_en = (text.count('('), text.count(')'))
    global_hits = []
    if paren_cn[0] != paren_cn[1]:
        global_hits.append({'cat': '全半角', 'msg': f'中文括号不配对（（ {paren_cn[0]} 个，） {paren_cn[1]} 个）', 'fix': '补齐或删除'})
    if paren_en[0] != paren_en[1]:
        global_hits.append({'cat': '全半角', 'msg': f'英文括号不配对（( {paren_en[0]} 个，) {paren_en[1]} 个）', 'fix': '补齐或删除'})
    lone = len(re.findall(r'(?<!—)—(?!—)', text))
    if lone > 4:
        global_hits.append({'cat': '标点', 'msg': f'单个「—」出现 {lone} 处（疑似连接号与破折号混用）', 'fix': '连接号与破折号各自统一，或改用文字连接'})
    dash = len(re.findall('——', text))
    if dash > 2:
        global_hits.append({'cat': '标点', 'msg': f'破折号 {dash} 处（工科论文全文 ≤2）', 'fix': '改用冒号或拆句'})

    # 编号连续性
    figs = [int(t[1]) for t in re.findall(r'图\s*(\d+)-(\d+)', text)]
    tabs = [int(t[1]) for t in re.findall(r'表\s*(\d+)-(\d+)', text)]
    for name, seq in (('图', figs), ('表', tabs)):
        if len(seq) >= 3:
            seen = sorted(set(seq))
            expect = list(range(1, len(seen) + 1))
            if seen != expect:
                global_hits.append({'cat': '编号引用', 'msg': f'{name}编号不连续：{seen}', 'fix': '检查跳号/重号'})

    return hits, global_hits


def render(hits, global_hits, max_show):
    out = []
    cats = {}
    for h in hits:
        cats.setdefault(h['cat'], []).append(h)

    out.append(f'== 低级错误扫描：{len(hits)} 处行内问题，{len(global_hits)} 处全文问题 ==')
    if not hits and not global_hits:
        out.append('  没扫到可机械判定的错误。')
        return '\n'.join(out)

    for cat, items in cats.items():
        out.append('')
        out.append(f'【{cat}】{len(items)} 处')
        shown = {}
        for h in items[:max_show]:
            out.append(f"  L{h['line']}  {h['msg']}：{h['text']!r}  …{h['snippet']}…")
            out.append(f"        → {h['fix']}")
        # 同类聚合计数
        for h in items:
            shown[h['msg']] = shown.get(h['msg'], 0) + 1
        if len(items) > max_show:
            out.append(f'  …还有 {len(items) - max_show} 处同类别')
        out.append('  分类计数：' + '；'.join(f'{k}×{v}' for k, v in sorted(shown.items(), key=lambda x: -x[1])))

    if global_hits:
        out.append('')
        out.append('【全文级】')
        for g in global_hits:
            out.append(f"  {g['msg']}  → {g['fix']}")

    out.append('')
    out.append('【提醒】同类错误从不单独出现。改完这一处，用编辑器全文搜同一个模式再扫一遍。')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--max-show', type=int, default=3)
    ap.add_argument('--json', default='')
    a = ap.parse_args()
    text = Path(a.path).read_text(encoding='utf-8', errors='ignore')
    hits, gh = check(text)
    print(render(hits, gh, a.max_show))
    if a.json:
        Path(a.json).write_text(json.dumps({'line_hits': hits, 'global': gh}, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
