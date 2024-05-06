from typing import Optional, BinaryIO

import enum
import requests


_TIMEOUT_ = 5


class DB(enum.auto):
    HMagazines = 0
    HGame_CG = 2
    DoujinshiDB = 3
    Pixiv_Images = 5
    Nico_Nico_Seiga = 8
    Danbooru = 9
    Drawr_Images = 10
    Nijie_Images = 11
    Yandere = 12
    Openingsmoe = 13
    Shutterstock = 15
    FAKKU = 16
    HMisc = 18
    TwoDMarket = 19
    MediBang = 20
    Anime = 21
    HAnime = 22
    Movies = 23
    Shows = 24
    Gelbooru = 25
    Konachan = 26
    SankakuChannel = 27
    AnimePicturesnet = 28
    E621net = 29
    IdolComplex = 30
    Bcynet_Illust = 31
    Bcynet_Cosplay = 32
    PortalGraphicsnet = 33
    DeviantArt = 34
    Pawoonet = 35
    Madokami = 36
    MangaDex = 37
    HMisc_EHentai = 38
    Artstation = 39
    FurAffinity = 40
    Twitter = 41
    Furry_Network = 42
    ALL = 999


class Hide(enum.auto):
    NONE = 0
    KNOWN = 1
    SUSPECTED = 2
    ALL = 3


class BgColor(enum.auto):
    NONE = 'none'
    WHITE = 'white'
    BLACK = 'black'
    GREY = 'grey'


class _OutputType(enum.auto):
    HTML = 0
    XML = 1
    JSON = 2

class BasicSauce:
    def __init__(self, raw):
        result_header = raw['header']

        self.raw:        dict = raw
        self.similarity: float = float(result_header['similarity'])
        self.thumbnail:  str = result_header['thumbnail']
        self.index_id:   int = result_header['index_id']
        self.index_name: str = result_header['index_name']
        self.title:      Optional[str] = self._get_title(raw['data'])
        self.urls:       list[str] = self._get_urls(raw['data'])
        self.author:     Optional[str] = self._get_author(raw['data'])

    @staticmethod
    def _get_title(data):
        # Order is important!
        if 'title' in data:
            return data['title']
        elif 'eng_name' in data:
            return data['eng_name']
        elif 'material' in data:
            return data['material']
        elif 'source' in data:
            return data['source']
        elif 'created_at' in data:
            return data['created_at']

    @staticmethod
    def _get_urls(data):
        if 'ext_urls' in data:
            return data['ext_urls']
        elif 'getchu_id' in data:
            return [f'http://www.getchu.com/soft.phtml?id={data["getchu_id"]}']
        return []

    @staticmethod
    def _get_author(data):
        # Order is important!
        if 'author' in data:
            return data['author']
        elif 'author_name' in data:
            return data['author_name']
        elif 'member_name' in data:
            return data['member_name']
        elif 'pawoo_user_username' in data:
            return data['pawoo_user_username']
        elif 'twitter_user_handle' in data:
            return data['twitter_user_handle']
        elif 'company' in data:
            return data['company']
        elif 'creator' in data:
            if isinstance(data['creator'], list):
                return data['creator'][0]
            return data['creator']

    def __repr__(self):
        return f'<BasicSauce(title={repr(self.title)}, similarity={self.similarity:.2f})>'


class BookSauce(BasicSauce):
    def __init__(self, raw):
        super().__init__(raw)
        data = raw['data']

        self.part: str = data['part']

    def __repr__(self):
        return f'<BookSauce(title={repr(self.title)}, part={repr(self.part)}, similarity={self.similarity:.2f})>'


class VideoSauce(BasicSauce):
    def __init__(self, raw):
        super().__init__(raw)
        data = raw['data']

        self.part:     str = data['part']
        self.year:     str = data['year']
        self.est_time: str = data['est_time']

    def __repr__(self):
        return f'<VideoSauce(title={repr(self.title)}, part={repr(self.part)}, similarity={self.similarity:.2f})>'


