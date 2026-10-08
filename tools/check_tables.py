# -*- coding: utf-8 -*-
"""
check_tables.py — 表卷体检 + 原件定位：生成 06-表卷与原件索引.md

正史"表"卷多因电子化困难而仅存序文、或以"（表略）"占位。本脚本扫描 05-卷名总表.md
中全部"表"类卷，检测其表体可用性并分级，供检索时先判可用性、避免空查或编造。

同一文件内另附：
  · OCR 机读辅助稿册表（38 册点校本扫描件 OCR 稿，**明确标注机器识别**），
    册表自带「卷次范围」列，故本文件自足，无需外部 07 参照。
  · 原件 PDF 文件名（点校本扫描件）——供占位卷回查原件。

用法: python tools/check_tables.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from _paths import pdf_dir                                    # noqa: E402  库外原件目录（不写死）

DATA = ROOT / "data"
VOL_INDEX = ROOT / "05-卷名总表.md"
OCR_DIR = ROOT / "06-表卷OCR机读稿"          # 繁体机读稿目录（册表来源）
OCR_CACHE = ROOT / "tools" / "_ocr_cache"    # 逐页识别缓存（统计已识别页数）
OUT = ROOT / "06-表卷与原件索引.md"

PAGE_RE = re.compile(r'^###\s*第\s*(\d+)\s*页')   # 机读稿的页标记（数页数用，见 _draft_pages）

# 08 目录繁体书名 → 06 简体书名，需归一
T2S = {'史記': '史记', '漢書': '汉书', '新唐書': '新唐书', '宋史': '宋史',
       '遼史': '辽史', '金史': '金史', '元史': '元史', '明史': '明史', '清史稿': '清史稿'}

from _lib import cn2int  # noqa: E402


def vol_num(vol_text):
    """从「卷一百一十四」或「卷一百一十四上」提取 (整数, 后缀) 后缀为'上/中/下'或''。"""
    m = re.search(r'卷([一二三四五六七八九十百千〇○零]+)([上中下]?)', vol_text or '')
    if not m:
        return None, ''
    return cn2int(m.group(1)), m.group(2)


def _pdf_meta():
    """扫描点校本扫描件目录（见 `tools/_paths.pdf_dir()`，**不写死路径**）下的 PDF，
    返回 {册号: (繁体书名, 册次, 卷次范围, 原件名)}。目录不可用时返回 {}。
    PDF 名形如「點校本二十四史224·[清]張廷玉等·明史(全二十八冊)·第十一冊·卷一〇七至卷一一二·表（中華書局1974）.pdf」。"""
    BOOKS = ('史記', '漢書', '後漢書', '三國志', '晉書', '宋書', '南齊書', '梁書', '陳書', '魏書',
             '北齊書', '周書', '隋書', '南史', '北史', '舊唐書', '新唐書', '舊五代史', '新五代史',
             '宋史', '遼史', '金史', '元史', '明史', '清史稿')
    meta = {}
    d = pdf_dir()
    if d is None:
        return meta
    for fp in d.iterdir():
        if fp.suffix.lower() not in ('.pdf',) and 'downloading' not in fp.name.lower():
            continue
        stem = re.sub(r'\.(PDF|pdf)(\.baiduyun\.p\.downloading)?$', '', fp.name, flags=re.I)
        segs = stem.split('·')
        m = re.match(r'.*?(\d{3})', segs[0])
        if not m:
            continue
        no = m.group(1)
        book = ''
        for s in segs:
            s2 = re.sub(r'[（(].*?[)）]', '', s)
            for b in BOOKS:
                if b in s2:
                    book = b
                    break
            if book:
                break
        if not book:
            continue
        vol = ''
        mv = re.search(r'第\s*([0-9〇零一二三四五六七八九十百]+)\s*冊', stem)
        if mv:
            vol = '第%s冊' % mv.group(1)
        rng = ''
        mr = re.search(r'(卷[一二三四五六七八九十百千〇○零]+(?:[上中下])?至卷[一二三四五六七八九十百千〇○零]+(?:[上中下])?)', stem)
        if mr:
            rng = mr.group(1)
        meta[no] = (book, vol, rng, fp.name)
    return meta


def _cache_pages(no):
    """从 _ocr_cache/<册号>.jsonl 计已识别页数。"""
    c = OCR_CACHE / ('%s.jsonl' % no)
    if not c.exists():
        return 0
    n = 0
    for ln in c.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not ln.strip():
            continue
        try:
            if not json.loads(ln).get('err'):
                n += 1
        except Exception:
            pass
    return n


def _prev_rows():
    """原件目录不可用时的**降级来源**：从已有的 06 里继承册表元数据。

    这样"不写死原件路径"不会把本脚本弄瘸——PDF 目录不在本机时，重跑仍能重建 06，
    册表沿用上一版的原件册名（要刷新册名时再配 `ERSHISI_PDF_DIR`）。
    """
    prev = {}
    if not OUT.exists():
        return prev
    for l in OUT.read_text(encoding='utf-8', errors='replace').splitlines():
        p = l.split('｜')
        if len(p) >= 6 and re.fullmatch(r'\d{3}', p[0].strip()):
            prev[p[0].strip()] = (p[1], p[2], p[3], p[5])
    return prev


def _draft_pages(fp):
    """从机读稿自身的 `### 第 N 页` 标记数出页数。

    这是**缓存不可用时的兜底**：`tools/_ocr_cache/` 已在 2026-09-21 有意清空（38 册产出后即无
    检索价值）。若只看缓存，重跑会把 38 册的「机读状态」全标成"未识别"——与事实相反。
    """
    n = 0
    for ln in fp.read_text(encoding='utf-8', errors='replace').splitlines():
        m = PAGE_RE.match(ln)
        if m:
            n = max(n, int(m.group(1)))
    return n


def load_ocr_drafts():
    """扫 06 机读稿目录得出已 OCR 的册，返回 (drafts, rows)。
    drafts = {简体书名: [(册号, 起始卷, 终止卷, 册次)]}
    rows   = [(册号, 繁体书名, 册次, 卷次范围, 已识别页, 总页, 原件名)]，按册号排序。"""
    drafts, rows = {}, []
    if not OCR_DIR.exists():
        return drafts, rows
    meta = _pdf_meta()
    if not meta:
        meta = _prev_rows()          # 见 _prev_rows()：路径不写死的代价由它兜住
        if meta:
            print('# 提示：未找到点校本原件目录，册表沿用上一版 06 的原件册名；'
                  '如需刷新请设环境变量 ERSHISI_PDF_DIR')
    for fp in sorted(OCR_DIR.glob('*.md')):
        m = re.match(r'(\d{3})_', fp.name)
        if not m:
            continue
        no = m.group(1)
        book, vol, rng, oname = meta.get(no, ('', '', '', ''))
        if not book:
            continue
        if not rng:
            # 退回：从机读稿文件名解析卷次
            mr = re.search(r'_(卷.+?)\.md$', fp.name)
            rng = mr.group(1) if mr else ''
        # 总页数：优先读对应 JSONL 缓存的最大页序（无 pymupdf 依赖）；
        # 缓存已被清空时退回**机读稿自身的页标记**（见 _draft_pages），否则状态会集体退化成"未识别"
        seen = _cache_pages(no)
        if not seen:
            seen = _draft_pages(fp)
        total = seen  # 缓存完整时 seen==total；否则仅作下限
        rows.append((no, book, vol, rng, seen, total, oname))
        sbook = T2S.get(book, book)
        rm2 = re.match(r'^卷(.+?)至卷(.+)$', rng)
        if not rm2:
            continue
        lo, _ = vol_num('卷' + rm2.group(1))
        hi, _ = vol_num('卷' + rm2.group(2))
        if lo is None or hi is None or lo > hi:
            continue
        drafts.setdefault(sbook, []).append((no, lo, hi, vol))
    for v in drafts.values():
        v.sort(key=lambda x: x[1])
    rows.sort(key=lambda x: x[0])
    return drafts, rows


def find_draft(drafts, book, vol_text):
    """按书名+卷次找对应 OCR 册号，返回 '224册' 或 ''。"""
    clean = re.sub(r'^\d+', '', book).replace('文白', '')
    n, _ = vol_num(vol_text)
    if n is None:
        return ''
    for no, lo, hi, _ce in drafts.get(clean, []):
        if lo <= n <= hi:
            return '%s册' % no
    return ''


def status_of(text):
    if re.search(r'（表略）|（略）|\(表略\)|（缺）', text) and len(text) < 1500:
        return '占位', '正文仅"（表略）/（略）"，表体与序文均无'
    pipe_rows = sum(1 for ln in text.splitlines() if ln.count('|') >= 3)
    if pipe_rows >= 5:
        return '有表体', '含 Markdown 表格，可直接检索条目'
    yearish = sum(1 for ln in text.splitlines()
                  if 2 <= len(ln.strip()) <= 40 and re.search(r'(元年|二年|三年|四年|五年|六年)', ln))
    if yearish >= 20:
        return '有表体', '文本式年表（逐年条目），可检索'
    if len(text) >= 500:
        return '仅文字', '有序文/说明文字，但无表格本体（表体未电子化）'
    return '极短', '内容极少，疑为残缺卷'


def main():
    drafts, draft_rows = load_ocr_drafts()
    rows = []
    seen = set()
    for line in VOL_INDEX.read_text(encoding='utf-8').splitlines():
        if not line or line[0] in '#>':
            continue
        parts = line.split('｜')
        if len(parts) < 5 or parts[2] != '表':
            continue
        book, juan, _kind, name, rel = parts[0], parts[1], parts[2], parts[3], parts[4]
        key = (book, juan, name, rel)
        if key in seen:
            continue
        seen.add(key)
        fp = DATA / rel.replace('\\', '/')
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except Exception:
            rows.append((book, juan, name, '缺失', '文件不存在', rel, ''))
            continue
        st, note = status_of(text)
        draft = find_draft(drafts, book, juan) if st in ('占位', '仅文字') else ''
        if draft:
            note += '；表体见 06 机读稿 %s' % draft
        rows.append((book, juan, name, st, note, rel, draft))

    order = {'有表体': 0, '仅文字': 1, '占位': 2, '极短': 3, '缺失': 4}
    rows.sort(key=lambda r: (order.get(r[3], 9), r[0], r[1]))

    stat = {}
    for r in rows:
        stat[r[3]] = stat.get(r[3], 0) + 1
    with_draft = sum(1 for r in rows if r[6])

    out = [
        '# 06-表卷与原件索引（自动生成）',
        '',
        '> **用途**：正史"表"卷多因电子化困难而**仅存序文**或作"（表略）"占位。问表类问题前先用本表判定可用性；'
        '**占位/仅文字卷严禁编造表内条目**。',
        '> **两段**：①表卷可用性（`书名｜卷次｜篇名｜状态｜说明｜路径`）'
        '②机读稿册表（`册号｜书名｜册次｜卷次范围｜机读状态｜原件`，末列即点校本原件 PDF **册名**'
        '——原件本身不在本库内，见册名自行对号）。'
        '状态四级：**有表体**（可检索）／**仅文字**（只有序文）／**占位**（仅"（表略）"）／**极短**。'
        '处理顺序（勿跳步）见 `SKILL.md`「表卷三级处理」。',
        '> **⚠ 机器识别声明**：第二段 38 册是**机器识别结果、非原文**（错率约 5%–15%，生僻人名更甚），只作检索定位；'
        '表内数据须标注"机器识别稿，字形待核"，**不得声称读过扫描件原文，严禁编造表内条目**。',
        '> **取文件（Windows/PowerShell，禁用 `find`）**：在本 skill 目录下按册号 `dir "06-表卷OCR机读稿（简体）\\224_*.md"`；列全部索引 `dir "0*.md"`。详见 `SKILL.md`「取文件」节。',
        '> 生成：`tools/check_tables.py`。语料、05 表或 06 机读稿更新后重跑本脚本（或跑 `tools/make_indexes.py` 一键重建）。',
        '',
    ]

    # ── 段一：表卷可用性（按可用性排序）──
    n_pm = stat.get('占位', 0) + stat.get('仅文字', 0)
    out.append('# 表卷可用性（共 %d 卷，按可用性排序：有表体 %d／仅文字 %d／占位 %d／极短 %d）'
               % (len(rows), stat.get('有表体', 0), stat.get('仅文字', 0), stat.get('占位', 0), stat.get('极短', 0)))
    out.append('')
    draft_dir = ROOT / '06-表卷OCR机读稿'
    n_draft = len(list(draft_dir.glob('*.md'))) if draft_dir.exists() else 0
    if n_draft:
        out.append('> 旁注："占位/仅文字"共 %d 卷，其中 **%d 卷已由点校本扫描件 OCR 录入** `06-表卷OCR机读稿/`'
                   '（现存 %d 册，见下段册表）——问表内具体人名/年代/任免时，先按行内册号去那里 Grep，再回查 PDF 原件核对。'
                   '未标注册号者（如《新元史》諸表）点校本未收，只能走替代文本。'
                   % (n_pm, with_draft, n_draft))
        out.append('')
    for book, juan, name, st, note, rel, draft in rows:
        out.append('%s｜%s｜%s｜%s｜%s｜%s' % (book, juan, name, st, note, rel))

    # ── 段二：OCR 机读辅助稿册表 ──
    out += ['', '# OCR 机读辅助稿（共 %d 册；⚠ 机器识别，非原文，仅供检索）' % len(draft_rows), '']
    out.append('> 均为**机器识别稿**：对点校本扫描 PDF（繁体、无文本层；原件不在本库内）做 OCR 得到。'
               '简繁双版——原目录 `06-表卷OCR机读稿/`（繁体）＋ `06-表卷OCR机读稿（简体）/`（简体，**检索走此版**）；'
               '两版文件名、页码一一对应。识别引擎 PP-OCRv5 det(server)+rec(server)，页码为 PDF 物理页序。凡引用必须回查 PDF 原件。')
    out.append('')
    for no, book, ce, rng, seen, total, oname in draft_rows:
        st = '机读完整' if total and seen >= total else ('机读 %d 页' % seen if seen else '未识别')
        out.append('%s｜%s｜%s｜%s｜%s｜%s' % (no, book, ce, rng or '-', st, oname))

    OUT.write_text('\n'.join(out) + '\n', encoding='utf-8', newline='\n')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('tables=%d %s  可定位机读稿=%d卷  机读册=%d' % (len(rows), stat, with_draft, len(draft_rows)))


if __name__ == '__main__':
    main()
