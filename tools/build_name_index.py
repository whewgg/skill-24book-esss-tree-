# -*- coding: utf-8 -*-
"""
build_name_index.py — 生成 02-人物字号索引.md

扫描 data/ 下全部 .md 文件，提取两类信息：
1. 「X，字Y」「X者，字Y」句式 → 人物字号条目（人名｜字｜源文件相对路径）
2. 「自号/号曰/时人号/绰号」等明确句式中的"号" → 附录（严格口径，宁缺毋滥）

用法: python tools/build_name_index.py
语料更新后重跑本脚本即可重建索引。
"""
import re
import sys
from collections import defaultdict

from _lib import ROOT, DATA, is_baihua  # noqa: E402

OUT = ROOT / "02-人物字号索引.md"
ALIASES = ROOT / "tools" / "aliases.md"
BLOCKLIST = ROOT / "tools" / "name_blocklist.md"


def load_blocklist():
    """tools/name_blocklist.md：非人名词条 → 剔除理由。

    「X，字Y」句式会把介词/动词短语当姓名（「乃宣敕告之」「食邑于贾地」），
    这些都过了姓氏白名单却不是人物。02 与 03 都要剔除，故做成两脚本共用的数据文件。
    两种粒度：
      名级  `词条｜理由`              —— 该名的**全部**条目剔除
      行级  `词条｜字：某｜理由`      —— 只剔 (名=词条, 字=某) 这一对
              （如「武｜字：狄遮」是隋书《西域传》「王姓昭武，亦康国种类，字狄遮」的碎片，
                但「武｜字：成兴」是达奚武——同名下真假混杂，只能按行剔）
    """
    out = {}
    if not BLOCKLIST.exists():
        return out
    for line in BLOCKLIST.read_text('utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('｜')]
        nm = parts[0]
        if not nm:
            continue
        if len(parts) > 1 and parts[1].startswith('字：'):
            out['%s｜%s' % (nm, parts[1])] = parts[2].strip() if len(parts) > 2 else ''
        else:
            out[nm] = parts[1].strip() if len(parts) > 1 else ''
    return out


def is_blocked(nm, zi, block):
    """(人名, 字) 是否命中屏蔽表：先查行级（名｜字：X），再查名级。"""
    if not block:
        return False
    if zi and ('%s｜字：%s' % (nm, zi)) in block:
        return True
    return nm in block


def load_aliases():
    """读取异文对照表：name -> 组内其他写法集合。"""
    m = {}
    if ALIASES.exists():
        for line in ALIASES.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split('｜') if p.strip()]
            for p in parts:
                m.setdefault(p, set()).update(x for x in parts if x != p)
    return m

HAN = r'一-鿿'
# 「X，字Y」：史记、两唐书等主流句式
PAT_MAIN = re.compile(r'([%s]{2,5})，字([%s]{1,3})(?=[，。；：、！？\n])' % (HAN, HAN))
# 「X者，字Y」
PAT_ZHE = re.compile(r'([%s]{1,4})者，字([%s]{1,3})(?=[，。；：、！？\n])' % (HAN, HAN))
# 「X字Y」无逗号：三国志/资治通鉴常见（如「諸葛亮字孔明」）；
# 左侧排除「名字/表字/小字」等词，以及副词/介词（防"以字相称"类白话搭配）
PAT_NOCOMMA = re.compile(
    r'(?<![名表改更一又自冠賜赐小别題题文寫写数數画畫簽签草行隸隶篆楷識识读讀說说書书以乃遂即便则方欲將将能可敢肯愿要會会得須须應应當当嘗尝曾常每各皆俱並并全尽均咸同共互相再仍也都只就且而之其于於與与及為为謂谓曰云])([%s]{2,5})字([%s]{1,3})(?=[，。；：、！？\n])' % (HAN, HAN))
