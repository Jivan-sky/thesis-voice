#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从句库里挑"真实原句"：按修辞功能分类抽取，供写论文时对照。

用法: python3 extract_patterns.py <目录> [每类条数]
"""
import re
import sys
from pathlib import Path

PATTERNS = {
    '开篇-背景': r'^(随着|近年来|当前|在.{2,20}(背景|浪潮|趋势)|如今)',
    '开篇-问题': r'^(针对|面向|为了解决|为解决)',
    '指出现有不足': r'(现有|传统|已有|当前).{2,20}(方法|系统|研究|工作).{0,30}(不足|难以|无法|缺乏|尚未|局限|单一|割裂)',
    '转折': r'^(然而|但是|但|不过|尽管|虽然)',
    '本文动作': r'^本文.{0,6}(提出|设计|构建|实现|采用|引入|建立)',
    '实验引出': r'(实验(结果)?表明|结果(表明|显示)|测试(结果|表明)|对比(结果|实验))',
    '条件限定': r'(在.{4,30}(条件下|场景中|前提下|范围内)|仅|只|限于)',
    '承认局限': r'(局限|不足(之处)?|尚未|仍有待|未能|受.{0,8}限制|有待(进一步)?验证|不代表)',
    '收束-小结': r'(综上|因此.{0,10}本文|这为|为.{0,20}提供(了)?(参考|依据|支撑))',
    '因果链': r'(由于.{6,40}(导致|使得|从而)|从而|进而)',
}

SRC = '。！？'


def load(path: Path) -> str:
    t = path.read_text(encoding='utf-8', errors='ignore')
    t = re.sub(r'===== PDF PAGE \d+ =====', '\n', t)
    t = t.replace('中国知网', '').replace('https://www.cnki.net', '')
    lines, buf = [x.strip() for x in t.split('\n')], []
    for ln in lines:
        if not ln:
            continue
        if buf and not re.search(r'[。！？：；”"）)]$', buf[-1]) and len(buf[-1]) > 8:
            buf[-1] += ln
        else:
            buf.append(ln)
    return '\n'.join(buf)


def main():
    d = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    files = sorted(d.glob('*.txt'))
    sents = []
    for f in files:
        txt = load(f)
        for m in re.finditer(f'[^{SRC}]*[{SRC}]', txt):
            s = m.group(0).strip(' \n')
            if 12 <= len(re.findall(r'[\u4e00-\u9fff]', s)) <= 120:
                sents.append((f.stem, s))
    for cat, pat in PATTERNS.items():
        print(f'\n### {cat}')
        hits = [(src, s) for src, s in sents if re.search(pat, s)]
        seen = set()
        shown = 0
        for src, s in hits:
            key = s[:14]
            if key in seen:
                continue
            seen.add(key)
            print(f'- ({src[:24]}) {s}')
            shown += 1
            if shown >= n:
                break
        print(f'  [共 {len(hits)} 句命中]')


if __name__ == '__main__':
    main()
