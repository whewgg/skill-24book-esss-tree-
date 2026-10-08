# -*- coding: utf-8 -*-
"""
build_story_index.py — 生成 04-故事精选索引.md

扫描全库，按"情节密度"预筛高故事性段落窗口，供讲故事一次定位。
行格式：相对路径｜起始行｜结束行｜情节分｜首句摘录

口径：行级文白判别取文言段行（自动适配各书原文/译文排版差异），
按 2-5 个连续段组窗（80-600 字），情节分 = 情节词×2 +「曰/云」×1，
≥4 分入录，每卷至多 6 窗；占位卷、附录/序已剔除。
"""
import sys

from _lib import ROOT, DATA, is_baihua  # noqa: E402

OUT = ROOT / "04-故事精选索引.md"

# 情节词（简繁并收）：冲突/刑罚/谋略/情绪/异动
ACTION = set('战戰杀殺诛誅戮赦囚奔亡反叛逆降破斩斬擒灭滅怒惧懼悔悟诈詐谋謀刺弑逐嫁娶废廢围圍困败敗胜勝克拔屠坑缢縊鸩鴆磔烹窜竄贬貶谪謫雪冤诬誣谗讒劾奏诏詔敕薨卒崩殒陨产產梦夢异異奇怪神鬼妖灾災变變泣哭叹嘆笑醉')
DIALOG = ('曰', '云')
MAX_PER_FILE = 6

SKIP_NAME = ('附录', '發刊', '发刊', '缀言', '綴言')


def seg_score(text):
    sc = 0
    for ch in ACTION:
        c = text.count(ch)
        if c:
            sc += 2 * min(c, 2)
    for w in DIALOG:
        sc += min(text.count(w), 6)
    return sc



def load_type_map():
    """读 05-卷名总表 的「路径 | 类型」，用于剔除「志／表」这类非叙事卷——04 是
    **故事**精选索引，只应留 列传／本纪／世家／编年／载记 等叙事体。缺 05 时返回
    空表（降级为不过滤）。2026-09-23"""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '05-卷名总表.md')
    m = {}
    if not os.path.exists(p):
        return m
    for ln in open(p, encoding='utf-8'):
        parts = ln.rstrip().split('｜')
        if len(parts) == 5 and parts[4].endswith('.md'):
            m[parts[4]] = parts[2]
    return m