# ⚠ 2026-09-24 试过一道"末字＋字＝名词"的尾字精度闸（想一次清掉「乃译成汉字备不虞」→
#   假人「成汉，字备不虞」这类既进 02 甲区（首字恰好是姓，姓氏白名单挡不住）又进
#   03 立目（顶着 03 31 卷）的噪声——它会顶在「猜刘粲」这类谜题的真答案旁边）。
#   **实测否决**：这些尾字同样出现在真人人名里，一刀切砍掉 1040 条命中，其中
#   吴汉字子颜、赵广汉字子都、薛汉字公子（汉）／周抚字道和、滕抚字叔辅、杜抚字叔和
#   （抚，"抚字"又是抚民之义）／褚伯玉字元璩（玉）／徐瑞字元珪（瑞）／李金字道赐（金）／
#   樊梵字文高（梵）／安石字子坚（石）／宋史·德文字子矼、南齐书·海陵恭王昭文字季尚、
#   通鉴卷118·恭皇帝德文字德文（文）——为砍约 40 个假人赔上 13 个有名有姓的真人，净亏。
#   结论：无逗号句式的假人**只能逐条走 `tools/name_blocklist.md`（附语料证据）**，
#   加 BAD_ZI_HEAD 只拦**虚词首字**（如「字者」族）；不做尾字类规则。

# 「X者，<籍贯等≤12字>，字Y」：如「項籍者，下相人也，字羽」
PAT_SPAN = re.compile(r'([%s]{1,4})者，[^，。；]{0,12}?，字([%s]{1,3})(?=[，。；：、！？\n])' % (HAN, HAN))
# 「X，<籍贯等≤12字>，字Y」无"者"版：如「端木赐，卫人，字子贡」（史记列传高频句式）
PAT_GAP = re.compile(r'([%s]{2,5})，[^，。；]{1,12}?，字([%s]{1,3})(?=[，。；：、！？\n])' % (HAN, HAN))
# 单字名「X，字Y」：如「迁，字子长」；人名为虚词时丢弃（见 SUFFIX_DROP）
PAT_SINGLE = re.compile(r'([%s])，字([%s]{1,3})(?=[，。；：、！？\n])' % (HAN, HAN))
# 「号」：严格口径，允许引号包裹（如「时号“竹溪六逸”」）
PAT_HAO = re.compile(
    r'(?:自號|自号|號曰|号曰|時人號|时人号|時號|时号|人號|人号|民號|民号|'
    r'士人號|士人号|鄉人號|乡人号|綽號|绰号)["“”\'『』「」]?([%s]{2,5})["“”\'』』「」]?' % HAN)

PREFIX_MULTI = ['長子', '长子', '次子', '少子', '季子', '中子', '庶子', '嫡子', '嗣子',
                '養子', '养子', '族子', '從子', '从子', '猶子', '犹子', '兄子', '弟子',
                '小子', '愛子', '爱子', '太子', '公子']
PREFIX_SINGLE = ['子', '弟', '兄', '者', '諱', '讳', '初', '乃']

BAD_ZI_CHARS = set('號号')
# 字字段首字为虚词/白话常用字时多为「字曰太孙」「一块写字的绸缎」类误匹配，丢弃。
# ⚠ 2026-09-22 五轮：原名单把「则/正/安/方/常/曾/如/何/此/然」也当虚词拦了——
#   实测这些是**常见表字首字**（赵普字则平、邓世昌字正卿、赵国安字安国、张邦奇字常甫、
#   费密字此度、张奂字然明…），导致几百个真人的字号被漏收（02/03 里查不到）。
#   噪声侧（「文字虽短」「六七字便满」「字故也」）本就被 PAT 的负向前瞻挡住，
#   即便偶有漏网也只进 02 乙区（03 不收乙区），故这十字从黑名单移除，只留真正的噪声字。
#   ⚠ 2026-09-23：同批还漏了「令」——「令」是表字高频首字（李密字令伯、干宝字令升…），
#   之前被当「使令」的虚词误拦，导致李密（西晋·陈情表）整条字号缺席 02。一并移除。
BAD_ZI_HEAD = set('曰云謂谓之其乃即遂亦又因使以為为與与共及將将'
                  '的有是都在也就不還还被把讓让和跟同由從从向對对于而'
                  '或若但卻却只僅仅再仍已嘗尝便故雖虽'
                  '彼孰焉耳矣哉乎耶歟欤呢吗嘛啊吧麼么'
                 '稱称寫写念讀读說说講讲叫唤喚喊題题刻印織织绣繡缝縫'
                 '染画畫描涂塗抹撕剪烧燒埋藏捡撿拾拆洗晒曬叠疊'
                 '体體样樣误誤没沒'
                 '者')
