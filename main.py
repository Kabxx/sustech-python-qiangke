import asyncio
import json
import os.path
import time

import aiohttp
import lxml.etree
import requests
import yaml

CONFIG_PATH = './config.yaml'
CACHE_PATH = './cache.json'

_config: dict = {}
_info: dict = {}
_http: dict = {}
_cache: dict = {}

_courses: list = []


class Log:
    @staticmethod
    def success(msg: str):
        print(f'\033[32m[SUCCESS] {msg}\033[0m')

    @staticmethod
    def error(msg: str):
        print(f'\033[31m[ERROR] {msg}\033[0m')

    @staticmethod
    def info(msg: str):
        print(f'\033[38m[INFO] {msg}\033[0m')

    @staticmethod
    def warning(msg: str):
        print(f'\033[33m[WARNING] {msg}\033[0m')


class MessageException(Exception):
    pass


class ConfigLoadException(MessageException):
    pass


class CacheLoadException(MessageException):
    pass


class LoginException(MessageException):
    pass


class CookieExpireException(Exception):
    pass


def auto_update(fun):
    async def wrapper(*args, **kwargs):
        while True:
            try:
                return await fun(*args, **kwargs)
            except CookieExpireException:
                Log.warning('身份认证信息已过期, 重新进行身份认证')
                _http['cookies'] = await get_cookie()

    return wrapper


async def load_config() -> None:
    Log.info('正在加载配置文件')
    try:
        global _config, _info, _http, _courses
        try:
            with open(CONFIG_PATH, mode='r', encoding='utf8') as config_file:
                _config = yaml.safe_load(config_file)
        except:
            raise ConfigLoadException('配置文件解析失败')
        # load fields
        if 'info' not in _config:
            raise ConfigLoadException('配置文件缺少字段 "info"')
        if 'http' not in _config:
            raise ConfigLoadException('配置文件缺少字段 "http"')
        if 'courses' not in _config:
            raise ConfigLoadException('配置文件缺少字段 "courses"')
        _info = _config['info']
        _http = _config['http']
        _courses = _config['courses']
        if 'retry' not in _info or not isinstance(_info['retry'], bool):
            _info['retry'] = False
        if 'verify_cache' not in _info or not isinstance(_info['verify_cache'], bool):
            _info['verify_cache'] = True
        # verify fields
        if not isinstance(_info, dict):
            raise ConfigLoadException('"info" 字段不是对象')
        if not isinstance(_http, dict):
            raise ConfigLoadException('"http" 字段不是对象')
        if not isinstance(_courses, list):
            raise ConfigLoadException('"http" 字段不是数组')
        # load id, password
        if 'id' not in _info or 'password' not in _info:
            raise ConfigLoadException('"info" 字段中需要包含 id, password')
        # load cookie
        if 'cookies' in _http and _http['cookies']:
            if 'JSESSIONID' in _http['cookies'] \
                    and 'route' in _http['cookies'] \
                    and _http['cookies']['JSESSIONID'] \
                    and _http['cookies']['route']:
                Log.success('已从配置文件中加载身份认证信息')
                return
        Log.warning('配置文件中未包含身份认证信息, 正在尝试获取')
        _http['cookies'] = await get_cookie()
    except MessageException as e:
        raise e
    except Exception as e:
        raise ConfigLoadException(f'未知错误: {e}')


