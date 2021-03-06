# coding=utf-8
import sys, requests, re, threading, time, json
import Queue
import multiprocessing
from bs4 import BeautifulSoup
from sqlalchemy import exc
from app.utils import get_availalbe_proxy, Trie
from app.models import ArtistCategory, Artist, Album, Song, Comment
from app import Session, baseurl
from app.api import get_params, get_encSecKey


def get_artist_category_ids():
    session = Session()
    url = baseurl + '/discover/artist'
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    artist_category_id_list = []
    for item in soup.find_all(class_='cat-flag'):
        href = item['href']
        catname = item.string
        match = re.match('.*id=(\d{4})$', href)
        if match:
            catid = match.group(1)
            print catid, repr(catname)
            artist_category_id_list.append(catid)
            if not session.query(ArtistCategory).get(catid):
                artist_category = ArtistCategory()
                artist_category.id = catid
                artist_category.name = catname.encode('utf-8')
                session.add(artist_category)
                session.commit()
    Session.remove()
    return artist_category_id_list


def get_artist_by_category_id(artist_category_id_list):
    session = Session()
    artist_id_list = []
    for catid in artist_category_id_list:
        for initial in [0]+range(ord('A'), ord('Z') + 1):
            url = baseurl + '/discover/artist/cat?id=%s&initial=%d' % (catid, initial)
            print url
            r = requests.get(url)
            soup = BeautifulSoup(r.text, 'html.parser')
            for item in soup.find_all('a'):
                match = re.match('/artist\?id=(\d+)$', item['href'])
                if match:
                    artist_id = match.group(1)
                    artist_name = item['title'][:-3]
                    artist_id_list.append(artist_id)
                    if not session.query(Artist).get(artist_id):
                        artist = Artist()
                        artist.id = artist_id
                        artist.name = artist_name.encode('utf-8')
                        artist.category_id = catid
                        session.add(artist)
                        session.commit()
    Session.remove()
    return artist_id_list


def get_songid_by_artist(artist_list, song_list_filename, sem):
    # print '%s start' % threading.current_thread().getName()
    proxies = None
    album_list = []
    song_list = []
    artist_count = 0
    album_count = 0
    # 遍历歌手
    current_artist_count = 0
    total_artist_count = len(artist_list)
    for artist_id in artist_list:
        current_artist_count += 1
        url = baseurl + '/artist/album?id=%s&limit=200' % artist_id
        print 'active_thread:%d current_thread:%s %s artist_id:%s %d/%d' % (
        threading.active_count(), threading.current_thread().getName(), url, artist_id, current_artist_count, total_artist_count)
        while True:
            try:
                r = requests.get(url, proxies=proxies)
            except requests.exceptions.RequestException:
                proxies = get_availalbe_proxy()
            else:
                if r.status_code != 200:
                    proxies = get_availalbe_proxy()
                else:
                    break
        soup = BeautifulSoup(r.text, 'html.parser')
        # 遍历所有专辑
        for item in soup.find_all(id='m-song-module'):
            album_li_list = item.find_all('li')
            total_album_count = len(album_li_list)
            for li in album_li_list:
                # print li
                match = re.match('.*id=(\d+)$', li.find('a')['href'])
                if match:
                    alb_id = match.group(1)
                    album_count += 1
                    url = baseurl + '/album?id=%s' % alb_id
                    #print 'active_thread:%d current_thread:%s %s album_id:%s %d/%d' % (
                    #threading.active_count(), threading.current_thread().getName(), url, alb_id, album_count,
                    #total_album_count)
                    while True:
                        try:
                            r = requests.get(url, proxies=proxies)
                        except requests.exceptions.RequestException:
                            proxies = get_availalbe_proxy()
                        else:
                            if r.status_code != 200:
                                proxies = get_availalbe_proxy()
                            else:
                                break
                    soup = BeautifulSoup(r.text, 'html.parser')
                    for item in soup.find_all('a', href=re.compile('\/song\?id=\d+')):
                        song_href = item['href']
                        match = re.match('.*id=(\d+)$', song_href)
                        if match:
                            song_id = match.group(1)
                            song_list.append(song_id)

    with open(song_list_filename,'a') as fp:
        fp.write('\n'.join(song_list))
        fp.write('\n')

    sem.release()

