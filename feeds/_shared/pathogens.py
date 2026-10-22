"""Canonical pathogen list for HealthEvent.pathogen and pathogen_class.

classes: viral | bacterial | vectorBorne | foodborne | zoonotic | fungal | unknown
Order matters where aliases overlap (MERS before COVID, bird flu before influenza,
Guinea worm would collide with 'Guinea' so it is not here). Aliases are regexes,
matched case-insensitively on word boundaries.
"""
import re

PATHOGENS = [
    # slug, display name, class, aliases
    ('mers', 'MERS-CoV', 'viral', [r'mers(?:-cov)?', r'middle east respiratory syndrome']),
    ('h5n1', 'Avian influenza (H5N1)', 'zoonotic', [r'h5n1', r'h5n\d', r'h7n9', r'h9n2', r'avian (?:influenza|flu)', r'bird flu', r'hpai', r'highly pathogenic avian']),
    ('covid19', 'COVID-19', 'viral', [r'covid(?:-| )?19', r'covid', r'sars-cov-2', r'coronavirus(?! disease 2019)', r'omicron']),
    ('influenza', 'Influenza', 'viral', [r'influenza', r'\bflu\b', r'h1n1', r'h3n2']),
    ('rsv', 'RSV', 'viral', [r'\brsv\b', r'respiratory syncytial']),
    ('measles', 'Measles', 'viral', [r'measles', r'rubeola']),
    ('mumps', 'Mumps', 'viral', [r'mumps']),
    ('rubella', 'Rubella', 'viral', [r'rubella']),
    ('varicella', 'Chickenpox', 'viral', [r'varicella', r'chickenpox', r'chicken pox']),
    ('pertussis', 'Pertussis', 'bacterial', [r'pertussis', r'whooping cough']),
    ('diphtheria', 'Diphtheria', 'bacterial', [r'diphtheria']),
    ('polio', 'Poliovirus', 'viral', [r'polio(?:myelitis|virus)?', r'cvdpv\d?', r'vaccine-derived poliovirus']),
    ('ebola', 'Ebola disease', 'viral', [r'ebola(?:virus)?', r'bundibugyo', r'sudan virus disease', r'sudan ebolavirus', r'\bsvd\b', r'\bevd\b']),
    ('marburg', 'Marburg', 'viral', [r'marburg']),
    ('lassa', 'Lassa fever', 'zoonotic', [r'lassa']),
    ('nipah', 'Nipah virus', 'zoonotic', [r'nipah']),
    ('hantavirus', 'Hantavirus', 'zoonotic', [r'hantavirus', r'hanta']),
    ('cchf', 'Crimean-Congo haemorrhagic fever', 'vectorBorne', [r'crimean-?congo', r'\bcchf\b']),
    ('rvf', 'Rift Valley fever', 'vectorBorne', [r'rift valley']),
    ('mpox', 'Mpox', 'viral', [r'mpox', r'monkeypox', r'clade i[ab]?\b']),
    ('cholera', 'Cholera', 'bacterial', [r'cholera', r'vibrio cholerae', r'acute watery diarrh']),
    ('typhoid', 'Typhoid', 'bacterial', [r'typhoid', r'enteric fever', r'salmonella typhi']),
    ('shigella', 'Shigellosis', 'foodborne', [r'shigell(?:a|osis)']),
    ('salmonella', 'Salmonella', 'foodborne', [r'salmonell(?:a|osis)(?! typhi)']),
    ('ecoli', 'E. coli (STEC)', 'foodborne', [r'e\.? ?coli', r'\bstec\b', r'o157', r'escherichia']),
    ('listeria', 'Listeria', 'foodborne', [r'listeri(?:a|osis)']),
    ('botulism', 'Botulism', 'foodborne', [r'botuli(?:sm|num)']),
    ('campylobacter', 'Campylobacter', 'foodborne', [r'campylobacter']),
    ('vibrio', 'Vibriosis', 'foodborne', [r'vibrio(?:sis)?(?! cholerae)', r'vibrio vulnificus']),
    ('norovirus', 'Norovirus', 'viral', [r'norovirus', r'norwalk']),
    ('rotavirus', 'Rotavirus', 'viral', [r'rotavirus']),
    ('hepatitis_a', 'Hepatitis A', 'viral', [r'hepatitis a\b', r'\bhav\b']),
    ('hepatitis_e', 'Hepatitis E', 'viral', [r'hepatitis e\b']),
    ('cyclospora', 'Cyclospora', 'foodborne', [r'cyclospor(?:a|iasis)']),
    ('dengue', 'Dengue', 'vectorBorne', [r'dengue']),
    ('chikungunya', 'Chikungunya', 'vectorBorne', [r'chikungunya']),
    ('zika', 'Zika', 'vectorBorne', [r'zika']),
    ('yellow_fever', 'Yellow fever', 'vectorBorne', [r'yellow fever']),
    ('malaria', 'Malaria', 'vectorBorne', [r'malaria']),
    ('west_nile', 'West Nile virus', 'vectorBorne', [r'west nile']),
    ('oropouche', 'Oropouche', 'vectorBorne', [r'oropouche']),
    ('jev', 'Japanese encephalitis', 'vectorBorne', [r'japanese encephalitis']),
    ('leishmaniasis', 'Leishmaniasis', 'vectorBorne', [r'leishmaniasis']),
    ('plague', 'Plague', 'zoonotic', [r'\bplague\b', r'yersinia pestis']),
    ('anthrax', 'Anthrax', 'zoonotic', [r'anthrax']),
    ('rabies', 'Rabies', 'zoonotic', [r'rabies']),
    ('brucellosis', 'Brucellosis', 'zoonotic', [r'brucell(?:a|osis)']),
    ('leptospirosis', 'Leptospirosis', 'zoonotic', [r'leptospir(?:a|osis)']),
    ('tularemia', 'Tularemia', 'zoonotic', [r'tularemia']),
    ('meningitis', 'Meningococcal disease', 'bacterial', [r'meningococc\w+', r'meningitis']),
    ('legionella', "Legionnaires' disease", 'bacterial', [r'legionnaire', r'legionell']),
    ('tb', 'Tuberculosis', 'bacterial', [r'tuberculosis', r'\btb\b(?! test)']),
    ('strep_a', 'Group A strep', 'bacterial', [r'group a strep\w*', r'scarlet fever', r'\bigas\b']),
    ('mycoplasma', 'Mycoplasma pneumonia', 'bacterial', [r'mycoplasma']),
    ('candida_auris', 'Candida auris', 'fungal', [r'candida auris', r'c\. auris']),
    ('scabies', 'Scabies', 'unknown', [r'scabies']),
    ('chapare', 'Chapare haemorrhagic fever', 'zoonotic', [r'chapare']),
    ('cronobacter', 'Cronobacter', 'foodborne', [r'cronobacter']),
    ('hepatitis_b', 'Hepatitis B', 'viral', [r'hepatitis b\b']),
    ('unknown', 'Undiagnosed illness', 'unknown', [r'unknown (?:illness|disease|a?etiology|origin|cause)', r'mystery (?:illness|disease)',
                                                     r'undiagnosed', r'pneumonia of (?:unknown|undetermined)', r'(?:unknown|undetermined) a?etiology',
                                                     r'unexplained (?:illness|deaths?)', r'undetermined (?:origin|cause)']),
]

CLASSES = {'viral', 'bacterial', 'vectorBorne', 'foodborne', 'zoonotic', 'fungal', 'unknown'}
BY_SLUG = {p[0]: {'slug': p[0], 'name': p[1], 'cls': p[2]} for p in PATHOGENS}

_PATTERNS = [(re.compile(r'(?<![\w-])(?:' + '|'.join(aliases) + r')(?![\w-])', re.I), slug) for slug, _, _, aliases in PATHOGENS]


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


def is_outbreak_language(text: str) -> bool:
    return bool(re.search(r'outbreak|epidemic|pandemic|cases? (?:reported|confirmed|rise|surge|spike)|cluster|'
                          r'public health emergency|health alert|health advisory|quarantine|infections?\b', text or '', re.I))