class SauceResponse:
    _BOOK_INDEXES = [DB.HMagazines, DB.Madokami, DB.MangaDex]
    _VIDEO_INDEXES = [DB.Anime, DB.HAnime, DB.Movies, DB.Shows]

    def __init__(self, resp):
        resp_header = resp['header']
        parsed_results = self._parse_results(resp['results'])

        self.raw:                 dict = resp
        self.user_id:             int = resp_header['user_id']
        self.account_type:        int = resp_header['account_type']
        self.short_limit:         str = resp_header['short_limit']
        self.long_limit:          str = resp_header['long_limit']
        self.long_remaining:      int = resp_header['long_remaining']
        self.short_remaining:     int = resp_header['short_remaining']
        self.status:              int = resp_header['status']
        self.results_requested:   int = resp_header['results_requested']
        self.search_depth:        str = resp_header['search_depth']
        self.minimum_similarity:  float = resp_header['minimum_similarity']
        self.results_returned:    int = resp_header['results_returned']
        self.results:             list[BasicSauce] = parsed_results

    def _parse_results(self, results):
        if results is None:
            return []

        sorted_results = sorted(results, key=lambda r: float(r['header']['similarity']), reverse=True)

        parsed_results = []
        for result in sorted_results:
            index_id = result['header']['index_id']
            if index_id in self._BOOK_INDEXES:
                parsed_results.append(BookSauce(result))
            elif index_id in self._VIDEO_INDEXES:
                parsed_results.append(VideoSauce(result))
            else:
                parsed_results.append(BasicSauce(result))
        return parsed_results

    def __len__(self):
        return len(self.results)

    def __bool__(self):
        return bool(self.results)

    def __getitem__(self, item):
        return self.results[item]

    def __repr__(self):
        return (f'<SauceResponse(count={repr(len(self.results))}, long_remaining={repr(self.long_remaining)}, '
                f'short_remaining={repr(self.short_remaining)})>')

class SauceNao:
    SAUCENAO_URL = 'https://saucenao.com/search.php'

    def __init__(self,
                 api_key:  Optional[str] = None,
                 *,
                 testmode: int = 0,
                 dbmask:   Optional[int] = None,
                 dbmaski:  Optional[int] = None,
                 db:       int = DB.ALL,
                 numres:   int = 6,
                 frame:    int = 1,
                 hide:     int = Hide.NONE,
                 bgcolor:  int = BgColor.NONE,
                 ) -> None:

        params = dict()

        if api_key is not None:
            params['api_key'] = api_key
        if dbmask is not None:
            params['dbmask'] = dbmask
        if dbmaski is not None:
            params['dbmaski'] = dbmaski

        params['testmode'] = testmode
        params['db'] = db
        params['numres'] = numres
        params['hide'] = hide
        params['frame'] = frame
        params['bgcolor'] = bgcolor               # from https://saucenao.com/testing/
        params['output_type'] = _OutputType.JSON
        self.params = params

    def from_file(self, file: BinaryIO) -> SauceResponse:
        return self._search(self.params, {'file': file})

    def from_url(self, url: str) -> SauceResponse:
        params = self.params.copy()
        params['url'] = url
        return self._search(params)

    def _search(self, params, files=None):
        resp = requests.post(self.SAUCENAO_URL, params=params, files=files, timeout=_TIMEOUT_)
        status_code = resp.status_code

        if status_code == 200:
            raw = self._verify_response(resp, params)
            return SauceResponse(raw)

        # Taken from https://saucenao.com/tools/examples/api/identify_images_v1.1.py
        # Actually server returns 200 and user_id=0 if key is bad
        elif status_code == 403:
            raise BadKeyError('Invalid API key')

        elif status_code == 413:
            raise BadFileSizeError('File is too large')

        elif status_code == 429:
            if 'Daily' in resp.json()['header']['message']:
                raise LongLimitReachedError('24 hours limit reached')
            raise ShortLimitReachedError('30 seconds limit reached')

        raise UnknownApiError(f'Server returned status code {status_code}')

    @staticmethod
    def _verify_response(resp, params):
        parsed_resp = resp.json()
        resp_header = parsed_resp['header']

        status = resp_header['status']
        user_id = int(resp_header['user_id'])

        # Taken from https://saucenao.com/tools/examples/api/identify_images_v1.1.py
        if status < 0:
            raise UnknownClientError('Unknown client error, status < 0')
        elif status > 0:
            raise UnknownServerError('Unknown API error, status > 0')
        elif user_id < 0:
            raise UnknownServerError('Unknown API error, user_id < 0')

        # Request passed, but api_key was ignored
        elif user_id == 0 and 'api_key' in params:
            raise BadKeyError('Invalid API key')

        long_remaining = resp_header['long_remaining']
        short_remaining = resp_header['short_remaining']

        # Taken from https://saucenao.com/tools/examples/api/identify_images_v1.1.py
        if short_remaining < 0:
            raise ShortLimitReachedError('30 seconds limit reached')
        elif long_remaining < 0:
            raise LongLimitReachedError('24 hours limit reached')

        return parsed_resp


class SauceNaoApiError(Exception):
    pass


class UnknownApiError(SauceNaoApiError):
    pass


class UnknownServerError(UnknownApiError):
    pass


class UnknownClientError(UnknownApiError):
    pass


class BadKeyError(SauceNaoApiError):
    pass


class BadFileSizeError(SauceNaoApiError):
    pass


class LimitReachedError(SauceNaoApiError):
    pass


class ShortLimitReachedError(LimitReachedError):
    pass


class LongLimitReachedError(LimitReachedError):
    pass