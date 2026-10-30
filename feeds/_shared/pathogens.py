"""Canonical pathogen list for HealthEvent.pathogen and pathogen_class.

classes: viral | bacterial | vectorBorne | foodborne | zoonotic | fungal | unknown
Order matters where aliases overlap (MERS before COVID, bird flu before influenza,
Guinea worm would collide with 'Guinea' so it is not here). Aliases are regexes,
matched case-insensitively on word boundaries; an alias written in Arabic or CJK
script is matched without boundaries (Arabic prefixes attach to the word, Chinese
has no spaces), so those aliases are kept long enough not to hide inside other words.

2026-10-30 (PLAN §11, the HealthMap delta): aliases in Spanish, French, Portuguese,
Arabic, Chinese, Russian and Indonesian, the languages the Google News feed now reads,
plus the zoonoses WAHIS, APHIS and WHISPers report (screwworm, Q fever, EEE, Hendra).
The Haiku curator confirms every non-English match before it is mapped.
"""
import re

PATHOGENS = [
    # slug, display name, class, aliases
    ('mers', 'MERS-CoV', 'viral', [r'mers(?:-cov)?', r'middle east respiratory syndrome', r'síndrome respiratorio de oriente medio',
                                    r'ميرس', r'كورونا الشرق الأوسط', r'中东呼吸综合征', r'中東呼吸症候群']),
    ('h5n1', 'Avian influenza (H5N1)', 'zoonotic', [r'h5n1', r'h5n\d', r'h7n9', r'h9n2', r'avian (?:influenza|flu)', r'bird flu', r'hpai',
                                                      r'highly pathogenic avian', r'high pathogenicity avian influenza',
                                                      r'influenza a viruses of high pathogenicity',
                                                      r'gripe aviar(?:ia)?', r'influenza aviar(?:ia)?', r'grippe aviaire', r'gripe aviária',
                                                      r'influenza aviária', r'[إا]نفلونزا الطيور', r'禽流感', r'птичь\w* грипп\w*', r'грипп\w* птиц',
                                                      r'flu burung']),
    ('covid19', 'COVID-19', 'viral', [r'covid(?:-| )?19', r'covid', r'sars-cov-2', r'coronavirus(?! disease 2019)', r'omicron',
                                       r'كوفيد', r'كورونا', r'新冠', r'ковид\w*', r'коронавирус\w*']),
    ('influenza', 'Influenza', 'viral', [r'influenza', r'\bflu\b', r'h1n1', r'h3n2', r'gripe', r'grippe', r'[إا]نفلونزا', r'流感', r'грипп\w*']),
    ('rsv', 'RSV', 'viral', [r'\brsv\b', r'respiratory syncytial', r'virus respiratorio sincitial', r'virus respiratoire syncytial',
                              r'vírus sincicial respiratório', r'呼吸道合胞病毒']),
    ('measles', 'Measles', 'viral', [r'measles', r'rubeola', r'sarampi[óo]n', r'rougeole', r'sarampo', r'حصبة', r'麻疹', r'кор[ьию]', r'campak']),
    ('mumps', 'Mumps', 'viral', [r'mumps', r'paperas', r'parotiditis', r'oreillons', r'caxumba', r'papeira', r'النكاف', r'腮腺炎', r'паротит\w*',
                                  r'gondongan']),
    ('rubella', 'Rubella', 'viral', [r'rubella', r'rubéola', r'rubéole', r'الحصبة الألمانية', r'风疹', r'風疹', r'德国麻疹', r'德國麻疹', r'краснух\w*']),
    ('varicella', 'Chickenpox', 'viral', [r'varicella', r'chickenpox', r'chicken pox', r'varicela', r'varicelle', r'catapora', r'الجدري المائي',
                                           r'جدري الماء', r'水痘', r'ветрян\w*', r'cacar air']),
    ('pertussis', 'Pertussis', 'bacterial', [r'pertussis', r'whooping cough', r'tos ?ferina', r'coqueluche', r'tosse convulsa', r'السعال الديكي',
                                              r'百日咳', r'коклюш\w*', r'batuk rejan', r'pertusis']),
    ('diphtheria', 'Diphtheria', 'bacterial', [r'diphtheria', r'difteria', r'diphtérie', r'الدفتيريا', r'白喉', r'дифтери\w*', r'difteri']),
    ('polio', 'Poliovirus', 'viral', [r'polio(?:myelitis|virus)?', r'cvdpv\d?', r'vaccine-derived poliovirus', r'poliomielitis', r'poliomyélite',
                                       r'poliomielite', r'شلل الأطفال', r'脊髓灰质炎', r'脊髓灰質炎', r'小儿麻痹', r'小兒麻痺', r'полиомиелит\w*']),
    ('ebola', 'Ebola disease', 'viral', [r'ebola(?:virus)?', r'bundibugyo', r'sudan virus disease', r'sudan ebolavirus', r'\bsvd\b', r'\bevd\b',
                                          r'ébola', r'[إا]يبولا', r'埃博拉', r'伊波拉', r'эбол\w*']),
    ('marburg', 'Marburg', 'viral', [r'marburg', r'marburgo', r'ماربورغ', r'马尔堡', r'馬爾堡', r'марбург\w*']),
    ('lassa', 'Lassa fever', 'zoonotic', [r'lassa', r'لاسا', r'拉沙热', r'拉薩熱', r'ласса']),
    ('nipah', 'Nipah virus', 'zoonotic', [r'nipah', r'نيباه', r'尼帕', r'нипах']),
    ('hendra', 'Hendra virus', 'zoonotic', [r'hendra']),
    ('hantavirus', 'Hantavirus', 'zoonotic', [r'hantavirus', r'hanta', r'هانتا', r'汉坦', r'漢他', r'хантавирус\w*']),
    ('cchf', 'Crimean-Congo haemorrhagic fever', 'vectorBorne', [r'crimean?[- ]congo', r'\bcchf\b', r'crimée-congo', r'حمى القرم', r'克里米亚-刚果',
                                                                   r'克里米亞-剛果', r'крымск\w*[- ]конго', r'конго-крымск\w*']),
    ('rvf', 'Rift Valley fever', 'vectorBorne', [r'rift valley', r'valle del rift', r'vallée du rift', r'vale do rift', r'الوادي المتصدع', r'裂谷热',
                                                  r'裂谷熱', r'рифт\w*']),
    ('mpox', 'Mpox', 'viral', [r'mpox', r'monkeypox', r'clade i[ab]?\b', r'viruela (?:símica|simica|del mono)', r'variole (?:du singe|simienne)',
                                r'varíola (?:dos macacos|símia)', r'جدري القرود', r'جدري القردة', r'猴痘', r'оспа обезьян', r'cacar monyet']),
    ('cholera', 'Cholera', 'bacterial', [r'cholera', r'vibrio cholerae', r'acute watery diarrh', r'c[óo]lera', r'choléra', r'كوليرا', r'霍乱', r'霍亂',
                                          r'холер\w*', r'kolera']),
    ('typhoid', 'Typhoid', 'bacterial', [r'typhoid', r'enteric fever', r'salmonella typhi', r'fiebre tifoidea', r'fièvre typhoïde', r'febre tif[oó]ide',
                                          r'التيفوئيد', r'التيفود', r'伤寒', r'傷寒', r'брюшн\w* тиф\w*', r'tifus', r'tipes']),
    ('shigella', 'Shigellosis', 'foodborne', [r'shigell(?:a|osis)', r'shigelosis', r'shigelose', r'shigellose', r'志贺', r'志賀', r'шигелл\w*']),
    ('salmonella', 'Salmonella', 'foodborne', [r'salmonell(?:a|osis)(?! typhi)', r'salmonela', r'salmonelose', r'السالمونيلا', r'沙门氏菌', r'沙門氏菌',
                                                r'сальмонелл\w*']),
    ('ecoli', 'E. coli (STEC)', 'foodborne', [r'e\.? ?coli', r'\bstec\b', r'o157', r'escherichia', r'大肠杆菌', r'大腸桿菌', r'кишечн\w* палочк\w*']),
    ('listeria', 'Listeria', 'foodborne', [r'listeri(?:a|osis)', r'listériose', r'listeriose', r'الليستيريا', r'李斯特菌', r'листери\w*']),
    ('botulism', 'Botulism', 'foodborne', [r'botuli(?:sm|num)', r'botulismo', r'botulisme', r'التسمم الوشيقي', r'肉毒', r'ботулизм\w*']),
    ('campylobacter', 'Campylobacter', 'foodborne', [r'campylobacter', r'弯曲杆菌', r'彎曲桿菌', r'кампилобактер\w*']),
    ('vibrio', 'Vibriosis', 'foodborne', [r'vibrio(?:sis)?(?! cholerae)', r'vibrio vulnificus', r'víbrio', r'创伤弧菌', r'創傷弧菌']),
    ('norovirus', 'Norovirus', 'viral', [r'norovirus', r'norwalk', r'نوروفيروس', r'诺如病毒', r'諾羅病毒', r'норовирус\w*']),
    ('rotavirus', 'Rotavirus', 'viral', [r'rotavirus', r'rotavírus', r'轮状病毒', r'輪狀病毒', r'ротавирус\w*']),
    ('hepatitis_a', 'Hepatitis A', 'viral', [r'hepatitis a\b', r'\bhav\b', r'hépatite a\b', r'hepatite a\b', r'التهاب الكبد أ', r'甲型肝炎', r'甲肝']),
    ('hepatitis_e', 'Hepatitis E', 'viral', [r'hepatitis e\b', r'hépatite e\b', r'hepatite e\b', r'戊型肝炎', r'戊肝']),
    ('cyclospora', 'Cyclospora', 'foodborne', [r'cyclospor(?:a|iasis)', r'ciclospor\w*']),
    ('dengue', 'Dengue', 'vectorBorne', [r'dengue', r'الضنك', r'登革', r'денге', r'demam berdarah', r'\bdbd\b']),
    ('chikungunya', 'Chikungunya', 'vectorBorne', [r'chikungunya', r'chikunguña', r'شيكونغونيا', r'基孔肯雅', r'чикунгунь\w*']),
    ('zika', 'Zika', 'vectorBorne', [r'zika', r'زيكا', r'寨卡', r'茲卡', r'зика']),
    ('yellow_fever', 'Yellow fever', 'vectorBorne', [r'yellow fever', r'fiebre amarilla', r'fièvre jaune', r'febre amarela', r'الحمى الصفراء', r'黄热病',
                                                      r'黃熱病', r'ж[её]лт\w* лихорадк\w*', r'demam kuning']),
    ('malaria', 'Malaria', 'vectorBorne', [r'malaria', r'paludismo', r'paludisme', r'malária', r'الملاريا', r'疟疾', r'瘧疾', r'маляри\w*']),
    ('west_nile', 'West Nile virus', 'vectorBorne', [r'west nile', r'nilo occidental', r'nil occidental', r'nilo ocidental', r'غرب النيل', r'西尼罗', r'西尼羅',
                                                      r'западн\w* нил\w*']),
    ('oropouche', 'Oropouche', 'vectorBorne', [r'oropouche']),
    ('jev', 'Japanese encephalitis', 'vectorBorne', [r'japanese encephalitis', r'encefalitis japonesa', r'encéphalite japonaise', r'encefalite japonesa',
                                                      r'日本脑炎', r'日本腦炎', r'японск\w* энцефалит\w*']),
    ('eee', 'Eastern equine encephalitis', 'vectorBorne', [r'eastern equine encephal\w+', r'equine encephalomyelitis \(eastern\)', r'\beee\b', r'triple e\b',
                                                            r'encefalitis equina del este']),
    ('leishmaniasis', 'Leishmaniasis', 'vectorBorne', [r'leishmani[ao]s[ie]s', r'leishmaniose', r'利什曼', r'лейшманиоз\w*']),
    ('plague', 'Plague', 'zoonotic', [r'\bplague\b', r'yersinia pestis', r'peste (?:bub[óo]nica|neum[óo]nica|bubônica|pneumônica|bubonique|pulmonaire)',
                                       r'الطاعون', r'鼠疫', r'чум[аыеу]\b']),
    ('anthrax', 'Anthrax', 'zoonotic', [r'anthrax', r'ántrax', r'carbunco', r'maladie du charbon', r'charbon bactéridien', r'antraz', r'carbúnculo',
                                         r'الجمرة الخبيثة', r'炭疽', r'сибирск\w* язв\w*', r'antraks']),
    ('rabies', 'Rabies', 'zoonotic', [r'rabies', r'\brabia\b', r'rage canine', r'\braiva\b', r'داء الكلب', r'狂犬病', r'бешенств\w*']),
    ('brucellosis', 'Brucellosis', 'zoonotic', [r'brucell(?:a|osis)', r'brucelosis', r'brucelose', r'brucellose', r'الحمى المالطية', r'布鲁氏菌', r'布魯氏菌',
                                                 r'布病', r'бруцелл[её]з\w*']),
    ('leptospirosis', 'Leptospirosis', 'zoonotic', [r'leptospir(?:a|osis)', r'leptospirose', r'钩端螺旋体', r'鉤端螺旋體', r'лептоспироз\w*']),
    ('tularemia', 'Tularemia', 'zoonotic', [r'tularemia', r'tularémie', r'tularemie', r'土拉菌', r'兔热病', r'туляреми\w*']),
    ('q_fever', 'Q fever', 'zoonotic', [r'q fever', r'coxiella', r'fiebre q\b', r'fièvre q\b', r'febre q\b', r'q热', r'ку-лихорадк\w*']),
    ('screwworm', 'New World screwworm', 'zoonotic', [r'screwworm', r'cochliomyia hominivorax', r'myiasis', r'gusano barrenador', r'miasis', r'bicheira',
                                                       r'lucilie bouchère', r'螺旋锥蝇']),
    ('meningitis', 'Meningococcal disease', 'bacterial', [r'meningococc\w+', r'meningitis', r'méningite', r'meningite', r'التهاب السحايا', r'脑膜炎', r'腦膜炎',
                                                           r'менингит\w*', r'менингококк\w*']),
    ('legionella', "Legionnaires' disease", 'bacterial', [r'legionnaire', r'legionell', r'legionel[ao]', r'legionelosis', r'legionelose', r'légionellose',
                                                           r'退伍军人', r'军团菌', r'軍團菌', r'легионелл\w*']),
    ('tb', 'Tuberculosis', 'bacterial', [r'tuberculosis', r'\btb\b(?! test)', r'tuberculose', r'结核', r'結核', r'туберкул[её]з\w*', r'tuberkulosis', r'\btbc\b']),
    ('strep_a', 'Group A strep', 'bacterial', [r'group a strep\w*', r'scarlet fever', r'\bigas\b', r'escarlatina', r'scarlatine', r'estreptococo (?:del grupo )?a\b',
                                                r'streptocoque a\b', r'الحمى القرمزية', r'猩红热', r'猩紅熱', r'скарлатин\w*']),
    ('mycoplasma', 'Mycoplasma pneumonia', 'bacterial', [r'mycoplasma', r'micoplasma', r'mycoplasme', r'支原体', r'黴漿菌', r'микоплазм\w*']),
    ('candida_auris', 'Candida auris', 'fungal', [r'candida auris', r'c\. auris', r'耳念珠菌']),
    ('scabies', 'Scabies', 'unknown', [r'scabies', r'\bsarna\b', r'escabiose', r'الجرب', r'疥疮', r'疥瘡', r'чесотк\w*']),
    ('chapare', 'Chapare haemorrhagic fever', 'zoonotic', [r'chapare']),
    ('cronobacter', 'Cronobacter', 'foodborne', [r'cronobacter']),
    ('hepatitis_b', 'Hepatitis B', 'viral', [r'hepatitis b\b', r'hépatite b\b', r'hepatite b\b', r'乙型肝炎', r'乙肝']),
    ('unknown', 'Undiagnosed illness', 'unknown', [r'unknown (?:illness|disease|a?etiology|origin|cause)', r'mystery (?:illness|disease)',
                                                     r'undiagnosed', r'pneumonia of (?:unknown|undetermined)', r'(?:unknown|undetermined) a?etiology',
                                                     r'unexplained (?:illness|deaths?)', r'undetermined (?:origin|cause)',
                                                     r'enfermedad (?:desconocida|misteriosa|no identificada)', r'mal (?:desconocido|misterioso)',
                                                     r'maladie (?:inconnue|mystérieuse|non identifiée)', r'doença (?:desconhecida|misteriosa)',
                                                     r'مرض (?:غامض|مجهول)', r'不明原因', r'不明疾病', r'неизвестн\w* (?:болезн|заболеван|инфекц)\w*',
                                                     r'загадочн\w* (?:болезн|заболеван)\w*', r'penyakit (?:misterius|tak dikenal)']),
]