async def load_cache() -> None:
    Log.info('正在加载缓存文件')
    # get semester data
    semester = await get_semester()
    # get selected courses
    selected = await get_selected_courses(semester)
    # try load _cache file
    global _cache
    if not os.path.exists(CACHE_PATH):
        Log.warning('缓存文件不存在, 正在重新获取课程信息')
    else:
        try:
            with open(CACHE_PATH, mode='r') as cache_file:
                _cache = json.load(cache_file)

            if not _info['verify_cache'] or \
                    (_cache['id'] == _info['id'] and _cache['semester'] == semester and set(_cache['selected']) == set(selected)):
                Log.success(f'{"" if _info["verify_cache"] else "缓存文件校验关闭, "} 成功从缓存文件加载课程信息')
                return
        except:
            Log.warning('缓存文件解析失败, 正在重新获取课程信息')
    # init cache
    _cache = {'id': _info['id'], 'semester': semester,
              'courses': {}, 'selected': selected}
    # _cache expire or not exist, get all courses
    task = []
    for keyword, name in {
        'bxxk': '通识必修选课',
        'xxxk': '通识选修选课',
        'kzyxk': '培养方案内课程',
        'zynknjxk': '非培养方案内课程',
        'jhnxk': '计划内选课新生',
        'cxxk': '重修选课',
    }.items():
        task.append(
            asyncio.create_task(
                load_courses(semester, keyword, name)
            )
        )
    await asyncio.gather(*task)
    with open(CACHE_PATH, mode='w') as fd:
        fd.write(json.dumps(_cache))
    Log.success('已将课程信息写入缓存文件')


@auto_update
async def get_semester() -> dict:
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        url='https://tis.sustech.edu.cn/Xsxk/queryXkdqXnxq',
                        data={
                            'mxpylx': 1
                        },
                        headers=_http['headers'],
                        cookies=_http['cookies'],
                        allow_redirects=False,
                ) as res:
                    if res.status == 302:
                        raise CookieExpireException
                    semester = json.loads(await res.read())
                    Log.success('成功获取学期信息')
                    return semester
        except CookieExpireException as e:
            raise e
        except Exception:
            Log.warning('获取学期信息失败, 正在尝试重新获取')


@auto_update
async def get_selected_courses(semester: dict):
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url='https://tis.sustech.edu.cn/Xsxk/queryYxkc',
                    headers=_http['headers'],
                    cookies=_http['cookies'],
                    data={
                        "p_xn": semester['p_xn'],
                        "p_xq": semester['p_xq'],
                        "p_xnxq": semester['p_xnxq'],
                        "p_pylx": 1,
                        "mxpylx": 1,
                        "p_xkfsdm": 'yixuan',
                        "pageNum": 1,
                        "pageSize": 1000
                    },
                    allow_redirects=False,
                ) as res:
                    if res.status == 302:
                        raise CookieExpireException
                    return [course['rwmc'] for course in json.loads(await res.read())['yxkcList']]
        except CookieExpireException as e:
            raise e
        except:
            Log.warning(f'获取已选课程失败, 正在尝试重新获取')


@auto_update
async def load_courses(semester: dict, keyword: str, name: str) -> None:
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        url='https://tis.sustech.edu.cn/Xsxk/queryKxrw',
                        headers=_http['headers'],
                        cookies=_http['cookies'],
                        data={
                            "p_xn": semester['p_xn'],
                            "p_xq": semester['p_xq'],
                            "p_xnxq": semester['p_xnxq'],
                            "p_pylx": 1,
                            "mxpylx": 1,
                            "p_xkfsdm": keyword,
                            "pageNum": 1,
                            "pageSize": 1000
                        },
                        allow_redirects=False,
                ) as res:
                    if res.status == 302:
                        raise CookieExpireException
                    for course in json.loads(await res.read())['kxrwList']['list']:
                        _cache['courses'][course['rwmc']] = {
                            'id': course['id'],
                            'name': course['rwmc'],
                            'kind': keyword,
                        }
                    Log.success(f'已成功获取 "{name}" 的全部课程')
                    return
        except CookieExpireException as e:
            raise e
        except:
            Log.warning(f'获取 "{name}" 的课程信息失败, 正在尝试重新获取')


