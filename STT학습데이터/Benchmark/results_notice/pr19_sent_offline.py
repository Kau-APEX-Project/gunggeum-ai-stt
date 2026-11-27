import asyncio, json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ['TRANSLATE_PROVIDER'] = 'aws'
for k in ('TRANSLATE_TERM_MODE', 'TRANSLATE_TERMS_PATH', 'TRANSLATE_TERMINOLOGY'):
    os.environ.pop(k, None)
from app.live import translate as T
sent = []
class C:
    def translate_text(self, Text, SourceLanguageCode, TargetLanguageCode, **kw):
        sent.append(Text); return {'TranslatedText': Text}
tr = T.AwsTranslator(C())
for line in open(sys.argv[2], encoding='utf-8'):
    r = json.loads(line)
    for f in ('title', 'body'):
        sent.clear()
        asyncio.run(tr.translate(r[f], 'en', notice=True))
        print(f"[{r['id']} {f}]\n  원문 {r[f]!r}\n  보냄 {sent[0]!r}")