def get_song_details(song_list, sem):
    print '%s start' % threading.current_thread().getName()
    proxies = None
    db_song = []
    db_album = []
    db_comment = []
    error_file = open(threading.current_thread().getName() + '.txt', 'a')
    # 遍历songid_list
    curcount = 0
    totalcount = len(song_list)
    for song_id in song_list:
        curcount += 1
        url = 'http://music.163.com/api/song/detail/?id=%s&ids=[%s]' % (song_id, song_id)
        song = None
        flag = False
        while True:
            try:
                r = requests.get(url, proxies=proxies)
            except requests.exceptions.RequestException:
                proxies = get_availalbe_proxy()
            else:
                if r.status_code != 200:
                    proxies = get_availalbe_proxy()
                else:
                    try:
                        song = json.loads(r.content)
                    except:
                        print 'parse json error'
                        continue
                    else:
                        if not len(song['songs']) > 0:
                            print 'error_song_id:%s' % song_id
                            error_file.write(song_id)
                            error_file.write('\n')
                            flag = True
                        break
        if flag:
            continue

        song_json = song['songs'][0]
        album_json = song_json['album']
        artist_json = song_json['artists'][0]

        song_id = song_json['id']
        song_name = song_json['name']
        song_duration = song_json['duration']
        artist_id = artist_json['id']
        album_id = album_json['id']
        album_name = album_json['name']
        album_size = album_json['size']
        album_cover = album_json['picUrl']
        release_time = album_json['publishTime']
        release_comp = album_json['company']

        comment_thread = song_json['commentThreadId']
        print 'active_thread:%d current_thread:%s song_id:%s %d/%d' % (
        threading.active_count(), threading.current_thread().getName(), song_id, curcount, totalcount)

        url = 'http://music.163.com/weapi/v1/resource/comments/' + comment_thread
        params = get_params(1)
        encSecKey = get_encSecKey()
        data = {
            "params": params,
            "encSecKey": encSecKey
        }
        json_comment = None
        while True:
            try:
                r = requests.post(url, data=data, proxies=proxies)
            except requests.exceptions.RequestException:
                proxies = get_availalbe_proxy()
            else:
                if r.status_code != 200:
                    proxies = get_availalbe_proxy()
                else:
                    try:
                        json_comment = json.loads(r.content)
                    except:
                        print 'parse json error'
                        continue
                    else:
                        break

        total = json_comment.get('total', 0)

        song = Song()
        song.id = song_id
        song.name = song_name
        song.duration = song_duration
        song.album_id = album_id
        song.artist_id = artist_id
        song.comment_count = total
        db_song.append(song)

        album = Album()
        album.id = album_id
        album.alb_name = album_name
        album.alb_size = album_size
        album.alb_cover = album_cover
        album.artist_id = artist_id
        try:
            timestamp = str(release_time)
            album.release_time = time.strftime('%Y-%m-%d',
                                               time.localtime(float(timestamp[:10] + '.' + timestamp[-3:])))
        except:
            print 'timestamp error:%s' % release_time
            album.release_time = ''
        album.release_comp = release_comp
        if album.id not in [item.id for item in db_album]:
            db_album.append(album)
        hot_comments = json_comment['hotComments']
        for item in hot_comments:
            comment = Comment()
            comment.song_id = int(song_id)
            # 过滤emoji表情
            try:
                highpoints = re.compile(u'[\U00010000-\U0010ffff]')
            except re.error:
                highpoints = re.compile(u'[\uD800-\uDBFF][\uDC00-\uDFFF]')

            content = highpoints.sub(u'??', item['content'])
            comment.content = content[:300]
            comment.liked_count = int(item['likedCount'])
            timestamp = str(item['time'])
            comment.timestamp = time.strftime('%Y%m%d%H%M%S',
                                              time.localtime(float(timestamp[:10] + '.' + timestamp[-3:])))
            comment_user = item.get('user')
            if comment_user:
                comment.user_id = int(comment_user.get('userId', 0))
                comment.nickname = comment_user.get('nickname')
            db_comment.append(comment)
    error_file.close()
    print '%s:start update database' % threading.current_thread().getName()
    # 更新数据库
    while True:
        try:
            session = Session()
            session.execute(
                Song.__table__.insert().prefix_with('IGNORE'),
                [{'id': song.id,
                  'name': song.name,
                  'duration': song.duration,
                  'artist_id': song.artist_id,
                  'album_id': song.album_id,
                  'artist_id': song.artist_id,
                  'comment_count': song.comment_count
                  } for song in db_song]
            )
            session.execute(
                Comment.__table__.insert().prefix_with('IGNORE'),
                [{'song_id': comment.song_id,
                  'content': comment.content,
                  'liked_count': comment.liked_count,
                  'timestamp': comment.timestamp,
                  'user_id': comment.user_id,
                  'nickname': comment.nickname
                  } for comment in db_comment]
            )
            for album in db_album:
                session.execute(
                    Album.__table__.insert().prefix_with('IGNORE'),
                    {'id': album.id,
                     'alb_name': album.alb_name,
                     'alb_desc': '',
                     'alb_cover': album.alb_cover,
                     'alb_size': album.alb_size,
                     'artist_id': album.artist_id,
                     'release_time': album.release_time,
                     'release_comp': album.release_comp}
                )
        except exc.OperationalError:
            print 'OperationalError'
            session.rollback()
            continue
        else:
            session.commit()
            Session.remove()
            break
    print '%s end' % threading.current_thread().getName()
    sem.release()