# ⚠ 2026-09-24：补「者」——「X字者」句式里「字」是名词（文字/名字/汉字…），
#   「者」是代词，整条是噪声。实测 02 原有 95 条「字：者／字：者并改／字：者也」
#   全是假人（司有部｜玉简金｜王爵｜木加交｜水加各｜康成破｜通文｜金石文｜
#   契丹｜女直｜丞相文｜但有王爵…），零真表字以「者」开头。
#   ⚠ 同批**没有**补「所」：「郑思肖字所南」「王就学字所敬」是真人的真表字。
# 人名含这些字必为白话噪声
BAD_NAME_CHARS = set('的了着呢吗嘛啊呀吧麼么甚')
# 人名尾字黑名单（全局）：绝不作人名尾字者——称谓/籍贯残留、白话虚词、方位数字爵称。
# 注意：真人名常见尾字「之（王羲之/裴松之）」「其（郦食其/审食其）」「夫（灌夫）」不在其列；
# 它们带来的白话噪声（"平素称其""时人多以"）由四字复姓规则与姓氏白名单拦截。
BAD_NAME_TAIL = set('人以也者的了着矣焉耳乎哉'
                    '大小多少长短高低远近新旧老幼初终始末先后前内中间旁侧左右东西南北上下里外'
                    '一二三四五六七八九十两几数'
                    '王侯帝皇后妃主将帅使守尉师众妇父母兄弟姐妹儿女孙')
# 量词/副词短语开头必为白话噪声
BAD_NAME_HEAD = ('一个', '一块', '一条', '一只', '一张', '一把', '一件', '一次',
                 '一句', '一行', '一字', '一种', '一边', '一直', '一向', '一旦',
                 '一同', '一起', '一般', '一定', '一样', '一部', '一卷', '一首',
                 '一幅', '一顿', '一番', '一阵', '一場', '一场', '一群', '一批',
                 '一岁', '一歲', '一年', '一月', '一日', '一夜', '一生', '一世')
# 白话行专用扩展：zi 首字为称呼/书写类动词（雅/敬/谦/美/载/述/世/闻/见/知/书/存/念/尊/行
# 均为表字高频字，不得列入——司马通字雅达、周邦彦字美成皆真表字）
BAD_ZI_HEAD_BH = set('相呼号昵称俗恶褒贬讥讽誉毁咒骂詈叱唤喊叫'
                     '谈议评赞颂叹诵读抄录叙署签钤封藏留挂贴示寄递授献呈还缴'
                     '著忘记认背默题印送')
