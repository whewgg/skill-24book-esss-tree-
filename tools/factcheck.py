# -*- coding: utf-8 -*-
"""
factcheck.py — 史实稿件机械核查（配合 tools/facts/*.md 事实卡）

用法：
  python tools/factcheck.py 稿件.txt --person 海瑞
  python tools/factcheck.py --list                          # 只列可用事实卡
  type 稿件.txt | python tools/factcheck.py - --person 海瑞

检查项（机械可判者）：
  ① 纪年/公元是否落在人物生卒区间内（超出生年、卒年之后 → FAIL）
     ⚠ 两条约定（2026-09-25 补，为西汉及更早人物）：
       · **公元前记负数**：`died: -157` ＝卒于公元前 157 年（文帝）。此前 `isdigit()` 只认
         无符号数，负数卡一律判"缺 born/died"→ 退码 2（＝卡不可用），西汉人物整张卡直接失效。
       · **`born: 0` ＝ 生年无据**（正史不载年寿），跳过"早于生年"判定——不要当成公元 0 年。
         西汉／先秦人物若生年不载，用 0；已知则写负数。
  ② 事实卡 banned 表命中的错误用词（如"进士""板子""沈括访王安石"）→ FAIL，并给出正确写法与出处
  ③ 地名与任职窗口冲突（如"江南/应天"出现在 1569 年之外）→ WARN
  ④ 公案/断案类题材缺"虚构"标注 → WARN
  ⑤ ***同名异人***（卡内 `same_name:` 登记）：稿件若出现**另一个同名者**的特征词
     （如写西晋李密却出现「瓦岗/蒲山公」）→ FAIL；无论如何都 INFO 列出全部同名者 → 防"只找到一个人"
  ⑥ 年号未附公元、官职用词是否在 card 的 title_ok 内 → INFO 提示人工确认
年号表：内置明清年号；**跨朝人物请在卡里补 `era: 年号|元年公元年`（如 `era: 熙宁|1068`）**，
        否则宋、唐等年号不被识别，纪年检查会静默失效。
        **公元前年号写负数**：`era: 高祖|-206` → 高祖三年＝-204（算式 a+n-1 对负数同样成立）。
退出码：0 = 无 FAIL；1 = 有 FAIL；2 = 用法错／事实卡不可用（缺生卒、卡不存在等）。
注意：本工具只做机械比对，**不能代替 Read 原文**；判定结果仍须回查《书名·卷次·行号》。
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / 'tools' / 'facts'

ERA = {'洪武': 1368, '建文': 1399, '永乐': 1403, '洪熙': 1425, '宣德': 1426, '正统': 1436, '景泰': 1450,
       '天顺': 1457, '成化': 1465, '弘治': 1488, '正德': 1506, '嘉靖': 1522, '隆庆': 1567, '万历': 1573,
       '泰昌': 1620, '天启': 1621, '崇祯': 1628, '顺治': 1644, '康熙': 1662, '雍正': 1723, '乾隆': 1736,
       '嘉庆': 1796, '道光': 1821, '咸丰': 1851, '同治': 1862, '光绪': 1875, '宣统': 1909}
# 事实卡认可的键位。不在此列＝拼错，必须报出来——否则对应检查会静默失效
# （如把 born 写成 bron，纪年基线就没了）。
# ⚠ 2026-09-22：`alias` 与 `aliases` **都接受**并归一存到 `aliases`——沈括/王安石/海瑞三张卡
#   用的是复数 `aliases`，而原名单只登记了单数 `alias`，于是这三张卡全被报「无法识别的键名」
#   （`aliases` 进 _unknown）。键名正则要求全小写（见 load_card）。
# ⚠ 2026-09-25：`sourced_theme` 归入**多值键**。它原先落在标量分支，于是卡里写 6 行
#   `sourced_theme:` 只有**最后一行**被保留（包拯卡 4 条丢 3 条），"可用的正史支点" INFO
#   长期只印一条。读取端本来就用 `as_list()`（期望可多值），是键位归类写错了。
LIST_KEYS = ('anchor', 'banned', 'office_window', 'note', 'alias', 'aliases', 'era', 'same_name',
             'sourced_theme')
CARD_KEYS = set(LIST_KEYS) | {'born', 'died', 'died_note', 'identity', 'title_ok',
                              'fiction_warning'}


from _lib import cn2int  # noqa: E402


def load_card(name):
    p = FACTS / ('%s.md' % name)
    if not p.exists():
        return None
    card = {k: [] for k in LIST_KEYS if k != 'alias'}   # alias 归一到 aliases
    unknown = []
    for ln in p.read_text(encoding='utf-8').splitlines():
        if ln.startswith('#') or ln.startswith('>') or not ln.strip():
            continue
        m = re.match(r'^([a-z_]+)\s*:\s*(.+)$', ln)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if k == 'alias':
            k = 'aliases'          # 单复数据兼容（卡里两种写法都合法）
        if k in LIST_KEYS:
            card[k].append(v)
        elif k in CARD_KEYS:
            card[k] = v
        else:
            unknown.append(k)
    if unknown:
        card['_unknown'] = sorted(set(unknown))
    return card


def find_years(text, era=None):
    """返回 [(年号或'公元', 年份, 原文片段, 行号)]。
    era：年号→元年公元年 的映射；卡内 `era: 熙宁|1068` 行会并入（见 load_card）。"""
    era = ERA if era is None else era
    names = '|'.join(re.escape(name) for name in sorted(era, key=len, reverse=True))
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        spans = []
        if names:
            for m in re.finditer(r'(%s)\s*(元|[〇○零一二两三四五六七八九十百0-9]{1,4})\s*年' % names, line):
                spans.append(m.span())
                n = 1 if m.group(2) == '元' else cn2int(m.group(2))
                if n:
                    out.append((m.group(1), era[m.group(1)] + n - 1, m.group(0), i))
        for m in re.finditer(r'公元\s*(前\s*)?([1-9]\d{0,3})\s*年|(?<![\d万])([1-9]\d{2,3})\s*年', line):
            if any(start <= m.start() < end for start, end in spans):
                continue
            year = int(m.group(2) or m.group(3))
            out.append(('公元', -year if m.group(1) else year, m.group(0), i))
    seen, uniq = set(), []
    for era, y, frag, ln in out:
        if (y, ln) in seen:
            continue
        seen.add((y, ln))
        uniq.append((era, y, frag, ln))
    return uniq


def main():
    # ⚠ 必须在任何 print 之前 reconfigure：本机控制台默认 GBK，被 subprocess 捕获时
    #   输出会变成 GBK 字节，调用方按 UTF-8 解码即乱码（selftest 的 `--list` 检查就是这样
    #   被判失败的）。一次重构曾把本行弄丢，故固定在 main 的第一句。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('src', nargs='?', help='稿件文件，或 - 从 stdin 读取')
    parser.add_argument('--person', help='事实卡对应的人名')
    parser.add_argument('--list', action='store_true', help='列出可用事实卡')
    args = parser.parse_args()
    if args.list or not sys.argv[1:]:
        cards = sorted(p.stem for p in FACTS.glob('*.md')) if FACTS.is_dir() else []
        print('可用事实卡：%s' % ('、'.join(cards) or '（无，请先在 tools/facts/ 建卡）'))
        if not sys.argv[1:]:
            print(__doc__)
        return 0
    src, person = args.src, args.person
    if not src or not person:
        print('需要稿件路径和 --person <人名>（从 stdin 读取请用 -）')
        return 2
    if any(c in person for c in '/\\') or person in ('.', '..'):
        print('--person 只能是事实卡名称，不能包含路径')
        return 2
    try:
        card = load_card(person)
    except OSError:
        print('无法读取事实卡：%s' % person)
        return 2
    if not card:
        print('未找到事实卡 tools/facts/%s.md —— 先建卡再核稿' % person)
        return 2

    # 生卒是纪年检查的基线：缺了整条 ① 项就失效。早先是 KeyError 直接崩，
    # 而崩溃的退出码又恰好是 1（与"有硬错"同码）——等于把崩溃伪装成正常 FAIL。
    # 允许**负数**（公元前）：`isdigit()` 只认无符号数，西汉人物的 `-157` 会被误判成缺值。
    bad_keys = [k for k in ('born', 'died')
                if not re.fullmatch(r'-?\d+', str(card.get(k, '')).strip())]
    if bad_keys:
        print('FAIL 事实卡 tools/facts/%s.md 缺有效的 %s —— 生卒是纪年检查的基线，请先补卡'
              % (person, '、'.join(bad_keys)))
        return 2

    try:
        text = sys.stdin.read() if src == '-' else Path(src).read_text(encoding='utf-8', errors='replace')
    except OSError:
        print('无法读取稿件：%s' % src)
        return 2
    if not text.strip():
        print('稿件为空，无法核查')
        return 2
    born, died = int(card['born']), int(card['died'])

    # 年号表：内置明清表 + 卡内 `era: 年号|元年公元年`（卡可补唐、宋等，避免跨朝漏检纪年）
    era = dict(ERA)
    extra_era = []
    for item in card.get('era', []):
        # 负数＝元年在公元前（`era: 高祖|-206`）：算式 a+n-1 对负数同样正确
        # （高祖三年＝-206+3-1＝-204）。此前正则只认 `\d{3,4}`，西汉年号一律被静默丢弃。
        m = re.match(r'^(.+?)\s*\|\s*(-?\d{3,4})$', item)
        if m:
            era[m.group(1).strip()] = int(m.group(2))
            extra_era.append(m.group(1).strip())
    fails, warns, infos = [], [], []
    for k in card.get('_unknown', []):
        warns.append('事实卡里有无法识别的键「%s」——键名拼错会让对应检查静默失效' % k)

    # ① 生卒区间
    years = find_years(text, era)
    for era, y, frag, ln in years:
        if y > died:
            fails.append('第%d行「%s」＝%d：**人物已卒**（卒于 %d，%s）' % (ln, frag, y, died, card.get('died_note', '')))
        elif y == died:
            warns.append('第%d行「%s」＝卒年 %d：情节须在卒前，或明确写为身后（%s）' % (ln, frag, y, card.get('died_note', '')))
        elif y < born:
            # born == 0 ＝「生年无据」（正史不载年寿）：无下界可比，跳过（对公元前人物同样适用，
            # 否则 0 会把「公元前 203 年」这类正常的更早年份一律判成"早于生年"）。
            if not born:
                continue
            fails.append('第%d行「%s」＝%d：早于生年（约 %d，正史未载确切生年）' % (ln, frag, y, born))
    if not years:
        infos.append('文中未检出纪年/公元——建议每个事件标「年号（公元）」以便核对')
    elif not any(e != '公元' for e, _, _, _ in years):
        infos.append('只用了公元纪年——史实向写作宜加年号（如「万历十五年（1587）」）')

    # ② 错误用词
    for item in card['banned']:
        m = re.match(r'^(.*?)\s*=>\s*(.+)$', item)
        if not m:
            continue
        pat, fix = m.group(1).strip(), m.group(2).strip()
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(pat, line):
                fails.append('第%d行命中错误用词「%s」→ 应作：%s' % (i, re.search(pat, line).group(0), fix))

    # ③ 地名 × 任职窗口
    lines = text.splitlines()
    for item in card['office_window']:
        # 窗口两端都允许负数（公元前）；上界可省（单点＝只那一年）。
        # ⚠ 原先用 `[\d\-]+` + `partition('-')` 拆区间，遇到 `-196--180` 会拆出空串 →
        #   int('') 抛异常 → 退出码 1，把"解析崩溃"伪装成"史实有硬错"（调用方据此去改对的稿子）。
        m = re.match(r'^(.*?)\s*=>\s*(-?\d+)(?:\s*-\s*(-?\d+))?\s*(.*)$', item)
        if not m:
            continue
        pat, desc = m.group(1).strip(), m.group(4).strip()
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) is not None else lo
        win = '%d-%d' % (lo, hi)
        # 只比对**出现该地名的那几行**里的年份。原先把全文所有年份都拿去比单一窗口，
        # 文档一旦跨多个任职就必然误报（WARN 噪音会淹没真问题）。
        hit_lines = {i for i, line in enumerate(lines, 1) if re.search(pat, line)}
        if not hit_lines:
            continue
        near = [y for y in years if y[3] in hit_lines]
        if not near:
            warns.append('文中出现「%s」但同段无年份——该处任职：%s' % (pat, desc))
            continue
        for _era, y, frag, ln in near:
            if not (lo <= y <= hi):
                warns.append('第%d行「%s」＝%d，与「%s」的任职窗口 %s 冲突（%s）'
                             % (ln, frag, y, pat, win, desc))

    # ④ 题材标注
    if card.get('fiction_warning') and re.search(r'断案|审案|判案|公案|私访|微服', text):
        if not re.search(r'虚构|传说|小说|演绎|文学|戏说|改编', text):
            warns.append('属公案/断案题材但未见"虚构/传说"标注——%s' % card['fiction_warning'])

    # ⑤ 同名异人：重名是"找到一个人就开写"型错误的最大来源——检索时先撞见
    #    《陈情表》的李密，笔下却写成了瓦岗的李密。卡里登记了 `same_name:` 就
    #    逐条比对特征词；命中即 FAIL（串人是硬错），并**无论如何**列出全部同名者，
    #    逼出"我是不是只找到了这一个人"这一步。
    for item in card.get('same_name', []):
        parts = [p.strip() for p in item.split('｜') if p.strip()]
        if len(parts) < 2:
            warns.append('事实卡 same_name 条目应写「身份｜特征词,逗号分隔｜出处」，收到：%s' % item)
            continue
        who = parts[0]
        marks = [m.strip() for m in re.split(r'[,，]', parts[1]) if m.strip()]
        where = parts[2] if len(parts) > 2 else '出处未注'
        infos.append('同名异人：%s（%s）——稿件若写的是他，请换卡/换人' % (who, where))
        for mk in marks:
            for i, line in enumerate(lines, 1):
                if mk in line:
                    fails.append('第%d行出现「%s」——这是**另一个「%s」**（%s）的特征词；'
                                 '本卡写的是「%s」，两者不可互串' % (i, mk, person, who, card.get('identity', '本卡人物')[:24]))
                    break

    # ⑥ 提示
    def as_list(v):
        return v if isinstance(v, list) else ([v] if v else [])

    if card.get('title_ok'):
        infos.append('官职用词应限于卡片白名单：%s' % as_list(card['title_ok'])[0])
    for t in as_list(card.get('sourced_theme')):
        infos.append('可用的正史支点：%s' % t)

    print('# 事实核查：%s（生约 %d – 卒 %d）｜稿件 %s' % (person, born, died, src))
    if extra_era:
        print('# 卡内补充年号：%s' % '、'.join(extra_era))
    print('# 检出纪年 %d 处：%s' % (len(years), '、'.join('%s%d' % (e if e != '公元' else '', y) for e, y, _, _ in years) or '无'))
    for tag, arr in (('FAIL', fails), ('WARN', warns), ('INFO', infos)):
        for x in arr:
            print('%s %s' % (tag, x))
    print('# 结论：%s' % ('存在 %d 处硬错，必须改' % len(fails) if fails else '无硬错（%d 处待人工确认）' % len(warns)))
    print('# 事实卡锚点（改稿以这些为准）：')
    for a in card.get('anchor', []):
        print('   - %s' % a)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
