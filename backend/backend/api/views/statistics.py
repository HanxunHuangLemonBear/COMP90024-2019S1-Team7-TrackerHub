# coding: utf-8

import logging
import ujson
import time
from shapely.geometry import shape, point

from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from backend.handler.couch_handler import couch_db_banlancer
from backend.handler.influxdb_handler import influxdb_handler
from backend.handler.object_storage_handler import json_storage_handler
from backend.common.couchdb_map import statistics_track_random
from backend.common.utils import make_dict, init_http_not_found, init_http_success, check_api_key, make_json_response, \
    str_to_str_datetime_utc
from backend.config.config import COUCHDB_TWEET_DB
from backend.settings import BASE_DIR


logger = logging.getLogger('django.debug')
tweet_couch_db = couch_db_banlancer
melb_json = ujson.load(open(BASE_DIR + '/backend/common/melb_geo.json'))


@require_http_methods(['POST', 'OPTIONS'])
@check_api_key
def statistics_time_router(request, *args, **kwargs):
    if request.method == 'POST':
        return statistics_time_get(request)
    elif request.method == 'OPTIONS':
        return HttpResponse()
    return HttpResponseNotAllowed()


@require_http_methods(['POST', 'OPTIONS'])
@check_api_key
def statistics_track_router(request, *args, **kwargs):
    user_id = None
    number = 100
    for arg in args:
        if isinstance(arg, dict):
            user_id = arg.get('user_id', None)
            number = arg.get('number', 100)

    if request.method == 'POST':
        return statistics_track_get(request, user_id=user_id, number=number)
    elif request.method == 'OPTIONS':
        return HttpResponse()
    return HttpResponseNotAllowed()


def statistics_time_get(request):
    key = ['start_time', 'end_time', 'tags']
    content = ujson.loads(request.body)
    content = make_dict(key, content)

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
                if isinstance(tags[needed][tag], str):
                    result.append(tags[needed][tag])
                elif (tags[needed][tag] > value) and (not ignore or tag not in ignore):
                    result.append(tag)
        return result

    def make_this_point(_length, _timer):
        if user_id:
            influxdb_handler.make_point(key='api/statistics/track/:user_id/', method='GET', error='success',
                                        prefix='API', user=_length, timer=_timer)
        else:
            influxdb_handler.make_point(key='api/statistics/track/random/', method='GET', error='success', prefix='API',
                                        user=_length, timer=_timer)

    start_timer = time.time()

    params = ujson.loads(request.body) if request.body else {}
    start_time = params.get('start_time', None)
    end_time = params.get('end_time', None)
    target_tag = params.get('tags', [])
    skip = params.get('skip', 0)
    threshold = params.get('threshold', 0.95)
    single = int(params.get('single', 50))

    try:
        start_time = str_to_str_datetime_utc(start_time) if start_time else None
        end_time = str_to_str_datetime_utc(end_time) if end_time else None
    except Exception as e:
        resp = init_http_not_found('Data time format error')
        return make_json_response(HttpResponseBadRequest, resp)

    number = 1 if user_id else number
    today = timezone.now().strftime('%Y-%m-%d')

    json_name = 'track\\{}\\{}\\{}\\{}\\{}\\{}\\{}\\{}.json'
    json_name = json_name.format(user_id, number, single, None if not start_time else start_time.replace(' ', '-'),
                                 None if not end_time else end_time.replace(' ', '-'),
                                 None if len(target_tag) == 0 else '-'.join(sorted(target_tag)), skip, today)

    try:
        result_file = json_storage_handler.download(json_name)
        results = ujson.load(result_file)
        timer = (time.time() - start_timer)

        make_this_point(len(results), timer)
        resp = init_http_success()
        resp['data'].update(results)
        return make_json_response(HttpResponse, resp)
    except Exception as e:
        pass

    mango = statistics_track_random(start_time=start_time, end_time=end_time, user_id=user_id, limit=500000, skip=skip)

    while True:
        try:
            tweets = tweet_couch_db.find(mango)
            break
        except Exception as e:
            logger.debug('Query Timeout %s' % e)
            influxdb_handler.make_point(key='api/statistics/track/:user_id/', method='GET', error=500, prefix='API')
            continue

    results = {}
    geo_exists = {}
    for tweet in tweets:
        user = tweet.get('user')
        results.update({user: []}) if user not in results else None
        geo_exists.update({user: []}) if user not in geo_exists else None
        tags = tweet.get('tags')
        result_tags = {}
        if 'gluttony' in target_tag:
            _result_tags = get_tags(tags, 'food179', threshold, ['non_food'])
            if _result_tags:
                result_tags.update({'gluttony': _result_tags})
        if 'lust' in target_tag:
            _result_tags = get_tags(tags, 'nsfw', threshold, ['neutral', 'drawings', 'sexy'])
            if _result_tags:
                result_tags.update({'lust': _result_tags})
        _result_tags = get_tags(tags, 'text', threshold)
        for _tag in _result_tags:
            if _tag in target_tag:
                result_tags.update({'text': _tag + '.text'})
            elif 'emotion' in target_tag and _tag in ['positive', 'negative', 'neutral']:
                result_tags.update({'emotion': _tag})

        if tweet.get('geo') not in geo_exists[user] and len(results[user]) < single and ((target_tag and result_tags) or not target_tag):
            geo_exists[user].append(tweet.get('geo'))
            results[user].append(dict(
                time=parse_datetime(tweet.get('date')).astimezone(timezone.get_current_timezone()).strftime(
                    '%Y-%m-%d %H:%M:%S%z'),
                geo=tweet.get('geo'),
                img_id=tweet.get('img_id'),
                tags=result_tags
            ))

    results = dict(sorted(results.items(), key=lambda item: len(item[1]), reverse=True)[0: number])
    for user in results:
        results[user].sort(key=lambda x: x.get('time'))
        
    json_file = ujson.dumps(results)
    try:
        json_storage_handler.upload(json_name, json_file)
    except Exception as e:
        json_storage_handler.reconnect()
        json_storage_handler.upload(json_name, json_file)

    timer = (time.time() - start_timer)

    make_this_point(len(results), timer)
    resp = init_http_success()
    resp['data'].update(results)
    return make_json_response(HttpResponse, resp)


if __name__ == '__main__':
    # import datetime
    #
    # today = datetime.datetime.now().strftime('%Y-%m-%d')
    # json_name = 'track\\{}\\{}\\{}\\{}\\{}\\{}\\{}.json'.format(None, 100, None, None, None, None, today)
    # print(json_name)
    # statistics_track_get(None)
    temp = []
    if temp:
        print(1)