# 白话行专用扩展：人名为副词/时间词/常用语开头（首字撞姓者高危，如"通"是姓）
BAD_NAME_HEAD_BH = ('通常', '经常', '常常', '常年', '平常', '日常', '时常', '非常',
                    '异常', '都是', '都会', '全都', '全部', '曾经', '尚且', '方才',
                    '平时', '平日', '夏天', '春天', '秋日', '秋天', '冬季', '冬天',
                    '季节', '时候', '时光', '时代', '时刻', '古代', '古人', '今天',
                    '今日', '今年', '明天', '明日', '明年', '明明', '明显', '后来',
                    '后代', '后天', '年代', '年纪', '当时', '当初', '从来', '从前',
                    '以往', '以前', '将来', '未来', '过去', '现在', '目前', '刚刚',
                    '刚才', '恰巧', '恰好', '正好', '刚好', '幸亏', '确实', '其实',
                    '其他', '其余', '果然', '果真', '显然', '必定', '必然', '必须',
                    '肯定', '一定', '一般', '可能', '可以', '或许', '或者', '大约',
                    '大概', '大致', '大都', '屡次', '多次', '再度', '再次', '再三',
                    '一再', '十分', '特别', '特殊', '尤其', '格外', '越发', '更加',
                    '比较', '稍微', '略微', '毫不', '并不', '绝不', '切莫', '务必',
                    '尽量', '尽快', '及时', '随时', '顺便', '随意', '任意', '全力',
                    '极力', '争取', '竞相', '纷纷', '连续', '陆续', '持续', '偶尔',
                    '偶然', '顿时', '立刻', '立即', '即刻', '旋即', '当即', '当面',
                    '当众', '公开', '毅然', '断然', '悄悄', '暗暗', '暗自', '偷偷',
                    '私自', '私下', '径自', '独自', '单独', '亲身', '亲自', '轮流',
                    '依次', '逐一', '逐字', '逐句', '连日', '连夜')
# 单字人名候选为虚词时丢弃（「也，字X」之类）
SUFFIX_DROP = set('也矣焉耳者之乎哉曰云乃遂即皆俱咸並并又亦且而則则故或若夫盖蓋其是此彼於于以與与及不弗未勿莫無无有何安孰誰谁豈岂寧宁庸詎讵人臣君子')
HAO_BLACKLIST = {'年號', '年号', '國號', '国号', '廟號', '庙号', '諡號', '谥号',
                 '尊號', '尊号', '徽號', '徽号', '帝號', '帝号', '王號', '王号',
                 '名號', '名号', '稱號', '称号', '口號', '口号', '旗號', '旗号',
                 '軍號', '军号', '發號', '发号', '施號', '施号', '記號', '记号',
                 '符號', '符号', '編號', '编号', '大號', '大号', '正號', '正号',
                 '令', '稱', '称', '曰', '皇帝', '皇太后', '皇后', '太后', '太子',
                 '公主', '天子', '陛下', '將軍', '将军', '丞相', '刺史', '太守',
                 '皇曾孫', '皇曾孙', '大人', '先生', '長者', '长者'}
# 号词以身份称谓结尾的多为尊号/封号而非别号，丢弃
HAO_BAD_TAIL = ('皇帝', '太后', '皇后', '太子', '公主', '天子', '陛下',
                '將軍', '将军', '丞相', '刺史', '太守', '尚書', '尚书')


def variants(name):
    """返回应收录的人名变体集合。

    捕获串可能带身份/称谓/句首前缀（如「宣皇帝讳懿」「長子建」），
    故对每个捕获串生成全部长度 2–4 的右子串，再剥称谓前缀；
    冗余系有意保留，以提高命中（核对原文由 Read 完成）。
    """
    out = set()
    n = len(name)
    cands = set()
    if 2 <= n <= 4:
        cands.add(name)
    # 右子串（含全串）：从每个位置截取至末尾，长度 2-4
    for i in range(max(0, n - 4), n):
        seg = name[i:]
        if 2 <= len(seg) <= 4:
            cands.add(seg)
    for c in list(cands):
        out.add(c)
        for p in PREFIX_MULTI:
            if c.startswith(p) and len(c) - len(p) >= 2:
                out.add(c[len(p):])
        for p in PREFIX_SINGLE:
            if c.startswith(p) and len(c) - 1 >= 2:
                out.add(c[1:])
    return {v for v in out
            if 2 <= len(v) <= 4
            and v not in SUFFIX_DROP
            and v[-1] not in BAD_NAME_TAIL
            and not any(ch in BAD_NAME_CHARS for ch in v)
            and not v.startswith(BAD_NAME_HEAD)}


