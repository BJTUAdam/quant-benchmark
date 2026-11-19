from __future__ import annotations

import json
from pathlib import Path
import unittest

from deployment.build_strategy_skill import ArticleMarkdown
from finlab.docs import inline, markdown


class OfficialDocumentationTests(unittest.TestCase):
    def test_article_keeps_units_code_and_ignores_site_scripts(self):
        source = '''<html><nav>site menu</nav><div class="content col-md-9 col-sm-8 col-xs-12">
        <div class="search-panel"><span>search controls</span></div><h2>Test API</h2>
        <p>每次6000条；vol单位：手；amount单位：千元。</p>
        <table><tr><th>名称</th><th>说明</th></tr><tr><td>ts_code</td><td>A|B<br>代码</td></tr></table>
        <pre><code>if available:\n    pro.daily(ts_code="000001.SZ")\n</code></pre>
        <script>steal_secret()</script></div><footer>not article</footer></html>'''
        parser = ArticleMarkdown('https://tushare.pro/document/2?doc_id=27')
        parser.feed(source)
        body = parser.result()
        self.assertIn('amount单位：千元', body)
        self.assertIn('    pro.daily', body)
        self.assertIn('A\\|B / 代码', body)
        for unrelated in ['site menu', 'search controls', 'steal_secret', 'not article']:
            self.assertNotIn(unrelated, body)

    def test_missing_or_truncated_article_cannot_be_successful(self):
        for source in ['<p>login page</p>', '<div class="content col-md-9"><p>truncated']:
            parser = ArticleMarkdown('https://tushare.pro/')
            parser.feed(source)
            with self.assertRaises(ValueError): parser.result()

    def test_code_example_cannot_turn_into_executable_html(self):
        result = markdown('```python\nprint("</script><script>unsafe()</script>")\n```')
        self.assertNotIn('<script>', result)
        self.assertIn('&lt;script&gt;', result)

    def test_document_links_work_and_reject_executable_url_schemes(self):
        self.assertIn('href="references/sdk.md"', inline('[SDK](references/sdk.md)'))
        self.assertNotIn('href=', inline('[unsafe](javascript:alert)'))
        self.assertIn('href="https://tushare.pro/"', inline('[source](https://tushare.pro/)'))

    def test_all_pro_apis_have_actual_parameter_and_output_tables(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / 'catalog/tushare_pro.v2.json').read_text(encoding='utf-8'))
        base = root / 'skills/finlab-strategy/references/tushare'
        snapshot = json.loads((base / 'snapshot.json').read_text(encoding='utf-8'))
        articles = {x['api_name']: x for x in snapshot['documents'] if x.get('api_name')}
        self.assertEqual(set(articles), set(catalog['endpoints']))
        for name, item in articles.items():
            with self.subTest(api=name):
                self.assertEqual(item['content_status'], 'full_article')
                content = (base / item['path']).read_text(encoding='utf-8')
                for field in item['official_params'] + item['official_fields']:
                    self.assertIn(field, content)
                self.assertIn('| ---', content)


if __name__ == '__main__': unittest.main()
