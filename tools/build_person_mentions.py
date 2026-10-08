# -*- coding: utf-8 -*-
"""
build_person_mentions.py — 生成 03-人物出现索引.md

以 02-人物字号索引.md 中的人名（2-4 字，已清洗）为词表，
全库扫描每个人名出现的所有卷（含他人传记/纪/志中的提及），输出：

    人名｜出现N卷｜★本传/主要出处：…｜他卷提及：…

★ 判定：文件名含人名，或该文件为 02 索引中此人之"字"的出处卷。

用法: python tools/build_person_mentions.py
依赖: 需先运行 build_name_index.py 生成 02-人物字号索引.md。
"""
import re
import sys
from pathlib import Path
from collections import defaultdict

from _lib import ROOT, DATA  # noqa: E402

ZI_INDEX = ROOT / "02-人物字号索引.md"
VOL_INDEX = ROOT / "05-卷名总表.md"
EXTRA_NAMES = ROOT / "tools" / "extra_names.md"
OUT = ROOT / "03-人物出现索引.md"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_name_index import known_surname, load_blocklist  # noqa: E402  复用姓氏白名单与屏蔽表

LINE = re.compile(r'^([^｜>#][^｜]{0,3})｜字：([^｜]{1,3})｜(.+)$')
MAX_LIST = 40  # 每人最多列出的提及卷数，超出以「…另N卷」收尾

# 类传/民族/宗室等**类别题名**：05 列传篇名里的这些不是传主人名
# （如宋史「宦者一」、隋书「东夷」、明史「后妃二」、清史稿「诸王七」）。
# 姓氏白名单拦不住它们——宦、后、东、幸、能都是姓。末尾的「一/二/…/十」先剥掉再比对。
CATEGORY_TITLES = frozenset((
    '后妃', '公主', '诸王', '宗室', '宦官', '宦者', '幸臣', '伶官', '外戚', '忠义', '孝义',
    '孝友', '独行', '孝行', '循吏', '酷吏', '良吏', '能吏', '儒林', '文苑', '文艺', '文学',
    '文行', '道学', '隐逸', '遗逸', '处士', '逸民', '列女', '方技', '方伎', '艺术', '卓行',
    '高逸', '佞幸', '恩幸', '义儿', '死节', '死事', '四夷', '诸夷', '东夷', '南蛮', '西羌',
    '北狄', '蛮夷', '外国', '西域', '吐蕃', '回鹘', '突厥', '契丹', '女真', '党项', '吐谷浑',
    '高丽', '高句丽', '百济', '新罗', '日本', '安南', '南诏', '大理', '西夏', '沙陀', '铁勒',
    '薛延陀', '靺鞨', '室韦', '周三臣', '文四子', '明四王', '武二王',
))
_NUMS = '一二三四五六七八九十'


def is_category_title(nm):
    core = nm
    while core and core[-1] in _NUMS:
        core = core[:-1]
    if core in CATEGORY_TITLES:
        return True
    return any(k in nm for k in ('宗室', '诸王'))


def names_from_volume_index():
    """从 05-卷名总表 的列传篇名提取传主人名（覆盖无"字"记载者，如尉迟敬德、申时行）。

    篇名可能形如「魏徵」「申时行等」「李维桢」，剥"等/附/上/下"后按姓氏白名单校验。
    类传/民族/宗室等**类别题名**（宦者一、东夷、后妃二、诸王七…）不是人名，在此拦下。
    """
    out = set()
    n_cat = 0
    if not VOL_INDEX.exists():
        return out, n_cat
    for line in VOL_INDEX.read_text(encoding='utf-8').splitlines():
        if not line or line[0] in '#>':
            continue
        parts = line.split('｜')
        if len(parts) < 5 or parts[2] != '列传':
            continue
        name = parts[3]
        # 05 的卷题级篇名可能带「（含：A、B）」传主提示（build_volume_index 自动提取）——
        # 括号内是合传成员而非篇名本身，剥掉避免把提示串当篇名再拆出碎名。
        name = re.sub(r'（含：[^）]*）$', '', name)
        for seg in re.split(r'[、，,·]', name):
            seg = re.sub(r'^(列传|传)', '', seg.strip())
            seg = re.sub(r'[等附上下中]$', '', seg)
            if not (2 <= len(seg) <= 4) or not known_surname(seg):
                continue
            # 有些书的篇名是**卷题级**（如「列传第五十」，无传主名）——剥掉"列传"后只剩序号「第五十」。
            # 而「第五」是真实复姓（第五伦），姓氏白名单会放行，于是史记卷110–119 的十个列传序号
            # 被当成十个人名收进 03。真人名不可能整串都是"第+数词"，据此剔除。
            if re.fullmatch(r'第?[〇零一二三四五六七八九十百千]+', seg):
                continue
            # 类传题名：姓氏白名单放行但绝非人物（宦、后、东、幸、能都是姓）
            if is_category_title(seg):
                n_cat += 1
                continue
            out.add(seg)
    return out, n_cat


