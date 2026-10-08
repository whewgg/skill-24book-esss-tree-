# -*- coding: utf-8 -*-
"""共享工具库 —— 把散在各脚本里、且**已出现版本不一致**的重复实现收成单一事实源。

2026-09-22 审计发现：
  · cn2int 有三份，只有 check_tables 版支持「二一五」逐位缩写与「○/〇」占位
    ——其余两份会把它算成 5 / 15，属隐性 bug，现已统一为最全的这份；
  · zhconv 的 site-packages 路径硬编码三份（rag / q / make_ocr_simplified），
    venv 一换位置要改三处，现已只此一处；
  · is_baihua（白话行判定）在 build_name_index / build_story_index 里逐字重复。

用法：各脚本 `from _lib import ROOT, DATA, bootstrap_zhconv, cn2int, is_baihua`。
脚本用 `python tools/xxx.py` 直跑时，sys.path[0] 即 tools/，本库可直接 import。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
OCR_T = ROOT / '06-表卷OCR机读稿'
OCR_S = ROOT / '06-表卷OCR机读稿（简体）'

# ---- 输出编码：导入本库即统一 stdout 为 UTF-8 ----
# 本机控制台默认 GBK，被 subprocess 捕获时输出 GBK 字节，调用方按 UTF-8 解码就成乱码。
# 各脚本原来自行在 main() 里 reconfigure，**位置一旦晚于第一个 print 就会漏**——
# 实测：一次重构把 factcheck.py 的这行弄丢，其 `--list` 输出变乱码，selftest 因此判 FAIL。
# 放在这里＝一处兜住所有 import 本库的脚本（另有 pick_story/check_dupnames/selftest/
# build_table_ocr/q 未 import 本库，它们的 reconfigure 位置已确认在首个 print 之前）。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# ---- 繁简转换（zhconv 装在隔离 venv 里） ----
_ZHCONV = None
_ZHCONV_TRIED = False


def bootstrap_zhconv():
    """注入隔离 venv 的 site-packages 并返回 zhconv.convert；失败返回 None。

    ⚠ 本机 venv 路径只此一处（库外路径唯一落点之一，与 _paths.pdf_dir 同规）。
    缺 zhconv 时调用方**静默**降级会导致「繁简互通」失效——selftest.py 有繁简断言守着。
    """
    global _ZHCONV, _ZHCONV_TRIED
    if _ZHCONV_TRIED:
        return _ZHCONV
    _ZHCONV_TRIED = True
    for p in (r'C:\Users\25284\.workbuddy\binaries\python\envs\default\Lib\site-packages',):
        if p not in sys.path and Path(p).is_dir():
            sys.path.append(p)
    try:
        from zhconv import convert
        _ZHCONV = convert
    except Exception:
        _ZHCONV = None
    return _ZHCONV


# ---- 中文数字 → 整数 ----
CN_NUM = {'〇': 0, '○': 0, '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
          '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn2int(s):
    """中文数字转整数（卷次/年号通用）。

    支持：规范写法（一百一十四）、逐位缩写（二一五=215、一五=15）、ASCII 数字、
    ○/〇 占位（一〇五=105）。无法解析返回 None。
    """
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    # 无十百千：全是数字字符，按逐位读（点校本卷次范围常用「二一五」）
    if '十' not in s and '百' not in s and '千' not in s:
        if all(c in CN_NUM for c in s):
            v = 0
            for c in s:
                v = v * 10 + CN_NUM[c]
            return v
        return None
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in CN_NUM:
            number = CN_NUM[ch]
        elif ch == '十':
            section += (number or 1) * 10
            number = 0
        elif ch == '百':
            section += (number or 1) * 100
            number = 0
        elif ch == '千':
            total += (number or 1) * 1000
            number = 0
        else:
            return None
    return total + section + number


# ---- 行级白话判别：白话译文行整行跳过，只扫文言原文行 ----
# 单字强标记：文言几乎不用（"的"文言极罕、"着"为"著"之俗体、"这/那"文言用此/彼、
# "呢吗嘛啊吧"为白话语气词）；"了/他/什/怎"文言偶见，需与其他标记共现（阈值 2）。
_BH_CHARS = '的了着这這那他她什怎呢吗嘛啊吧'
_BH_WORDS = ('什么', '怎麼', '怎么', '这么', '這麼', '那么', '那麼', '已经', '已經',
             '因为', '因為', '所以', '如果', '虽然', '雖然', '但是', '現在', '现在',
             '对于', '對於', '自己', '我们', '我們', '你们', '你們', '时候', '時候',
             '地方', '事情', '东西', '東西', '一个', '一個', '没有', '沒有', '可以',
             '应该', '應該', '这些', '這些', '那些', '这样', '這樣', '那样', '那樣',
             '还是', '還是', '就是', '而且', '或者', '一块', '一塊', '一直', '一种', '一種')


def is_baihua(line):
    """含 ≥2 个白话强标记即视为白话译文行。文言原文行几乎不可能达标。"""
    s = 0
    for ch in _BH_CHARS:
        s += line.count(ch)
        if s >= 2:
            return True
    for w in _BH_WORDS:
        s += 2 * line.count(w)
        if s >= 2:
            return True
    return False
