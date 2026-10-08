# -*- coding: utf-8 -*-
"""
build_volume_index.py — 生成 05-卷名总表.md

为全部 4408 个文件建立「书名｜卷次｜类型｜篇名｜路径」映射。
篇名来源：①文件名中的实质篇名（如「五帝本纪第一」「释老志」）；
②文件名仅为占位名（如「志第二十」「帝纪第三」）时，从正文起始行提取短标题（如「释老十」）。
并附「主题速查」：按关键词把志/纪/传/表归类到主题（宗教/经济/法律/职官/地理/军事/天文历法/典籍/礼乐/民族外交等）。

用法: python tools/build_volume_index.py
"""
import re
import sys
from pathlib import Path
from collections import defaultdict

from _lib import ROOT, DATA  # noqa: E402

OUT = ROOT / "05-卷名总表.md"
OUT01 = ROOT / "01-卷目速查.md"   # 与 05 同一次运行产出：01 的路径就是 05 的第 5 列

PLACEHOLDER = re.compile(r'^(志|帝纪|本纪|列传|表)第?[一二三四五六七八九十百]+[上下]?$')
# 篇名候选中的非篇名词（后缀/版本标记）
NOISE_PARTS = {'文白', '译文', '白话', 'en', '译', '现代文翻译', '现代汉语', '文白对照',
               '主题', '人物', '上', '下', '中', '附'}

# 篇名完全缺失的卷（如明史部分志卷），按正文强/弱特征词判定所属志类
# 强词（专有、高置信）×3，弱词（通用）×1，取最高分；阈值 3
CONTENT_HINTS = (
    ('食货', ('户口', '田制', '赋税', '漕运', '盐法', '钱钞', '荒政', '俸饷', '会计', '矿税', '漕粮', '常平仓'),
     ('屯田', '仓', '漕', '盐', '钞', '赋', '税')),
    ('选举', ('科目', '荐举', '铨选', '科举', '进士', '举人', '武科', '荫叙', '书院'),
     ('学校', '考', '选', '举')),
    ('职官', ('宗人府', '都察院', '布政司', '按察司', '六部', '内阁', '翰林院', '国子监', '都指挥使司', '通政司', '大理寺'),
     ('官制', '品级', '司', '寺', '监')),
    ('地理', ('形胜', '疆域', '山川', '卫所', '州府', '郡县'),
     ('州', '县', '山', '水', '疆')),
    ('礼', ('祭祀', '郊祀', '庙祀', '谥法', '大礼', '朝贺', '封禅'),
     ('祭', '祀', '礼', '庙')),
    ('乐', ('乐章', '乐器', '雅乐', '俗乐', '律吕', '宫调'),
     ('乐', '律', '吕', '舞')),
    ('刑法', ('三法司', '诏狱', '狱讼', '赎刑', '刑制', '五刑'),
     ('刑', '狱', '罪', '律')),
    ('艺文', ('图书', '典籍', '书录', '艺文', '目录', '经籍', '书目', '四部'),
     ('书', '文', '集', '篇')),
    ('五行', ('灾异', '日食', '水旱', '火灾', '地震', '雷震', '疾疫'),
     ('灾', '异', '星', '变')),
    ('兵志', ('兵制', '京营', '马政', '驿传', '军器', '卫所兵'),
     ('兵', '营', '马', '军')),
    ('天象历法', ('时宪', '历法', '星变', '日躔', '月离', '天象', '交食'),
     ('历', '象', '度', '星')),
)


def content_theme(text):
    best, score = '', 0
    for label, strong, weak in CONTENT_HINTS:
        sc = 3 * sum(text.count(k) for k in strong) + sum(text.count(k) for k in weak)
        if sc > score:
            best, score = label, sc
    return (best, score) if score >= 3 else ('', 0)

# 清史稿表卷的实质表名（2026-09-22 补）：这些卷的文件名只到「表十X」，正文又是「（表略）」占位，
# 于是篇名只剩「表十四」这种编号——**按「大学士年表」「疆臣年表」Grep 会零命中**。
# 依据＝机读稿页眉实测（如 260 册「表九 清史稿卷一百六十九 诸臣封爵世表二」；
# 262 册「表十 四 大学士年表一」），并以卷第206–208＝疆臣年表十/十一/十二、卷210＝藩部世表二 交叉印证。
QING_TABLE_NAME = {
    '卷一百六十九': '诸臣封爵世表二', '卷一百七十': '诸臣封爵世表三',
    '卷一百七十一': '诸臣封爵世表四', '卷一百七十二': '诸臣封爵世表五',
    '卷一百七十三': '诸臣封爵世表六',
    '卷一百七十四': '大学士年表一', '卷一百七十五': '大学士年表二',
    '卷一百九十七': '疆臣年表一', '卷一百九十八': '疆臣年表二', '卷一百九十九': '疆臣年表三',
    '卷二百': '疆臣年表四', '卷二百一': '疆臣年表五', '卷二百二': '疆臣年表六',
    '卷二百三': '疆臣年表七', '卷二百四': '疆臣年表八', '卷二百五': '疆臣年表九',
    '卷二百六': '疆臣年表十', '卷二百七': '疆臣年表十一', '卷二百八': '疆臣年表十二',
    '卷二百一十': '藩部世表二', '卷二百十一': '藩部世表三',
    '卷二百十二': '交聘年表一', '卷二百十三': '交聘年表二',
}