def good_zi(zi, bh=False):
    if not (1 <= len(zi) <= 3):
        return False
    if any(c in BAD_ZI_CHARS for c in zi):
        return False
    if zi[0] in BAD_ZI_HEAD:
        return False
    if bh and zi[0] in BAD_ZI_HEAD_BH:
        return False
    return True


# ---- 姓氏白名单（百家姓 + 史书扩展；人名首字/复姓校验）----
_S1_STR = (
    '赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜'
    '戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐'
    '费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄'
    '和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁'
    '杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍'
    '虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚'
    '程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓'
    '牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙'
    '叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双'
    '闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通边扈燕冀郏浦尚农'
    '温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘'
    '匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空'
    '曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公'
    # 史书常见补充单姓（含少数民族政权人物常用姓氏用字）
    '源浑玉铁木述速月乃塔忽答拔脱兀朵帖代豆奚沙银术合赤火虎鹿'
    '爨麴冼邸邝苻眭睢么仝缑綦繁句藉覃黑种盖查隗尉逢盛卷端阿'
)
SURNAMES1 = frozenset(_S1_STR)
SURNAMES_MULTI = frozenset((
    '司马', '诸葛', '欧阳', '夏侯', '上官', '皇甫', '尉迟', '公孙', '令狐',
    '宇文', '慕容', '长孙', '拓跋', '万俟', '鲜于', '闾丘', '司徒', '司空',
    '司寇', '颛孙', '端木', '巫马', '公西', '漆雕', '乐正', '壤驷', '公良',
    '宰父', '穀梁', '段干', '百里', '东郭', '南门', '呼延', '羊舌', '微生',
    '梁丘', '左丘', '东门', '西门', '南宫', '第五', '公羊', '澹台', '公冶',
    '宗政', '濮阳', '淳于', '单于', '太叔', '申屠', '仲孙', '轩辕', '钟离',
    '子车', '仉督', '毌丘', '士孙', '夹谷', '贺兰', '独孤', '尔朱', '纥干',
    '端沐', '公西', '公冶', '公羊', '公仪', '公叔', '公输', '公祖', '公冉',
    '库狄', '斛律', '斛斯', '叱罗', '步六孤', '豆卢', '秃发', '沮渠',
    '耶律', '完颜', '徒单', '蒲察', '石抹', '女奚烈', '兀颜', '乌古论',
    '移剌', '粘合', '奥屯', '抹颜', '术虎', '唐括', '蒲鲜', '斜卯',
    '阿史那', '阿史德', '孛儿只斤', '爱新觉罗', '叶赫那拉', '乌拉那拉',
    '辉发那拉', '瓜尔佳', '钮祜禄', '舒穆禄', '他塔喇', '萨克达', '赫舍里',
    '索绰罗', '郭络罗', '伊尔根觉罗', '佟佳', '马佳', '富察', '戴佳',
    '章佳', '纳喇', '乌雅', '纳兰',
))


def known_surname(name):
    """人名以已知姓氏开头（复姓优先）；四字人名必须复姓开头（单姓+三名结构不存在）。"""
    if len(name) == 4:
        return (name[:2] in SURNAMES_MULTI or name[:3] in SURNAMES_MULTI
                or name[:4] in SURNAMES_MULTI)
    for L in (5, 4, 3, 2):
        if len(name) > L and name[:L] in SURNAMES_MULTI:
            return True
    return name[0] in SURNAMES1


# 行级白话判别（is_baihua）已统一到 _lib（见文件头 import）。