async def get_cookie() -> dict[str, str]:
    async def get_cas_cookies() -> dict[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    url='https://cas.sustech.edu.cn/cas/login',
                    data={
                        'username': _info['id'],
                        'password': _info['password'],
                        'execution': lxml.etree.HTML(
                            requests.get(url='https://cas.sustech.edu.cn/cas/login').content).xpath(
                            '//input[@name="execution"]/@value'),
                        '_eventId': 'submit',
                        'geolocation': ''
                    },
                    headers=_http['headers'],
            ) as res:
                cookies_ = {
                    'TGC': res.cookies['TGC'].value
                }
                Log.success('成功获取CAS身份认证信息')
                return cookies_

    async def get_tis_cookies() -> dict[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    url='https://tis.sustech.edu.cn/authentication/main',
                    headers=_http['headers'],
                    allow_redirects=False,
            ) as res:
                cookies_ = {
                    'JSESSIONID': res.cookies['JSESSIONID'].value,
                    'route': res.cookies['route'].value,
                }
            async with session.get(
                    url='https://cas.sustech.edu.cn/cas/login?service=https://tis.sustech.edu.cn/cas',
                    headers=_http['headers'],
                    cookies=await get_cas_cookies(),
                    allow_redirects=False,
            ) as res:
                if 'Location' not in res.headers:
                    raise LoginException
                ticket = res.headers['Location']
            async with session.get(
                    url=ticket,
                    cookies=cookies_,
                    headers=_http['headers'],
                    allow_redirects=False,
            ) as res:
                if res.status != 302 or \
                        res.headers['Location'] != 'https://tis.sustech.edu.cn/authentication/main':
                    raise LoginException
                Log.success('成功获取TIS身份认证信息')
            return cookies_
    while True:
        try:
            cookies = await get_tis_cookies()
            Log.success(
                f'身份认证信息获取成功: JSESSIONID: {cookies["JSESSIONID"]}, route: {cookies["route"]}')
            return cookies
        except:
            if _info['retry']:
                Log.warning('身份认证信息获取失败, 正在重试')
            else:
                raise LoginException('身份认证信息获取失败')


def verify_course() -> None:
    global _courses
    courses = []
    for course in _courses:
        if course not in _cache['courses']:
            Log.warning(f'"{course}" - 课程名称已选择或不存在, 跳过该课程')
        else:
            courses.append(_cache['courses'][course])
    _courses = courses


def remove_course(course: dict) -> None:
    if course['name'] == _courses[0]['name']:
        _courses.pop(0)


@auto_update
async def select_course() -> bool:
    if len(_courses) <= 0:
        return False
    semester = _cache['semester']
    course = _courses[0]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    url='https://tis.sustech.edu.cn/Xsxk/addGouwuche',
                    headers=_http['headers'],
                    cookies=_http['cookies'],
                    data={
                        "p_pylx": 1,
                        "p_xktjz": "rwtjzyx",
                        "p_xn": semester['p_xn'],
                        "p_xq": semester['p_xq'],
                        "p_xnxq": semester['p_xnxq'],
                        "p_xkfsdm": course['kind'],
                        "p_id": course['id'],
                        "p_sfxsgwckb": 1,
                    },
                    allow_redirects=False,
            ) as res:
                if res.status == 302:
                    raise CookieExpireException
                message = json.loads(await res.read())['message']
                if "成功" in message:
                    Log.success(f'选课 "{course["name"]}" {message}, 进行下一课程')
                    remove_course(course)
                    return False
                elif '冲突' in message or \
                        '已选' in message or \
                        '已满' in message:
                    Log.info(f'"{course["name"]}" {message}, 跳过该课程')
                    remove_course(course)
                    return False
                elif '选课请求频率过高' in message:
                    Log.info(f'"{course["name"]}" {message}, 正在重试')
                    return False
                else:
                    Log.info(f'"{course["name"]}" {message}, 等待重试')
                    return True
    except CookieExpireException as e:
        raise e
    except:
        Log.warning(f'选课 "{course["name"]}" 时发生未知错误, 正在重试')
        return False


async def main() -> None:
    try:
        await load_config()
    except MessageException as e:
        Log.error(f'加载配置文件失败: {e}')
        return
    await load_cache()
    verify_course()
    while len(_courses) > 0:
        try:
            start = time.monotonic()
            wait = await asyncio.wait_for(asyncio.shield(select_course()), timeout=1.2)
            if wait:
                end = time.monotonic()
                last = 1.2 - (end - start)
                if last > 0:
                    await asyncio.sleep(last)
            else:
                await asyncio.sleep(0.1)
        except LoginException as e:
            Log.error(f'{e}')
            return
        except asyncio.TimeoutError:
            pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except:
        pass
