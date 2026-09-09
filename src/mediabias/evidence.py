import hashlib
import json
from .config import DATA
from .schema import Comparison, Finding

TAXONOMY = json.loads((DATA / 'taxonomy.json').read_text())
CONCEPTS = {entry['id']: entry for entry in TAXONOMY['entries']}
TAXONOMY_HASH = hashlib.sha256((DATA / 'taxonomy.json').read_bytes()).hexdigest()


def ground_evidence(items, articles):
    for item in items:
        article = articles.get(item.article_id)
        if article is None:
            raise ValueError('Evidence references an unknown article.')
        start = article['text'].find(item.quote)
        if start < 0:
            raise ValueError('Evidence quotation does not occur in the analyzed snapshot.')
        # A supplied offset must resolve to the same text; repeated quotes may use later spans.
        if item.start is not None:
            start = item.start
            if start < 0 or article['text'][start:start + len(item.quote)] != item.quote:
                raise ValueError('Evidence start offset is invalid.')
        end = start + len(item.quote)
        if item.end is not None and item.end != end:
            raise ValueError('Evidence end offset is invalid.')
        item.start, item.end = start, end


def ground_finding(finding: Finding, articles):
    concept = CONCEPTS.get(finding.concept_id)
    if not concept or concept['layer'] != finding.layer:
        raise ValueError('Unknown concept or incorrect taxonomy layer.')
    ground_evidence(finding.evidence, articles)
    contexts = [articles[e.article_id] for e in finding.evidence]
    if finding.concept_id in {'contextual_omission', 'one_sided_sourcing', 'selective_statistics'}:
        complete = all(a['content_scope'] == 'full_text' and not a.get('truncated') for a in contexts)
        comparative = len({a['source_id'] for a in contexts}) >= 2
        if not (complete and comparative):
            finding.status = 'candidate'
            finding.missing_context_tr = 'Tam, karşılaştırılabilir metin ve ilgili karşı kanıt gerekli. ' + finding.missing_context_tr
    if finding.status == 'candidate' and not finding.missing_context_tr.strip():
        raise ValueError('Candidate findings must state what evidence is missing.')
    return finding


def validate_comparison(value, snapshots):
    result = Comparison.model_validate(value)
    articles = {a['id']: a for a in snapshots}
    ids = [f.id for f in result.findings]
    if len(ids) != len(set(ids)):
        raise ValueError('Finding IDs must be unique.')
    for f in result.findings:
        ground_finding(f, articles)
    for c in result.what_happened + result.agreements + result.differences:
        ground_evidence(c.evidence, articles)
    for c in result.agreements + result.differences:
        if len({articles[e.article_id]['source_id'] for e in c.evidence}) < 2:
            raise ValueError('Cross-source claims require at least two distinct outlets.')
    for d in result.differences:
        if set(d.finding_ids) - set(ids):
            raise ValueError('Difference refers to missing findings.')
    if any(a.get('truncated') for a in snapshots):
        result.limitations_tr.append('Bazı metinler uzunluk sınırında kesildi; bulunmayan bağlam hakkında sonuç çıkarılamaz.')
    if any(a['content_scope'] != 'full_text' for a in snapshots):
        result.limitations_tr.append('Özetler tam haberi temsil etmeyebilir; bağlamın yokluğu kanıtlanmış değildir.')
    return result
