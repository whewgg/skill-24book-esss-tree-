# -*- coding: utf-8 -*-
"""
check_dupnames.py — 跨书同名文件体检

为什么需要它：全库多个书文件夹使用同一套卷次/篇名命名（如 `001_卷一·本纪第一.md`
同时存在于 06宋书 / 17新唐书 / 20宋史）。按篇名 Glob 会一次命中多部书，是"刘宋
赵宋分不清"这类错误的机制性源头。本脚本输出当前的重名清单，供人工确认。

用法：
    python tools\\check_dupnames.py            # 概览：重名簇 + 高集中书
    python tools\\check_dupnames.py --all      # 逐条列出全部重名文件
    python tools\\check_dupnames.py --strict   # 有重名即返回 1（供外部 gate 使用）

退出码：**默认 0**。跨书重名是本库的结构性常态（当前 89 种／187 个文件），
"有重名就报错"等于把常态当故障，外部脚本会永远红。要拿它当闸门请加 `--strict`。

配套阅读：references\\dynasty-disambiguation.md 第 0.2 节。
"""
import argparse
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')


def scan():
    names = collections.defaultdict(list)
    total = 0
    for dp, _dn, fn in os.walk(DATA):
        for f in fn:
            if not f.endswith('.md'):
                continue
            total += 1
            rel = os.path.relpath(os.path.join(dp, f), DATA).replace('\\', '/')
            names[f].append(rel)
    dup = {k: v for k, v in names.items() if len(v) > 1}
    return total, dup


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--all', action='store_true', help='逐条列出全部重名文件')
    ap.add_argument('--strict', action='store_true',
                    help='有重名即返回退出码 1（默认返回 0，因为重名是本库常态）')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    total, dup = scan()
    files = sum(len(v) for v in dup.values())
    print('全库 md 文件 %d ｜ 重名 basename %d 种 ｜ 涉及文件 %d 个' % (total, len(dup), files))

    if not dup:
        print('无跨书重名。')
        return 0

    clusters = collections.Counter()
    for v in dup.values():
        clusters[tuple(sorted({p.split('/')[0] for p in v}))] += 1
    print('\n重名簇（同组书共用多少种文件名）:')
    for books, n in clusters.most_common():
        print('  %3d 种  %s' % (n, ' + '.join(books)))

    if args.all:
        print('\n逐条明细:')
        for name in sorted(dup):
            print('  %s' % name)
            for p in sorted(dup[name]):
                print('      %s' % p)

    print('\n提醒：Glob 必须带书文件夹前缀（如 data\\06宋书文白\\现代文\\*卷一·本纪第一*.md）；'
          '拿到的路径先看第一段文件夹名再定书名。')
    return 1 if args.strict else 0


if __name__ == '__main__':
    sys.exit(main())
