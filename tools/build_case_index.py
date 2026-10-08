# -*- coding: utf-8 -*-
"""build_case_index.py — 生成 08-案件索引.md

数据源：`tools/cases.md`（手工维护：案件名｜别名｜时代｜类型｜当事人｜检索词｜按语）。
本脚本**只信语料**：对每案检索词回扫 `data\\`（含繁简/异体扩展），每条出处的
书名、卷次、篇名、路径、行号、摘句全部实测得出——检索词零命中即如实标 `⚠`，
绝不顺着"这个案子应该有"编一个卷次出来。

用法：python tools\\build_case_index.py
"""
import re
import sys

from _lib import ROOT, DATA, is_baihua  # noqa: E402
import rag  # noqa: E402

CASES = ROOT / 'tools' / 'cases.md'
VOLS = ROOT / '05-卷名总表.md'
OUT = ROOT / '08-案件索引.md'

MAX_EVID = 2          # 每案最多给出的出处条数
EXCERPT = 30          # 摘句长度上限
# 叙事类卷在选出处时优先（案件的本事多在本纪/列传；志/表里的登场常是旁证）
NARRATIVE = {'本纪', '世家', '列传', '载记', '编年'}
_JUNK_PUNCT = '。，；：！？「」『』"'
# 白话译文识别补充词：`_lib.is_baihua` 的标记表之外，白话常用而文言罕用的一批词。
# 实测踩过：明史 213「皇帝初政时，张居正曾经编纂古代治乱之事…」被当成原文摘句输出。
EXTRA_MODERN = ('曾经', '当时', '后来', '最终', '成为', '认为', '表示', '由于', '进行',
                '为了', '通过', '以及', '以后', '之后', '之前', '一样', '一起', '许多',
                '很多', '知道', '看到', '听到', '进攻')


def is_trans(line):
    """判为白话译文行：`_lib` 强标记 ∪ 白话常用词 ∪ 白话度评分。"""
    return (is_baihua(line) or any(w in line for w in EXTRA_MODERN)
            or rag.baihua_score(line) >= 0.05)


def is_junk_line(line):
    """标题行、目录（篇名）行、注行——不能当史料摘句。"""
    s = line.strip().lstrip('>').strip()
    if not s:
        return True
    if s.startswith(('#', '△', '*[', '**')):
        return True
    if '〔' in s and '。' not in s:            # 目录行的传主名单：「…〔赵庸〕 杨璟 胡美」
        return True
    if not any(c in s for c in _JUNK_PUNCT) and len(s) < 50:
        return True                            # 「李善长 汪广洋」「苏轼（子过）」这类无句读短行
    return False


def split_terms(terms_s):
    """检索词串 → [(词, [排除串…])]。

    `词!排除串1!排除串2`：命中行含排除串即不算该词命中。为**同形假阳性**设——
    实测「杨乃武」的头号命中是《晋书·卷八十四》骈句「殷杨乃武」，而真记载（清史稿卷479
    林启传「余杭巨猾杨乃武」）反被挤到第二；「哭庙」三处命中全是礼制用语（「同姓哭庙」
    「素服哭庙」「祭城隍庙狱曰哭庙」），金圣叹哭庙案本库实未载。没有这个开关，
    索引会把假阳性当出处印出来，比零命中更害人。"""
    out = []
    for t in terms_s.split('/'):
        t = t.strip()
        if not t:
            continue
        parts = [x.strip() for x in t.split('!')]
        out.append((parts[0], [x for x in parts[1:] if x]))
    return out