THEMES = [
    ('宗教·信仰', ('释老', '礼志', '乐志', '祭祀', '郊祀', '符瑞', '灵征', '封禅', '礼仪志', '符瑞志')),
    ('经济·财政', ('食货', '平准', '赋税', '钱币', '漕运', '河渠', '沟洫')),
    ('法律·刑罚', ('刑法', '刑罚')),
    ('职官·制度', ('百官', '职官', '官氏', '宰相世系', '舆服', '车服', '仪卫', '仪卫志')),
    ('文化·学术·教育', ('经籍', '艺文', '儒林', '儒学', '文学', '文苑', '文艺', '学校', '选举',
                  '隐逸', '方技', '艺术', '伶官', '道学', '忠义', '孝义', '卓行', '文行')),
    ('地理·疆域', ('地理', '地形', '州郡', '郡县', '郡国', '疆域')),
    ('军事', ('兵志', '兵卫', '军制', '兵卫志')),
    ('天文·历法·灾异', ('天文', '律历', '历志', '天象', '五行', '灾异')),
    ('民族·外交', ('四夷', '外国', '西域', '北狄', '南蛮', '东夷', '突厥', '回鹘', '吐蕃',
                '契丹', '鞑靼', '瓦剌', '女真', '高丽', '日本', '安南', '大理', '西夏', '蛮夷')),
]


# 志类专属词（篇名/标题含之即为志，补救"占位篇名"与"篇名未载"两类漏判）
ZHI_WORDS = ('食货', '刑罚', '官氏', '释老', '灵征', '天象', '地形', '律历', '职官', '选举',
             '地理', '艺文', '经籍', '五行', '刑法', '百官', '舆服', '河渠', '天文', '祭祀',
             '郊祀', '符瑞', '沟洫', '平准', '时宪', '礼乐', '礼仪', '音乐', '乐志', '礼志',
             '兵志', '兵卫', '舆服志', '选举志', '职官志', '百官志', '地理志', '经籍志',
             '艺文志', '天文志', '五行志', '律历志', '刑法志', '食货志', '礼四', '乐五')


def kind_of(name, title):
    s = name + title
    if name.startswith(('汉纪', '唐纪', '周纪', '秦纪', '魏纪', '晋纪', '宋纪', '齐纪', '梁纪',
                        '陈纪', '隋纪', '后梁纪', '后唐纪', '后晋纪', '后汉纪', '后周纪')):
        return '编年'
    if '载记' in s:
        return '载记'
    if '附录' in s or '缀言' in s or '发刊' in s or name in ('序', '跋'):
        return '附录'
    if '帝纪' in s or '本纪' in s:
        return '本纪'
    # ⚠ 顺序要紧：「列传」必须先于「志」判定。
    # 否则「列传第八十九_释老」「宋书·列传第二」这类会因篇名恰好含志类词（释老）或
    # 内容推测词（兵志）被误归为「志」（实测曾错 811 条）。
    if '列传' in s or '传' in s:  # 「刘表列传」类须先于「表」判定
        return '列传'
    if '志' in s or '书第' in s or any(k in s for k in ZHI_WORDS):  # 史记八书等同志
        return '志'
    if '表' in s:
        return '表'
    return '其他'