def names_from_extra():
    """补充人名表（tools/extra_names.md）：登记无"字"记载且未入篇名的人物（刺客/门客/宠臣等）。"""
    out = set()
    if EXTRA_NAMES.exists():
        for line in EXTRA_NAMES.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            for nm in re.split(r'[｜|、,\s]+', line):
                if 2 <= len(nm) <= 4:
                    out.add(nm)
    return out


def load_names():
    """从 02 索引甲区（姓氏校验通过的高可信条目）收集人名表（仅 2-4 字）、
    各人的'字出处'文件集合，以及各人的'字'集合（用于同名异人提示）。

    乙区（待核）人名不纳入：称谓残留与噪声经 03 倒排会被放大为批量误命中。
    """
    names = set()
    zi_src = defaultdict(set)
    zi_set = defaultdict(set)
    for line in ZI_INDEX.read_text(encoding='utf-8').splitlines():
        if line.startswith('# 乙区'):
            break
        m = LINE.match(line)
        if not m:
            continue
        nm, zi, rel = m.group(1), m.group(2), m.group(3).strip()
        rel = rel.split('｜')[0].strip()  # 行尾可能带「异文：X」标注，路径取第一段
        if 2 <= len(nm) <= 4:
            names.add(nm)
            zi_src[nm].add(rel)
            zi_set[nm].add(zi)
    block = load_blocklist()
    extra, n_cat = names_from_volume_index()
    extra -= names
    names |= extra
    names |= names_from_extra()
    # 非人名屏蔽表：句式误配（「乃宣敕告之」）与合传简称（「侯张薛」）都不是人物。
    # 03 的人名不带"字"，故行级屏蔽（武｜字：狄遮）在这里按名级生效——
    # 但只有当该名**所有** 02 条目都被行级屏蔽时才可从 03 剔除；简单起见，
    # 03 只应用名级屏蔽，行级碎片名（武/毗）本就有真人同名条目，留作 02 的精修。
    dropped = {n for n in names if n in block}
    names -= set(block)
    return names, zi_src, zi_set, dropped, n_cat


