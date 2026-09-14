import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import daily_sources as s
from test_daily_editorial import fixture


def response(title='视频模型新增参考控制', link='new', selected=False, more=False, cursor=None):
    return {'schemaVersion':1,'items':[{'title':title,'summary':'视频模型增加参考控制，实际适用范围尚未公布。','selected':selected,'source':{'name':'官方'},'links':{'aihot':'https://aihot.news/items/'+link}}], 'page':{'hasMore':more,'nextCursor':cursor}},{}


class SourcesTests(unittest.TestCase):
    def test_unselected_creative_news_is_not_lost(self):
        original=fixture()[0]
        result=s.supplement_creation(original,lambda url:response(),queries=('视频',))
        self.assertEqual(len(result['items']),2)
        self.assertFalse(result['items'][1]['selected'])
        self.assertEqual(result['items'][1]['matchedQueries'],['视频'])
        self.assertEqual(len(original['items']),1)

    def test_same_news_across_queries_is_merged(self):
        result=s.supplement_creation(fixture()[0],lambda url:response(),queries=('视频','图像'))
        self.assertEqual(len(result['items']),2)
        self.assertEqual(result['items'][1]['matchedQueries'],['视频','图像'])

    def test_pagination_retains_query_and_follows_cursor(self):
        fetch=Mock(side_effect=[response(more=True,cursor='opaque+/'),response(link='second')])
        result=s.supplement_creation(fixture()[0],fetch,queries=('视频',))
        query=parse_qs(urlparse(fetch.call_args_list[1].args[0]).query)
        self.assertEqual(query['cursor'],['opaque+/']);self.assertEqual(query['q'],['视频'])
        self.assertEqual(len(result['items']),3)

    def test_failed_query_is_not_treated_as_no_creative_news(self):
        with self.assertRaises(OSError):s.supplement_creation(fixture()[0],Mock(side_effect=OSError()),queries=('视频',))

    def test_missing_or_repeated_cursor_fails(self):
        with self.assertRaises(s.p.PipelineError):s.supplement_creation(fixture()[0],lambda url:response(more=True,cursor=None),queries=('视频',))
        with self.assertRaises(s.p.PipelineError):s.supplement_creation(fixture()[0],lambda url:response(more=True,cursor='same'),queries=('视频',))

if __name__=='__main__':unittest.main()