def kind_of_strict(name, title, rel):
    """以**文件名**为主判类型，篇名推测不参与类型判定（避免「内容近兵志」把列传判成志）。"""
    base = re.sub(r'^\d+_', '', Path(rel).stem)
    # 先看文件名本身的体例词（最可靠），再看正文标题、最后看篇名
    for src in (base, title, name):
        if not src:
            continue
        if src.startswith(('汉纪', '唐纪', '周纪', '秦纪', '魏纪', '晋纪', '宋纪', '齐纪', '梁纪',
                           '陈纪', '隋纪', '后梁纪', '后唐纪', '后晋纪', '后汉纪', '后周纪')):
            return '编年'
        if '载记' in src:
            return '载记'
        if '帝纪' in src or '本纪' in src:
            return '本纪'
        if '列传' in src:
            return '列传'
        if re.search(r'(^|[·_])志', src) or '书第' in src:
            return '志'
        if re.search(r'(^|[·_])表', src):
            return '表'
    if '载记' in name:
        return '载记'
    if '附录' in rel or '缀言' in rel or '发刊' in rel:
        return '附录'
    # 文件名无体例词：仅当篇名**不是**推测串时才用篇名判；推测串不参与
    if name and not name.startswith('（篇名未载'):
        return kind_of(name, title)
    return '其他'


def extract(fp):
    """返回 (篇名, 标题)。篇名优先取文件名中的实质部分，缺名/占位时依次去正文短标题、内容判定。"""
    stem = fp.stem
    # 资治通鉴（编年体）：篇名 = 纪名·卷号
    if '资治通鉴' in fp.name:
        ji = fp.parent.name
        mj = re.search(r'卷(\d+)', fp.name)
        return ('%s·卷%s' % (ji, mj.group(1)) if mj else ji), ''
    # 统一按 · 与 _ 切分文件名（各书命名规则不一），剥离序号与版本后缀
    s = re.sub(r'^\d+_', '', stem)
    parts = re.split(r'[·_]', s)
    tail = [p for p in parts[1:] if p and p not in NOISE_PARTS]
    cand = '_'.join(tail).strip()
    title = ''
    try:
        first = fp.read_text(encoding='utf-8', errors='replace').splitlines()[:24]
    except Exception:
        first = []
    for ln in first:
        t = ln.strip().strip('#').strip()
        if t.startswith('卷') and len(t) < 40:
            title = t
            break
    name = cand
    if not name or PLACEHOLDER.match(name):
        for ln in first:
            t = ln.strip().lstrip('>').strip().strip('　')
            if not t or t.startswith('#') or len(t) > 30 or '。' in t or '，' in t:
                continue
            # ⚠ 无汉字的行（`---`/`***`）不是篇名；`**` 开头的（**【原文】**/**【译文】**/**【名单】**）
            #   是结构标记，也不是篇名——否则宋史补入卷会被 `---`、`**【原文】**` 逐个顶掉
            #   （252–257 实测踩过）。
            if t.startswith('*') or not re.search(r'[一-鿿]', t):
                continue
            # 子目行（含空格/括号/○等）不是篇名，跳过，留给内容判定
            if ' ' in t or '(' in t or '（' in t or t.startswith('○') or '、' in t:
                continue
            if t.startswith('卷') or t == '译文':
                continue
            name = t
            break
    # ⚠ 「列传第X」「本纪第X」等**卷序篇名**是有效信息，不得被内容推测覆盖；
    #   但可以补一句"本卷含谁"的提示（用户要求：有的卷要写里面有什么，否则只能逐卷打开看）。
    #   触发面：卷题级篇名，或过短的类传/合传题名（「侯张薛」「周三臣」「武二王」这类不点开不知道是谁）。
    hint = bio_list_hint(first)
    voltitle = re.fullmatch(r'(列传|本纪|帝纪|载记|表|志)第?[一二三四五六七八九十百]+[上下]?', name or '')
    if hint and name and hint not in name and (voltitle or len(name) <= 6):
        name = '%s（含：%s）' % (name, hint)
    if name and voltitle:
        return name, title
    if not name or PLACEHOLDER.match(name):
        # 篇名完全缺失（如明史部分志卷）：按正文强/弱特征词判定，明示为推测
        head = ''
        try:
            head = fp.read_text(encoding='utf-8', errors='replace')[:3000]
        except Exception:
            pass
        label, sc = content_theme(head)
        if label:
            name = '（篇名未载·内容近%s，请核对）' % label
    return name or cand or '', title


