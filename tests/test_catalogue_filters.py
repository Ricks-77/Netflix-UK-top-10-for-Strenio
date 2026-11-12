import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import update as u

class LanguageCatalogues(unittest.TestCase):
    def test_primary_language_constraint_not_dubs_and_existing_thresholds(self):
        queries=[]
        def query(text):
            queries.append(text)
            return {"advancedTitleSearch":{"total":1,"pageInfo":{"hasNextPage":False},"edges":[{"node":{"title":{"id":"tt123","titleText":{"text":"Portuguese film"},"ratingsSummary":{"aggregateRating":8.1,"voteCount":11000}}}}]}}
        with patch.object(u,'imdb_graphql',side_effect=query):
            rows,*_=u.imdb_search('movie','pt')
        self.assertEqual(len(rows),1)
        self.assertIn('anyPrimaryLanguages: ["pt"]',queries[0])
        self.assertIn('ratingsCountRange: { min: 10000 }',queries[0])
        self.assertIn('aggregateRatingRange: { min: 7.5 }',queries[0])
        self.assertNotIn('anyLanguages',queries[0])

    def test_valid_empty_language_result_is_written_but_invalid_response_keeps_file(self):
        with tempfile.TemporaryDirectory()as d:
            path=Path(d)/'catalog/movie/imdb-last12m-movies-pt.json'
            with patch.object(u,'imdb_graphql',return_value={"advancedTitleSearch":{"total":0,"edges":[]}}):
                rows,*_=u.build_imdb_catalog('movie',path,'pt')
            self.assertEqual(rows,[])
            self.assertEqual(json.loads(path.read_text())['metas'],[])
            before=path.read_bytes()
            with patch.object(u,'imdb_graphql',return_value={"advancedTitleSearch":{}}):
                with self.assertRaises(RuntimeError):u.build_imdb_catalog('movie',path,'pt')
            self.assertEqual(path.read_bytes(),before)
            with patch.object(u,'imdb_graphql',return_value={"advancedTitleSearch":{"total":1,"edges":[]}}):
                with self.assertRaises(RuntimeError):u.build_imdb_catalog('movie',path,'pt')
            self.assertEqual(path.read_bytes(),before)

    def test_cinemeta_genres_are_retained_and_imdb_rating_wins(self):
        with patch.object(u,'get_json',return_value={'meta':{'id':'tt123','type':'movie','poster':'https://image.test/a.jpg','name':'Animated film','genres':['Animation','Comedy'],'imdbRating':'5.0'}}):
            row=u.resolve_cinemeta_id({'id':'tt123','name':'Original','rating':8.2},'movie')
        self.assertEqual(row['genres'],['Animation','Comedy'])
        self.assertEqual(row['imdbRating'],'8.2')

    def test_wrong_identity_type_or_missing_classification_is_omitted(self):
        for fields in ({'id':'tt999','type':'movie','genres':['Animation']},{'id':'tt123','type':'series','genres':['Animation']},{'id':'tt123','type':'movie'}):
            with patch.object(u,'get_json',return_value={'meta':{'poster':'https://image.test/a.jpg',**fields}}):
                self.assertIsNone(u.resolve_cinemeta_id({'id':'tt123','name':'Original','rating':8.2},'movie'))

    def test_unknown_language_cannot_broaden_source(self):
        with self.assertRaises(ValueError):u.imdb_search('movie','xx')

    def test_all_union_contains_scoped_titles_and_keeps_rating_vote_order(self):
        a={'id':'tt1','name':'All','imdbRating':'8.2','imdbVotes':6000}
        b={'id':'tt2','name':'Scoped','imdbRating':'8.2','imdbVotes':5000}
        c={'id':'tt3','name':'Other','imdbRating':'8.5','imdbVotes':12000}
        rows=u.merge_imdb_catalogues([[a],[b,a],[c]])
        self.assertEqual([r['id']for r in rows],['tt3','tt1','tt2'])

    def test_failed_language_refresh_preserves_all_four_published_files(self):
        with tempfile.TemporaryDirectory()as d,patch.object(u,'OUT',Path(d)):
            paths=[Path(d)/f'catalog/movie/imdb-last12m-movies{s}.json'for s in ('','-en','-pt','-es')]
            for path in paths:path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{"metas":[]}')
            before={p:p.read_bytes()for p in paths}
            def build(media,path,language=None):
                path.write_text('{"metas":[{"new":true}]}')
                if language=='pt':raise RuntimeError('upstream unavailable')
                return [],u.date(2026,11,12),u.date(2026,11,12),'source'
            with patch.object(u,'build_imdb_catalog',side_effect=build):
                with self.assertRaises(RuntimeError):u.refresh_imdb_type('movie')
            self.assertTrue(all(p.read_bytes()==original for p,original in before.items()))

if __name__=='__main__':unittest.main()
