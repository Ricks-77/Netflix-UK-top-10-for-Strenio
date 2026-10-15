import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import update as u

TODAY = date(2026, 10, 14)


def chart(day='2026-10-14'):
    return dict(date=day, source='verified FlixPatrol chart',
                movies=[f'Movie {i}' for i in range(1, 11)],
                series=[f'Series {i}' for i in range(1, 11)])


def html(day='October 14, 2026', country='the United Kingdom', count=10):
    body = f'<h1>TOP 10 on Netflix in {country} on {day}</h1>'
    for heading, prefix in [('TOP 10 Movies', 'Movie'), ('TOP 10 TV Shows', 'Series')]:
        body += f'<h3>{heading}</h3><table><tbody>'
        for i in range(1, count + 1):
            body += f'<tr><td>{i}.</td><td><a href="/title/{prefix}-{i}/">{prefix} {i}</a></td></tr>'
        body += '</tbody></table>'
    return body


def response(text):
    return Mock(text=text, raise_for_status=Mock())


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.local = self.root / 'rankings.json'
        self.out = self.root / 'docs'
        self.out.mkdir()
        for name, value in [('LOCAL_RANKINGS', self.local), ('OUT', self.out)]:
            p = patch.object(u, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(u, 'london_today', return_value=TODAY); p.start(); self.addCleanup(p.stop)

    def save(self, data):
        self.local.write_text(json.dumps(data))

    def test_live_attempted_before_stale_local(self):
        self.save(chart('2026-10-05'))
        with patch.object(u.requests, 'get', return_value=response(html())) as get:
            data = u.fetch_netflix_rankings()
        self.assertEqual(data['source'], 'direct')
        self.assertEqual(get.call_count, 1)
        self.assertTrue(get.call_args.args[0].endswith('/2026-10-14/'))

    def test_live_preferred_over_fresh_local(self):
        self.save(chart())
        with patch.object(u.requests, 'get', return_value=response(html())):
            self.assertEqual(u.fetch_netflix_rankings()['source'], 'direct')

    def test_jina_fallback(self):
        md = 'Title: TOP 10 on Netflix in the United Kingdom on October 14, 2026\n'
        for heading, prefix in [('TOP 10 Movies', 'Movie'), ('TOP 10 TV Shows', 'Series')]:
            md += f'### {heading}\n' + '\n'.join(f'{i}. [{prefix} {i}](https://flixpatrol.com/title/{i}/)' for i in range(1,11)) + '\n'
        with patch.object(u.requests, 'get', side_effect=[RuntimeError('blocked'), response(md)]):
            self.assertEqual(u.fetch_netflix_rankings()['source'], 'jina')

    def test_fresh_local_fallback_records_errors(self):
        self.save(chart())
        with patch.object(u.requests, 'get', side_effect=RuntimeError('offline')) as get:
            data = u.fetch_netflix_rankings()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(data['source'], 'verified-local')
        self.assertEqual(len(data['retrieval_errors']), 2)

    def test_bad_local_dates_fail(self):
        for day in ['2026-10-05', '2026-10-13', '2026-10-15', '', 'not a date']:
            with self.subTest(day=day):
                self.save(chart(day))
                with patch.object(u.requests, 'get', side_effect=RuntimeError('offline')):
                    with self.assertRaises(RuntimeError): u.fetch_netflix_rankings()

    def test_local_missing_date_and_provenance_fail(self):
        for key in ['date', 'source']:
            data = chart(); del data[key]; self.save(data)
            with patch.object(u.requests, 'get', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError): u.fetch_netflix_rankings()

    def test_invalid_title_lists(self):
        for bad in [[], ['x']*10, ['']*10, [None]*10, '0123456789', chart()['movies'][:9]]:
            data = chart(); data['movies'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError): u.validate_rankings(data, TODAY)

    def test_wrong_chart_date_country_missing_heading_fail(self):
        for body in [html('October 5, 2026'), html(country='France'), html().replace('<h1>', '<p>').replace('</h1>', '</p>')]:
            with self.subTest(body=body):
                with patch.object(u.requests, 'get', return_value=response(body)):
                    with self.assertRaises(RuntimeError): u.fetch_netflix_rankings()

    def test_navigation_date_is_not_chart_date(self):
        with self.assertRaises(ValueError):
            u.verify_chart_heading(html('October 5, 2026') + '<a>October 14, 2026</a>', TODAY)

    def test_partial_chart_fails(self):
        with patch.object(u.requests, 'get', return_value=response(html(count=8))):
            with self.assertRaises(RuntimeError): u.fetch_netflix_rankings()

    def seed(self):
        self.save(chart('2026-10-05'))
        for media, slug in [('movie','movies'), ('series','series')]:
            u.write_catalog(self.out / f'catalog/{media}/netflix-uk-top10-{slug}.json', [{'id': 'old', 'name': 'Old'}])
        (self.out/'status.json').write_text(json.dumps({'netflix': {'source':'verified-local', 'rankings_date':'2026-10-05', 'last_success_at':'old'}}))
        return {p: p.read_bytes() for p in self.out.rglob('*') if p.is_file() and p.name != 'status.json'} | {self.local:self.local.read_bytes()}

    def main(self):
        with patch.object(u, 'build_imdb_catalog', side_effect=RuntimeError('IMDb unavailable')), contextlib.redirect_stdout(io.StringIO()):
            return u.main()

    def test_source_failure_preserves_catalog_and_writes_failure_status(self):
        before = self.seed()
        with patch.object(u.requests, 'get', side_effect=RuntimeError('offline')):
            self.assertEqual(self.main(), 1)
        for p, content in before.items(): self.assertEqual(p.read_bytes(), content)
        status = json.loads((self.out/'status.json').read_text())['netflix']
        self.assertFalse(status['refresh_success']); self.assertTrue(status['stale'])
        self.assertEqual(status['rankings_date'], '2026-10-05')
        self.assertEqual(status['last_success_at'], 'old')

    def test_second_catalog_resolution_failure_preserves_both(self):
        before = self.seed()
        with patch.object(u, 'fetch_netflix_rankings', return_value=chart() | {'retrieval_errors': []}), patch.object(u, 'build_netflix_catalog', side_effect=[([{'id':'new'}], []), RuntimeError('series failed')]):
            self.main()
        for p, content in before.items(): self.assertEqual(p.read_bytes(), content)

    def test_missing_or_duplicate_metadata_fails_without_overwrite(self):
        path = self.out/'catalog.json'; path.write_text('previous')
        for meta in [None, {'id':'tt1', 'name':'same'}]:
            with patch.object(u, 'resolve_cinemeta_title', return_value=meta):
                with self.assertRaises(RuntimeError): u.build_netflix_catalog(chart()['movies'], 'movie', path)
            self.assertEqual(path.read_text(), 'previous')

    def test_success_updates_cache_and_status_even_when_imdb_fails(self):
        self.seed()
        with patch.object(u, 'fetch_netflix_rankings', return_value=chart() | {'retrieval_errors': []}), patch.object(u, 'resolve_cinemeta_title', side_effect=lambda title, media: {'id':title, 'name':title}):
            self.assertEqual(self.main(), 1)  # independent IMDb failure
        self.assertEqual(json.loads(self.local.read_text())['date'], '2026-10-14')
        status = json.loads((self.out/'status.json').read_text())['netflix']
        self.assertTrue(status['refresh_success']); self.assertFalse(status['stale'])
        self.assertEqual(status['rankings_date'], '2026-10-14')

    def test_publication_write_failure_rolls_back_both_and_cache(self):
        before = self.seed()
        original = u.write_catalog
        def fail_series(path, metas):
            if '/series/' in str(path):
                raise OSError('disk write failed')
            original(path, metas)
        with patch.object(u, 'fetch_netflix_rankings', return_value=chart() | {'retrieval_errors': []}), patch.object(u, 'resolve_cinemeta_title', side_effect=lambda title, media: {'id':title, 'name':title}), patch.object(u, 'write_catalog', side_effect=fail_series):
            self.main()
        for p, content in before.items(): self.assertEqual(p.read_bytes(), content)

    def test_full_success_exit_code(self):
        self.seed()
        with patch.object(u, 'fetch_netflix_rankings', return_value=chart() | {'retrieval_errors': []}), patch.object(u, 'resolve_cinemeta_title', side_effect=lambda title, media: {'id':title, 'name':title}), patch.object(u, 'build_imdb_catalog', return_value=([], TODAY, TODAY, 'test')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(u.main(), 0)

    def test_html_rank_gap_is_rejected(self):
        body = html().replace('<td>5.</td>', '<td>6.</td>')
        self.assertEqual(u.parse_html_section(body, 'TOP 10 Movies'), [])

    def test_duplicate_markdown_rank_is_rejected(self):
        with self.assertRaises(ValueError):
            u.parse_markdown_section('### TOP 10 Movies\n1. [First](/title/a/)\n1. [Second](/title/b/)', 'TOP 10 Movies')


if __name__ == '__main__': unittest.main()