def bio_list_hint(first):
    """从卷头部提取「本卷含哪些传主」提示（只认高置信句式，认不出返回空）。

    ① `**【A B C】**` 名单行：宋史补入卷「**【王景 王晏 郭从义 李洪信 …】**」
    ② △ 名单行：宋史「△韩通 李筠 李重进」、宋书「> △彭城王义康 南郡王义宣」
    ③ 合传说明行：新唐书「> 侯君集、张亮、薛万均等人的合传」
    ④ 纯文本名单行（南史/宋书/北齐书/清史稿…）：剥（世系）后 ≥2 个人名，如
        「刘穆之（曾孙祥 从子秀之） 徐羡之（…） 傅亮（…） 檀道济（…）」；判据＝
        无句读标点、剥括号后全是 2-4 字 CJK 词
    ⑤ `## 短标题`（本纪帝王/国名）：新元史「## 宪宗」、辽史「## 太祖下」
    为什么：卷题级篇名（「列传第九十七」）不含传主名，AI 只能一卷一卷打开看——
    把名单写进 05 的篇名列，一次 Grep 就能定位（2026-09-22 用户要求）。
    """
    for ln in first:
        s = ln.strip()
        if s.startswith('>'):
            s = s.lstrip('> ').strip()
        if not s or s.startswith('# '):
            continue
        # ① **【A B C】**（宋史补入卷）
        m = re.match(r'^\*\*【(.+?)】\*\*$', s) or re.match(r'^【(.+?)】$', s)
        if m:
            toks = [re.sub(r'〔[^〕]*〕', '', x).strip()
                    for x in re.split(r'[、，,·\s　]+', m.group(1)) if x.strip()]
            toks = [x for x in toks if re.fullmatch(r'[一-鿿]{2,5}', x)]
            if len(toks) >= 2:
                return '、'.join(toks[:8])
            continue
        if '△' in s:
            s2 = s.split('△', 1)[1].strip()
            toks = [t for t in re.split(r'[、，,·\s　]+', s2) if t]
            toks = [t for t in toks if re.fullmatch(r'[一-鿿]{2,5}', t)]
            if len(toks) >= 2:
                return '、'.join(toks[:4])
            continue
        m = re.match(r'^([一-鿿]{2,5}(?:[、，,][一-鿿]{2,5}){1,5})等[^，。]{0,6}合传$', s)
        if m:
            return '、'.join(re.split(r'[、，,]', m.group(1))[:4])
        # ④b 单传主＋附传括号：整行形如「苏轼（子过）」「蔡毓荣〔杭爱〕」——只有一个主名，
        #     规则④的「≥2 词」门槛会挡掉它（宋史补入卷大量此式，2026-09-22 实测踩过）
        m = re.fullmatch(r'([一-鿿]{2,4})[（(〔][^）)〕]*[）)〕]', s)
        if m:
            return m.group(1)
        # ④ 纯文本名单行：无句读、剥（世系）后 ≥2 个 2-4 字 CJK 词
        if not s.startswith('*') and not re.search(r'[，。；：！？、·]', s):
            s2 = re.sub(r'（[^）]*）', '', s)
            toks = [x for x in re.split(r'\s+', s2.strip()) if x]
            toks = [x for x in toks if re.fullmatch(r'[一-鿿]{2,4}', x)]
            if len(toks) >= 2:
                return '、'.join(toks[:8])
        # ⑤ ## 二级短标题（本纪帝王/国名）
        m = re.match(r'^##\s*([一-鿿]{2,6})\s*$', s)
        if m:
            return m.group(1)
    return ''