if __name__ == "__main__":
    album_thread_count = int(sys.argv[1])
    song_thread_count = int(sys.argv[2])
    concurrent = int(sys.argv[3])

    #lock = threading.Lock()
    song_list_filename = 'song_list2.txt'

    # artist_category_list = get_artist_category_ids()
    # artist_list_spider = get_artist_by_category_id(artist_category_list)

    # artist_list_spider = []
    # session = Session()
    # for artist in session.query(Artist):
    #     artist_list_spider.append(artist.id)
    # Session.remove()
    #
    # session = Session()
    # sql_result = session.execute('select distinct artist_id from album').fetchall()
    # artist_list_sql = [str(item[0]) for item in sql_result]
    # Session.remove()
    #
    # artist_trie_sql = Trie()
    # for artist_sql in artist_list_sql:
    #     artist_trie_sql.insert(artist_sql)
    #
    # artist_list = [id for id in artist_list_spider if not artist_trie_sql.search(id)]
    # artist_count = len(artist_list)
    # print 'artist count:%d' % artist_count

    try:
        with open(song_list_filename, 'r') as fp:
            song_list_file = fp.read().split('\n')
    except IOError as err:
        print str(err)
        album_thread_list = []
        artist_semaphore = threading.Semaphore(concurrent)
        for i in range(album_thread_count):
            if artist_semaphore.acquire():
                begin = artist_count / album_thread_count * i
                end = artist_count / album_thread_count * (i + 1)
                artist_list_slice = artist_list[begin:end]
                t = threading.Thread(target=get_songid_by_artist,
                                     args=(artist_list_slice, song_list_filename, artist_semaphore,))
                album_thread_list.append(t)
                t.start()
        for t in album_thread_list:
            t.join()
        print 'successfully saved to song_list_result.txt'
        with open(song_list_filename, 'r') as fp:
            song_list_file = fp.read().split('\n')

    session = Session()
    sql_result = session.execute('select id from song').fetchall()
    song_list_sql = [str(item[0]) for item in sql_result]
    Session.remove()

    song_trie_sql = Trie()
    for song_sql in song_list_sql:
        song_trie_sql.insert(song_sql)

    song_list = [id for id in song_list_file if not song_trie_sql.search(id)]
    song_count = len(song_list)
    print 'song count:%d' % song_count

    song_semaphore = threading.Semaphore(concurrent)
    song_thread_list = []
    for i in range(song_thread_count):
        if song_semaphore.acquire():
            begin = song_count / song_thread_count * i
            end = song_count / song_thread_count * (i + 1)
            song_list_slice = song_list[begin:end]
            t = threading.Thread(target=get_song_details, args=(song_list_slice, song_semaphore,))
            song_thread_list.append(t)
            t.start()