def main():
    zi_entries = defaultdict(set)   # book -> {(name, zi, relpath)}
    hao_entries = defaultdict(set)  # book -> {(hao, relpath)}
    nfiles = 0
    for fp in sorted(DATA.rglob('*.md')):
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        nfiles += 1
        rel = str(fp.relative_to(DATA)).replace('/', '\\')
        book = rel.split('\\')[0]
        for line in text.splitlines():
            # 译注/脚注行（形如 `> *[3] **词条**：…`，全库约 1.9 万行）是**后人编的注**，不是史料原文——
            # 它会把「苏武字子卿」这类**解释性**表述当成字号出处（旧唐书卷 81 的译注曾把苏武的
            # ★ 本传污染出第二来源）。整行跳过；实测仅影响 26 个「仅译注才有」的候选，且全为噪声
            # （"唐代文学家｜梦得""唐初功臣｜肇仁"之流）。2026-09-23
            _st = line.lstrip()
            if _st.startswith(">") and "*" in _st[:6] and "[" in _st[:8]:
                continue
            # 语料把传主名写成 **张居正**，字叔大（Markdown 加粗）。正则要求姓名紧贴「，字」，
            # 星号会把整条「X，字Y」挡在门外——张居正因此整条缺席 02/03（2026-09-22 实测确认）。
            # 只剥加粗标记 **，不动其他字符；is_baihua 的白话判据不受影响。
            line = line.replace('**', '')
            if is_baihua(line):
                # 白话译文行：「X，字Y」「X字Y」最严句式 + 姓氏白名单 +
                # 白话专用黑名单（动词性字搭配/副词开头），捞回"仅见于译文的字号"。
                for pat in (PAT_MAIN, PAT_NOCOMMA):
                    for m in pat.finditer(line):
                        raw, zi = m.group(1), m.group(2)
                        if not good_zi(zi, bh=True):
                            continue
                        for nm in variants(raw):
                            if known_surname(nm) and not nm.startswith(BAD_NAME_HEAD_BH):
                                zi_entries[book].add((nm, zi, rel))
                continue
            for pat in (PAT_MAIN, PAT_ZHE, PAT_NOCOMMA, PAT_SPAN, PAT_GAP):
                for m in pat.finditer(line):
                    raw, zi = m.group(1), m.group(2)
                    if not good_zi(zi):
                        continue
                    for nm in variants(raw):
                        zi_entries[book].add((nm, zi, rel))
            for m in PAT_SINGLE.finditer(line):
                raw, zi = m.group(1), m.group(2)
                if raw in SUFFIX_DROP or not good_zi(zi):
                    continue
                zi_entries[book].add((raw, zi, rel))
            for m in PAT_HAO.finditer(line):
                h = m.group(1)
                if (h in HAO_BLACKLIST or h[0] in ('令', '稱', '称', '曰', '云', '为', '為', '之', '其')
                        or h.endswith(HAO_BAD_TAIL)):
                    continue
                hao_entries[book].add((h, rel))

    books = sorted(set(zi_entries) | set(hao_entries))
    alias_map = load_aliases()
    block = load_blocklist()
    n_blocked = sum(1 for nm, _z, _r in
                    {e for es in zi_entries.values() for e in es} if nm in block)

    def fmt(nm, zi, rel):
        s = '%s｜字：%s｜%s' % (nm, zi, rel)
        if nm in alias_map:
            s += '｜异文：%s' % '、'.join(sorted(alias_map[nm]))
        return s
    lines = [
        '# 02-人物字号索引（自动生成）',
        '',
        '> **本文件仅供 Grep 检索，请勿整读（文件过大）。**',
        '> 用法：Grep 人名（如「魏徵」），命中行含其「字」与源文件相对路径；**命中后必须 Read 源文件、照录原文并注明出处**，不得只凭本索引行转述。',
        '> 未命中 ≠ 正史无载：异体字、繁简差异、更罕见的句式仍可能漏收；未命中时应再全文 Grep `data/` 复核，仍无则如实回答"正史未见记载"。**严禁编造字、号。**',
        '> 收录口径：原文中「X，字Y」「X字Y」「X者，字Y」「X者，…，字Y」句式（如「魏徵，字玄成」「諸葛亮字孔明」「項籍者，下相人也，字羽」「迁，字子长」）；'
        '**白话译文行只收最严句式「X，字Y／X字Y」且必须过姓氏白名单**（用来捞回"仅见于译文"的字号，'
        '故**甲区条目未必都出自文言原文**——引用前仍须 Read 源文件确认原文怎么写）；'
        '**但译注/脚注行（形如 `> *[3] …`）不参与抓取**——那是后人编的注、不是原文，'
        '曾把「苏武」的 ★ 本传污染出第二来源（旧唐书卷 81 译注「苏武字子卿」，2026-09-23 已排除）；'
        '身份/称谓前缀条目（如「宣皇帝讳懿」「長子建」）与剥前缀纯名条目并存，冗余系有意保留以提高命中。',
        '> 分区：**甲区**为人名通过姓氏白名单（百家姓+史书扩展姓氏）校验的高可信条目；**乙区**为校验未过者——多为称谓残留（「讳懿」「長子建」）、无姓人名（和珅、伯颜类以名行者）或噪声，可用但**引用前务必 Read 核对原文**。',
        '> **异文标注**：同一人存在多种写法（通假/版本异文，如「端木赐/端沐赐」「公西葴/公西舆如」）时，条目行尾标注「异文：X」。此类条目应**并录并注明差异**，不得自行取舍删改。对照表维护于 `tools/aliases.md`，发现新异文追加一行并重跑脚本即可。',
        '> **取文件（Windows/PowerShell，禁用 `find`）**：在本 skill 目录下用 Glob 工具或 `dir "0*.md"` 列全部索引；命令示例见 `SKILL.md`「取文件」节。',
        '> 生成：`tools/build_name_index.py`。语料更新后重跑该脚本重建本索引（或跑 `tools/make_indexes.py` 一键重建全部索引并验收）。',
        '',
        '# 甲区 · 姓氏校验通过（高可信）',
        '',
    ]
    total = 0
    total_b = 0
    b_lines = []
    for b in books:
        es = sorted(zi_entries.get(b, ()), key=lambda t: (t[0], t[1], t[2]))
        if not es:
            continue
        ok = [e for e in es if known_surname(e[0]) and not is_blocked(e[0], e[1], block)]
        sus = [e for e in es if not known_surname(e[0]) and not is_blocked(e[0], e[1], block)]
        if ok:
            lines.append('## %s' % b)
            lines += [fmt(nm, zi, rel) for nm, zi, rel in ok]
            lines.append('')
            total += len(ok)
        if sus:
            b_lines.append('## %s' % b)
            b_lines += [fmt(nm, zi, rel) for nm, zi, rel in sus]
            b_lines.append('')
            total_b += len(sus)

    lines += [
        '# 乙区 · 待核（姓氏表外：称谓残留、无姓人名或噪声，引用前务必 Read 核对）',
        '',
    ] + b_lines

    lines += [
        '---',
        '',
        '# 附录：原文所见"号"辑录（严格口径，宁缺毋滥）',
        '',
        '> 别号在正史中记载极少。此处仅收录「自号/号曰/时人号/绰号」等明确句式，按号词检索；命中后须 Read 原文确认所指人物。',
        '> **人物之"号"若此处查不到，即应回答"正史未载其号"，不得编造。**',
        '',
    ]
    htotal = 0
    for b in books:
        es = sorted(hao_entries.get(b, ()), key=lambda t: (t[0], t[1]))
        if not es:
            continue
        lines.append('## %s' % b)
        lines += ['号：%s｜%s' % (h, rel) for h, rel in es]
        lines.append('')
        htotal += len(es)

    # 显式 newline='\n'：默认的 Windows 转换会把索引写成 CRLF，与 data 语料（LF）不一致，
    # 跨机做逐字节比对时会产生假差异。
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('files=%d zoneA=%d zoneB=%d hao_entries=%d blocked=%d'
          % (nfiles, total, total_b, htotal, n_blocked))
    print('out=%s' % OUT)


if __name__ == '__main__':
    main()