def main():
    entries = []  # (book, juan, kind, name, rel)
    for fp in sorted(DATA.rglob('*.md')):
        rel = str(fp.relative_to(DATA)).replace('/', '\\')
        book = rel.split('\\')[0]
        name, title = extract(fp)
        juan = title.split('·')[0] if '·' in title else ''
        if not juan:
            mj = re.search(r'卷[一二三四五六七八九十百千\d]+[上下]?', fp.stem)
            juan = mj.group(0) if mj else ''
        kind = '附录' if '附录' in rel else kind_of_strict(name, title, rel)
        if book == '26清史稿文白' and re.fullmatch(r'表(第)?[一二三四五六七八九十百〇零0-9]+', name or ''):
            name = QING_TABLE_NAME.get(juan, name)     # 表名补全（见常量注释）
        entries.append((book, juan, kind, name, rel))

    lines = [
        '# 05-卷名总表（自动生成）',
        '',
        '> **用途**：定位"某篇在哪一卷"。部分书的**文件名只有占位篇名**（如魏书「卷一百一十四·志第二十」实为**释老志**），'
        '本表从文件名与正文标题行提取实质篇名，Grep 本表即可由篇名/主题反查卷目与路径。',
        '> 行格式：`书名｜卷次｜类型｜篇名｜路径`（「主题速查」段为 4 列，无路径——其路径见下方「全部卷目」同名条目）。'
        '命中后按路径 Read 原文；「主题速查」段按主题聚合了各书的志/纪/传。'
        '**卷题级篇名（如「列传第九十七」）后带「（含：A、B）」时，括号内是从该卷头部自动提取的传主/合传名单**，'
        '可直接据此定位"某传在哪一卷"；名单只作检索线索，人名仍以 02/03 索引与原文为准。',
        '> 提示：魏书、隋书、宋书等书名与篇名不匹配时以本表为准；本表篇名照录原文标题，个别底本标题残缺（如「释老十」）时以「志第X」为辅。',
        '> **取文件（Windows/PowerShell，禁用 `find`）**：在本 skill 目录下用 Glob 工具或 `dir "0*.md"` 列全部索引；命令示例见 `SKILL.md`「取文件」节。',
        '> 生成：`tools/build_volume_index.py`。语料更新后重跑（或跑 `tools/make_indexes.py` 一键重建）。',
        '',
        '# 主题速查（按主题聚合志/纪/传/载记/表）',
        '',
    ]
    # 主题速查：只列「书名｜卷次｜类型｜篇名」作为**入口**，不重复路径列——
    # 与下方「全部卷目」保持非重复，避免同一行出现两次导致 Grep 双命中（曾重复 1649 行）。
    # ⚠ 同一分册卷（同书同卷号两个文件）加上相同「（含：…）」提示后，4 列行会完全一样——
    #   selftest 的「05 无重复行」会 FAIL（2026-09-22 实测），故主题速查段按整行去重。
    seen_theme = set()
    for theme, kws in THEMES:
        hits = [e for e in entries if any(k in e[3] or k in e[1] for k in kws)]
        if not hits:
            continue
        lines.append('## %s（%d 条；路径见下方「全部卷目」同名条目）' % (theme, len(hits)))
        for book, juan, kind, name, rel in hits:
            row = '%s｜%s｜%s｜%s' % (book, juan or '-', kind, name or '-')
            if row in seen_theme:
                continue
            seen_theme.add(row)
            lines.append(row)
        lines.append('')

    lines += ['# 全部卷目（按书分节，含路径）', '']
    cur = None
    for book, juan, kind, name, rel in entries:
        if book != cur:
            cur = book
            lines.append('## %s' % book)
        lines.append('%s｜%s｜%s｜%s｜%s' % (book, juan or '-', kind, name, rel))

    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')

    # ---- 01-卷目速查.md：与 05 同一次运行产出 ----
    # 为什么放在这里：01 的每行路径就是 05「全部卷目」段的第 5 列（实测 4408 条集合完全相等），
    # 单独维护曾导致它**没有生成脚本**、挂在重建链外——语料一增补必然漂移，
    # 而 selftest 只能靠 01≡05 集合比对发现 FAIL，维护手册里却没有补救步骤（2026-09-22 审计确认）。
    # 排序说明：按文件相对路径排序（与 05「全部卷目」段同序、跨机可复现）。
    # 旧版 01 的"拼音序"来自 Windows 外壳排序，任何脚本都无法复现，故改为此序。
    out01 = [
        '# 全库卷目速查表（%d 卷）' % len(entries),
        '',
        '每行一个文件的相对路径（相对 `data\\`）。用 Grep 搜人名/篇名/卷号关键词，命中行即为可直接 Read 的文件路径。',
        '**卷题级卷**（如「列传第九十七」「本纪第四十七」）在路径后附 `｜含：…`——那是该卷的传主/内容名单（与 `05-卷名总表.md` 同源），**取路径时只取 `｜` 之前那一段**。',
        '注意：部分书的篇名含传主姓名（如史记「项羽本纪」、旧唐书「魏徵」「安禄山」「朱泚」、陈书/金史/元史/明史的帝王名），可直接按人名定位；但宋书、新唐书、辽史、宋史、清史稿等篇名只有「列传第X」无姓名，人名检索仍需全文 Grep。**传主名不在卷题里时，查人物请走 `02`／`03`。**',
        '**取文件（Windows/PowerShell，禁用 `find`）**：用 Glob 工具或 `dir`＋通配符定位；命令示例见 `SKILL.md`「取文件」节。',
        '> 生成：`tools/build_volume_index.py`（与 `05-卷名总表.md` 同一次运行产出，按文件相对路径排序）。语料更新后重跑（或跑 `tools/make_indexes.py` 一键重建）。',
        '',
    ]
    cur01 = None
    for book, juan, kind, name, rel in entries:
        if book != cur01:
            cur01 = book
            out01.append('## %s' % book)
        mh = re.search(r'（含：(.+)）$', name or '')
        out01.append('%s｜含：%s' % (rel, mh.group(1)) if mh else rel)
    OUT01.write_text('\n'.join(out01) + '\n', encoding='utf-8', newline='\n')

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    kc = defaultdict(int)
    for e in entries:
        kc[e[2]] += 1
    print('files=%d kinds=%s' % (len(entries), dict(kc)))
    print('out=%s' % OUT)
    print('out01=%s' % OUT01)


if __name__ == '__main__':
    main()
