import re
import json
import sys
import threading
import time
import random
import pymysql
import requests
from retrying import retry
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,'
              'application/signed-exchange;v=b3;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Connection': 'keep-alive',
    'Host': 'pan.baidu.com',
    'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="98", "Google Chrome";v="98"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'Referer': 'https://pan.baidu.com'
}

# URLs
BDSTOKEN_URL = 'https://pan.baidu.com/api/loginStatus?clienttype=0&web=1'
VERIFY_URL = 'https://pan.baidu.com/share/verify'
TRANSFER_URL = 'https://pan.baidu.com/share/transfer'
TRANSFER_REPID_URL = 'https://pan.baidu.com/api/rapidupload'
CREATE_DIR_URL = 'https://pan.baidu.com/api/create?a=commit'
GET_DIR_LIST_URL = 'https://pan.baidu.com/api/list?order=time&desc=1&showempty=0&web=1&page=1&num=1000'


class Database:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Database, cls).__new__(cls)
        return cls._instance

    def __init__(self, host=sys.argv[3], user=sys.argv[4], password=sys.argv[5], database=sys.argv[6]):
        if not hasattr(self, 'connection'):
            self.host = host
            self.user = user
            self.password = password
            self.database = database
            self.connection = None
            print('数据库配置信息已初始化。', flush=True)

    def connect(self):
        """创建数据库连接。"""
        if self.connection is None:
            print('正在连接数据库...', flush=True)
            self.connection = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database
            )
            print('数据库连接成功！', flush=True)

    def close(self):
        """关闭数据库连接。"""
        if self.connection:
            print('正在关闭数据库连接...', flush=True)
            self.connection.close()
            self.connection = None
            print('数据库连接已关闭。', flush=True)

    def execute(self, query, params=None):
        """执行数据库查询。"""
        print(f'正在执行查询: {query}', flush=True)
        self.connect()
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            self.connection.commit()
            results = cursor.fetchall()
            print('查询执行成功，结果已返回。', flush=True)
            return results

    def execute_many(self, query, params):
        """执行一批数据库查询。"""
        print(f'正在执行批量查询: {query}', flush=True)
        self.connect()
        with self.connection.cursor() as cursor:
            cursor.executemany(query, params)
            self.connection.commit()
            print('批量查询执行成功。', flush=True)

    def __enter__(self):
        """上下文管理器入口。"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """上下文管理器出口。"""
        self.close()


class PanTransfer:
    def __init__(self, cookie, dir_name):
        print('初始化PanTransfer类...', flush=True)
        self.headers = dict(HEADERS)
        self.headers['Cookie'] = cookie
        self.dir_name = dir_name
        self.bdstoken = None
        self.timeout = 10
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update(self.headers)
        print('请求头信息已设置。', flush=True)
        self.get_bdstoken()
        self.create_dir()

    @retry(stop_max_attempt_number=5, wait_fixed=1000)
    def post(self, url, post_data):
        print(f'正在发送POST请求到: {url}', flush=True)
        response = self.session.post(url=url, data=post_data, timeout=self.timeout, allow_redirects=False, verify=False)
        print('POST请求已发送。', flush=True)
        return response

    @retry(stop_max_attempt_number=5, wait_fixed=1000)
    def get(self, url):
        print(f'正在发送GET请求到: {url}', flush=True)
        response = self.session.get(url=url, timeout=self.timeout, allow_redirects=True)
        print('GET请求已发送。', flush=True)
        return response

    def get_bdstoken(self):
        print('正在获取bdstoken...', flush=True)
        response = self.get(BDSTOKEN_URL)
        bdstoken_list = re.findall('"bdstoken":"(.*?)"', response.text)
        if bdstoken_list:
            self.bdstoken = bdstoken_list[0]
            print(f'bdstoken获取成功: {self.bdstoken}', flush=True)
        else:
            raise ValueError('获取bdstoken失败！')

    def transfer_files_repid(self, rapid_data):
        print(f'正在进行快速转存文件，数据: {rapid_data}', flush=True)
        url = f"{TRANSFER_REPID_URL}?bdstoken={self.bdstoken}"
        post_data = {
            'path': f"{self.dir_name}/{rapid_data[3]}",
            'content-md5': rapid_data[0],
            'slice-md5': rapid_data[1],
            'content-length': rapid_data[2]
        }
        response = self.post(url, post_data)
        if response.json()['errno'] == 404:
            post_data['content-md5'] = post_data['content-md5'].lower()
            post_data['slice-md5'] = post_data['slice-md5'].lower()
            response = self.post(url, post_data)
        data = response.json()
        if data['errno'] == 0:
            print('转存成功！保存位置:' + data['info']['path'])
        else:
            raise ValueError('转存失败！errno:' + str(data['errno']))

    def transfer_files(self, shareid, user_id, fs_id_list):
        print(f'正在转存文件，shareid: {shareid}, user_id: {user_id}, fs_id_list: {fs_id_list}', flush=True)
        url = f"{TRANSFER_URL}?shareid={shareid}&from={user_id}&bdstoken={self.bdstoken}"
        if not self.dir_name.strip().startswith('/'):
            self.dir_name = '/' + self.dir_name.strip()
        fsidlist = f"[{','.join(i for i in fs_id_list)}]"
        post_data = {'fsidlist': fsidlist, 'path': self.dir_name}
        response = self.post(url, post_data)
        data = response.json()
        if data['errno'] == 0:
            for each in data['extra']['list']:
                print('转存成功！保存位置:' + each['to'], flush=True)
                return True
        else:
            print('转存失败！errno:' + str(data['errno']), flush=True)
            return False

    def get_dir_list(self):
        print(f'正在获取目录列表，目录名: {self.dir_name}', flush=True)
        url = f"{GET_DIR_LIST_URL}&dir={self.dir_name}&bdstoken={self.bdstoken}"
        response = self.get(url)
        data = response.json()
        if data['errno'] == 0:
            dir_list_json = data['list']
            if not isinstance(dir_list_json, list):
                raise ValueError('没获取到网盘目录列表,请检查cookie和网络后重试!')
            print('目录列表获取成功。', flush=True)
            return dir_list_json
        else:
            raise ValueError('获取网盘目录列表失败! errno:' + str(data['errno']))

    def create_dir(self):
        print(f'正在创建目录: {self.dir_name}', flush=True)
        if self.dir_name and self.dir_name != '/':
            dir_name_list = self.dir_name.split('/')
            dir_name = dir_name_list[-1]
            dir_name_list.pop()
            path = '/'.join(dir_name_list) + '/'
            dir_list_json = self.get_dir_list()
            dir_list = [dir_json['server_filename'] for dir_json in dir_list_json]
            if dir_name and dir_name not in dir_list:
                url = f"{CREATE_DIR_URL}&bdstoken={self.bdstoken}"
                post_data = {'path': self.dir_name, 'isdir': '1', 'block_list': '[]'}
                response = self.post(url, post_data)
                data = response.json()
                if data['errno'] == 0:
                    print('创建目录成功！', flush=True)
                else:
                    print('创建目录失败！路径中不能包含以下任何字符: \\:*?"<>|', flush=True)

    def verify_link(self, link_url, pass_code):
        print(f'正在验证链接: {link_url}，提取码: {pass_code}', flush=True)
        sp = link_url.split('/')
        url = VERIFY_URL + '?surl=' + sp[-1][1:]
        post_data = {'pwd': pass_code, 'vcode': '', 'vcode_str': ''}
        response = self.post(url, post_data)
        data = response.json()
        if data['errno'] == 0:
            bdclnd = data['randsk']
            cookie = self.session.headers['Cookie']
            if 'BDCLND=' in cookie:
                cookie = re.sub(r'BDCLND=(\S+?);', f'BDCLND={bdclnd};', cookie)
            else:
                cookie += f';BDCLND={bdclnd};'
            self.session.headers['Cookie'] = cookie
            print('链接验证成功！', flush=True)
            return data
        elif data['errno'] == -9:
            raise ValueError('提取码错误！')
        else:
            raise ValueError('验证链接失败！errno:' + str(data['errno']))

    def get_share_link_info(self, link_url, pass_code):
        print(f'获取分享链接信息，链接: {link_url}, 提取码: {pass_code}', flush=True)
        self.verify_link(link_url, pass_code)
        random_sleep(start=1, end=3)
        response = self.get(link_url)
        info = re.findall(r'locals\.mset\((.*)\);', response.text)
        if not info:
            raise ValueError("获取分享信息失败！")
        print('分享信息获取成功。', flush=True)
        return json.loads(info[0])

    def get_link_data(self, link_url, pass_code):
        print(f'获取链接数据，链接: {link_url}, 提取码: {pass_code}', flush=True)
        link_info = self.get_share_link_info(link_url, pass_code)
        shareid = link_info['shareid']
        user_id = link_info['share_uk']
        file_list = [{'fs_id': i['fs_id'], 'filename': i['server_filename'], 'isdir': i['isdir']} for i in
                     link_info['file_list']]
        if not file_list:
            raise ValueError('文件列表为空！')
        print('链接数据获取成功。', flush=True)
        return {'shareid': shareid, 'user_id': user_id, 'file_list': file_list}

    def transfer_common(self, link):
        print(f'转存普通链接: {link}', flush=True)
        link_url, pass_code, unzip_code = parse_url_and_code(link)
        link_data = self.get_link_data(link_url, pass_code)
        shareid, user_id = link_data['shareid'], link_data['user_id']
        fs_id_list = [str(data['fs_id']) for data in link_data['file_list']]
        if self.transfer_files(shareid, user_id, fs_id_list):
            return True, link_data['file_list'][0]['filename']
        return False, None

    def transfer_repid(self, link):
        print(f'转存快速链接: {link}', flush=True)
        rapid_data = link.split('#', maxsplit=3)
        self.transfer_files_repid(rapid_data)

    def transfer(self, link_list, p_id):
        print('开始转存链接列表...', flush=True)
        link_list = link_format(link_list)
        db = Database()
        for link in link_list:
            try:
                print('正在转存: ' + link, flush=True)
                link_type = check_link_type(link)
                if link_type == 'common':
                    sta, filename = self.transfer_common(link)
                    if sta:
                        db.execute("UPDATE cj_data_by_hct SET file_name=%s, upload_status=1 WHERE id=%s",
                                   (filename, p_id))
                        print('转存完成！', flush=True)
                    else:
                        db.execute("DELETE FROM cj_data_by_hct WHERE id=%s", (p_id,))
                    break
                elif link_type == 'rapid':
                    # self.transfer_repid(link)
                    print('识别到快速链接，但未实现转存功能！', flush=True)
                    break
                else:
                    raise ValueError('未知链接类型')
            except Exception as e:
                print('转存错误 --- ' + str(e), flush=True)
                db.execute("DELETE FROM cj_data_by_hct WHERE id=%s", (p_id,))


def random_sleep(start=1, end=3):
    sleep_time = random.randint(start, end)
    print(f'随机等待 {sleep_time} 秒...', flush=True)
    time.sleep(sleep_time)


def check_link_type(link):
    if 'pan.baidu.com/s/' in link:
        return 'common'
    elif link.count('#') > 2:
        return 'rapid'
    else:
        return 'unknown'


def link_format(links):
    return [link + ' ' for link in links if link]


def parse_url_and_code(url):
    url = url.lstrip('链接:').strip()
    res = re.sub(r'提取码*[：:](.*)', r'\1', url).split(' ', maxsplit=2)
    link_url = res[0]
    pass_code = res[1]
    unzip_code = None
    if len(res) == 3:
        unzip_code = res[2]
    link_url = re.sub(r'\?pwd=(.*)', '', link_url)
    return link_url, pass_code, unzip_code


if __name__ == "__main__":
    dir_name = sys.argv[2]
    with Database() as db:
        results = db.execute(
            "SELECT id, download_url, download_password FROM cj_data_by_hct WHERE cj_class IN ('app./源码', '企业/公司', '其它/源码', '办公/电脑', '商城/源码','推广/交流', '整站/源码', '电影/视频', '程序/源码','空间/建站', '精品/源码', '系统/程序', '素材/源码', '网站/源码','行业/源码', '装修/教育') AND upload_status=0"
        )
        cookie = db.execute("SELECT * FROM user_ck WHERE status = 1 and pan_name = 'baidu' limit 0,1")[0][1]
    pan_transfer = PanTransfer(cookie, dir_name)
    for down in results:
        links = [f"{down[1]} {down[2]}"]
        pan_transfer.transfer(links, down[0])