def load_cases():
    """→ [(案件名, 别名, 时代, 类型, 当事人, [检索词], 按语)]；字段数不符的行直接报错退出。"""
    out, bad = [], []
    for i, ln in enumerate(CASES.read_text(encoding='utf-8').splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith('#'):
            continue
        f = [x.strip() for x in s.split('｜')]
        if len(f) != 7:
            bad.append('cases.md:%d 字段 %d≠7' % (i, len(f)))
            continue
        out.append(f)
    if bad:
        print('⛔ %s' % '；'.join(bad))
        sys.exit(1)
    return out


def norm_book(b):
    """05 的书名带库内前缀后缀（`01史记文白`／`00资治通鉴`），按语里写的是《史记》——
    不归一化就会把所有《史记·李斯列传》判成「书名待核」（实测曾误报 30+ 条）。"""
    b = re.sub(r'^\d+', '', b.strip())
    return re.sub(r'文白$', '', b)


def load_volumes():
    """05-卷名总表「全部卷目」段（5 列带路径）→ {相对路径: (书, 卷次, 类型, 篇名)}。"""
    m = {}
    if not VOLS.exists():
        return m
    for ln in VOLS.read_text(encoding='utf-8', errors='replace').splitlines():
        parts = ln.rstrip().split('｜')
        if len(parts) == 5 and parts[4].endswith('.md'):
            m[parts[4]] = (norm_book(parts[0]),) + tuple(parts[1:4])
    return m


def load_book_index(volmap):
    """{书名: [(卷次, 篇名), …]}——用于回查案例按语里的《书·卷／篇》是否真在本库。"""
    idx = {}
    for book, juan, _typ, pian in volmap.values():
        idx.setdefault(book, []).append((juan, pian))
    return idx


def check_note_claims(note, books):
    """按语里凡写《书·卷…》的，回查 05：书要在、卷次要在。返回 (必错, 未能核)。

    只判**能证伪**的两项——05 的篇名多是卷题（「卷九十八_列传第六十八」「卷六_魏书六」），
    逐传主名根本核不了，拿它当判据会成片误报（实测《晋书·王敦传》《旧唐书·中宗纪》全被
    判「待核」，而《明史·刑法志》的 05 篇名写作「（篇名未载·内容近刑法，请核对）」）。
    篇名/传主名找不到时计入「未能核」，另行提示，不与必错混为一谈。"""
    bad, unverified = [], []
    for c in re.findall(r'《([^《》]+)》', note):
        parts = [x.strip() for x in c.split('·')]
        book, rest = parts[0], [x for x in parts[1:] if x]
        entries = books.get(book)
        if entries is None:
            # 无「·」的裸《某纪》《某传》是按语里的承前省略（「见《旧唐书·中宗纪》《玄宗纪》」），
            # 不是书名错——判必错会把 8 条正常按语全打成错（实测）。只有**带书名**而书名不在 05
            # 的才是硬错。
            if rest:
                bad.append(c)
            else:
                unverified.append('%s（承前省略书名，未核）' % c)
            continue
        juans = [r for r in rest if r.startswith('卷')]
        if juans and not any(j in (e[0] or '') for j in juans for e in entries):
            bad.append(c)
            continue
        for r in rest:
            if r.startswith('卷'):
                continue
            key = r[:-1] if len(r) > 2 and r.endswith('传') else r
            if not any(key in (p or '') for _j, p in entries):
                unverified.append('%s（05 无此篇名，多为卷题式篇名所致）' % c)
                break
    return bad, unverified


def cite_of(rel, volmap):
    """由路径生成《书名·卷次·篇名》；05 查不到时按目录名兜底（仍不编卷次）。"""
    rec = volmap.get(rel)
    if rec:
        book, juan, _typ, pian = rec
        pian = pian.split('（')[0].strip()
        if not pian or pian.startswith('（'):
            return '《%s·%s》' % (book, juan)
        if juan in pian:                     # 通鉴的篇名自带卷号（后周纪·卷290）
            return '《%s·%s》' % (book, pian)
        return '《%s·%s·%s》' % (book, juan, pian)
    return '《%s》' % norm_book(rel.split('\\')[0])


def excerpt_of(line, variants):
    """命中行摘句：剥引用标记，以首个命中词为锚取一个小窗。"""
    s = line.strip().lstrip('>').strip().lstrip('　')
    pos = [s.find(v) for v in variants if v and v in s]
    if not pos:
        return s[:EXCERPT]
    i = min(pos)
    a = max(0, i - 8)
    seg = s[a:i + EXCERPT]
    return ('…' if a > 0 else '') + seg + ('…' if len(s) > a + EXCERPT else '')


def main():
    cases = load_cases()
    volmap = load_volumes()
    books = load_book_index(volmap)

    all_terms = []
    for _n, _a, _d, _t, _p, terms, _note in cases:
        for t, _e in split_terms(terms):
            if t not in all_terms:
                all_terms.append(t)
    print('案件 %d 个，检索词 %d 个 → 一次全库定位…' % (len(cases), len(all_terms)))
    fsets = rag._clue_files(all_terms)          # {词: {路径}}（繁简/异体已扩展）
    zero = [t for t in all_terms if not fsets[t]]
    if zero:
        print('⚠ 零命中检索词（cases.md 需改词）：%s' % '、'.join(zero))

    rows, n_warn, n_note_bad, n_unver = [], 0, 0, 0
    for name, alias, era, typ, people, terms_s, note in cases:
        spec = split_terms(terms_s)
        terms = [t for t, _e in spec]
        excl = dict(spec)
        tv = {t: rag.expand_terms(t) for t in terms}
        cand = set()
        for t in terms:
            cand |= fsets.get(t, set())
        evid = []
        for fp in sorted(cand):
            rel = str(fp.relative_to(DATA)).replace('/', '\\')
            vtype = (volmap.get(rel) or ('', '', '', ''))[2]
            try:
                text = fp.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            hit_terms, occ, occ_wen = set(), 0, 0
            best = None                      # 选摘句行：先锚首要词，再多词同行，再取最文言
            for no, line in enumerate(text.splitlines(), 1):
                if is_junk_line(line):
                    continue
                vs_hit = [t for t in terms
                          if any(v in line for v in tv[t])
                          and not any(x in line for x in excl.get(t, ()))]
                if not vs_hit:
                    continue
                trans = is_trans(line)
                primary = terms[0] in vs_hit
                score = rag.baihua_score(line)
                for t in vs_hit:
                    hit_terms.add(t)
                    occ += sum(line.count(v) for v in tv[t])
                    if not trans:
                        occ_wen += sum(line.count(v) for v in tv[t])
                key = (0 if primary else 1, -len(vs_hit), score)
                if best is None or key < best[0]:
                    vv = [v for t in vs_hit for v in tv[t] if v in line]
                    best = (key, no, line, vv, trans)
            if best:
                evid.append((len(hit_terms), vtype in NARRATIVE, occ_wen, occ,
                             fp, best, terms[0] in hit_terms))
        # 排序：多词共现 > 叙事卷 > 文言命中数 > 总命中数（同序按路径，保证可复现）
        evid.sort(key=lambda x: (-x[0], not x[1], -x[2], -x[3], str(x[4])))
        # 出处须锚定在**首要检索词**上（如「方孝孺/十族」只取真有「方孝孺」的卷，
        # 否则「十族」会捞到「一百八十族」这类假阳性）。锚定后只剩孤卷时放宽
        # ——「胡惟庸党案」这类窄词只落 1 卷，独苗会把更该看的本纪/列传挤掉。
        anchored = [e for e in evid if e[6]]
        if len(anchored) >= 2:
            evid = anchored
        total_files = len(evid)
        parts = []
        for th, _narr, _ow, _oc, fp, best, _pri in evid[:MAX_EVID]:
            rel = str(fp.relative_to(DATA)).replace('/', '\\')
            _key, no, line, vv, trans = best
            frag = excerpt_of(line, vv)
            parts.append('%s%s:%d「%s」（%s）'
                         % (cite_of(rel, volmap), rel, no, frag,
                            '译文' if trans else '原文'))
        if parts:
            src = '；'.join(parts) + '（全案命中 %d 文件）' % total_files
        else:
            src = '⚠ 本库零命中（检索词：%s）' % '、'.join(terms)
            n_warn += 1
        miss = [t for t in terms if not fsets.get(t)]
        if miss and parts:
            src += '；⚠词「%s」零命中' % '、'.join(miss)
        bad_claims, unver = check_note_claims(note, books)
        if bad_claims:
            src += '；⚠按语书名/卷次错：%s' % '、'.join(bad_claims)
            print('⚠ 按语必错 %s：%s' % (name, '、'.join(bad_claims)))
            n_note_bad += 1
        n_unver += len(unver)
        rows.append((era, name, alias, typ, people, '/'.join(terms), src, note))

    # 分节（朝代＝时代字段「·」前一段）
    head = [
        '# 08-案件索引（自动生成）',
        '',
        '> **用途**：问「某案（胡惟庸案／巫蛊之祸／乌台诗案……）出处在哪」先读本表——按案件名或别名找到行，'
        '「出处」栏已给出《书名·卷次》＋`路径:行号`＋首句摘录，可直接 Read 核验；再用 `python tools\\rag.py <检索词>` 取完整证据块。',
        '> 行格式：`案件名｜时代·类型｜当事人｜检索词｜出处｜按语`。**别名**列的通称（如「胡蓝之狱」「诛十族」）'
        '多为后世叫法，本库未必出现；掉坑时看 `tools/aliases_events.md` 的改写或本行按语。',
        '> 纪律：①「检索词」全部经脚本实测存在于本库，可直接当 Grep/rag 的查询词；② 出处各栏（书名、卷次、路径、行号、摘句）'
        '**由脚本回扫语料得出**，未采信手工表，可逐条 Read 复核；③「按语」是编者提示（非史文），引用必须照录原文并注卷次；'
        '④ `⚠`＝该案（或该词）本库零命中：正史未用此名，须按按语换词，或如实答「本库未载」，**严禁凭记忆补卷次**。',
        '> 摘句尾的（原文）／（译文）：原文段可逐字照引；标（译文）的段落是后人所译，引用须注明。'
        '每条出处取**文言段中的命中行**（同卷有文言命中的不会被译文行顶掉），并优先叙事卷（本纪/列传/编年）——'
        '案件本事多在本纪列传，志/表里的登场常是旁证。',
        '> **取文件（Windows/PowerShell，禁用 `find`）**：本文件即本 skill 目录下的 `08-案件索引.md`，'
        '找不到时在本 skill 目录下 `dir "*案件索引*.md"` 一键命中。',
        '> 生成：`tools/build_case_index.py`（数据源 `tools/cases.md`，新增案件改数据文件即可，无需改脚本）。',
        '> **本库未载的著名案件（实测零命中或命中全是同形假阳性，须如实答「本库未载」，'
        '严禁凭记忆补卷次）**：哭庙案（金圣叹）、苏报案（章炳麟／邹容）、杨月楼案、太原奇案、宋教仁案、'
        '武昌起义、黄花岗起义、巨野教案、马神甫案、贵阳教案——语料止于《清史稿》（宣统）'
        '与《资治通鉴》（五代末），清末民初的报刊案、教案、革命党案多不在内；'
        '「清末四大奇案」四名实测只有杨乃武（清史稿卷四百七十九）、张文祥刺马两案在库，'
        '杨月楼／太原奇案均 0 命中；'
        '另有单名零命中者见对应行按语（陆生楠、张乐行、斧声、胤禩、杨翠喜）。',
        '',
    ]

    order, body = [], []
    cur = None
    for era, name, alias, typ, people, terms_s, src, note in rows:
        sec = era.split('·')[0]
        if sec != cur:
            cur = sec
            order.append(sec)
            if body:
                body.append('')
            body.append('## %s' % sec)
        alias_disp = '别名：%s｜' % alias if alias else ''
        body.append('%s｜%s·%s｜%s｜%s｜%s｜%s%s'
                    % (name, era, typ, people, terms_s, src, alias_disp, note))

    head.append('> 覆盖 %d 案，分 %d 节（%s）；每条 1–2 处实测出处。'
                % (len(rows), len(order), '、'.join(order)))
    if n_warn:
        head.append('> ⚠ 其中 %d 案检索词零命中，见对应行的按语。' % n_warn)
    if n_note_bad:
        head.append('> ⚠ 其中 %d 案按语所写《书·卷》经 05 回查为**必错**，见对应行尾标记。' % n_note_bad)
    if n_unver:
        head.append('> 另有 %d 处按语《书·篇》未能核：05 的篇名多为卷题式（「列传第六十八」），'
                    '逐传主名核不了——**不代表按语错**，引用仍以「出处」栏的实测行为准。' % n_unver)
    head.append('')

    OUT.write_text('\n'.join(head + body) + '\n', encoding='utf-8', newline='\n')
    print('→ %s（%d 案，%d 行）' % (OUT.name, len(rows), len(head) + len(body)))


if __name__ == '__main__':
    main()