def main():
    names, zi_src, zi_set, dropped, n_cat = load_names()
    first_chars = set(n[0] for n in names)
    # 每文件记录命中的人名
    mentions = defaultdict(set)  # name -> set(relpath)
    nfiles = 0
    for fp in sorted(DATA.rglob('*.md')):
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        nfiles += 1
        rel = str(fp.relative_to(DATA)).replace('/', '\\')
        hit = set()
        L = len(text)
        i = 0
        t = text
        ns = names
        fc = first_chars
        while i < L:
            if t[i] in fc:
                w = t[i:i + 2]
                if w in ns:
                    hit.add(w)
                w = t[i:i + 3]
                if w in ns:
                    hit.add(w)
                w = t[i:i + 4]
                if w in ns:
                    hit.add(w)
            i += 1
        for nm in hit:
            mentions[nm].add(rel)

    lines = [
        '# 03-人物出现索引（自动生成）',
        '',
        '> **本文件仅供 Grep 检索，请勿整读（文件过大）。**',
        '> 用法：Grep 人名（如「魏徵」），命中行列出此人出现的全部卷：**★ 开头为其本传/主要出处**（篇名含其名，或其"字"出于该卷——**译注/脚注行不计**），其余为在他人传记/本纪/志/表中的提及卷。**引用前仍须 Read 源文件核对原文。**',
        '> 用途：回答"某某的传记在哪""某某还在哪些人的传记里出现过"一类问题；配合 `02-人物字号索引.md`（查字号）与 `01-卷目速查.md`（查卷目）使用。',
        '> 口径：人名表 = `02-人物字号索引`**甲区**（有"字"记载者）＋ `05-卷名总表`**列传篇名所含传主**（覆盖无"字"记载者，如尉迟敬德、申时行）＋ `tools/extra_names.md`**手工补充**（无字且无专传者，如审食其、樊於期、荆轲、专诸），三来源均经姓氏白名单校验（手工补充除外）；精确字符串匹配，简体人名在资治通鉴 45 个繁体补入卷中可能漏配；单字名（如「迁」「籍」）与乙区待核人名（无姓人名、称谓残留）未纳入，其他查不到的人物可在 `tools/extra_names.md` 登记后重跑本脚本。',
        '> 剔除：①类传/民族/宗室等**类别题名**（宦者一、东夷、后妃二、诸王七…不是人物，`CATEGORY_TITLES` 规则自动拦）；②`tools/name_blocklist.md` 登记的**句式误配**（「乃宣敕告之」当成人名）与**合传简称**（「侯张薛」＝侯君集、张亮、薛万均）。02 索引用同一张屏蔽表，两库口径一致。',
        '> **取文件（Windows/PowerShell，禁用 `find`）**：在本 skill 目录下用 Glob 工具或 `dir "0*.md"` 列全部索引；命令示例见 `SKILL.md`「取文件」节。',
        '> 生成：`tools/build_person_mentions.py`。语料更新后依次重跑 `tools/build_name_index.py` 与本脚本（或跑 `tools/make_indexes.py` 一键重建）。',
        '',
    ]
    rows = []
    for nm in sorted(mentions):
        vols = mentions[nm]
        src = zi_src.get(nm, set())
        own = sorted(v for v in vols if v in src or nm in Path(v).stem)
        other = sorted(v for v in vols if v not in own)
        shown_other = other[:MAX_LIST]
        tail = '' if len(other) <= MAX_LIST else '；…另%d卷' % (len(other) - MAX_LIST)
        parts = []
        zs = sorted(zi_set.get(nm, ()))
        if len(zs) >= 2:
            parts.append('⚠同名异人（字：%s，须按出处/时代区分）' % '、'.join(zs))
        if own:
            parts.append('★' + '；'.join(own))
        if shown_other or tail:
            parts.append('提及：' + '；'.join(shown_other) + tail)
        rows.append('%s｜%d卷｜%s' % (nm, len(vols), '｜'.join(parts)))

    multi = sum(1 for nm in mentions if len(zi_set.get(nm, ())) >= 2)
    lines += rows
    head_extra = [
        '> **同名异人**：条目中带「⚠同名异人」字样者，为**同名多人的合并条目**（此类共 %d 个），其后列出各人不同的"字"作为区分线索，'
        '必须逐卷核实年代与身份，**不得将其卷目合并叙述为一人**；在同名条目内定位到具体某人后，用其人"字"回 02 索引查字号，或用 01 卷目定位其本传。' % multi,
    ]
    # 把同名异人说明插到口径说明之后（即「取文件」行之前）——
    # 顺序为：口径 → 同名异人 → 取文件 → 生成（前两条讲数据，后两条讲工具）。
    # ⚠ 锚点若换成 `> 生成：`，说明行会落到「取文件」之后，与磁盘版顺序不符、重跑即错位。
    idx = next(i for i, s in enumerate(lines) if s.startswith('> **取文件'))
    lines[idx:idx] = head_extra

    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('names=%d files=%d indexed=%d multi_zi=%d' % (len(names), nfiles, len(rows), multi))
    print('blocked=%d（句式误配/合传简称，见 tools/name_blocklist.md）  category_titles=%d'
          % (len(dropped), n_cat))
    print('out=%s' % OUT)


if __name__ == '__main__':
    main()
