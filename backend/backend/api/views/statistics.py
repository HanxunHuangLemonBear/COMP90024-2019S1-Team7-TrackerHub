# coding: utf-8

import logging
import ujson
from shapely.geometry import shape, point

from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseNotFound, FileResponse
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from backend.handler.couch_handler import couch_db_handler
from backend.handler.influxdb_handler import influxdb_handler
from backend.common.couchdb_map import statistics_track_random
from backend.common.utils import make_dict, init_http_not_found, init_http_success, check_api_key, make_json_response, str_to_str_datetime_utc
from backend.config.config import COUCHDB_TWEET_DB


logger = logging.getLogger('django.debug')
tweet_couch_db = couch_db_handler.get_database(COUCHDB_TWEET_DB)


@require_http_methods(['GET', 'OPTIONS'])
@check_api_key
def statistics_time_router(request, *args, **kwargs):
    if request.method == 'GET':
        return statistics_time_get(request)
    elif request.method == 'OPTIONS':
        response = HttpResponse()
        response['Access-Control-Request-Method'] = '*'
        response['Access-Control-Allow-Headers'] = '*'
        return response
    return HttpResponseNotAllowed()


@require_http_methods(['GET', 'OPTIONS'])
@check_api_key
def statistics_track_router(request, *args, **kwargs):
    user_id = None
    number = 100
    for arg in args:
        if isinstance(arg, dict):
            user_id = arg.get('user_id', None)
            number = arg.get('number', 100)

    if request.method == 'GET':
        return statistics_track_get(request, user_id=user_id, number=number)
    elif request.method == 'OPTIONS':
        response = HttpResponse()
        response['Access-Control-Request-Method'] = '*'
        response['Access-Control-Allow-Headers'] = '*'
        return response
    return HttpResponseNotAllowed()


def statistics_time_get(request):
    key = ['start_time', 'end_time', 'tags']
    content = ujson.loads(request.body)
    content = make_dict(key, content)

    # melb_json = ujson.load(open('../../common/melb_geo.json'))
    # print(melb_json)

    if 'start_time' in content:
        start_time = parse_datetime(content['start_time']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S%z')
    if 'end_time' in content:
        end_time = parse_datetime(content['end_time']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S%z')


def upload_statistics_file():
    pass


def down_statistics_file(request):
    pass


def statistics_track_get(request, user_id=None, number=100):

    def get_tags(tags, needed, value, ignore=None):
        result = []
        if needed in tags:
            for tag in tags[needed]:
                if tags[needed][tag] > value and (not ignore or tag not in ignore):
                    result.append(tag)
        return result

    params = ujson.loads(request.body) if request.body else {}

    start_time = params.get('start_time', None)
    start_time = str_to_str_datetime_utc(start_time) if start_time else None
    end_time = params.get('end_time', None)
    end_time = str_to_str_datetime_utc(end_time) if end_time else None
    target_tag = params.get('tags', [])
    skip = params.get('skip', 0)
    threshold = params.get('threshold', 0.9)

    mango = statistics_track_random(start_time=start_time, end_time=end_time, user_id=user_id, limit=100000, skip=skip)

    while True:
        try:
            tweets = tweet_couch_db.find(mango)
            break
        except Exception as e:
            logger.debug('Query Timeout %s' % e)
            influxdb_handler.make_point(key='api/statistics/track/:user_id/', method='GET', error=500, prefix='API')
            continue

    results = {}
    for tweet in tweets:
        user = tweet.get('user')
        results.update({user: []}) if user not in results else None
        tags = tweet.get('tags')
        result_tags = []
        if 'gluttony' in target_tag:
            _result_tags = get_tags(tags, 'food179', threshold, ['non_food'])
            if _result_tags:
                result_tags.append('gluttony')
        if 'lust' in target_tag:
            _result_tags = get_tags(tags, 'nsfw', threshold, ['neutral'])
            if _result_tags:
                result_tags.append('lust')
        _result_tags = get_tags(tags, 'text', threshold)
        for _tag in _result_tags:
            if _tag in target_tag:
                result_tags.append(_tag + '.text')

        results[user].append(dict(
            time=parse_datetime(tweet.get('date')).astimezone(timezone.get_current_timezone()).strftime('%Y-%m-%d %H:%M:%S%z'),
            geo=tweet.get('geo'),
            img_id=tweet.get('img_id'),
            tags=result_tags
        ))

    results = sorted(results.items(), key=lambda item: len(item[1]), reverse=True)[0: number]

    if user_id:
        influxdb_handler.make_point(key='api/statistics/track/:user_id/', method='GET', error='success', prefix='API', user=len(results))
    else:
        influxdb_handler.make_point(key='api/statistics/track/random/', method='GET', error='success', prefix='API', user=len(results))
    resp = init_http_success()
    resp['data'].update(results)
    return make_json_response(HttpResponse, resp)


if __name__ == '__main__':
    statistics_track_get(None)