CLASSES = {'viral', 'bacterial', 'vectorBorne', 'foodborne', 'zoonotic', 'fungal', 'unknown'}
BY_SLUG = {p[0]: {'slug': p[0], 'name': p[1], 'cls': p[2]} for p in PATHOGENS}

# Arabic and CJK aliases are matched without the (?<![\w-]) … (?![\w-]) wrapper: Arabic clitics (و، ب، ال) attach to
# the word and Chinese has no spaces, so a boundary rule would miss "والكوليرا" and "麻疹疫情".
_NO_BOUNDARY = re.compile(r'[؀-ۿ぀-ヿ㐀-鿿가-힯]')


def _compile(aliases):
    bounded = [a for a in aliases if not _NO_BOUNDARY.search(a)]
    free = [a for a in aliases if _NO_BOUNDARY.search(a)]
    parts = []
    if bounded:
        parts.append(r'(?<![\w-])(?:' + '|'.join(bounded) + r')(?![\w-])')
    if free:
        parts.append('(?:' + '|'.join(free) + ')')
    return re.compile('|'.join(parts), re.I)


_PATTERNS = [(_compile(aliases), slug) for slug, _, _, aliases in PATHOGENS]


def find(text: str) -> list:
    """Pathogens named in text, in order of first appearance, de-duplicated. Each: {slug, name, cls}."""
    if not text:
        return []
    hits = []
    for pat, slug in _PATTERNS:
        m = pat.search(text)
        if m:
            hits.append((m.start(), slug))
    seen, out = set(), []
    for _, slug in sorted(hits):
        if slug not in seen:
            seen.add(slug)
            out.append(BY_SLUG[slug])
    # a specific pathogen beats the generic 'unknown' bucket
    if len(out) > 1:
        out = [p for p in out if p['slug'] != 'unknown']
    return out


def primary(text: str):
    f = find(text)
    return f[0] if f else None


_OUTBREAK = re.compile(
    r'outbreak|epidemic|pandemic|cases? (?:reported|confirmed|rise|surge|spike)|cluster|public health emergency|health alert|'
    r'health advisory|quarantine|infections?\b|'
    r'brotes?\b|epidemia|casos\b|alerta (?:sanitaria|epidemiol[óo]gica)|cuarentena|contagios|'          # es
    r'épidémie|flambée|\bcas\b|alerte sanitaire|contaminations?\b|'                                        # fr
    r'surtos?\b|quarentena|'                                                                               # pt (epidemia, casos, alerta above)
    r'تفشي|وباء|[إا]صابات|حالات|حجر صحي|'                                                                  # ar
    r'疫情|爆发|爆發|暴发|病例|确诊|確診|'                                                                     # zh
    r'вспышк\w*|эпидеми\w*|случа\w*|заболевш\w*|заразивш\w*|карантин\w*|'                                  # ru
    r'wabah|kasus|\bklb\b|merebak|tertular', re.I)                                                         # id


def is_outbreak_language(text: str) -> bool:
    return bool(_OUTBREAK.search(text or ''))