def main():
    rows = []
    typ = load_type_map()
    NONSTORY = {'志', '表'}
    nfiles = 0
    for fp in sorted(DATA.rglob('*.md')):
        if any(k in fp.name for k in SKIP_NAME):
            continue
        try:
            lines = fp.read_text(encoding='utf-8', errors='replace').splitlines()
        except Exception:
            continue
        nfiles += 1
        rel = str(fp.relative_to(DATA)).replace('/', '\\')
        # 志／表类卷不是故事（2026-09-23）：04 里曾占 4038 窗（17%），抽到会讲成「制度/表格」
        if typ.get(rel) in NONSTORY:
            continue
        segs = []
        for i, ln in enumerate(lines, 1):
            t = ln.strip().lstrip('>').strip().strip('　')
            if len(t) < 20 or t.startswith('#'):
                continue
            # 译注/脚注行（`*[3]* …`）与细目/目录行（`△…`、多名并列）不是正文段落——曾混入
            # 窗口首句（04 里 511 条、2.15%），抽到后讲出来是「制度/目录」而非故事。2026-09-23
            if t.startswith('*[') or t.startswith('△'):
                continue
            _parts = t.split()
            if len(_parts) >= 4 and all(2 <= len(x) <= 4 for x in _parts) and ('，' not in t and '。' not in t):
                continue
            if is_baihua(t):
                continue
            segs.append((i, t))
        if not segs or sum(len(t) for _, t in segs) < 300:
            continue
        wins, cur, cur_chars = [], [], 0
        for ln_no, t in segs:
            if cur and (ln_no - cur[-1][0] > 6 or cur_chars + len(t) > 600):
                if cur_chars >= 80:
                    wins.append(cur)
                cur, cur_chars = [], 0
            cur.append((ln_no, t))
            cur_chars += len(t)
            if len(cur) >= 5:
                if cur_chars >= 80:
                    wins.append(cur)
                cur, cur_chars = [], 0
        if cur and cur_chars >= 80:
            wins.append(cur)
        # 窗口末行＝末段行 + 4（通常正好带出紧随其后的白话译文），但**不得越过下一个窗口
        # 的首行**——否则两个窗口行段重叠，索引就不再是"划分"，Read 区间会读到下一窗的开头。
        ends = []
        for i, w in enumerate(wins):
            nxt = wins[i + 1][0][0] if i + 1 < len(wins) else None
            e = min(w[-1][0] + 4, len(lines))
            if nxt is not None:
                e = min(e, nxt - 1)
            ends.append(max(e, w[-1][0]))          # 至少覆盖末段本行
        scored = []
        for w, e in zip(wins, ends):
            sc = sum(seg_score(t) for _, t in w)
            if sc >= 4:
                scored.append((sc, w[0][0], e, w[0][1][:24]))
        scored.sort(key=lambda x: -x[0])
        for sc, s, e, f in scored[:MAX_PER_FILE]:
            rows.append((rel, s, e, sc, f))

    out = ['# 04-故事精选索引（自动生成）', '',
           '> **讲史抽签专用**：**不要自己挑书挑窗**——用 `python tools\\pick_story.py` 抽签（分层等概率、自动轮换、可指定朝代），它直读本文件。用法与章法见 `SKILL.md`「讲史流程」。',
           '> 行格式：`路径｜起行｜止行｜情节分｜首句`（情节分≥25 为佳，默认门槛）。「起行–止行」为**窗口行**范围，不含 `## 书名` 标题行；'
           '末行通常带出紧随的白话译文（至多 +4 行），且不越过**位置相邻**窗口的首行。'
           '⚠ 本文件的行**按情节分排序**（每卷只留最高的若干窗），故同卷几条的行段看上去会互相交叉——那是排序造成的，不是窗口重叠。'
           '「N 窗」＝区间内窗口数，三者自洽，可直接 Read。',
           '> 口径：文言段每 2-5 段组窗（80–600 字）；情节分＝情节词×2＋「曰/云」×1，≥4 分入录，每卷至多 6 窗；'
           '文件名含「附录／发刊／缀言」者整卷剔除（**不包含「序」**）。',
           '> **取文件（Windows/PowerShell，禁用 `find`）**：本文件即本 skill 目录下的 `04-故事精选索引.md`，找不到时在本 skill 目录下 `dir "*故事精选索引*.md"` 一键命中。',
           '> 生成：`tools/build_story_index.py`。语料更新后重跑（或跑 `tools/make_indexes.py` 一键重建）。', '',
           # ⚠ 标题措辞别再写"先在此选书"——那是一句**自相矛盾的授权**：
           #   本文件第 2 行明说"不要自己挑书挑窗"，标题却邀请按目录选书，
           #   模型会据此自己读 04 挑窗开讲（然后又被 SKILL.md 要求抽签 → 讲两遍）。
           '# 书节目录（**只为定位行号，不是选书依据**——选书一律走 `pick_story.py` 抽签）', '',
           '__BOOK_INDEX__', '']

    body = []
    book_spans = []  # [book, 首窗行, 末窗行]（body 内 1-based 索引，均指窗口行、不含书节标题行）
    cur_book = None
    for rel, s, e, sc, f in rows:
        book = rel.split('\\')[0]
        if book != cur_book:
            if body:
                body.append('')          # 书节之间空一行，便于阅读与按行定位
            cur_book = book
            body.append('## %s' % book)
            book_spans.append([book, len(body) + 1, len(body) + 1])   # 首窗行＝标题行下一行
        book_spans[-1][2] = len(body) + 1
        body.append('%s｜%d｜%d｜%d｜%s' % (rel, s, e, sc, f))

    # 计算 head 行数（含占位符占 1 行），修正各书书节的行号
    head_len = 0
    for i, ln in enumerate(out):
        if ln == '__BOOK_INDEX__':
            head_len = i  # 占位符所在索引即其之前的行数
            break
    idx_lines = ['%s｜%d–%d｜%d 窗' % (b, lo + head_len + len(book_spans) + 1,
                                   hi + head_len + len(book_spans) + 1,
                                   hi - lo + 1)
                 for b, lo, hi in book_spans]
    out[out.index('__BOOK_INDEX__')] = '\n'.join(idx_lines)

    OUT.write_text('\n'.join(out + body) + '\n', encoding='utf-8', newline='\n')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('files=%d windows=%d' % (nfiles, len(rows)))


if __name__ == '__main__':
    main()
