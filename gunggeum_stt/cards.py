"""번역 자막 속 전각·용어 이름 → [이름](card:<id>) 링크.

gunggeum-ai-llm-gps/server/cards.py 의 link_names(glosses=False) 를 그대로 가져온 것(자막용 부분만).
RAG 답변과 같은 규칙으로 링크해야 앱이 같은 카드를 연다. 원본이 바뀌면 여기도 맞춘다.
"""
import re

LINK = re.compile(r"\[([^\[\]]*)\]\(card:([bt]-\d+)\)")


def card_id(meta):
    return f"b-{meta['serial']}" if meta["type"] == "building" else f"t-{meta['no']}"


def unique(labels):
    """{이름: 카드 id 집합} → {이름: 카드 id}. 같은 이름이 여럿이면 전각 우선, 용어끼리면 뺌."""
    out = {}
    for k, ids in labels.items():
        ids = {c for c in ids if c.startswith("b-")} or ids
        if len(ids) == 1:
            out[k] = ids.pop()
    return out


def link_names(text, metas, language):
    """자막에서 처음 나오는 카드 이름만 [이름](card:<id>) 로 감싼다. 언어별 표기(name_en/name_th)와 한국어 이름으로 찾음.
    태국어는 띄어쓰기가 없어 짧은 음역이 낱말 속에 걸리므로 용어집이 넣는 전체 표기로만 찾는다."""
    field = {"English": "name_en", "ภาษาไทย": "name_th"}.get(language, "name")
    labels = {}
    for m in metas:
        own = m.get(field) or ""
        if field == "name_th" and own:
            labels.setdefault(own.lower(), set()).add(card_id(m))
            own = ""
        for p in re.sub(r"\(.*?\)", "", f"{own}/{m.get('name') or ''}").split("/"):
            p = p.strip()
            if len(p) > 1:
                labels.setdefault(p.lower(), set()).add(card_id(m))
    labels = unique(labels)
    if not labels:
        return text
    seen = set(c for _, c in LINK.findall(text))

    def one(m):
        cid = labels[m[1].lower()]
        if cid in seen:
            return m[0]
        seen.add(cid)
        return f"[{m[1]}](card:{cid})"
    names = re.compile(f"(?<![A-Za-z])({'|'.join(map(re.escape, sorted(labels, key=len, reverse=True)))})(?![A-Za-z])", re.I)
    parts = re.split(r"(\[[^\[\]]*\]\(card:[bt]-\d+\))", text)
    return "".join(p if i % 2 else names.sub(one, p) for i, p in enumerate(parts))
